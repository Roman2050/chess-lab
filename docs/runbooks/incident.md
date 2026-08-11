# Production incident runbook

This is the first-response guide for the single-VPS Chess Lab deployment. Safety and
data preservation take priority over fast speculative restarts. Run commands from
`/opt/chess-lab`; replace placeholders explicitly.

Never paste `.env.production`, request headers, PGNs, report text/prompts, tokens,
passwords, database URLs, or unrestricted logs into an incident ticket. Use UTC and
bounded log windows.

## Declare, contain, and record

Open a private incident record with:

```text
incident ID and UTC start:
detector/symptom:
public impact:
last known good release tag/image digest:
current release tag/image digest:
last verified off-server backup:
operator and actions with UTC times:
```

Classify response:

- **critical** — suspected compromise, unrecoverable public outage, DB corruption/data
  loss, or credentials exposed;
- **high** — DB/Redis unavailable, failed migration, disk critical, or crash loop;
- **moderate** — one worker/queue degraded while public reads remain healthy.

For a critical incident, stop unrelated changes and designate one operator/action log.
Do not let multiple sessions independently “fix” the same state.

## Common read-only triage

Capture a bounded snapshot before restarts:

```bash
date -u
uptime
df -h
free -h
sudo docker compose --env-file .env.production -f compose.production.yaml ps -a
sudo docker stats --no-stream
sudo docker system df
sudo systemctl --failed
```

Read only the affected service and time window:

```bash
sudo docker compose --env-file .env.production -f compose.production.yaml \
  logs --since 30m --tail 500 <service>
```

Valid services are `caddy`, `api`, `worker-analysis`, `worker-reports`, `db`, and
`redis`. Check provider status/metrics and recent operator/deployment changes. Do not
render and share `docker compose config`, because the rendered application environment
contains secrets.

## Container down

1. Confirm whether it exited, is unhealthy, or is restart-looping:

   ```bash
   sudo docker compose --env-file .env.production -f compose.production.yaml ps -a <service>
   sudo docker compose --env-file .env.production -f compose.production.yaml \
     logs --since 30m --tail 500 <service>
   ```

2. Check host memory/disk and service dependencies before restart. For an API failure,
   check DB and Redis health; for Caddy, check API health; for a worker, inspect its
   queue/task state.
3. If the cause is understood and configuration/storage are intact, start only the
   exact service:

   ```bash
   sudo docker compose --env-file .env.production -f compose.production.yaml up -d <service>
   ```

4. Verify container state, bounded logs, `/health`, `/ready`, and the DB-backed demo as
   applicable.

Do not repeatedly restart an unexplained failure. A worker terminated mid-task requires
[celery-recovery.md](celery-recovery.md), not merely a green container.

## PostgreSQL unavailable

Public DB-backed reads, task progress, and reports may fail. Redis/Celery status alone
does not prove database health.

```bash
sudo docker compose --env-file .env.production -f compose.production.yaml ps -a db
sudo docker compose --env-file .env.production -f compose.production.yaml logs --since 30m --tail 500 db
sudo docker compose --env-file .env.production -f compose.production.yaml exec db sh -c \
  'pg_isready --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"'
df -h
sudo docker volume inspect chess-lab-production_postgres_data
```

1. Stop operator write requests. If clients are causing repeated errors, stop Caddy/API
   only after recording impact; do not stop the DB during diagnosis.
2. Check disk full, OOM/restarts, file permissions, and recent Docker/host changes.
3. If the container alone stopped and storage/config are intact, start `db`, wait for
   healthy, then validate API/workers.
4. If logs indicate corruption or failed recovery, do not delete/reinitialize the
   volume. Preserve it and recover a verified backup into a **new database** per
   [backup-restore.md](backup-restore.md#production-recovery-to-a-new-database).

Never run `initdb`, remove `postgres_data`, or restore over the only database as an
availability shortcut.

## Redis unavailable

`/ready` returns 503, protected writes fail closed, Lichess coordination is unavailable,
and Celery cannot consume/publish. PostgreSQL remains authoritative for completed data.

```bash
sudo docker compose --env-file .env.production -f compose.production.yaml ps -a redis
sudo docker compose --env-file .env.production -f compose.production.yaml logs --since 30m --tail 500 redis
sudo docker compose --env-file .env.production -f compose.production.yaml exec redis redis-cli ping
sudo docker compose --env-file .env.production -f compose.production.yaml exec redis redis-cli INFO persistence
sudo docker compose --env-file .env.production -f compose.production.yaml exec redis redis-cli INFO memory
df -h
```

1. Stop issuing operator POST requests and inspect disk/OOM/restart causes.
2. If the container alone stopped and `redis_data` is intact, start only Redis and wait
   for `PONG`/healthy.
3. Inspect `analysis` and `reports` queue lengths plus workers before resuming writes.
4. Audit DB `running`/`generating` state if Redis data or a worker was lost.

Never run `FLUSHALL`, delete queue/cooldown/quota keys, remove Redis AOF files, or delete
`redis_data` to make readiness green. Lichess lock/cooldown recovery follows
[lichess.md](lichess.md).

## Disk full or nearly full

At 75% start investigation; at 85% treat it as urgent. A full filesystem can corrupt
state and prevent logs, DB writes, Redis persistence, and image updates.

```bash
df -h
sudo du -xhd1 /var/lib/docker
sudo du -xhd1 /var/log
sudo du -xhd1 /var/backups/chess-lab
sudo docker system df -v
sudo journalctl --disk-usage
```

1. Identify the exact consumer: PostgreSQL volume, images/build cache, container logs,
   system journal, backup staging, or unrelated host data.
2. Confirm the latest off-server backup and checksum/restore status before deleting any
   database-adjacent file.
3. Preserve the current and previous immutable application images for rollback.
4. If verified uploads exist, remove only exact old staging files under
   `/var/backups/chess-lab` using the retention rule in the backup runbook.
5. Archive incident evidence before deliberately vacuuming old journal entries or
   removing a specifically identified unused image.
6. Recheck filesystem space, containers, DB/Redis health, queues, and backups.

Never run unconditional `docker system prune`, delete Docker volumes, manually remove
PostgreSQL/Redis files, or use a broad recursive delete. If safe cleanup cannot restore
headroom, expand the disk/provider volume before resuming writes.

## Queue backlog

A non-decreasing queue for 15 minutes is an initial alert, not automatic evidence of
failure.

```bash
sudo docker compose --env-file .env.production -f compose.production.yaml exec redis redis-cli LLEN analysis
sudo docker compose --env-file .env.production -f compose.production.yaml exec redis redis-cli LLEN reports
sudo docker compose --env-file .env.production -f compose.production.yaml exec worker-analysis \
  celery -A app.tasks.celery_app.celery_app inspect active
sudo docker compose --env-file .env.production -f compose.production.yaml exec worker-analysis \
  celery -A app.tasks.celery_app.celery_app inspect reserved
sudo docker stats --no-stream
```

1. Stop issuing new operator requests for the affected operation.
2. Verify the correct worker is up and subscribed to the correct queue; inspect its
   terminal/retry logs and provider/Stockfish dependency.
3. Distinguish healthy slow consumption from a stuck/crashing worker using multiple
   queue samples, task durations, CPU/RAM, and terminal events.
4. Keep default concurrency `1` on the 2 vCPU/4 GB VPS. Do not increase concurrency
   during an incident without measured capacity; doing so can worsen OOM/crashes.
5. After fixing the cause, observe that the queue trends downward and DB progress moves.

Never purge the queue. If individual stale DB rows exist, use exact recovery in
[celery-recovery.md](celery-recovery.md).

## Repeated worker crash

Separate the roles: analysis failures usually involve Stockfish/PGN/resource state;
report failures usually involve the external LLM, timeout/retry, or egress.

1. Capture Compose state, restart history, last 500 bounded log lines, task/queue state,
   memory/disk, image digest, and recent config changes.
2. To stop a restart storm, stop only the affected worker after identifying any active
   task:

   ```bash
   sudo docker compose --env-file .env.production -f compose.production.yaml stop <worker-service>
   ```

3. For analysis, verify the image's Stockfish binary, configured `Threads=1`, `Hash=128`,
   container memory, and exact `game_id`. For reports, verify egress/DNS, provider status,
   model, spending limit, and expected bounded LLM retries without exposing prompts/keys.
4. Roll back to the previous verified digest if the crash began with a release and DB
   schema is compatible. Otherwise fix the exact external/config issue and start one
   worker.
5. Reconcile tasks only after worker stability. Forced/crashed tasks may have stale DB
   state; follow the recovery runbook.

Do not create a restart loop, raise resource/concurrency limits blindly, or mass-reset DB
rows.

## Failed migration

A migration failure is a deployment stop condition. The deployment procedure keeps the
new API/workers down until migration succeeds.

1. Preserve the migration command output, new/previous image digests, pre-deploy backup
   ID, and UTC time.
2. Keep `caddy`, `api`, and both workers stopped. Do not repeatedly rerun the migration.
3. Read the current DB revision without changing it:

   ```bash
   sudo docker compose --env-file .env.production -f compose.production.yaml exec db sh -c \
     'psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --command \
     "SELECT version_num FROM alembic_version;"'
   ```

4. Compare it with the release migration chain in the exact source commit and determine
   whether PostgreSQL rolled back the failed transaction or any DDL/data operation
   committed.
5. Do not run an untested `alembic downgrade` or manually edit `alembic_version`.
6. If the old image supports the observed schema, restore its exact digest and start it
   through [deployment.md](deployment.md#exact-application-rollback).
7. If schema/data are uncertain or incompatible, restore the pre-deploy backup into a
   new database and run the chosen compatible image/migrations there. Keep the failed DB
   for diagnosis.

Reopen traffic only after migration revision, `/health`, `/ready`, DB-backed demo,
workers, queues, and logs pass.

## Secret rotation

Rotate routinely on a schedule and immediately after suspected exposure. Inventory:

- operator SSH keys and provider/registrar accounts;
- GHCR read-only token;
- PostgreSQL role password;
- `MVP_API_KEY`;
- external LLM key;
- optional Lichess token;
- S3 access key and rclone crypt password/salt.

General sequence: create new credential, update the minimum affected service through a
protected editor/secret UI, verify new access, revoke old credential, and inspect logs.
Never paste either value into a command or ticket.

### Application, LLM, and Lichess keys

Drain affected workers before recreation. Edit `.env.production` with `sudoedit`, then
validate silently and force-recreate only affected services:

```bash
sudo docker compose --env-file .env.production -f compose.production.yaml --profile migrate config --quiet
sudo docker compose --env-file .env.production -f compose.production.yaml up -d --force-recreate api
```

Recreate `worker-reports` after a warm drain for an LLM key change. Recreate the API for
`MVP_API_KEY`/Lichess changes. Revoke the old provider token only after new-key smoke,
unless it is already compromised—in that case revoke first and accept controlled
downtime.

### PostgreSQL password

Take a verified backup, stop public traffic/API/workers, and keep one psql session open.
Use psql's interactive `\password <db-role>` so the new password is not in history:

```bash
sudo docker compose --env-file .env.production -f compose.production.yaml stop caddy api
sudo docker compose --env-file .env.production -f compose.production.yaml stop worker-analysis worker-reports
sudo docker compose --env-file .env.production -f compose.production.yaml exec db sh -c \
  'psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"'
```

Run `\password <db-role>` in that psql session and enter the new value twice. Keep the
session open, use a second verified SSH session to update `DB_PASSWORD` in
`.env.production` through an editor, then silently validate and recreate the DB container
with its persistent volume followed by the application services:

```bash
sudo docker compose --env-file .env.production -f compose.production.yaml --profile migrate config --quiet
sudo docker compose --env-file .env.production -f compose.production.yaml up -d --force-recreate db
sudo docker compose --env-file .env.production -f compose.production.yaml \
  up -d --force-recreate api worker-analysis worker-reports
sudo docker compose --env-file .env.production -f compose.production.yaml up -d caddy
```

Do not remove the volume. Verify DB-backed smoke before closing the psql recovery
session.

### GHCR and backup credentials

For GHCR, revoke the old token, `sudo docker logout ghcr.io`, and use the interactive
`--password-stdin` procedure in the deployment runbook. Prefer anonymous pull for a
public package.

For S3, create a second bucket-scoped credential, update via interactive `rclone config`,
test list/upload/download/checksum, then revoke the old credential. Changing the
`rclone crypt` password does **not** re-key existing objects; create a new crypt remote
and re-encrypt/copy all retained backups, test restore, then retire the old remote/key.

## Compromised host or control-plane account

Treat the VPS as untrusted. Do not “clean” it and return it to production.

1. From a known-clean device, revoke/rotate provider, registrar/DNS, GHCR, LLM, Lichess,
   S3, and operator credentials accessible to the host. Protect off-server backups from
   deletion first. Rotate rclone crypt material after securing retained backups.
2. Isolate the VPS with the provider firewall or power it off according to evidence and
   containment needs. Preserve a provider snapshot only for private forensic analysis;
   never boot it back into the production network as trusted.
3. If DNS/control plane is compromised, lock the registrar/provider account and restore
   the last recorded exact records from the clean account.
4. Provision a fresh Ubuntu 24.04 VPS, new SSH host identity, and all-new secrets. Deploy
   the last verified immutable image using [deployment.md](deployment.md).
5. Select a backup known to predate compromise. A dump from an untrusted database can
   contain executable restore definitions; inspect it and restore only into an isolated
   disposable DB first. Validate checksum, migration, counts, and application behavior.
6. Cut DNS to the clean host, run external smoke/port checks, enable monitoring, and
   create/test a new off-server backup.
7. Document timeline, exposed credentials/data, recovery point, image digest, and
   preventive follow-ups. Notify affected parties when legally or contractually required.

Never reuse the compromised VPS, its SSH keys, `.env.production`, rclone config, Docker
credentials, or an unverified post-compromise backup.

## Close the incident

Closure requires:

- public `/health`, `/ready`, root metadata, and DB-backed demo checks pass;
- only intended ports are public;
- DB/Redis and both correct workers are healthy;
- queues trend normally and stale rows have been individually reconciled;
- logs contain a terminal recovery event without secrets;
- current/rollback image digests and Alembic revision are recorded;
- a new off-server backup and disposable restore are scheduled or completed;
- alerts are restored and follow-up actions have owners.

See also [observability.md](observability.md), [backup-restore.md](backup-restore.md), and
[dns-tls.md](dns-tls.md).
