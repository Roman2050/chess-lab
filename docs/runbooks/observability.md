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

- HTTPS `GET /health` every minute: process and TLS liveness only;
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

## Disk-pressure response

1. Inspect `df -h`, `docker system df`, container status, and bounded logs.
2. Identify whether space belongs to PostgreSQL data, Docker images, volumes, or an
   unexpected host file before changing anything.
3. Preserve the current and previous immutable application images for rollback.
4. Verify an off-server backup before deleting database or volume data.
5. If log files exceed the declared Docker bounds, treat that as a runtime/configuration
   incident; do not paper over it with a recurring prune.
