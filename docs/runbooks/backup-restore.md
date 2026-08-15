# PostgreSQL backup and restore runbook

Chess Lab uses daily logical PostgreSQL backups in custom `pg_dump` format. Backups are
uploaded off-server to an S3-compatible object store through `rclone crypt`, which
provides client-side encryption before data leaves the VPS. Provider-side encryption is
welcome but is not a substitute for the client-side layer.

This runbook is provider-neutral. Before the first deploy, evaluate **Cloudflare R2** and
**Backblaze B2** as recommended S3-compatible options. Choose based on account security,
region, egress/API cost, lifecycle rules, versioning/object-lock support, and tested
restore behavior—not only advertised storage price.

Use placeholders for endpoint, region, bucket, access key, and credentials. Never place
secret values in shell command arguments, shell history, unit files, or this repository.

## Recovery objectives and retention

Initial MVP targets:

- backup frequency / maximum data-loss window: 24 hours;
- alert if no verified successful upload exists for 26 hours;
- minimum remote retention: 14 daily copies and 8 weekly copies;
- monthly disposable-database restore drill, plus a drill before a major migration;
- a fresh verified backup immediately before every production update or destructive DB
  operation.

These are minimums, not proof of business continuity. Adjust them after measuring dump
size, restore time, and acceptable data loss.

## Threat model and credentials

Create a dedicated bucket or exact prefix for Chess Lab. The S3 credential should have
only the object list/read/write/delete permissions required for that bucket/prefix; it
must not administer the storage account or other buckets. Enable account 2FA and access
alerts. Enable versioning and object lock/immutability only when the chosen provider
supports them and the recovery behavior has been tested. A compromised VPS credential
with delete permission is why provider-side versioning or object lock is valuable.

`rclone crypt` uses a password and optional salt to derive the client-side encryption
key. Store both in an offline/operator password manager separate from the VPS and from
the object-store credentials. Losing the crypt password/salt makes every remote backup
unrecoverable. The rclone config contains credentials and lightly obscured crypt secrets,
so protect it as a secret even when config-file encryption is not practical for an
unattended timer.

## Install and configure rclone

Install rclone from a trusted Ubuntu or official rclone package source and record the
installed version in the private operations log:

```bash
sudo apt update
sudo apt install rclone
sudo rclone version
sudo install -d -m 0700 /etc/chess-lab/backup
sudo install -m 0600 /dev/null /etc/chess-lab/backup/rclone.conf
```

Configure interactively so credentials do not enter shell history:

```bash
sudo rclone config --config /etc/chess-lab/backup/rclone.conf
```

Create two remotes:

1. `backup-s3` (`s3` backend): select the exact provider when offered, otherwise
   `Other`; enter the provider endpoint, region, bucket-scoped access key and secret.
   Do not put credentials in environment variables or command arguments.
2. `backup-crypt` (`crypt` backend): point it at
   `backup-s3:<bucket>/chess-lab-encrypted`, use standard filename encryption, and leave
   directory-name encryption **off** so provider lifecycle rules can target the visible
   `daily/` and `weekly/` class prefixes. Generate a strong password and salt and save
   both in the separate password manager.

The provider sees class prefixes and encrypted object names/content; it does not see the
database archive names or bytes. Lock down and inspect the config:

```bash
sudo chmod 600 /etc/chess-lab/backup/rclone.conf
sudo chown root:root /etc/chess-lab/backup/rclone.conf
sudo stat -c '%a %U:%G %n' /etc/chess-lab/backup/rclone.conf
sudo rclone lsd backup-crypt: --config /etc/chess-lab/backup/rclone.conf
```

Configure provider lifecycle policy on the **raw** encrypted prefixes only after a test
upload shows their exact names:

- expire `chess-lab-encrypted/daily/` objects after at least 14 days;
- expire `chess-lab-encrypted/weekly/` objects after at least 56 days;
- when versioning is supported, retain noncurrent versions long enough to recover
  accidental deletion;
- do not apply a bucket-wide rule that can delete unrelated data.

If the provider cannot express these exact lifecycle rules, use the manual audited
retention procedure below rather than an unreviewed scheduled delete.

## Install the daily backup job

Create a root-only local staging directory:

```bash
sudo install -d -m 0700 -o root -g root /var/backups/chess-lab
```

Create `/usr/local/sbin/chess-lab-backup` with `sudoedit` and the following content:

```bash
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly PROD_DIR=/opt/chess-lab
readonly COMPOSE_FILE=compose.production.yaml
readonly ENV_FILE=.env.production
readonly STAGING=/var/backups/chess-lab
readonly RCLONE_CONFIG=/etc/chess-lab/backup/rclone.conf

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
base="chess-lab-${timestamp}.dump"
partial="${STAGING}/${base}.partial"
dump="${STAGING}/${base}"
checksum="${dump}.sha256"
verify_dir="$(mktemp -d /var/tmp/chess-lab-backup-verify.XXXXXX)"

cleanup() {
  rm -f -- "${partial}"
  rm -rf -- "${verify_dir}"
}
trap cleanup EXIT

cd "${PROD_DIR}"
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T db sh -c \
  'pg_dump --format=custom --no-owner --no-privileges --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"' \
  >"${partial}"

test -s "${partial}"
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T db \
  pg_restore --list <"${partial}" >/dev/null
mv -- "${partial}" "${dump}"

(
  cd "${STAGING}"
  sha256sum "${base}" >"${base}.sha256"
)

rclone copyto "${dump}" "backup-crypt:daily/${base}" --config "${RCLONE_CONFIG}"
rclone copyto "${checksum}" "backup-crypt:daily/${base}.sha256" --config "${RCLONE_CONFIG}"

# Download through the crypt remote and compare bytes. Upload success alone is not a restore test.
rclone copyto "backup-crypt:daily/${base}" "${verify_dir}/${base}" --config "${RCLONE_CONFIG}"
cp -- "${checksum}" "${verify_dir}/${base}.sha256"
(
  cd "${verify_dir}"
  sha256sum --check "${base}.sha256"
)

if test "$(date -u +%u)" = 7; then
  rclone copyto "${dump}" "backup-crypt:weekly/${base}" --config "${RCLONE_CONFIG}"
  rclone copyto "${checksum}" "backup-crypt:weekly/${base}.sha256" --config "${RCLONE_CONFIG}"
fi

# Local copies are staging only. This exact directory/pattern is pruned only after remote verification.
find "${STAGING}" -maxdepth 1 -type f \
  \( -name 'chess-lab-*.dump' -o -name 'chess-lab-*.dump.sha256' \) \
  -mtime +3 -delete

echo "backup.completed utc=${timestamp} object=daily/${base}"
```

Make it root-only and run ShellCheck locally if available; no new project dependency is
required:

```bash
sudo chown root:root /usr/local/sbin/chess-lab-backup
sudo chmod 700 /usr/local/sbin/chess-lab-backup
```

Create `/etc/systemd/system/chess-lab-backup.service`:

```ini
[Unit]
Description=Chess Lab encrypted PostgreSQL backup
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/chess-lab-backup
# Add only after installing and testing the root-only helper from observability.md.
ExecStartPost=/usr/local/sbin/chess-lab-heartbeat backup
User=root
Group=root
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7
```

Create `/etc/systemd/system/chess-lab-backup.timer`:

```ini
[Unit]
Description=Run Chess Lab PostgreSQL backup daily

[Timer]
OnCalendar=*-*-* 03:17:00 UTC
RandomizedDelaySec=15m
Persistent=true
Unit=chess-lab-backup.service

[Install]
WantedBy=timers.target
```

Reload, run once manually, and inspect the bounded status. The first run is incomplete
until a disposable restore succeeds:

```bash
sudo systemctl daemon-reload
sudo systemctl start chess-lab-backup.service
sudo systemctl status chess-lab-backup.service --no-pager
sudo journalctl -u chess-lab-backup.service --since today --no-pager
sudo systemctl enable --now chess-lab-backup.timer
sudo systemctl list-timers chess-lab-backup.timer
```

The job fails if `pg_dump`, archive listing, upload, download, or checksum verification
fails. `ExecStartPost` runs only after the backup command succeeds; configure its
external heartbeat for a 24-hour period plus two-hour grace. Install and test the
root-only URL files and helper from [observability.md](observability.md#root-only-heartbeat-helper)
before adding that line. If external monitoring is not ready during bootstrap, omit the
line temporarily, but the production backup alert is incomplete until it is present and
a service run records both events:

```text
backup.completed utc=<timestamp> object=daily/<archive>
heartbeat.sent target=backup
```

Alert on a failed unit and on absence of either verified success signal for 26 hours.
Never log the rclone config, heartbeat URL, or output from `rclone config show` into
shared output.

## Read-only backup audit

List decrypted object names and sizes without downloading data:

```bash
sudo rclone lsf backup-crypt:daily --files-only --format 'tp' \
  --config /etc/chess-lab/backup/rclone.conf
sudo rclone lsf backup-crypt:weekly --files-only --format 'tp' \
  --config /etc/chess-lab/backup/rclone.conf
```

Confirm recent `.dump` and `.sha256` pairs, nonzero size, daily/weekly age, systemd
success, and provider lifecycle/versioning status. A listing/checksum is necessary but
does not replace a restore drill.

## Disposable restore drill

Perform monthly and before a major migration. Choose one exact remote object from the
read-only audit; do not automatically select/delete by a broad wildcard.

```bash
sudo install -d -m 0700 /var/tmp/chess-lab-restore
sudo rclone copyto backup-crypt:daily/<exact-backup.dump> \
  /var/tmp/chess-lab-restore/<exact-backup.dump> \
  --config /etc/chess-lab/backup/rclone.conf
sudo rclone copyto backup-crypt:daily/<exact-backup.dump.sha256> \
  /var/tmp/chess-lab-restore/<exact-backup.dump.sha256> \
  --config /etc/chess-lab/backup/rclone.conf
cd /var/tmp/chess-lab-restore
sudo sha256sum --check <exact-backup.dump.sha256>
```

Use a unique disposable name made only from lowercase letters, digits, and underscores.
Record it before continuing:

```bash
RESTORE_DB=chess_lab_restore_yyyymmdd
if [[ ! "$RESTORE_DB" =~ ^chess_lab_restore_[a-z0-9_]+$ ]]; then
  echo 'Unsafe restore DB name' >&2
  exit 1
fi
```

Validate the archive, create the disposable DB from `template0`, and restore with errors
treated as fatal:

```bash
cd /opt/chess-lab
sudo docker compose --env-file .env.production -f compose.production.yaml exec -T db \
  pg_restore --list </var/tmp/chess-lab-restore/<exact-backup.dump> >/dev/null
sudo docker compose --env-file .env.production -f compose.production.yaml exec -T db sh -c \
  'createdb --username "$POSTGRES_USER" --template template0 "$1"' sh "$RESTORE_DB"
sudo docker compose --env-file .env.production -f compose.production.yaml exec -T db sh -c \
  'pg_restore --exit-on-error --no-owner --no-privileges --username "$POSTGRES_USER" --dbname "$1"' \
  sh "$RESTORE_DB" </var/tmp/chess-lab-restore/<exact-backup.dump>
```

Inspect migration and core data before changing it:

```bash
sudo docker compose --env-file .env.production -f compose.production.yaml exec -T db sh -c \
  'psql --username "$POSTGRES_USER" --dbname "$1" --command \
  "SELECT version_num FROM alembic_version; SELECT count(*) AS games FROM games; SELECT count(*) AS reports FROM player_reports;"' \
  sh "$RESTORE_DB"
```

Test forward migration compatibility using the same immutable application image intended
for production:

```bash
sudo docker compose --env-file .env.production -f compose.production.yaml --profile migrate \
  run --rm -e DB_NAME="$RESTORE_DB" migrate
```

Repeat the version/count queries and record archive object, checksum result, source and
target PostgreSQL major version, pre/post Alembic revisions, duration, row counts,
operator, UTC time, and result. PostgreSQL 16 `pg_dump` cannot safely dump a newer server
major version; keep dump tooling at the same or newer major and test upgrades explicitly.

Dropping the disposable database is destructive. Do it only after the restore evidence
has been recorded and the exact name has passed the guard again:

```bash
if [[ ! "$RESTORE_DB" =~ ^chess_lab_restore_[a-z0-9_]+$ ]]; then
  echo 'Refusing drop' >&2
  exit 1
fi
sudo docker compose --env-file .env.production -f compose.production.yaml exec -T db sh -c \
  'dropdb --username "$POSTGRES_USER" "$1"' sh "$RESTORE_DB"
sudo rm -f -- /var/tmp/chess-lab-restore/<exact-backup.dump> \
  /var/tmp/chess-lab-restore/<exact-backup.dump.sha256>
```

## Production recovery to a new database

Never overwrite the only production database during first recovery. Restore into a new,
exact database name so the damaged/current database remains available for rollback and
forensics.

1. Declare an incident, stop public traffic and all writers, and record container/DB
   state:

```bash
cd /opt/chess-lab
sudo docker compose --env-file .env.production -f compose.production.yaml stop caddy api
sudo docker compose --env-file .env.production -f compose.production.yaml stop worker-analysis worker-reports
```

2. Select one exact off-server object, download, verify checksum, and inspect archive as
   in the disposable drill. Record why this recovery point was chosen.
3. Create `chess_lab_recovery_<utcstamp>` from `template0`, restore with
   `--exit-on-error`, and run the target image's forward migrations exactly as in the
   drill. Validate Alembic revision and core row counts.
4. Edit only `DB_NAME` in `.env.production` to the exact recovered database. Do not drop
   or rename the previous DB.
5. Validate Compose silently, recreate services so they receive the new setting, and
   smoke:

```bash
sudo docker compose --env-file .env.production -f compose.production.yaml --profile migrate config --quiet
sudo docker compose --env-file .env.production -f compose.production.yaml up -d db redis
sudo docker compose --env-file .env.production -f compose.production.yaml \
  up -d api worker-analysis worker-reports caddy
sudo docker compose --env-file .env.production -f compose.production.yaml ps
curl --fail --silent --show-error https://<api.example.com>/health
curl --fail --silent --show-error https://<api.example.com>/ready
curl --fail --silent --show-error https://<api.example.com>/api/v1/demo
```

6. Verify DB-backed demo reads, worker queues, logs, and create a new off-server backup of
   the recovered production database. Retain the old DB until the recovery observation
   window and backup drill pass. Any later drop requires its own exact-target review and
   verified backup.

## Manual remote retention when lifecycle rules are unavailable

Use only after the daily/weekly paths and a recent restore drill are verified. First run
the exact target as a dry run and save its object list privately:

```bash
sudo rclone delete backup-crypt:daily --min-age 14d --include 'chess-lab-*.dump*' \
  --dry-run --config /etc/chess-lab/backup/rclone.conf
sudo rclone delete backup-crypt:weekly --min-age 56d --include 'chess-lab-*.dump*' \
  --dry-run --config /etc/chess-lab/backup/rclone.conf
```

Check that at least 14 daily and 8 weekly recoverable pairs remain and that a recent
restore succeeded. Only then rerun the reviewed command without `--dry-run`. Never run
`rclone purge`, delete the crypt root, or apply a bucket-wide lifecycle rule.

## References

- [PostgreSQL 16 `pg_dump`](https://www.postgresql.org/docs/16/app-pgdump.html)
- [PostgreSQL backup and restore](https://www.postgresql.org/docs/16/backup.html)
- [rclone S3 backend](https://rclone.org/s3/)
- [rclone crypt backend](https://rclone.org/crypt/)
- [Cloudflare R2 S3 compatibility](https://developers.cloudflare.com/r2/api/s3/api/)
- [Backblaze B2 S3-compatible API](https://www.backblaze.com/docs/cloud-storage-s3-compatible-api)
