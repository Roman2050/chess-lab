# Production deployment verification — 2026-08-15

This record captures non-secret evidence from the first Chess Lab production deployment.
It intentionally omits credentials, account identifiers, private source addresses,
heartbeat URLs, raw PGN files, and mappings between real players and public aliases.

## Deployment target

- Public API: `https://api.chessscope.dev`
- Host platform: single x86-64 VPS running Ubuntu Server 24.04 LTS
- Runtime topology: Caddy, API, PostgreSQL, Redis, analysis worker, and reports worker
  through `compose.production.yaml`
- Published host ports: `80/tcp` and `443/tcp`; SSH is separately source-restricted
- PostgreSQL, Redis, and Uvicorn: internal-only Compose networking
- Application version: `0.1.0`
- OCI source revision: `b48dc4f073cad24be746a8a55a36fd4244d6d115`
- Bootstrap image:
  `ghcr.io/roman2050/chess-lab@sha256:9d18df61dca018987bbf77a69c9e0835a89d9fb146ab61d73b004f8bf8b51eff`
- Image architecture: `amd64`
- Alembic revision: `e2f4a6b8c0d1`

The explicit bootstrap digest was pulled anonymously, inspected for its OCI labels and
architecture, deployed, and exercised through the production smoke. After the final
`v0.1.0` image replaces it, retain this digest as the verified rollback application
target for the release observation window.

## DNS and TLS

- Cloudflare serves a DNS-only `A` record for the API hostname.
- No `AAAA` record is published because the host has no configured public IPv6 target.
- Caddy obtained and serves a valid Let's Encrypt certificate for the exact API hostname.
- Direct TLS verification confirmed the subject, issuer, validity window, and SAN.
- The public application and API documentation loaded successfully in a private browser
  session.

## Application and demo verification

- `/health` returned `200` with `{"status":"ok"}`.
- `/ready` returned `200` with Redis readiness reported as `ok`.
- Public metadata, OpenAPI, Swagger, and all six links returned by `/api/v1/demo` were
  reachable without an operator key.
- Four sampled write operations returned `401` without the server-side operator key.
- The production demo database contains exactly 20 permitted, pseudonymized games for
  `DemoPlayer`; all 20 analyses completed and no analysis remained pending or stale.
- One English scouting report was generated and its cached GET returned successfully.
- Redis analysis and reports queue lengths were zero after processing.

An audit found legacy local-testing records from before the controlled demo import. The
cleanup deleted exactly 23 non-demo games and one non-demo report. Post-cleanup database
checks confirmed 20 demo games, one demo report, no unexpected player names, and no known
real-player reference in stored PGN. Stateless containers were recreated before the
final bounded-log audit.

## Logs and resource bounds

- Every long-lived Compose service uses Docker's `local` logging driver with five 10 MB
  files per container.
- The post-cleanup audit found no configured secret names, report text, or known private
  player reference in current container logs.
- Root filesystem usage was below 10% after deployment; the host retained adequate memory
  reserve under the configured single-worker concurrency.

## Backup and restore evidence

- Off-server destination: private Cloudflare R2 Standard storage in the EU jurisdiction.
- Access uses a token restricted to object read/write operations for one bucket.
- Client-side encryption uses `rclone crypt`; filename encryption is standard and
  directory-name encryption is disabled so lifecycle rules can target class prefixes.
- The root-owned rclone configuration is mode `0600`; the backup helper is mode `0700`.
- A daily custom-format PostgreSQL dump and its checksum were uploaded, downloaded
  through the encrypted remote, and byte-verified.
- A disposable database restore confirmed 20 games, one report, and Alembic revision
  `e2f4a6b8c0d1`; forward migration compatibility passed before cleanup.
- Provider lifecycle rules expire only the encrypted `daily/` class after 14 days and
  `weekly/` class after 56 days. No bucket-wide deletion rule exists.
- `chess-lab-backup.timer` is enabled, and a completed service run emitted both
  `backup.completed` and `heartbeat.sent target=backup`.

## External monitoring evidence

- An HTTPS liveness monitor checks `/health` over IPv4.
- A separate DB-backed monitor checks the public `DemoPlayer` statistics response.
- A daily backup heartbeat detects missing scheduled backups.
- A 15-minute host heartbeat is driven by a local disk/TLS guard with disk thresholds at
  75% warning and 85% critical, and a minimum TLS lifetime of 14 days.
- The explicit host failure drill created an incident and email notification; the
  recovery ping was recorded and returned the heartbeat to `Up`.

Automated memory, container-restart, queue-backlog, and stale-analysis alerts remain
documented follow-ups. Their read-only operator checks are available in the observability
and Celery recovery runbooks; no database, Redis, Docker API, or metrics port is exposed
to implement monitoring.

## Secret boundary

The following remain outside Git and this record:

- `.env.production` and all application/database/API credentials;
- SSH private keys and source IP allowlists;
- Cloudflare account identifiers and R2 access credentials;
- `rclone crypt` password and salt;
- Better Stack heartbeat URLs;
- raw/private PGN files and any identity-to-alias mapping.

The operator password manager is the recovery source for secret material. This document
is evidence of configuration and verification, not a substitute for that recovery data
or for a current restore drill.

## Release handoff

The next release step is to add portfolio README content and sanitized visuals, merge the
final documentation through review, publish GitHub Release `v0.1.0`, record its GHCR
digest and OCI revision, update production to that exact digest, repeat the production
smoke, and preserve the bootstrap digest above as the rollback target during the
observation window.
