# Runtime observability runbook

Chess Lab deliberately uses provider metrics plus bounded Docker stdout logs for the
single-VPS MVP. There is no Loki, ELK, Prometheus, or Grafana deployment. Production
application records are JSON Lines and can be correlated by `operation_id`, Celery
`task_id`, or `game_id`.

Run commands from the production directory. The examples never render the Compose
environment and never print credentials.

## Service and resource overview

```bash
docker compose --env-file .env.production -f compose.production.yaml ps
docker stats
docker system df
df -h
```

`docker system df` is an inspection command. Do not automate or run an unconditional
`docker system prune`: previous immutable images are rollback targets. Investigate the
exact disk consumer before removing anything.

## Read bounded logs

Replace `<service>` with `caddy`, `api`, `worker-analysis`, `worker-reports`, `db`,
or `redis`.

```bash
docker compose --env-file .env.production -f compose.production.yaml logs --tail 200 <service>
docker compose --env-file .env.production -f compose.production.yaml logs --since 1h <service>
docker compose --env-file .env.production -f compose.production.yaml logs -f <service>
```

The Docker `local` driver retains at most five 10 MB files per long-lived container.
Caddy access records contain method, path, HTTP status, duration, response size, and
declared request size. They omit all request headers, the original URI/query string,
and routine `/health` and `/ready` polling.

Never paste an unrestricted production log stream into an issue. First narrow it by
service and time, then verify that the excerpt contains no private player metadata or
operational identifiers that are unnecessary for the recipient.

## Correlate an operation

API records use a server-generated opaque `operation_id`. Copy it from an
`api.request.completed` or `api.request.failed` line and search the bounded time window:

```bash
docker compose --env-file .env.production -f compose.production.yaml logs --since 1h api | grep '<operation_id>'
```

Lichess `lichess.request.started` and its one terminal event share their own
`operation_id`; see [lichess.md](lichess.md) for the allowed integration fields and
failure categories.

Celery lifecycle events use `task_id`. Analysis events also carry `game_id`; report
events carry normalized player identity and language, but never the prompt or report
text. Expected terminal events are:

- `analysis.task.succeeded`, `analysis.task.skipped`, or `analysis.task.failed`;
- `report.task.succeeded`, `report.task.retrying`, or `report.task.failed`.

Exceptions are deliberately represented by a bounded failure category, exception
type, and at most 20 server-side stack frames. Original exception text is excluded
because an upstream client or parser may place response content in it.

## Inspect workers and queues

Celery inspection is advisory: a worker that is down cannot answer. Compare it with
PostgreSQL state and Redis queue length before taking recovery action.

```bash
docker compose --env-file .env.production -f compose.production.yaml exec worker-analysis celery -A app.tasks.celery_app.celery_app inspect active
docker compose --env-file .env.production -f compose.production.yaml exec worker-analysis celery -A app.tasks.celery_app.celery_app inspect reserved
docker compose --env-file .env.production -f compose.production.yaml exec redis redis-cli LLEN analysis
docker compose --env-file .env.production -f compose.production.yaml exec redis redis-cli LLEN reports
```

Use this read-only audit to count analyses that have remained `running` for more than
two hours. Two hours is the initial MVP alert threshold; increase it only from measured
healthy runtimes, and keep it above the longest expected analysis plus shutdown grace.

```bash
docker compose --env-file .env.production -f compose.production.yaml exec db sh -c 'psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --command "SELECT count(*) AS stale_running FROM games WHERE analysis_status = '\''running'\'' AND analysis_started_at < now() - interval '\''2 hours'\'';"'
```

A stale row is not proof that it is safe to reset. The current early-ack contract does
not recover a task after process death. Confirm that the old worker/task is no longer
alive and follow the dedicated Celery recovery runbook before any targeted mutation.

## External checks and alerts

Configure the VPS/provider monitoring plane after the production hostname and demo
dataset exist:

- HTTPS `GET /health` every one to three minutes: process and TLS liveness only;
- a separate HTTPS DB-backed demo GET such as
  `GET /api/v1/games/stats/DemoPlayer` every five minutes;
- disk warning at 75% and critical at 85% usage;
- sustained host memory above 85% for 10 minutes;
- any long-lived container stopped or restarting, and any restart-count increase;
- non-decreasing `analysis` or `reports` queue length for 15 minutes;
- any analysis still `running` past the measured stale threshold (initially two hours).

`/ready` checks Redis so protected writes fail closed; it is useful for diagnosis but
must not be treated as proof that PostgreSQL-backed demo reads work. Queue and stale-row
alerts require a small provider check or scheduled read-only script during the first
deployment phase; do not expose Redis, PostgreSQL, Docker metrics, or the Docker socket
to the public network to implement them.

### Root-only heartbeat helper

An external heartbeat complements HTTP checks by detecting a backup or host-check job
that stopped running. The heartbeat URL is a bearer secret: anyone who has it can forge
success or failure. Store each URL in the operator password manager and in a separate
root-only file on the VPS; never put it in the repository, a unit file, shell history,
or shared logs.

The first production deployment uses one heartbeat for the daily backup and a separate
heartbeat for the host guard. Create their files interactively with `sudoedit`:

```bash
sudo install -d -m 0700 -o root -g root /etc/chess-lab/monitoring
sudo install -m 0600 -o root -g root /dev/null \
  /etc/chess-lab/monitoring/backup-heartbeat.url
sudo install -m 0600 -o root -g root /dev/null \
  /etc/chess-lab/monitoring/host-guard-heartbeat.url
sudoedit /etc/chess-lab/monitoring/backup-heartbeat.url
sudoedit /etc/chess-lab/monitoring/host-guard-heartbeat.url
```

Each file contains exactly one HTTPS heartbeat URL and a final newline. For Better
Stack, use a 24-hour period plus two-hour grace for the backup, and a 15-minute period
plus ten-minute grace for the host guard. Confirm permissions without printing the
contents:

```bash
sudo stat -c '%a %U:%G %n' /etc/chess-lab/monitoring \
  /etc/chess-lab/monitoring/backup-heartbeat.url \
  /etc/chess-lab/monitoring/host-guard-heartbeat.url
```

Create `/usr/local/sbin/chess-lab-heartbeat` with `sudoedit`:

```bash
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly MONITORING_DIR=/etc/chess-lab/monitoring

case "${1:-}" in
  backup)
    heartbeat_file="${MONITORING_DIR}/backup-heartbeat.url"
    failure_suffix=""
    ;;
  host-ok)
    heartbeat_file="${MONITORING_DIR}/host-guard-heartbeat.url"
    failure_suffix=""
    ;;
  host-fail)
    heartbeat_file="${MONITORING_DIR}/host-guard-heartbeat.url"
    failure_suffix="/fail"
    ;;
  *)
    echo 'usage: chess-lab-heartbeat {backup|host-ok|host-fail}' >&2
    exit 2
    ;;
esac

heartbeat_url="$(tr -d '\r\n' <"${heartbeat_file}")"
if [[ "${heartbeat_url}" != https://uptime.betterstack.com/api/v1/heartbeat/* ]]; then
  echo 'invalid or unsupported heartbeat URL' >&2
  exit 1
fi

# Read the secret from a protected file and pass it over stdin so it is absent from the
# process arguments and shell history.
curl --config - <<EOF
url = "${heartbeat_url}${failure_suffix}"
fail
silent
show-error
max-time = 15
retry = 2
EOF

echo "heartbeat.sent target=${1}"
```

Protect and syntax-check the helper, then send one success ping to each heartbeat:

```bash
sudo chown root:root /usr/local/sbin/chess-lab-heartbeat
sudo chmod 700 /usr/local/sbin/chess-lab-heartbeat
sudo bash -n /usr/local/sbin/chess-lab-heartbeat
sudo /usr/local/sbin/chess-lab-heartbeat backup
sudo /usr/local/sbin/chess-lab-heartbeat host-ok
```

### Disk and TLS host guard

The host guard turns local disk and certificate checks into the second heartbeat. It
reports warning at 75% root-filesystem usage, critical at 85%, and failure when the
public certificate has fewer than 14 days remaining. Replace `<api.example.com>` once
while installing; do not place a URL containing credentials in this script.

Create `/usr/local/sbin/chess-lab-host-guard` with `sudoedit`:

```bash
#!/usr/bin/env bash
set -Eeuo pipefail

readonly APP_DOMAIN='<api.example.com>'
readonly DISK_WARNING_PCT=75
readonly DISK_CRITICAL_PCT=85
readonly TLS_MIN_DAYS=14

disk_usage_pct="$(df -P / | awk 'NR == 2 {gsub(/%/, "", $5); print $5}')"
if [[ ! "${disk_usage_pct}" =~ ^[0-9]+$ ]]; then
  echo 'host.guard.failed reason=disk-usage-unreadable' >&2
  /usr/local/sbin/chess-lab-heartbeat host-fail || true
  exit 1
fi

failure_reason=''
if (( disk_usage_pct >= DISK_CRITICAL_PCT )); then
  failure_reason='disk-critical'
elif (( disk_usage_pct >= DISK_WARNING_PCT )); then
  failure_reason='disk-warning'
fi

if ! timeout 20 openssl s_client \
  -connect "${APP_DOMAIN}:443" \
  -servername "${APP_DOMAIN}" \
  </dev/null 2>/dev/null \
  | openssl x509 -checkend "$((TLS_MIN_DAYS * 86400))" -noout >/dev/null; then
  if [[ -n "${failure_reason}" ]]; then
    failure_reason="${failure_reason},tls-expiring-or-unavailable"
  else
    failure_reason='tls-expiring-or-unavailable'
  fi
fi

if [[ -n "${failure_reason}" ]]; then
  /usr/local/sbin/chess-lab-heartbeat host-fail || true
  echo "host.guard.failed reason=${failure_reason} disk_usage_pct=${disk_usage_pct} tls_min_days=${TLS_MIN_DAYS}" >&2
  exit 1
fi

/usr/local/sbin/chess-lab-heartbeat host-ok
echo "host.guard.succeeded disk_usage_pct=${disk_usage_pct} tls_min_days=${TLS_MIN_DAYS}"
```

Install and validate it:

```bash
sudo chown root:root /usr/local/sbin/chess-lab-host-guard
sudo chmod 700 /usr/local/sbin/chess-lab-host-guard
sudo bash -n /usr/local/sbin/chess-lab-host-guard
sudo /usr/local/sbin/chess-lab-host-guard
```

Create `/etc/systemd/system/chess-lab-host-guard.service`:

```ini
[Unit]
Description=Chess Lab host disk and TLS guard
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/chess-lab-host-guard
User=root
Group=root
```

Create `/etc/systemd/system/chess-lab-host-guard.timer`:

```ini
[Unit]
Description=Run Chess Lab host guard every 15 minutes

[Timer]
OnBootSec=5m
OnUnitActiveSec=15m
AccuracySec=1m
Unit=chess-lab-host-guard.service

[Install]
WantedBy=timers.target
```

Validate the units, run the guard once, and enable its timer:

```bash
sudo systemd-analyze verify \
  /etc/systemd/system/chess-lab-host-guard.service \
  /etc/systemd/system/chess-lab-host-guard.timer
sudo systemctl daemon-reload
sudo systemctl start chess-lab-host-guard.service
sudo systemctl show chess-lab-host-guard.service \
  --property=Result --property=ExecMainStatus
sudo journalctl -u chess-lab-host-guard.service -n 20 --no-pager
sudo systemctl enable --now chess-lab-host-guard.timer
sudo systemctl list-timers chess-lab-host-guard.timer
```

Finally, test the complete notification path rather than only command exit codes. Warn
the incident recipient first, send an explicit failure, confirm the incident and email,
then send recovery and confirm the heartbeat returns to `Up`:

```bash
sudo /usr/local/sbin/chess-lab-heartbeat host-fail
sudo /usr/local/sbin/chess-lab-heartbeat host-ok
```

Do not automate this failure drill. Record its UTC time and result in the private
operations log without copying the heartbeat URL.

## Disk-pressure response

1. Inspect `df -h`, `docker system df`, container status, and bounded logs.
2. Identify whether space belongs to PostgreSQL data, Docker images, volumes, or an
   unexpected host file before changing anything.
3. Preserve the current and previous immutable application images for rollback.
4. Verify an off-server backup before deleting database or volume data.
5. If log files exceed the declared Docker bounds, treat that as a runtime/configuration
   incident; do not paper over it with a recurring prune.
