# Deployment, update, and rollback runbook

This runbook is the operator procedure for a single x86-64 VPS running Ubuntu Server
24.04 LTS. It deliberately assumes no hidden provisioning automation. Complete Chat 9
locally before buying the VPS; use this procedure during Chat 10.

The commands use these placeholders:

- `<operator>` — the non-root sudo account;
- `<admin-cidr>` — the operator's trusted public IP/CIDR for SSH;
- `<api.example.com>` — the production API hostname;
- `<release-tag>` — an immutable release such as `v0.1.0`;
- `<image-digest>` — a verified `sha256:...` GHCR manifest digest;
- `<release-commit>` — the full Git commit SHA that produced the image.

Replace every placeholder deliberately. Never paste passwords, API keys, registry
tokens, or a complete `.env.production` into a command, terminal transcript, issue, or
chat. Run all Compose commands from `/opt/chess-lab`.

## Provider prerequisites

Before provisioning, prepare:

- an x86-64 Ubuntu Server 24.04 LTS VPS with at least 2 vCPU, 4 GB RAM, and
  40–80 GB NVMe;
- a static public IPv4 address and provider console/recovery access;
- provider account 2FA, saved recovery codes, billing alerts, and disk/availability
  monitoring;
- an Ed25519 SSH public key; the private key never leaves the operator device;
- provider firewall rules allowing TCP `22` only from `<admin-cidr>` where practical,
  and TCP `80`/`443` from the Internet;
- outbound DNS and HTTPS access for Ubuntu/Docker packages, GHCR, ACME, and the report
  worker's external LLM provider;
- a verified GHCR release tag/digest and a completed off-server backup setup plan.

Do not create public rules for PostgreSQL `5432`, Redis `6379`, Uvicorn `8000`, or the
Docker daemon. If IPv6 is assigned, apply equivalent IPv6 firewall rules or disable the
unused address at the provider boundary; an IPv4-only firewall is not an IPv6 policy.

## Create and verify the operator account

Use the provider's initial account only for bootstrap. Keep its first session open until
the new account and hardened SSH have both been verified from separate sessions.

Generate a dedicated key on the trusted operator device if one does not already exist:

```bash
ssh-keygen -t ed25519 -a 100 -f ~/.ssh/chess_lab_prod
```

On the VPS, create the account and grant sudo:

```bash
sudo adduser <operator>
sudo usermod -aG sudo <operator>
sudo install -d -m 700 -o <operator> -g <operator> /home/<operator>/.ssh
sudoedit /home/<operator>/.ssh/authorized_keys
sudo chown <operator>:<operator> /home/<operator>/.ssh/authorized_keys
sudo chmod 600 /home/<operator>/.ssh/authorized_keys
```

Paste only the `.pub` key line into `authorized_keys`. From a second local terminal,
verify key login and sudo before changing SSH policy:

```bash
ssh -i ~/.ssh/chess_lab_prod <operator>@<server-ip>
sudo -v
```

Only after that succeeds, create a drop-in with `sudoedit
/etc/ssh/sshd_config.d/99-chess-lab-hardening.conf`:

```text
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
AllowUsers <operator>
```

Validate before reload; a validation failure means do not reload:

```bash
sudo sshd -t
sudo systemctl reload ssh
```

Open a third independent SSH session as `<operator>` and run `sudo -v` again. Do not
close the bootstrap sessions until this post-hardening login works. Confirm provider
console access as the lockout recovery path.

## Configure the host firewall

Set the provider firewall first, then UFW. When `<admin-cidr>` is not stable, document
the approved SSH range and console recovery procedure instead of accidentally locking
out the only operator.

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from <admin-cidr> to any port 22 proto tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status verbose
```

Docker-published ports can bypass ordinary UFW forwarding rules. The production Compose
file therefore publishes only `80` and `443`; the provider firewall is a second boundary.
After deployment, verify externally that only intended ports are reachable.

## Patch Ubuntu and install Docker from the official repository

Install security updates and reboot if required before application setup:

```bash
sudo apt update
sudo apt full-upgrade
test ! -f /var/run/reboot-required || sudo reboot
```

After reconnecting, add Docker's official Ubuntu repository. Do not use the convenience
installation script on production.

```bash
sudo apt install ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt update
sudo apt install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker version
sudo docker compose version
```

Do not add `<operator>` to the `docker` group: Docker socket access is effectively root
access. Use `sudo docker ...`. Never expose `/var/run/docker.sock` through a container or
TCP listener.

Enable Ubuntu automatic security updates, but schedule Docker Engine upgrades and host
reboots as controlled maintenance with a backup and worker drain:

```bash
sudo apt install unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

## Create the production directory

```bash
sudo install -d -m 0750 -o <operator> -g <operator> /opt/chess-lab
cd /opt/chess-lab
```

Transfer exactly these three files from the locally verified `<release-commit>` using
`scp` or another authenticated channel:

```text
compose.production.yaml
Caddyfile
.env.production.example
```

Do not copy source code, `.git`, local `.env`, PGNs, tests, or development Compose files
to the VPS. Record local SHA-256 checksums before transfer and compare them on the VPS:

```bash
sha256sum compose.production.yaml Caddyfile .env.production.example
```

Create the real environment without putting secret values in command arguments:

```bash
umask 077
cp .env.production.example .env.production
chmod 600 .env.production
sudoedit .env.production
stat -c '%a %U:%G %n' .env.production compose.production.yaml Caddyfile
```

Populate every example value. Generate DB/operator secrets directly into a password
manager or through a protected file, then enter them in the editor. Required production
decisions include:

- `CHESS_LAB_IMAGE=ghcr.io/<owner>/chess-lab@sha256:<verified-digest>` when possible;
- matching `APP_VERSION`, real `APP_DOMAIN`, and monitored `ACME_EMAIL`;
- unique `DB_PASSWORD` and `MVP_API_KEY`;
- real Lichess application identity and optional token;
- external `LLM_BASE_URL`, model, key, and spending limit;
- `STOCKFISH_MULTIPV=2`, worker concurrency `1`, Stockfish threads `1`, and hash
  `128` for the default VPS size.

Check for examples without printing matching secret-bearing lines:

```bash
if grep -Eq 'replace-with|example\.com' .env.production; then
  echo 'Refusing deployment: example values remain' >&2
  exit 1
fi
test "$(stat -c '%a' .env.production)" = 600
```

The real file is never committed, backed up with application source, or pasted into
`docker compose config` output shared with others.

## Registry access and immutable image verification

For a public GHCR package, prefer anonymous pull. For a private package, create a
dedicated read-only token with only `read:packages`; do not use a personal token with
repository write/admin scope. Read it without placing it in shell history:

```bash
read -rsp 'GHCR read-only token: ' GHCR_TOKEN
echo
printf '%s' "$GHCR_TOKEN" | sudo docker login ghcr.io --username <github-user> --password-stdin
unset GHCR_TOKEN
```

Validate configuration silently and pull the declared images:

```bash
cd /opt/chess-lab
sudo docker compose --env-file .env.production -f compose.production.yaml --profile migrate config --quiet
sudo docker compose --env-file .env.production -f compose.production.yaml config --services
sudo docker compose --env-file .env.production -f compose.production.yaml config --images
sudo docker compose --env-file .env.production -f compose.production.yaml pull
```

The source manifest should contain exactly two `published:` entries: `80` and `443`.
Review that fact without rendering application environment values:

```bash
grep -n 'published:' compose.production.yaml
```

Inspect the pulled application image and compare its repo digest and OCI revision with
the release record:

```bash
sudo docker image inspect ghcr.io/<owner>/chess-lab@sha256:<image-digest> \
  --format '{{json .RepoDigests}}'
sudo docker image inspect ghcr.io/<owner>/chess-lab@sha256:<image-digest> \
  --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
```

The revision must equal `<release-commit>`. Stop if the digest, revision, architecture,
or release tag differs. Preserve the current and previous image locally; do not run an
unconditional `docker system prune`.

## First controlled deployment

Confirm DNS/firewall prerequisites in [dns-tls.md](dns-tls.md). Validate Caddy syntax
before starting the edge:

```bash
sudo docker compose --env-file .env.production -f compose.production.yaml \
  run --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

Start stateful services, run the one-shot migration, and only then start the application:

```bash
sudo docker compose --env-file .env.production -f compose.production.yaml up -d db redis
sudo docker compose --env-file .env.production -f compose.production.yaml --profile migrate run --rm migrate
sudo docker compose --env-file .env.production -f compose.production.yaml \
  up -d api worker-analysis worker-reports caddy
sudo docker compose --env-file .env.production -f compose.production.yaml ps
```

A failed migration is a hard stop. Do not start the new API/workers; follow
[incident.md](incident.md#failed-migration).

Smoke from the VPS and then from an external network:

```bash
curl --fail --silent --show-error https://<api.example.com>/health
curl --fail --silent --show-error https://<api.example.com>/ready
curl --fail --silent --show-error https://<api.example.com>/
curl --fail --silent --show-error https://<api.example.com>/openapi.json >/dev/null
curl --fail --silent --show-error https://<api.example.com>/api/v1/demo
```

Also verify `/docs` in a signed-out browser, correct worker queues, internal-only DB/Redis,
and structured bounded logs. See [observability.md](observability.md). Record:

```text
deployment UTC time:
release tag:
image digest:
source commit:
Alembic revision:
backup object used/pre-deploy backup:
operator:
smoke result:
```

Store the record in the operator's private deployment log, not in `.env.production`.

## Controlled update

Never deploy merely because a moving tag changed. Identify and verify the new immutable
digest first, and retain the current digest as `<previous-image>`.

1. Read release notes and Alembic migrations. Decide whether the old application can
   safely run against the new schema; if uncertain, use the downtime sequence below.
2. Complete a fresh off-server backup and verify its checksum/listing per
   [backup-restore.md](backup-restore.md).
3. Record current/new image digests and the current Alembic revision.
4. Inspect both workers and Redis queues as described in
   [celery-recovery.md](celery-recovery.md). Wait for active tasks to finish. Reserved or
   queued tasks are not data loss, but they must be compatible with the new task code.
5. Stop accepting traffic and warm-stop workers. `TERM` is Celery's warm shutdown; never
   use `kill -9` during planned maintenance.

```bash
sudo docker compose --env-file .env.production -f compose.production.yaml stop caddy api
sudo docker compose --env-file .env.production -f compose.production.yaml stop worker-analysis worker-reports
```

The Compose grace periods are 30 minutes for analysis and 15 minutes for reports. If a
worker does not stop, do not force it until its exact task has been identified and a
recovery decision recorded.

6. Edit only `CHESS_LAB_IMAGE` and `APP_VERSION` in `.env.production`, validate silently,
   pull, and inspect the new digest/labels.
7. Run migrations as a one-shot job, then recreate long-lived services:

```bash
sudo docker compose --env-file .env.production -f compose.production.yaml --profile migrate config --quiet
sudo docker compose --env-file .env.production -f compose.production.yaml pull
sudo docker compose --env-file .env.production -f compose.production.yaml --profile migrate run --rm migrate
sudo docker compose --env-file .env.production -f compose.production.yaml \
  up -d api worker-analysis worker-reports caddy
sudo docker compose --env-file .env.production -f compose.production.yaml ps
```

8. Run the complete smoke, check logs/restarts/queues, and append the private deployment
   record. Keep `<previous-image>` and its compatible backup until the new release has
   passed the observation window.

## Exact application rollback

Rollback is a controlled deployment of the previously recorded immutable tag/digest,
not `latest` and not a rebuilt image.

1. Stop traffic, inspect/drain workers, and preserve incident logs.
2. Determine database compatibility from the release's migrations.
3. If the old application is compatible with the current schema, set
   `CHESS_LAB_IMAGE=<previous-image>` and its matching `APP_VERSION`, then validate,
   pull, start, and smoke exactly as in the update procedure.
4. Do not run an unreviewed `alembic downgrade`. If the previous application is not
   schema-compatible, restore the pre-deploy backup into a new database following
   [backup-restore.md](backup-restore.md#production-recovery-to-a-new-database), point
   `DB_NAME` at that recovered database, then start the previous image.
5. Keep the failed database and image for diagnosis. Delete neither until recovery is
   verified and another off-server backup exists.

Rollback evidence must record the failed/current digest, restored digest, database name
or backup object, Alembic revision, reason, operator, UTC time, and smoke result.

## References

- [Docker Engine installation on Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
- [Ubuntu OpenSSH server guidance](https://documentation.ubuntu.com/server/how-to/security/openssh-server/)
- [Ubuntu firewall guidance](https://documentation.ubuntu.com/server/how-to/security/firewalls/)
- [Celery worker shutdown semantics](https://docs.celeryq.dev/en/stable/userguide/workers.html#worker-shutdown)

