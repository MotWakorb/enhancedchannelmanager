# Runbook: HTTPS Reverse Proxy for MCP OAuth (Custom Connectors)

> Front the MCP server's plain-HTTP port 6101 with TLS so Claude Desktop Custom Connectors can complete the OAuth flow. Required when using Claude Desktop's built-in Connector UI (no Node needed). The MCP SDK rejects non-loopback http:// issuers; HTTPS is the fix.

- **Severity**: Operator setup guide (no production alert)
- **Owner**: Project Engineer / operator
- **Last reviewed**: 2026-05-21
- **Related beads**: `enhancedchannelmanager-buiqr.11` (OAuth docs bundle), `enhancedchannelmanager-buiqr` (OAuth 2.1 epic)
- **Related ADR**: `docs/adr/ADR-009-mcp-oauth-authorization-server-split.md` §4 (oauth_allow_insecure), §Context (MCP SDK http rejection)

---

## Background

Claude Desktop's **Custom Connector** UI (Settings → Connectors → Add custom connector) uses OAuth 2.1 + PKCE. ECM acts as the OAuth Authorization Server; the MCP container acts as the Resource Server.

> ⚠️ **This runbook is only for the Custom Connector path, which requires PUBLIC internet exposure.** The Custom Connector is brokered by Anthropic's infrastructure — **Anthropic's servers connect *out* to your MCP server**, so it must be reachable from the public internet, not just over HTTPS on your LAN. A private/homelab deployment (internal DNS, RFC-1918 address) will fail the Custom Connector with *"Couldn't reach the MCP server"* — HTTPS alone does **not** fix that, because Anthropic's cloud can't route into your private network. **If you want to keep ECM private, you do not need this runbook at all** — use the `mcp-remote` bridge (Claude Desktop) or `.mcp.json` (Claude Code) instead; both run on your machine and connect over your LAN/VPN. See the README "Choose your connection method" and `docs/user_guide/integrations/mcp.md`.

So this runbook covers **two** requirements for the Custom Connector path: (1) **public reachability** — a public DNS name + a [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) (no inbound ports, cleanest for homelab) or a port-forward; and (2) **HTTPS** — described below.

The MCP SDK (1.27.0+) **rejects `http://` issuer URLs for non-loopback hostnames** during OAuth discovery. A deployment on `http://192.168.x.x:6101` or `http://my-server:6101` falls into this category — the SDK refuses to proceed. (Note this is a *second*, separate reason the Custom Connector needs HTTPS, on top of the public-reachability requirement above.)

**Solution**: front the MCP container with HTTPS using a reverse proxy (Caddy, nginx, or Traefik). The proxy terminates TLS on a public port (e.g., 443) and forwards traffic to the MCP container's plain-HTTP port 6101 on the local network.

**Trade-off the PO accepted**: this replaces "install Node.js" (the mcp-remote requirement) with "set up HTTPS". Neither is free. For operators who want neither, `oauth_allow_insecure=true` in `settings.json` opts into plain-HTTP OAuth (see the "HTTP-only LAN escape hatch" section below).

---

## Critical: OAUTH_ISSUER Must Match on Both Containers

This is the single most common misconfiguration. Both the ECM container and the MCP container must have `OAUTH_ISSUER` set to the **same external HTTPS origin**.

**Why this matters**: ECM (the Authorization Server) mints tokens with `iss = OAUTH_ISSUER`. MCP (the Resource Server) verifies tokens by checking `iss` matches its own `OAUTH_ISSUER`. If they differ by even one character, every OAuth Bearer token fails verification with a silent 401.

**Required on both containers** (in `docker-compose.yml` or environment):

```yaml
# ECM container
environment:
  - OAUTH_ISSUER=https://mcp.yourdomain.com

# MCP container
environment:
  - OAUTH_ISSUER=https://mcp.yourdomain.com
```

**Default behavior when OAUTH_ISSUER is unset**: both containers default to `https://ecm.local`, which only works loopback. For Custom Connectors on a LAN or the internet, you must set this explicitly.

**Discovery warning**: if `OAUTH_ISSUER` is unset, the discovery endpoint (`/.well-known/oauth-authorization-server`) derives the issuer from the incoming HTTP request's `Host` header. This creates a mismatch: tokens are minted with `iss=https://ecm.local` but the discovery doc advertises a request-derived URL. Strict OAuth clients (including Claude Desktop) reject tokens whose `iss` does not exactly match the discovery document's `issuer` field. Always set `OAUTH_ISSUER` explicitly.

---

## Option A: Caddy (Recommended — automatic TLS)

Caddy handles certificate provisioning automatically via Let's Encrypt. Requires a public DNS name pointing at your server and ports 80/443 open.

### Prerequisites

- A domain name or subdomain pointing at your server's public IP (e.g., `mcp.yourdomain.com`)
- Ports 80 and 443 open on your firewall/router
- Caddy installed ([caddyserver.com](https://caddyserver.com/docs/install))

### Caddyfile

```caddyfile
mcp.yourdomain.com {
    reverse_proxy localhost:6101
}
```

Save as `/etc/caddy/Caddyfile` (or include in your existing `Caddyfile`). Caddy provisions and renews the certificate automatically.

### docker-compose.yml additions

```yaml
services:
  ecm-ecm:
    # ... existing ECM config ...
    environment:
      - OAUTH_ISSUER=https://mcp.yourdomain.com

  ecm-mcp:
    # ... existing MCP config ...
    environment:
      - OAUTH_ISSUER=https://mcp.yourdomain.com
      - MCP_PORT=6101
    # No port 6101 exposure needed — Caddy reaches it on the host

  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    networks:
      - ecm_default

volumes:
  caddy_data:
  caddy_config:
```

### Reload after config change

```bash
docker exec <caddy-container> caddy reload --config /etc/caddy/Caddyfile
```

### Verify

```bash
curl -s https://mcp.yourdomain.com/.well-known/oauth-protected-resource | python3 -m json.tool
# Expect: {"issuer": "https://mcp.yourdomain.com", ...}

curl -s https://mcp.yourdomain.com/.well-known/oauth-authorization-server | python3 -m json.tool
# Expect: {"issuer": "https://mcp.yourdomain.com", "authorization_endpoint": "https://mcp.yourdomain.com/api/oauth/authorize", ...}
```

Both discovery documents should show `OAUTH_ISSUER` as `issuer`. If you see `https://ecm.local` or a mismatch, `OAUTH_ISSUER` is not set correctly on one of the containers.

---

## Option B: nginx

Use nginx when you have an existing nginx installation or need more control over TLS configuration.

### Prerequisites

- A TLS certificate for your domain. Obtain from Let's Encrypt:
  ```bash
  certbot certonly --standalone -d mcp.yourdomain.com
  # Certificates land at /etc/letsencrypt/live/mcp.yourdomain.com/
  ```
- nginx installed on the host.

### nginx site config

Save as `/etc/nginx/sites-available/mcp.yourdomain.com`:

```nginx
server {
    listen 443 ssl http2;
    server_name mcp.yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/mcp.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mcp.yourdomain.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # Proxy to MCP container
    location / {
        proxy_pass         http://localhost:6101;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto https;

        # MCP uses SSE/streaming — disable buffering
        proxy_buffering    off;
        proxy_read_timeout 3600s;
    }
}

# Redirect HTTP to HTTPS
server {
    listen 80;
    server_name mcp.yourdomain.com;
    return 301 https://$host$request_uri;
}
```

Enable and reload:

```bash
ln -s /etc/nginx/sites-available/mcp.yourdomain.com /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

### docker-compose.yml additions

```yaml
services:
  ecm-ecm:
    environment:
      - OAUTH_ISSUER=https://mcp.yourdomain.com

  ecm-mcp:
    environment:
      - OAUTH_ISSUER=https://mcp.yourdomain.com
      - MCP_PORT=6101
    ports:
      - "127.0.0.1:6101:6101"  # Bind to loopback only — nginx reaches it
```

Binding to `127.0.0.1:6101` means only the local nginx can reach the MCP container directly; external traffic goes through HTTPS only.

### Certificate renewal

Let's Encrypt certificates expire every 90 days. Set up auto-renewal:

```bash
# Test renewal (dry run)
certbot renew --dry-run

# The certbot systemd timer handles automatic renewal.
# Verify the timer is active:
systemctl status certbot.timer
```

Add a post-renewal hook to reload nginx: create `/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh`:

```bash
#!/bin/bash
systemctl reload nginx
```

```bash
chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
```

### Verify

```bash
curl -s https://mcp.yourdomain.com/health
# Expect: {"status": "ok", ...}

curl -s https://mcp.yourdomain.com/.well-known/oauth-protected-resource | python3 -m json.tool
# Expect: {"issuer": "https://mcp.yourdomain.com", ...}
```

---

## Nginx Proxy Manager (NPM) — field notes

NPM is a UI over nginx, so the nginx guidance applies. Add **one** Proxy Host per hostname (`ecm.yourdomain.com` → `:6100`, `ecm-mcp.yourdomain.com` → `:6101`), Scheme `http`, Force SSL + HTTP/2, Websockets Support ON. Two gotchas that have cost operators hours:

**1. Do NOT put `proxy_http_version 1.1;` in the Advanced tab when Websockets Support is on.** NPM already emits `proxy_http_version 1.1;` (and the `Connection`/`Upgrade` headers) when Websockets Support is enabled. If your Advanced (SSE) block *also* sets it, nginx fails with `"proxy_http_version" directive is duplicate` — and **NPM silently rolls back that host's config file**. The host + cert still look fine in the UI, but nginx serves no server block for the hostname, so TLS fails with `unrecognized name` and clients get *"couldn't reach the server."* For the MCP host, the Advanced tab should contain **only**:
```nginx
proxy_buffering off;
proxy_request_buffering off;
proxy_read_timeout 3600s;
proxy_send_timeout 3600s;
```

**2. Each hostname needs its own cert (or a wildcard).** A per-host cert for `ecm.yourdomain.com` does **not** cover `ecm-mcp.yourdomain.com`. Either issue a cert per host, or — cleanest for internal/homelab names — a `*.yourdomain.com` **wildcard via DNS-01** (e.g. Cloudflare/Route53), assigned to both Proxy Hosts.

**Confirm a host is really being served** (vs. a UI-only / rolled-back config), from any box that can reach NPM:
```bash
echo | openssl s_client -connect <npm-ip>:443 -servername ecm-mcp.yourdomain.com 2>&1 | openssl x509 -noout -subject
# A subject line = good. "tlsv1 unrecognized name" = nginx has no server block for that SNI
# (rolled-back conf or missing cert) — check the Advanced tab for a duplicate directive.
```

---

## Option C: Traefik (Docker-native, auto TLS)

Traefik is popular for Docker Compose deployments. It discovers services via container labels.

### Prerequisites

- A domain name pointing at your server.
- Ports 80 and 443 open.
- An email address for Let's Encrypt.

### docker-compose.yml

```yaml
services:
  traefik:
    image: traefik:v3
    restart: unless-stopped
    command:
      - "--api.insecure=false"
      - "--providers.docker=true"
      - "--providers.docker.exposedbydefault=false"
      - "--entrypoints.web.address=:80"
      - "--entrypoints.websecure.address=:443"
      - "--certificatesresolvers.le.acme.email=you@yourdomain.com"
      - "--certificatesresolvers.le.acme.storage=/letsencrypt/acme.json"
      - "--certificatesresolvers.le.acme.httpchallenge.entrypoint=web"
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - traefik_letsencrypt:/letsencrypt
    networks:
      - ecm_default

  ecm-ecm:
    # ... existing ECM config ...
    environment:
      - OAUTH_ISSUER=https://mcp.yourdomain.com

  ecm-mcp:
    # ... existing MCP config ...
    environment:
      - OAUTH_ISSUER=https://mcp.yourdomain.com
      - MCP_PORT=6101
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.ecm-mcp.rule=Host(`mcp.yourdomain.com`)"
      - "traefik.http.routers.ecm-mcp.entrypoints=websecure"
      - "traefik.http.routers.ecm-mcp.tls=true"
      - "traefik.http.routers.ecm-mcp.tls.certresolver=le"
      - "traefik.http.services.ecm-mcp.loadbalancer.server.port=6101"
      # Disable buffering for MCP streaming responses
      - "traefik.http.middlewares.ecm-mcp-buf.buffering.maxResponseBodyBytes=0"
      # HTTP → HTTPS redirect
      - "traefik.http.routers.ecm-mcp-http.rule=Host(`mcp.yourdomain.com`)"
      - "traefik.http.routers.ecm-mcp-http.entrypoints=web"
      - "traefik.http.routers.ecm-mcp-http.middlewares=redirect-to-https"
      - "traefik.http.middlewares.redirect-to-https.redirectscheme.scheme=https"
    networks:
      - ecm_default

volumes:
  traefik_letsencrypt:
```

### Verify

```bash
docker compose up -d traefik
# Wait for Let's Encrypt certificate provisioning (watch logs):
docker compose logs -f traefik | grep -i "cert\|acme\|obtained"

# Once ready:
curl -s https://mcp.yourdomain.com/.well-known/oauth-protected-resource | python3 -m json.tool
```

---

## HTTP-only LAN Escape Hatch (`oauth_allow_insecure`)

If you cannot set up HTTPS — for example, a closed LAN with no DNS — you can explicitly opt into plain-HTTP OAuth by setting `oauth_allow_insecure: true` in `settings.json` (ECM Settings → Advanced, or directly in `/config/settings.json`):

```json
{
  "oauth_allow_insecure": true
}
```

**Security implications of `oauth_allow_insecure: true`**:
- OAuth Bearer tokens transit the network in cleartext. Anyone on the same network segment can intercept and replay them.
- The MCP SDK may still reject `http://` non-loopback issuers in some versions. If it does, you cannot use Custom Connectors without HTTPS regardless of this flag.
- This flag does not affect the static `?api_key=` path, which works over HTTP regardless.

**Default is `false` — fail-closed.** Without this flag, the OAuth discovery endpoints return 404 on plain-HTTP, non-loopback deployments. This is intentional: the default posture refuses the insecure flow rather than silently allowing token interception.

> **Real-deploy verification note (AC5)**: The recipes in this runbook are correct and standard, but a real deployment verification (completing an end-to-end OAuth flow through the proxy) requires a live HTTPS environment. The releaser must walk through the manual verification checklist in `docs/runbooks/mcp-release-verification.md` before tagging a release that includes OAuth changes.

---

## Post-Proxy: Connecting Claude Desktop

After HTTPS is working, in Claude Desktop:

1. Go to **Settings → Connectors → Add custom connector**
2. Enter the MCP server URL: `https://mcp.yourdomain.com/mcp`
3. Claude Desktop discovers the OAuth endpoints automatically via `/.well-known/oauth-protected-resource`
4. Complete the authorization in the ECM consent screen (opens in your browser)
5. Claude Desktop stores the OAuth token — no Node.js required

> **redirect_uri caveat**: ECM's registered `redirect_uri` for Claude Desktop is currently a placeholder pending verification (bead `enhancedchannelmanager-buiqr.6`). The connector authorization flow will not complete until the correct Claude Desktop callback URI is confirmed and registered. This is a known open item; watch bead buiqr.6 for resolution.

---

## Troubleshooting

### Silent 401s on every tool call

**Most likely cause**: `OAUTH_ISSUER` mismatch between ECM and MCP containers.

```bash
# Check what issuer the token was minted with:
docker exec ecm-ecm-1 python3 -c "
import json, base64
# Paste a raw Bearer JWT here (get one from browser network tab during auth)
token = 'PASTE_JWT_HERE'
payload = token.split('.')[1]
# Add padding
payload += '=' * (4 - len(payload) % 4)
print(json.dumps(json.loads(base64.b64decode(payload)), indent=2))
" 2>/dev/null | grep iss

# Check what issuer the MCP container expects:
curl -s http://localhost:6101/.well-known/oauth-protected-resource | python3 -m json.tool | grep issuer
```

If the `iss` field in the token differs from the MCP container's `issuer`, set `OAUTH_ISSUER` identically on both containers and restart.

### Discovery returns 404

The MCP container's `oauth_allow_insecure` is `false` (default) and the request reached MCP over plain HTTP. Either:
- Your proxy is not working — check that requests to `https://mcp.yourdomain.com` are actually forwarded to MCP.
- You're hitting MCP directly on port 6101 without TLS — this is expected behavior. Only use the HTTPS proxy URL.

### `spawn npx ENOENT` in Claude Desktop logs

This error means you configured the `mcp-remote` bridge method (the Node.js path), not the Custom Connector. The Custom Connector path uses Settings → Connectors → Add custom connector and does not run `npx`. If you see this error, remove the `mcpServers` entry from `claude_desktop_config.json` and use the Connector UI instead.

### Certificate errors

```bash
# Test TLS from another machine:
curl -v https://mcp.yourdomain.com/health

# Check certificate expiry:
echo | openssl s_client -connect mcp.yourdomain.com:443 2>/dev/null \
  | openssl x509 -noout -dates
```

---

## References

- `docs/adr/ADR-009-mcp-oauth-authorization-server-split.md` — architecture decision: ECM=AS, MCP=RS, offline JWT verification, dual-path routing
- `docs/security/threat_model_mcp_oauth.md` — STRIDE threat model for the OAuth surface
- `docs/runbooks/mcp-release-verification.md` — per-release manual verification checklist
- [Caddy documentation](https://caddyserver.com/docs/) — official Caddy reverse proxy docs
- [nginx SSL configuration](https://nginx.org/en/docs/http/ngx_http_ssl_module.html) — nginx TLS reference
- [Traefik documentation](https://doc.traefik.io/traefik/) — Traefik v3 Docker provider docs
