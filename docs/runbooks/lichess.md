# Lichess import runbook

This runbook covers the synchronous Chess Lab Lichess import and its deployment-wide
Redis coordination. It does not authorize parallel clients, retries, or deletion of
production cooldown keys.

## Configuration source of truth

[`.env.example`](../../.env.example) is the canonical settings list. The integration
requires a non-generic `LICHESS_USER_AGENT`; `LICHESS_API_TOKEN` is optional. Timeout,
response-size, and cooldown values should normally remain at their reviewed defaults.

### Choose the application User-Agent

Use one stable identity for every process and replica:

```text
ChessLab/<deployed-version> (+<monitored-public-email-or-URL>)
```

The contact must reach the actual operator. Do not use `python-httpx`, `curl`, a fake
contact, a per-user value, or a value that changes between requests. After changing
the setting, restart all API replicas so they use the same identity.

### Add, rotate, or revoke the optional token

Public game export works without a token. If a server-side token is used:

1. Create and revoke tokens from the authenticated Lichess token page at
   <https://lichess.org/account/oauth/token>. Grant only the access the deployment
   needs.
2. In production, enter the value through the hosting platform's secret-manager UI
   or secret injection mechanism. Never paste it into a command, image, manifest,
   ticket, chat, or log field.
3. For local development, edit the ignored `.env` file directly in a local editor.
   Confirm it is ignored with `git check-ignore .env`; do not use `echo`, command-line
   assignment, or any command that places the value in shell history.
4. Restart every API replica after adding or rotating the token.
5. To revoke it, revoke the credential in Lichess first, remove it from secret
   storage (or leave `LICHESS_API_TOKEN=` locally), and restart the API. Do not print
   the old or new value while checking the rollout.

## Runtime invariants

- `chess-lab:lichess:request-lock` permits one outbound export across the deployment.
- `chess-lab:lichess:cooldown` suppresses all outbound calls after an upstream `429`.
- Requests never wait for the lock, retry automatically, or sleep inside FastAPI.
- A Redis coordination failure fails closed; it is never safe to bypass Redis with a
  second script, pod, worker, IP, proxy, token, or User-Agent.
- `/health` and `/ready` do not call Lichess. `/ready` checks the Redis backend used to
  enforce request quotas.

Multiple bypassing clients violate Lichess's one-request-at-a-time rule and can turn a
recoverable cooldown into a longer deployment-wide block. The lock is a safety
boundary, not an inconvenience to route around.

## Interpret local responses

| Response | Meaning | Safe operator action |
|---|---|---|
| `409` | Another import owns the distributed lock. No second outbound call was made. | Let the active request finish. Correlate its lifecycle events; do not delete the lock. If its process died, wait for the lock TTL. |
| `429` | A local cooldown is active or the current upstream call received `429`. | Honor `Retry-After`, inspect the cooldown TTL read-only, and make no Lichess requests until it expires. |
| `503` | Redis coordination/quota enforcement, Lichess authorization/configuration, or temporary upstream/network availability failed. | Use the response detail and the matching `failure_kind`; restore Redis or configuration first. Do not bypass coordination. |
| `404` | Lichess returned not-found for the requested account. | Verify the account spelling once. Repeated HTML `404` for known accounts may indicate a crawler block; follow the escalation section. |
| `502` | Lichess returned a status, media type, encoding, or body size outside the supported protocol. | Inspect safe response metadata in the terminal event; never log or copy the response body. |

## Inspect coordination state safely

Use an authenticated deployment console or Redis administration session. For the
local Docker Compose service:

```bash
docker compose exec redis redis-cli PTTL chess-lab:lichess:cooldown
docker compose exec redis redis-cli PTTL chess-lab:lichess:request-lock
```

For managed Redis, run the same `PTTL <key>` operations through the provider's secure
console or a CLI whose connection credentials come from secret injection. Never paste
a credential-bearing Redis URL into shell history.

Interpret `PTTL` as follows:

- a positive number is remaining milliseconds;
- `-2` means the key does not exist;
- `-1` means the key has no expiry and is invalid for this integration; requests fail
  closed with `503` until the Redis state/configuration is repaired.

Do not run `DEL`, `SET`, or `EXPIRE` against production coordination keys to force a
request through. A cooldown must expire naturally. A live lock must be released by
its token owner; after process death its configured TTL releases it.

## Diagnose one operation from logs

Every accepted integration invocation has exactly two lifecycle events with one
opaque `operation_id`:

```text
lichess.request.started
lichess.request.succeeded | busy | rate_limited | failed
```

Search for the `started` event, copy its `operation_id`, and query all Lichess events
with that exact value. There must be one start and one terminal event. Structured log
collection must preserve the Python `LogRecord` extra fields.

Fields shared by both events are `operation_id`, `username_normalized`, `max_games`,
`perf_type`, and categorical `status`. Terminal events also provide `duration_ms`,
`upstream_http_status`, and `retry_after`. A rate-limit terminal adds
`rate_limit_source` (`local_cooldown` or `upstream`); a failed terminal adds one
bounded `failure_kind`:

| `failure_kind` | First check |
|---|---|
| `redis` | Redis reachability, authentication, key expiry, and replica consistency |
| `configuration` | deployed User-Agent/token values and whether the token was revoked |
| `timeout` | Lichess availability and the configured total timeout; do not retry in a loop |
| `network` | DNS, egress, TLS, proxy, and firewall health |
| `not_found` | username spelling; then repeated masked-block symptoms |
| `upstream` | Lichess service status and the upstream code |
| `protocol` | normalized content type and declared/factual byte counts |
| `cancelled` | API process shutdown or request cancellation |
| `unexpected` | application traceback outside the expected integration families |

`status` is never an HTTP code. The upstream code appears only in
`upstream_http_status`; the local code belongs to the HTTP access log. Logs may carry
only the fields above plus normalized content type and body byte counts. They must
never contain PGN, response body/prefix, Authorization token, MVP key, original
exception text, or the complete outbound URL/query.

## Escalate persistent Lichess blocking

Escalate only after confirming there is one client, the cooldown has been honored,
the User-Agent has a real monitored contact, and an optional token is valid. Contact
the `#lichess-api-support` channel linked from the official
[Lichess API tips](https://lichess.org/page/api-tips) when any of these persist:

- known existing accounts repeatedly produce HTML `404` responses;
- `429` returns after full cooldowns and single-request operation;
- `401`/`403` continues after token/configuration verification;
- protocol behavior consistently differs from the documented game-export contract.

Provide timestamps, the application name/version, sanitized event metadata,
`operation_id` values, upstream status, and content type. Never provide tokens, API
keys, complete request URLs/queries, PGN, or upstream bodies.

## Controlled smoke after a User-Agent or token change

1. Confirm `/health` returns `200` and `/ready` returns `200`.
2. Confirm there is no active cooldown with the read-only `PTTL` check. If one exists,
   wait for it to expire. Do not delete it.
3. Read the Chess Lab API key interactively so it is not written to shell history:

   ```bash
   read -rp "Chess Lab base URL: " CHESS_LAB_BASE_URL
   read -rp "Known public Lichess username: " LICHESS_SMOKE_USERNAME
   read -rsp "Chess Lab API key: " CHESS_LAB_OPERATOR_KEY; echo
   curl --fail-with-body -X POST \
     -H "X-API-Key: $CHESS_LAB_OPERATOR_KEY" \
     "$CHESS_LAB_BASE_URL/games/lichess/$LICHESS_SMOKE_USERNAME?max_games=1"
   unset CHESS_LAB_OPERATOR_KEY CHESS_LAB_BASE_URL LICHESS_SMOKE_USERNAME
   ```

4. Make exactly one request. Expect `200` and a normal `UploadResponse`; a repeat
   import may validly report `saved_new=0` because game identity is idempotent.
5. Verify one `started` and one terminal event share the same `operation_id`, with no
   sensitive values in logs or the response.

Do not run a loop, concurrency test, or load test against real Lichess, and never try
to provoke a real `429`. Concurrency, cooldown, Redis-failure, and retry-header cases
belong in the mocked automated suite.

Return to the [Chess Lab README](../../README.md#operations-and-safety).
