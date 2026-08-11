# Celery task recovery runbook

Chess Lab has two queues and two dedicated workers:

| Queue | Worker service | Task |
|---|---|---|
| `analysis` | `worker-analysis` | `app.tasks.celery_app.analyze_game` |
| `reports` | `worker-reports` | `app.tasks.celery_app.generate_player_report` |

PostgreSQL is the authoritative progress store. Celery has no result backend. Tasks use
early acknowledgement (`task_acks_late=False`) and prefetch `1`. If an analysis worker
dies after claiming a game, Redis will not redeliver it and the row may remain `running`.
There is intentionally no automatic lease/reaper for analysis tasks.

A time threshold identifies a **candidate for investigation**, not permission to reset.
Never recover a row until the old worker/task is proven no longer alive. If evidence is
ambiguous, leave the row unchanged and escalate.

Run commands from `/opt/chess-lab`.

## Inspect workers and queues

Start with read-only state:

```bash
cd /opt/chess-lab
sudo docker compose --env-file .env.production -f compose.production.yaml ps
sudo docker compose --env-file .env.production -f compose.production.yaml exec worker-analysis \
  celery -A app.tasks.celery_app.celery_app inspect ping
sudo docker compose --env-file .env.production -f compose.production.yaml exec worker-analysis \
  celery -A app.tasks.celery_app.celery_app inspect active
sudo docker compose --env-file .env.production -f compose.production.yaml exec worker-analysis \
  celery -A app.tasks.celery_app.celery_app inspect reserved
sudo docker compose --env-file .env.production -f compose.production.yaml exec worker-analysis \
  celery -A app.tasks.celery_app.celery_app inspect scheduled
sudo docker compose --env-file .env.production -f compose.production.yaml exec redis redis-cli LLEN analysis
sudo docker compose --env-file .env.production -f compose.production.yaml exec redis redis-cli LLEN reports
```

Inspection is advisory: a dead or network-isolated worker cannot reply. Expect two named
workers (`analysis@...` and `reports@...`). Compare replies with Compose state, Redis queue
lengths, PostgreSQL rows, restart counts, and structured task logs. Do not infer “no task”
from one timed-out inspect command. The control client may run inside either healthy
worker container; substitute `worker-reports` in the commands if `worker-analysis` is
the failed service.

Useful bounded logs:

```bash
sudo docker compose --env-file .env.production -f compose.production.yaml logs --since 2h worker-analysis
sudo docker compose --env-file .env.production -f compose.production.yaml logs --since 2h worker-reports
```

Expected terminal events are `analysis.task.succeeded|skipped|failed` and
`report.task.succeeded|retrying|failed`. Correlate by `task_id`; analysis events also
carry `game_id`.

## Planned warm worker drain

Before update, inspect active/reserved tasks and queue lengths. Prefer waiting until
active tasks finish. Queue messages can remain in Redis only when the new task code is
compatible with them.

```bash
sudo docker compose --env-file .env.production -f compose.production.yaml stop worker-analysis worker-reports
```

Compose sends `TERM`, which starts Celery warm shutdown. The service grace periods are
30 minutes for analysis and 15 minutes for reports. Do not escalate to `kill -9` merely
because shutdown is slow. Identify the exact active task first; forced termination may
create a stale `running` analysis or an expired `generating` report.

## Find stale analysis candidates

The initial alert threshold is two hours. It must remain above the longest measured
healthy analysis runtime plus operational margin.

```bash
sudo docker compose --env-file .env.production -f compose.production.yaml exec db sh -c \
  'psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --command \
  "SELECT id, analysis_started_at, analysis_attempts, white_player, black_player \
   FROM games \
   WHERE analysis_status = '\''running'\'' \
     AND analysis_started_at < now() - interval '\''2 hours'\'' \
   ORDER BY analysis_started_at;"'
```

Choose one exact numeric `game_id`. Audit it without mutation:

```bash
sudo docker compose --env-file .env.production -f compose.production.yaml exec db sh -c \
  'psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --command \
  "SELECT id, unique_id, white_player, black_player, is_analyzed, analysis_status, \
          analysis_started_at, analysis_attempts, analysis_error \
   FROM games WHERE id = <game-id>;"'
```

Do not copy PGN or `analysis_data` into the incident record.

## Prove the old analysis task is dead

All of the following must be satisfied and recorded for the exact `game_id`:

1. The row is still `running`, `is_analyzed=false`, and its exact
   `analysis_started_at` value is recorded.
2. Both expected workers answer `inspect ping`, or a missing worker is independently
   confirmed stopped/down through Compose and container logs.
3. `inspect active`, `reserved`, and `scheduled` do not contain the game/task. If the
   `task_id` is known from logs, query it explicitly:

   ```bash
   sudo docker compose --env-file .env.production -f compose.production.yaml exec worker-analysis \
     celery -A app.tasks.celery_app.celery_app inspect query_task <task-id>
   ```

4. Bounded logs since `analysis_started_at` show no later success/failure and establish
   worker exit, child loss, host restart, or another concrete reason the task cannot
   still write.
5. The worker container is not currently restarting and no planned warm shutdown is
   still waiting for that task.

Elapsed time alone, an empty Redis queue, or one absent inspect response is insufficient.
Stockfish analysis can legitimately run for a long time.

## Controlled transition of one stale analysis

This is the only supported manual DB mutation in this runbook. It is a compare-and-set
for one audited ID and its exact old timestamp. It preserves `analysis_attempts` and the
old timestamp for evidence; the next legitimate claim updates the timestamp and clears
the error.

First take/verify a current backup if the database is otherwise healthy. Open psql
without putting DB credentials in history:

```bash
sudo docker compose --env-file .env.production -f compose.production.yaml exec db sh -c \
  'psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"'
```

In psql, replace both placeholders with values from the read-only audit:

```sql
\set ON_ERROR_STOP on
BEGIN;

SELECT id, is_analyzed, analysis_status, analysis_started_at, analysis_attempts
FROM games
WHERE id = <game-id>
FOR UPDATE;

UPDATE games
SET analysis_status = 'failed',
    analysis_error = 'operator_recovery_worker_lost_confirmed'
WHERE id = <game-id>
  AND analysis_status = 'running'
  AND is_analyzed = false
  AND analysis_started_at = TIMESTAMP '<exact-analysis-started-at>'
RETURNING id, is_analyzed, analysis_status, analysis_started_at, analysis_attempts;
```

The update must return exactly one row with `analysis_status='failed'`. If it returns
zero or more than one row, run `ROLLBACK;` and re-audit. If exactly one row is correct,
run `COMMIT;`, then `\q`. Never remove the timestamp condition, use a player-wide
`UPDATE`, change completed rows, or reset a batch.

## Re-enqueue through the protected API

Do not publish a Celery message manually. The existing exact-game API shares the normal
quota and audit boundary. Read the API key interactively and pass it through a temporary
`0600` header file so its value is absent from shell history and process arguments:

```bash
umask 077
API_HEADER_FILE="$(mktemp)"
trap 'rm -f -- "$API_HEADER_FILE"' EXIT
read -rsp 'Chess Lab operator API key: ' CHESS_LAB_API_KEY
echo
printf 'X-API-Key: %s\n' "$CHESS_LAB_API_KEY" >"$API_HEADER_FILE"
unset CHESS_LAB_API_KEY
curl --fail-with-body --request POST \
  --header "@${API_HEADER_FILE}" \
  https://<api.example.com>/api/v1/games/<game-id>/analyze
rm -f -- "$API_HEADER_FILE"
trap - EXIT
```

Expect a queued response. Then verify the `analysis` queue/active worker, new
`analysis.task.started` event, incremented `analysis_attempts`, updated
`analysis_started_at`, and exactly one terminal event. A `429` must honor `Retry-After`;
do not bypass quotas through Redis or direct Celery calls.

## Report `generating` diagnosis and recovery

Reports use DB status `ready|generating|failed`. A `generating` row is reclaimable by the
normal API only after `REPORT_GENERATION_LEASE_SECONDS` (default 900 seconds), but queue
wait is unbounded. Therefore an expired lease is not proof that the old task disappeared:
a queued/reserved report can still start and produce a duplicate external LLM call.

Read the configured lease in `.env.production` with an editor, then audit report state:

```bash
sudo docker compose --env-file .env.production -f compose.production.yaml exec db sh -c \
  'psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --command \
  "SELECT id, player_name, language, status, analyzed_games_count, updated_at, \
          (now() - updated_at) AS age \
   FROM player_reports \
   WHERE lower(player_name) = lower('\''<player-name>'\'') \
     AND language = '\''<language>'\'';"'
```

Then:

1. inspect the `reports` queue, active/reserved/scheduled tasks, worker restarts, and
   bounded logs for normalized player/language and `task_id`;
2. remember that one initial LLM call plus three retries is expected; intermediate
   `report.task.retrying` events leave status `generating`;
3. if the task is active, reserved, queued, or the lease is live, do not intervene;
4. if the lease has expired **and** the old task is conclusively absent, call the normal
   operator endpoint; its atomic claim decides whether regeneration is allowed:

```bash
umask 077
API_HEADER_FILE="$(mktemp)"
trap 'rm -f -- "$API_HEADER_FILE"' EXIT
read -rsp 'Chess Lab operator API key: ' CHESS_LAB_API_KEY
echo
printf 'X-API-Key: %s\n' "$CHESS_LAB_API_KEY" >"$API_HEADER_FILE"
unset CHESS_LAB_API_KEY
curl --fail-with-body --request POST \
  --header "@${API_HEADER_FILE}" \
  'https://<api.example.com>/api/v1/report/<url-encoded-player>?language=<language>'
rm -f -- "$API_HEADER_FILE"
trap - EXIT
```

Poll the public status endpoint and inspect terminal logs:

```bash
curl --fail --silent --show-error \
  'https://<api.example.com>/api/v1/report/<url-encoded-player>/status?language=<language>'
```

Do not manually flip `player_reports.status`, delete a generating row, or shorten the
lease during an incident. The API already implements safe atomic reclaim and preserves a
previous cached report on failure.

## Prohibited shortcuts

Never use these as recovery shortcuts:

- `celery purge`, Redis `FLUSH*`, deleting `analysis`/`reports` keys, or deleting the
  Redis volume;
- player-wide or status-wide SQL updates;
- resetting a stale candidate without proving its old task dead;
- manually calling `.delay()` in a Python shell or bypassing the protected API/quota;
- changing early/late acknowledgement settings during an incident;
- storing an API key, PGN, report text, prompt, or full `.env.production` in the incident
  log.

## Recovery record

For every manual recovery, record privately:

```text
incident UTC window:
queue and worker:
game_id or normalized player/language:
task_id if known:
old status/timestamp/attempt count:
evidence old task was dead:
backup identifier:
exact mutation (analysis only):
protected API response:
new task/terminal event:
operator and final result:
```

## References

- [Celery monitoring and inspect commands](https://docs.celeryq.dev/en/stable/userguide/monitoring.html)
- [Celery warm shutdown](https://docs.celeryq.dev/en/stable/userguide/workers.html#worker-shutdown)
- [Runtime observability](observability.md)
