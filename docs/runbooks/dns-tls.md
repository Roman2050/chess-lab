# DNS and TLS runbook

This procedure publishes the single-VPS API through Caddy. Replace
`<api.example.com>` and `<server-ip>` with the exact production hostname and static VPS
IPv4 address. Do not create an `AAAA` record unless IPv6 routing and both firewall
layers have been deliberately configured.

## Secure the registrar and DNS account

Before changing DNS:

1. Enable registrar/DNS-provider 2FA with a hardware key or authenticator app.
2. Save recovery codes offline in the operator password manager.
3. Enable registrar lock and domain auto-renew; verify the payment method and expiry
   alert address.
4. Restrict DNS API tokens to the smallest zone and permissions. Chess Lab's default
   HTTP ACME flow needs no DNS API token on the VPS.
5. Record the current authoritative nameservers and existing records before edits.

Do not enable HSTS preload, a CDN proxy, or provider-specific TLS modes during the first
deployment. Establish direct origin TLS first.

## Create the DNS record

At the authoritative DNS provider, create:

```text
Type: A
Name: api
Value: <server-ip>
TTL: 300 during initial deployment; increase after stability
Proxy/CDN: DNS-only during first deployment
```

Check authoritative and public resolution from the operator device. Replace the
nameserver with one returned by the first command:

```bash
dig NS example.com +short
dig @<authoritative-nameserver> <api.example.com> A +short
dig <api.example.com> A +short
```

The answer must be exactly `<server-ip>`. Remove stale `A`/`AAAA` records that would send
clients elsewhere only after confirming they are not used by another service.

## Verify the network boundary

Provider firewall and UFW must both allow inbound TCP `80` and `443`. SSH remains
restricted as described in [deployment.md](deployment.md). PostgreSQL, Redis, Uvicorn,
and Docker are never public.

On the VPS:

```bash
sudo ufw status verbose
cd /opt/chess-lab
grep -n 'published:' compose.production.yaml
sudo docker compose --env-file .env.production -f compose.production.yaml ps
```

The manifest inspection must show only published host ports `80` and `443`. From an
external host, test only the approved address/ports; do not run intrusive scans against
networks you do not own.

## Validate Caddy before issuance

Confirm `.env.production` contains the exact hostname and monitored ACME email without
printing the whole file. Review them interactively with an editor if necessary. Then:

```bash
cd /opt/chess-lab
sudo docker compose --env-file .env.production -f compose.production.yaml \
  run --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

The Compose topology mounts named volumes `caddy_data` at `/data` and `caddy_config` at
`/config`. Caddy stores certificates, private keys, and renewal state in `caddy_data`.
This volume is persistent state, not a cache: never delete or recreate it as a routine
TLS fix.

After the API is healthy, start Caddy once:

```bash
sudo docker compose --env-file .env.production -f compose.production.yaml up -d caddy
sudo docker compose --env-file .env.production -f compose.production.yaml logs --tail 100 caddy
```

Caddy obtains and renews the certificate automatically. If issuance fails, fix DNS,
firewall, clock, or ACME errors before retrying. Do not create a restart/retry loop;
repeated failed issuance can hit CA rate limits.

## Direct HTTPS smoke

Before introducing a CDN, test the origin directly while preserving the correct TLS
hostname/SNI:

```bash
curl --fail --silent --show-error --resolve <api.example.com>:443:<server-ip> \
  https://<api.example.com>/health
curl --head --resolve <api.example.com>:80:<server-ip> http://<api.example.com>/health
```

The HTTPS request must return `200`. HTTP must redirect to the same hostname over HTTPS.
Then test normal public resolution:

```bash
curl --fail --silent --show-error https://<api.example.com>/health
curl --fail --silent --show-error https://<api.example.com>/ready
curl --fail --silent --show-error https://<api.example.com>/openapi.json >/dev/null
```

Inspect the served certificate without exporting its private key:

```bash
openssl s_client -connect <api.example.com>:443 -servername <api.example.com> </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates -ext subjectAltName
```

Verify that the SAN contains `<api.example.com>`, dates are valid, the chain is trusted,
and the issuer is public. Repeat the smoke in a signed-out browser and from a second
network.

## Observe renewal

Caddy normally renews without operator action. Monitoring must alert before expiry; do
not depend on memory.

- External TLS monitoring: warning at 21 days remaining, critical at 7 days.
- Review Caddy logs after deployment and during the first expected renewal window.
- Confirm `caddy_data` remains mounted and the Caddy container is not restart-looping.
- Test HTTPS after Caddy image updates or DNS/firewall changes.

Useful read-only checks:

```bash
cd /opt/chess-lab
sudo docker compose --env-file .env.production -f compose.production.yaml logs --since 24h caddy
sudo docker volume inspect chess-lab-production_caddy_data
```

Volume inspection shows metadata/mount location but not certificate contents. Do not
copy private keys into tickets or operator workstations.

## HSTS and optional CDN

Consider HSTS only after direct HTTPS and renewal have remained stable through an
observation period and every required subdomain is HTTPS-ready. Start with a short
`max-age`; do not select `includeSubDomains` or preload until their long-lived impact is
understood. HSTS preload can make mistakes difficult to reverse.

If a CDN is added later:

1. preserve end-to-end certificate validation to the Caddy origin;
2. use a strict/full TLS mode, never an HTTP or validation-disabled origin mode;
3. restrict origin ingress to the CDN only after a tested operator bypass/recovery path
   exists;
4. re-test request-size limits, timeouts, real client IP handling, CORS, `/health`, and
   the DB-backed demo endpoint;
5. document where DNS, CDN, and origin certificates are now controlled.

The MVP needs no CDN. A DNS-only `A` record and Caddy automatic HTTPS are the simplest
supported boundary.

## DNS or certificate incident

1. Preserve current DNS records, Caddy logs, certificate dates, and container state.
2. Confirm authoritative DNS first, then public resolvers, provider firewall, UFW, and
   host clock.
3. Do not delete `caddy_data`, disable TLS validation, or repeatedly restart Caddy.
4. If DNS was changed accidentally, restore the last recorded exact value and wait for
   TTL expiry; do not make multiple speculative changes.
5. For suspected account compromise, follow
   [incident.md](incident.md#compromised-host-or-control-plane-account).

## References

- [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https)
- [Caddy data-directory conventions](https://caddyserver.com/docs/conventions#data-directory)

