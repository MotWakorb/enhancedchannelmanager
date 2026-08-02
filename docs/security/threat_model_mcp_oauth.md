> **⚠️ SUPERSEDED: MCP OAuth removed (bd-9axgc, bd-jir0m).** The MCP OAuth 2.1
> "Custom Connector" offering was retired by PO decision (`enhancedchannelmanager-9axgc`):
> the OAuth Authorization Server endpoints were unregistered (404), the MCP
> Resource Server rejected OAuth/JWT-shaped Bearer tokens, and the OAuth UI was
> hidden. The OAuth code was then **fully removed from the codebase** in v0.17.3
> (`enhancedchannelmanager-jir0m`): the AS/provider/store modules, the RS verify
> path, the OAuth config fields/helpers, the consent UI, and the OAuth-only tests
> are gone. The `looks_like_jwt` no-fail-cascade guard was kept (JWT-shaped
> Bearers are still rejected, never compared to the static key). This threat model
> is **retained for security-audit history** only. The supported MCP authentication
> method is the static `?api_key=` path. See ADR-009 (Superseded).

# STRIDE Threat Model: MCP OAuth 2.1 (ECM as Authorization Server, MCP as Resource Server)

**Bead:** `enhancedchannelmanager-buiqr.1` (ADR + STRIDE threat model, the security companion to `docs/adr/ADR-009-mcp-oauth-authorization-server-split.md`); informs implementation children `buiqr.2`–`buiqr.9`
**Author:** Security Engineer persona (Claude)
**Date:** 2026-05-20 (draft); 2026-05-21 (accepted)
**Status:** Accepted. PO signed off 2026-05-21 (AC#5 on ADR-009 + Assumptions §6 + Residual Risks §8), incorporating the Option A amendment (token store ECM-managed; RS verifies offline and never reads the store)
**Related:** epic `enhancedchannelmanager-buiqr` (PO-locked decisions), ADR-009 (the architecture this model secures: every mitigation below ties to an ADR-009 §section), `enhancedchannelmanager-ak7xa` (closed: CI gate precondition), `docs/architecture.md` (MCP Server static-key baseline + `settings.json` credential schema), `backend/auth/tokens.py` (HS256 + `jti` revocation + `hash_token()` patterns reused), `docs/adr/ADR-008-interactive-stream-dedup.md`, `docs/security/threat_model_dbas_import.md` (template mirrored)

> **Amendment 2026-05-21 (Option A: `buiqr.2` / blocker `gswk2`).** The MCP
> container mounts `ecm-config:/config` **read-only**, making an MCP-managed
> token store and any RS read of `mcp_oauth.db` infeasible. The token store is
> therefore **ECM-managed** (`backend/auth/oauth_store.py`); the **RS verifies
> Bearer JWTs purely offline and never opens the store.** Consequences for this
> model: the AS↔store↔RS shared-state boundary collapses to **ECM-only**
> (the RS no longer touches `mcp_oauth.db`); the revocation-gap row **TS2** and
> hardening item **#15** change from "RS reads the revocation table" to
> "refresh-token revocation enforced at the AS `/token` + access-token window
> bounded solely by the short TTL (≤ 15 min)." Rows below are updated in place;
> see ADR-009's Revision History for the full rationale and rejected alternative.

---

## 1. Scope & System Overview

Epic `buiqr` adds OAuth 2.1 + PKCE so Claude Desktop's **Custom Connector** UI can authenticate to ECM's MCP server **without** the Node.js `mcp-remote` bridge. The architecture (ADR-009) splits the OAuth roles across the two existing containers:

- **ECM container = Authorization Server (AS):** hosts `GET /authorize` + consent UI, `POST /token` (PKCE S256), and `GET /.well-known/oauth-authorization-server`. It owns the admin user session.
- **MCP container = Resource Server (RS):** hosts `GET /.well-known/oauth-protected-resource`, validates Bearer JWTs **offline** (HS256, shared secret from `/config/settings.json`, **no per-request callback to ECM**), and serves the `/mcp` tools surface.

A **second** authentication path now coexists with the **permanent** static `?api_key=` path. The RS routes by credential **shape** (JWT-shaped → OAuth-only; static-key-shaped → static-key-only) and **never** fail-cascades from a failed OAuth validation to the static-key check (ADR-009 §2). Tokens are stored hashed-at-rest (SHA-256) in an **ECM-managed** SQLite DB at `/config/mcp_oauth.db` (WAL), written and read **only by ECM** (the RS does not access it, per Option A).

This threat model covers the **OAuth subsystem ECM + MCP will build.** The current static-key MCP auth (`APIKeyAuthMiddleware`, `mcp_api_key`, per `docs/architecture.md`) is the **inherited baseline**; its protections are table stakes and the dual-path interaction is modeled here (§3.6) because two paths sharing one RS is precisely where auth-bypass bugs live.

Attack surfaces modeled:

1. **Discovery**: `/.well-known/oauth-authorization-server` (AS), `/.well-known/oauth-protected-resource` (RS): unauthenticated metadata, information leakage, HTTP-downgrade.
2. **Authorization (`/authorize` + consent)**: browser-driven flow: redirect-URI validation, open-redirect, consent-phishing/clickjacking, PKCE downgrade.
3. **Token (`/token`)**: code-for-token exchange: PKCE enforcement, code interception/replay, rate-limit bypass.
4. **Resource access (`/mcp` Bearer validation)**: the dual-path-by-shape router: confused-deputy / fail-cascade bypass, token replay, JWT alg-confusion.
5. **Token store**: `/config/mcp_oauth.db`: at-rest compromise, revocation correctness.
6. **Shared secret**: the HS256 key in `settings.json`: forgery on compromise, rotation.
7. **Cross-boundary**: MCP → Dispatcharr trust boundary; `dispatcharr_api_key` isolation.

---

## 2. Data Flow (Trust Boundaries)

```
[Admin browser / Claude Desktop] --TLS (operator-supplied proxy)-->
   |
   |--(discover)--> [MCP RS /.well-known/oauth-protected-resource] --points at--> [ECM AS]
   |--(discover)--> [ECM AS /.well-known/oauth-authorization-server]
   |
   |--(/authorize + PKCE S256 challenge)--> [ECM AS] --> admin session (existing JWT auth)
   |                                                  --> consent UI (client name pinned)
   |<--(redirect to allowlisted redirect_uri + code)--
   |
   |--(/token: code + PKCE verifier)--> [ECM AS] --HS256 sign--> JWT (sub=admin, jti, aud=RS)
   |                                            --writes + reads hash--> [/config/mcp_oauth.db] (ECM rw, sole owner)
   |                                            --refresh rotation + family-reuse check on refresh
   |
   |--(/mcp + Authorization: Bearer <JWT>)--> [MCP RS dual-path-by-shape router]
   |                                            --offline HS256 verify (shared secret; NO store read)
   |                                            --> ~110 MCP tools --> [ECM backend :6100]
   |                                                                 --> [Dispatcharr (separate boundary)]
[Claude Code / scripts] --(/mcp + ?api_key= OR Bearer static-key)--> [static-key path only]
```

Trust boundaries crossed:

- **Client → operator-supplied TLS proxy → ECM AS / MCP RS** (the network boundary; **TLS is operator-supplied**, not ECM-managed, per ADR-009 §4/§8).
- **AS ⇄ shared `/config/settings.json`** (`mcp_oauth_signing_secret`, `oauth_allow_insecure`, `mcp_api_key`). ECM mounts `/config` **rw**; the MCP RS mounts it **read-only** and reads only the **dedicated** `mcp_oauth_signing_secret` + flag it needs for offline verify. The RS **never** reads `auth_settings.json` (ECM's user-session `jwt.secret_key` stays ECM-only: SR1 blast-radius isolation, amended 2026-05-21).
- **AS ⇄ `/config/mcp_oauth.db` (ECM-only).** Hashed tokens/codes/refresh/revocation records. **Option A:** the store is owned and accessed **exclusively by ECM**: the RS does **not** read or write it (it verifies offline). There is no shared OAuth *state* across the two containers and no runtime HTTP coupling between AS and RS (ADR-009 §1/§5).
- **MCP RS → ECM backend → Dispatcharr** (the existing service boundary; Dispatcharr is admin-configured & trusted per the existing model; the OAuth token must **not** widen authority beyond the admin's existing reach; see §3.6 / threat O1).

---

## 3. STRIDE Analysis

**Legend:** `status` ∈ {**baseline** (already enforced by the existing static-key MCP auth or ECM JWT subsystem), **to-build** (an OAuth child `buiqr.2`–`buiqr.9` must implement), **accepted-risk** (PO-signed deviation)}.
Each row's mitigation cites the ADR-009 §section it derives from. Severity is relative to the **MCP OAuth subsystem**, not the whole product.

### 3.1 Spoofing

| # | Surface | Threat | Attack scenario | Mitigation (ADR-009 ref) | Status | Sev |
|---|---------|--------|-----------------|--------------------------|--------|-----|
| SP1 | Token validation | Attacker forges a Bearer JWT to impersonate the admin | Attacker crafts a JWT with `sub`=admin and presents it to `/mcp` | Offline HS256 verify against the shared secret rejects any signature not produced by the AS (§1); secret never leaves `/config/settings.json` (§6/SR1) | done (`buiqr.8`: `oauth_rs.verify_oauth_token` returns 401 on bad/absent signature; secret read-only, never logged) | High |
| SP2 | Token validation | **JWT `alg` confusion / `alg:none`**: attacker submits a token with `alg:none` or swaps HS256→RS256 to bypass signature check | Token header set to `{"alg":"none"}` or `{"alg":"RS256"}` so the verifier "validates" with no/attacker key | Verifier pins **exactly `HS256`** and rejects any other `alg` (incl. `none`) with 401; mirrors `backend/auth/tokens.py` `ALGORITHM = "HS256"`. Explicit allowlist, not "whatever the header says" (§1, §3) | done (`buiqr.8`: `ALLOWED_ALGORITHMS=["HS256"]` explicit allowlist; `alg:none` + RS256-confusion regression tests assert 401) | **High** |
| SP3 | `/authorize` | Unauthenticated actor reaches `/authorize` and obtains a code | Attacker hits `/authorize` without an admin session | `/authorize` requires the existing ECM admin session before rendering consent (§3); no session → ECM login | to-build (`buiqr.3`/`buiqr.7`) | High |
| SP4 | Client identity | Attacker registers/spoofs a client to obtain tokens | Attacker presents an unknown or look-alike `client_id` | Hardcoded client registry; unknown `client_id` rejected; **no Dynamic Client Registration** (§3) | to-build (`buiqr.6`) | High |
| SP5 | Discovery | Rogue server impersonates the AS to a client | Attacker MITMs discovery and points the client at an attacker AS | Operator-supplied TLS authenticates the AS host; RS protected-resource doc names the canonical AS issuer; discovery 404 on plain HTTP unless opted in (§4/§7) | baseline + done (`buiqr.5`: RS doc `authorization_servers` → canonical issuer; fail-closed 404) | Med |
| SP6 | Static-key path | Static key presented as a forged OAuth session | Attacker submits the static key shaped as a JWT to confuse routing | Routing is by **shape** decided pre-validation; a static-key value is not JWT-shaped so it never enters the OAuth path, and vice-versa (§2) | done (`buiqr.8`: `oauth_rs.looks_like_jwt` classifies pre-validation; a JWT-shaped value that fails OAuth verify is never compared to `mcp_api_key`) | Med |

### 3.2 Tampering

| # | Surface | Threat | Attack scenario | Mitigation (ADR-009 ref) | Status | Sev |
|---|---------|--------|-----------------|--------------------------|--------|-----|
| T1 | Token in transit | MITM modifies the Bearer token / authorization code in flight | Plain-HTTP deploy; attacker on the LAN rewrites the token | TLS (operator-supplied) is the in-transit integrity control; on plain HTTP, OAuth is **off by default** (discovery 404, §4); the operator must explicitly opt in (HT1) | baseline + to-build | High |
| T2 | Token contents | Attacker mutates JWT claims (e.g., extends `exp`, changes `scope`) | Edit the payload segment of a captured token | HS256 signature covers header+payload; any claim mutation invalidates the signature → 401 (§1) | done (`buiqr.8`: `verify_oauth_token` HS256 signature check → any payload mutation = `InvalidSignatureError` → 401; bad-signature regression test) | High |
| T3 | PKCE | **PKCE downgrade**: attacker forces `plain` to defeat code-interception protection | Client/attacker sends `code_challenge_method=plain` | **S256 only**; `plain` (or missing challenge) rejected with **400 `invalid_request`** at `/authorize` and `/token` (§3, AC#5) | to-build (`buiqr.3`) | **High** |
| T4 | Token store | Attacker tampers with `mcp_oauth.db` to un-revoke or forge a token record | Write access to `/config/mcp_oauth.db` | Store holds **hashes**, not tokens: cannot reconstruct a usable token from a record (§5); file lives on the protected `/config` volume (filesystem perms = compensating control, §6/TS1); **Option A narrows the writers**: only ECM mounts `/config` rw; the MCP container mounts it `:ro` and cannot write the DB at all, so an MCP-side compromise cannot tamper with the store | to-build (`buiqr.2`) | Med |
| T5 | Settings flag | Attacker flips `oauth_allow_insecure=true` to enable HTTP OAuth | Write access to `settings.json` | Flag default false; flipping it requires write access to `settings.json` (same trust level as the HS256 secret itself; if an attacker has that, the secret is already lost). Change is operator-visible in `settings.json` (§4) | to-build (`buiqr.5`) | Med |
| T6 | Consent params | Tampered `redirect_uri` / `return_to` to redirect the code/flow | Attacker alters the redirect target in the authorize request | **Exact-match allowlist** of `redirect_uri` against the hardcoded client registry; consent `return_to` enforced same-origin/allowlist (§3; see RD1, OR1) | to-build (`buiqr.6`/`buiqr.7`) | High |
| **CSRF1** | Consent approval | **Consent CSRF: forged `state` on `/authorize/approve` grants a code in the admin's name** | Attacker tricks the admin's browser into POSTing `/authorize/approve` with an attacker-chosen `state`/params (login-CSRF / cross-session), minting a code bound to the admin for the attacker's flow | **`state` bound to the admin subject at `/authorize`** (single-use, ≤10-min, hashed-at-rest binding in `mcp_oauth.db`); `/authorize/approve` consumes it and rejects any missing/forged/mismatched/replayed/cross-session `state` with **400 `invalid_request`** before a code is minted (§3). Server-side seam consumed by the consent UI (`buiqr.7`) | done (`buiqr.4`) | High |

### 3.3 Repudiation

| # | Surface | Threat | Attack scenario | Mitigation (ADR-009 ref) | Status | Sev |
|---|---------|--------|-----------------|--------------------------|--------|-----|
| R1 | Consent grant | Admin denies having authorized Claude Desktop | Dispute over who granted access | AS writes an audit/journal entry at consent: user (admin `sub`), client name, scope, timestamp, request id; token carries `jti` for later correlation (§3, §5; mirrors `backend/auth/tokens.py` `jti`) | to-build (`buiqr.3`/`buiqr.7`) | Med |
| R2 | Token issuance | No record of which token was issued / to which client | Untraceable token in the wild | `/token` records the issued token's `jti`+hash in `mcp_oauth.db` with client + issued-at (§5) | to-build (`buiqr.2`/`buiqr.3`) | Med |
| R3 | Revocation | No record of a revocation action | Dispute over whether a token was revoked | Revocation marks the `jti`/hash record revoked with a timestamp; mirrors `revoke_token(jti)` (§5) | to-build (`buiqr.2`) | Low |
| R4 | Resource access | Cannot distinguish an OAuth-authenticated call from a static-key call in logs | Audit blind spot if both paths log identically | Shape-routing means the path is known before tool dispatch; the RS records auth-mode (oauth vs static-key) per request, the dual-path distinction is preserved, not collapsed (§2; a side benefit of refusing fail-cascade) | done (`buiqr.8`: logs `auth_method=oauth sub=…` vs `auth_method=static_key` on every successful auth (AC5); regression-tested via `caplog`) | Med |

### 3.4 Information Disclosure

| # | Surface | Threat | Attack scenario | Mitigation (ADR-009 ref) | Status | Sev |
|---|---------|--------|-----------------|--------------------------|--------|-----|
| ID1 | Discovery | **`/.well-known/*` leaks secrets or host-internal details** | Anyone GETs the public discovery docs and reads the HS256 secret, the internal Docker host `ecm:6100`, filesystem paths, or `mcp_api_key` | Discovery docs expose **only** protocol-required fields (issuer, endpoint URLs, `code_challenge_methods=["S256"]`, `scopes=["mcp"]`); **never** the secret, internal hostnames, paths, or the static key; shape pinned by snapshot tests (§7, AC#4) | done (`buiqr.5`: ID1-clean shape + snapshot + no-leak tests, both `/.well-known/*`) | **High** |
| ID2 | Token store | At-rest compromise of `mcp_oauth.db` yields usable tokens | Attacker reads the DB file from a backup or the volume | Tokens **hashed-at-rest (SHA-256)**: a stolen record is a hash, not a bearer credential (§5, mirrors `hash_token()`) | to-build (`buiqr.2`) | High |
| ID3 | Token in transit | Token observed on plain HTTP and read | Plain-HTTP deploy; passive sniffer captures the Bearer token | OAuth off by default on HTTP (discovery 404, §4); if opted in, the operator accepted this (HT1); short TTL bounds the window (§3) | to-build (`buiqr.5`) | High |
| ID4 | Error responses | Error bodies leak stack traces / secret material / internal paths | Malformed `/token` request returns a verbose 500 | Errors return short OAuth-standard codes (`invalid_request`, `invalid_grant`, etc.); full detail server-side only (mirrors existing ECM error pattern) | to-build (`buiqr.3`) | Med |
| ID5 | Backup/export | The HS256 secret or `mcp_oauth.db` is swept into an export artifact in plaintext | Operator exports config; the OAuth secret rides along | The dedicated `mcp_oauth_signing_secret` is credential-class in `settings.json` and **is** in the shared `_SETTINGS_CREDENTIAL_FIELDS` redaction tuple (`backup.py`), same as `mcp_api_key`/`dispatcharr_api_key` (§5/§6; ties to `docs/architecture.md` redaction rule) | done (`buiqr.3`: added to `_SETTINGS_CREDENTIAL_FIELDS`) | Med |

### 3.5 Denial of Service

| # | Surface | Threat | Attack scenario | Mitigation (ADR-009 ref) | Status | Sev |
|---|---------|--------|-----------------|--------------------------|--------|-----|
| D1 | `/token` | **Brute-force / rate-limit bypass** on the token endpoint | Attacker floods `/token` guessing codes or grinding the secret | **Rate limiting per-IP + per-user** on `/token` (§6, AC#9); short-lived single-use authorization codes (§3) | done (`buiqr.4`: slowapi per-IP + per-user buckets, default `10/min`, env-configurable) | High |
| D2 | `/authorize` | Consent-spam / authorization-flood | Attacker hammers `/authorize` to exhaust the AS or spam the admin | **Rate limiting per-IP + per-user** on `/authorize` (§6, AC#9) | done (`buiqr.4`: slowapi per-IP + per-user buckets, default `5/min`, env-configurable) | Med |
| D3 | Token validation | Offline verify is cheap by design: DoS via expensive validation is low | Flood `/mcp` with tokens | In-process HMAC verify is O(token size); no per-request callback to amplify (§1); the static-key path's existing protections apply to its share | done (`buiqr.8`: in-process PyJWT HMAC verify; no network/DB read per request (AC4/AC6)) | Low |
| D4 | Token store | `mcp_oauth.db` lock contention / unbounded growth | High token-issuance rate bloats the DB or WAL | WAL mode (§5); single-admin issuance keeps volume bounded; a reaper for expired/revoked rows is an additive lever (Assumptions §6/A4) | to-build (`buiqr.2`) | Low |

### 3.6 Elevation of Privilege

| # | Surface | Threat | Attack scenario | Mitigation (ADR-009 ref) | Status | Sev |
|---|---------|--------|-----------------|--------------------------|--------|-----|
| **CD1** | Dual-auth router | **Confused-deputy via dual-auth fail-cascade (THE HEADLINE THREAT)** | Attacker presents a JWT-shaped-but-invalid Bearer value (bad signature / expired / `alg:none`); a fail-cascade design would "fall back" to evaluating it as a static key, collapsing OAuth's guarantees onto the weaker path, or an OAuth misconfig silently routes all traffic to static-key | **Route by credential SHAPE, decided before any validation.** JWT-shaped → OAuth-only; **OAuth-validation FAILURE returns 401 and NEVER falls through to the static-key check.** Static-key-shaped → static-key-only. No path retries the other on failure (§2, AC#2/AC#7). Dedicated regression test asserts a JWT-shaped-but-invalid token is **not** evaluated as a static key (`buiqr.9`) | done (`buiqr.8`: `APIKeyAuthMiddleware._handle_oauth` never reads `mcp_api_key`; `test_dual_path_routing.py` spies on the static-key reader and asserts it is NEVER called on any OAuth failure, including invalid-Bearer-with-valid-`?api_key=` present. Broader matrix follows in `buiqr.9`) | **Crit** |
| **RD1** | `/authorize` | **Redirect-URI not validated → token/code delivered to attacker** | Attacker uses a legit `client_id` with an attacker-controlled `redirect_uri`; the code is sent to the attacker | **Exact-match allowlist** of `redirect_uri` against the hardcoded client registry (no prefix/substring/wildcard matching); mismatch rejected before code issuance (§3) | done (`buiqr.3` `validate_redirect_uri`; regression-tested `buiqr.4`: 400 + no open redirect at `/authorize` + `/authorize/approve`) | **High** |
| **OR1** | Consent `return_to` | **Open-redirect via consent `return_to`** | Consent flow carries a `return_to`/post-consent param set to `https://evil.example`; ECM redirects the admin there, enabling phishing/credential capture | `return_to` enforced **same-origin / allowlist**; any off-allowlist target rejected or replaced with a safe default (§3) | done (`buiqr.7`: `safe_return_to` allowlist-by-shape on the consent `return_to`: only a single-`/` relative path is honored; absolute / `//host` / scheme-bearing values replaced with the safe internal default; regression-tested) | High |
| **CP1** | Consent UI | **Consent-phishing / clickjacking** | Attacker frames the consent page (clickjacking) or crafts a flow that tricks the admin into approving a malicious client; or vague copy hides what is being granted | Anti-framing headers (`X-Frame-Options: DENY` / `frame-ancestors 'none'` CSP); **client name pinned from the hardcoded registry** (not attacker input); **clear consent copy**: the PO-locked single-`mcp`-scope wording naming exactly what is granted (§3) | done (`buiqr.7`: consent route serves CSP `frame-ancestors 'none'` in addition to the global `X-Frame-Options: DENY`; client name fetched server-side from the registry via `/api/oauth/authorize/consent-context`, never from the `client_id` query input; PO-locked permission copy shown verbatim; pending security-engineer copy sign-off per AC2) | High |
| **O1** | MCP → Dispatcharr | **Dispatcharr trust boundary: OAuth token grants beyond the admin's existing authority** | An OAuth-authenticated MCP call reaches Dispatcharr with more reach than the admin already has, or leaks `dispatcharr_api_key` through the OAuth path | The OAuth token authorizes the admin acting **as themselves**; MCP→Dispatcharr uses the existing `dispatcharr_api_key` exactly as today; the OAuth token does **not** widen Dispatcharr authority. `dispatcharr_api_key` is **never referenced in OAuth code paths** (CI grep guard, §6, AC#10) | to-build (`buiqr.4`/`buiqr.8`) | High |
| EP1 | Token scope | Token used outside its intended audience/scope | A token minted for the MCP RS is replayed against another endpoint, or the single `mcp` scope is ignored | `aud` claim bound to the MCP RS and checked; `scope=mcp` carried and enforced; single-scope in v1 (§3) | done (`buiqr.8`: `verify_oauth_token` enforces `aud=="ecm-mcp"` + `iss` + manual `scope` contains `"mcp"`; wrong-aud / wrong-scope regression tests assert 401) | Med |
| EP2 | Static-key path | OAuth bug silently widens the static-key path's reach | A shared helper change affects both paths | The two paths are dispatched separately by shape; no shared validation fallthrough (§2); dual-path matrix is a permanent CI fixture (epic AC#3) | done (`buiqr.8`: OAuth + static-key handled in separate `_handle_oauth` / `_handle_static_key` methods with no shared fallthrough; static-key regression suite asserts existing behavior unchanged. Permanent matrix in `buiqr.9`) | Med |
| **EP3** | Static-key authority | **`mcp_api_key` is admin-equivalent: INCLUDING ECM user-account management** | Anyone holding `mcp_api_key` authenticates as the transient `auth_provider="mcp"` service principal (`is_admin=True`), which the global `auth_middleware` accepts for **any** `/api/*` path. That includes the user-management routes (`/api/auth/admin/users*`, covering create / update / delete of ECM user accounts), not just channel operations. The key is therefore a **full admin credential**, equal in blast radius to compromising the ECM admin login. **Scoped explicitly:** the MCP service principal is itself NOT a user account: self-mutation routes (`PUT /api/auth/me`, `POST /api/auth/change-password`) reject it with a clean **403** (it is transient/non-persisted; a `session.refresh` on it would otherwise 500). So the key can manage *other* accounts but cannot edit its own non-existent one. | **Treat `mcp_api_key` as an admin credential at rest and in transit:** it is credential-class in `settings.json` and **is** redacted from backups/exports via `_SETTINGS_CREDENTIAL_FIELDS` (`backup.py`, §6/ID5; same tuple as `dispatcharr_api_key` / `mcp_oauth_signing_secret`; **confirmed present**). Constant-time compare on both the RS (`server.py:_handle_static_key`) and the backend (`main.py auth_middleware`, `auth/dependencies._is_mcp_service_token`) via `hmac.compare_digest`. No timing oracle, no plaintext in exports. Self-mutation guard `reject_mcp_service_principal_mutation` keeps the transient principal from reaching transient-User ORM ops. | done (`bd-1wq7z.24` (b)/(c), `bd-i3axt`: redaction confirmed; constant-time compares on RS + backend; `/api/auth/me` & `/change-password` 403-guard the MCP principal) | **High** |

### 3.7 Transport / Deployment-posture threats (HTTP-only)

| # | Surface | STRIDE | Threat | Attack scenario | Mitigation (ADR-009 ref) | Status | Sev |
|---|---------|--------|--------|-----------------|--------------------------|--------|-----|
| **HT1** | Whole flow | Info-Disclosure / Tampering | **Token-replay over plain HTTP / HTTP-only deploy downgrade** | Operator runs `http://<LAN-IP>:6101`; tokens, codes, and the PKCE exchange traverse cleartext; a LAN attacker captures and **replays** a Bearer token | **Fail-closed:** discovery returns **404 on plain HTTP** unless `oauth_allow_insecure=true` is explicitly set (§4, AC#6); the default refuses the insecure flow. If opted in, the operator accepted the risk; **short access-token TTL** bounds the replay window (§3); docs steer operators to HTTPS via reverse proxy (`buiqr.11`) | done (`buiqr.5`: `oauth_allow_insecure` default-false fail-closed gate on both `/.well-known/*`; one-WARN-per-startup; 200/404 matrix tests) | **High** |
| HT2 | Discovery | Info-Disclosure | SDK http-issuer rejection masks a misconfig as "OAuth broken" | Operator on HTTP can't connect, doesn't know why | The `oauth_allow_insecure` flag is the documented, deliberate switch; the 404 is intentional fail-closed behavior, documented in the HTTPS guide (`buiqr.11`) | done (`buiqr.5`: deliberate 404 + one WARN per startup explaining the gate / opt-in) | Low |

### 3.8 Secret-management threats (dedicated OAuth HS256 key)

| # | Surface | STRIDE | Threat | Attack scenario | Mitigation (ADR-009 ref) | Status | Sev |
|---|---------|--------|--------|-----------------|--------------------------|--------|-----|
| **SR1** | Dedicated OAuth HS256 secret | Spoofing / EoP | **OAuth-secret compromise → MCP-scope token forgery (NOT admin-session forgery)** | Attacker reads `mcp_oauth_signing_secret` from `settings.json` (volume access, backup leak, export) and mints valid **MCP-scope** tokens | **Dedicated-secret isolation (amended 2026-05-21, `buiqr.3`):** the OAuth signing secret is `settings.json` → `mcp_oauth_signing_secret`, **separate from ECM's user-session `jwt.secret_key`** (`auth_settings.json`, which the MCP RS never reads). So this compromise forges only `mcp`-scope tokens: it does **NOT** yield ECM admin sessions (the blast-radius bound this row exists to enforce). Secret is credential-class: `/config/settings.json` file perms (compensating control); redacted from exports via `_SETTINGS_CREDENTIAL_FIELDS` (§6, ID5); recoverable by **rotation** (SR2) without touching admin sessions | done (`buiqr.3`: dedicated secret + redaction) / to-build (`buiqr.4`: rate-limit) | **High** |
| SR2 | Secret rotation | Availability / EoP | **Rotation of the shared secret invalidates all live tokens (or fails to)** | Operator rotates the HS256 key; either all Claude Desktop sessions break with no warning, or stale tokens keep validating because rotation wasn't propagated to the RS | Both containers read the secret from the **same** `settings.json` (single source); rotation invalidates all live tokens **by design** (acceptable: short TTL means re-consent is cheap); document the operator-facing effect (re-add the connector) in `buiqr.11`. No second copy of the secret to drift | to-build (`buiqr.4`/`buiqr.11`) | Med |

### 3.9 Token-store threats (`/config/mcp_oauth.db`)

| # | Surface | STRIDE | Threat | Attack scenario | Mitigation (ADR-009 ref) | Status | Sev |
|---|---------|--------|--------|-----------------|--------------------------|--------|-----|
| TS1 | `mcp_oauth.db` | Info-Disclosure / Tampering | At-rest compromise or tamper of the token store | See ID2 (disclosure) + T4 (tamper) | Hashes-not-tokens (§5); `/config` volume perms; WAL durability; (cross-ref ID2/T4) | to-build (`buiqr.2`) | Med |
| TS2 | Revocation enforcement | EoP | **Revocation gap**: a revoked *access* token keeps working because the RS verifies offline and (Option A) never reads the store | Admin revokes a grant; the offline RS honors a still-live access token until its TTL expires | **Option A enforcement:** the RS does **not** read the store; revocation is enforced at the **AS `/token`**: a revoked refresh token / killed family cannot mint a new access token, so renewal stops at once and the live access token dies within the **short TTL (≤ 15 min, §3)**, which is the sole access-token backstop. The gap is the documented offline-verification trade-off (ADR-009 §1/§5). | to-build (`buiqr.3` AS-side; access-token TTL `buiqr.3`) | Med |

**Cell coverage note.** The six canonical STRIDE dimensions are all covered (§3.1–§3.6), plus three deployment/secret/store dimension tables (§3.7–§3.9) where a single surface warranted dedicated rows. Every grooming-named concern from AC#4 appears as an explicit row; see the AC#4 coverage map in §7.

---

## 4. Hardening Checklist (acceptance criteria for the OAuth children)

The OAuth implementation must satisfy **all** of the following, each mapped to STRIDE rows above and to the epic's acceptance criteria:

1. **Dual-path-by-shape routing, no fail-cascade**: the RS classifies by credential shape before validation; a JWT-shaped-but-invalid token returns 401 and is **never** evaluated as a static key. Regression test asserts this. *(CD1, SP6, EP2, epic AC#2/AC#7)*
2. **JWT `alg` pinned to HS256**: verifier rejects `alg:none`, `RS256`, or any non-HS256 algorithm. *(SP2, T2)*
3. **PKCE S256 only**: `plain` or missing `code_challenge_method` → 400 `invalid_request` at `/authorize` and `/token`. *(T3, epic AC#5)*
4. **Exact-match redirect-URI allowlist**: `redirect_uri` matched exactly against the hardcoded client registry; no wildcard/prefix/substring matching. *(RD1, T6)*
5. **Open-redirect guard on consent `return_to`**: same-origin/allowlist enforced; off-allowlist rejected or defaulted. *(OR1, T6)*
6. **Consent anti-framing + pinned client name + clear copy**: `X-Frame-Options: DENY` / CSP `frame-ancestors 'none'`; client name from the registry; PO-locked single-`mcp`-scope copy. *(CP1)*
7. **`oauth_allow_insecure` fail-closed**: discovery returns 404 on plain HTTP unless the flag is explicitly true; default false. *(HT1, HT2, ID3, epic AC#6)*
8. **Discovery hygiene**: `/.well-known/*` exposes only protocol-required fields; no secret, internal hostname, path, or static-key leakage; shape pinned by snapshot tests. *(ID1, SP5, epic AC#4)*
9. **Tokens hashed-at-rest**: `mcp_oauth.db` stores SHA-256 hashes (mirroring `hash_token()`), never plaintext tokens. *(ID2, TS1, epic AC#8)*
10. **Rate limiting on `/authorize` + `/token`**: per-IP + per-user. *(D1, D2, epic AC#9)*
11. **`dispatcharr_api_key` isolation**: never referenced in any OAuth code path; CI grep guard returns zero hits. *(O1, epic AC#10)*
12. **Secret + token-store redaction in exports**: the HS256 secret and `mcp_oauth.db` are credential-class; covered by `_SETTINGS_CREDENTIAL_FIELDS` / export redaction. *(ID5, SR1)*
13. **Audience + scope enforcement**: `aud` bound to the MCP RS and checked; `scope=mcp` carried and enforced. *(EP1)*
14. **Single-use, short-TTL authorization codes + short-TTL access tokens**: bounds code-replay and the offline revocation gap. *(D1, TS2, HT1)*
15. **Revocation enforced at the AS, not the RS (Option A)**: the RS verifies offline and does **not** read the store; refresh-token / family revocation is enforced at `/token` (a revoked refresh cannot mint a new access token), and the access-token window is bounded solely by the short TTL. Mirrors `revoke_token(jti)` on the AS side. *(TS2, R3)*
16. **Audit on consent / issuance / revocation**: AS journals consent (admin `sub`, client, scope, request id), token issuance (`jti`+hash), and revocation; RS records auth-mode per request. *(R1, R2, R3, R4)*
17. **Error-response hygiene**: OAuth-standard short error codes; full detail server-side only. *(ID4)*

---

## 5. Test Cases (split by container: `mcp-server/tests/` for RS, `backend/tests/` for AS + store)

> The `mcp-server/tests/` suite is now CI-gated (precondition `enhancedchannelmanager-ak7xa`, closed), so the RS tests will not rot silently. **Under Option A** the token-store tests live in `backend/tests/unit/test_oauth_store.py` (the store is ECM-managed), already merged with `buiqr.2`, and the AS endpoint tests land in the backend suite with `buiqr.3`. RS-side tests (dual-path routing, offline verify, discovery) stay in `mcp-server/tests/`. Detailed test infrastructure landed in `buiqr.9`.

> **`buiqr.9` test infrastructure (merged).** The permanent OAuth regression + abuse-case fixtures now live in real files:
> - **Abuse-case suite (10 cases, split by container).** RS-side cases (expired access token, wrong audience, malformed JWT, cross-instance token): `mcp-server/tests/test_oauth.py`. AS-side cases (PKCE plain, PKCE verifier mismatch, auth-code replay, mismatched `redirect_uri`, missing `code_challenge`, refresh-token reuse): `backend/tests/routers/test_oauth_abuse.py`. The split is forced by the AS code not being importable in the RS CI job; each file's header carries the full 10-case map.
> - **Dual-path regression matrix (CD1, PO decision #4).** `mcp-server/tests/test_server.py::TestMCPAuthMatrix` / `TestMCPInitializeMatrix` parametrize every auth-mode-agnostic case over BOTH `auth_mode=static_key` and `auth_mode=oauth_bearer`.
> - **`.mcp.json` compat guard (epic AC2/AC3).** `mcp-server/tests/test_server.py::TestMcpJsonCompatGuard` loads the repo-root `.mcp.json` literal config and drives an initialize round-trip on the static path.
> - **Captured-traffic replay harness (PO decision #6).** `mcp-server/tests/test_oauth_flow_replay.py` replays `mcp-server/tests/fixtures/claude_desktop_oauth_flow.json` (a SYNTHETIC capture generated from the real AS+RS endpoints; see its `generate_oauth_flow_fixture.py`; re-capture from a real Claude Desktop flow once the `buiqr.6` `redirect_uri` is verified).

- `test_dual_path_routing.py` *(CD1: the headline)*
  - `test_invalid_jwt_not_evaluated_as_static_key`: JWT-shaped Bearer with bad signature → 401, and the value is **never** compared to `mcp_api_key`.
  - `test_expired_jwt_returns_401_no_fallback`: expired token → 401, no static-key retry.
  - `test_alg_none_rejected`: `{"alg":"none"}` token → 401. *(SP2)*
  - `test_static_key_path_unaffected`: `?api_key=` and `Bearer <static-key>` still authenticate (epic AC#2/AC#3).
- `test_pkce.py` *(T3)*
  - `test_plain_method_rejected_400`: `code_challenge_method=plain` → 400 `invalid_request`.
  - `test_missing_challenge_rejected`: no challenge → 400.
- `test_redirect_validation.py` *(RD1, OR1)*
  - `test_unregistered_redirect_uri_rejected`: off-allowlist `redirect_uri` → rejected before code issuance.
  - `test_redirect_uri_exact_match_only`: a prefix/substring of a registered URI → rejected.
  - `test_consent_return_to_off_origin_rejected`: `return_to=https://evil` → rejected/defaulted.
- `test_consent.py` *(CP1)*
  - `test_consent_sets_anti_framing_headers`.
  - `test_consent_pins_client_name_from_registry`: attacker-supplied client name not reflected.
- `test_discovery.py` *(ID1, HT1, AC#4)*
  - `test_well_known_shape_snapshot`: RFC 8414 / RFC 9728 shape pinned.
  - `test_discovery_no_secret_or_internal_host`: grep response for the HS256 secret, `ecm:6100`, paths, `mcp_api_key` → absent.
  - `test_discovery_404_on_plain_http_when_insecure_false`.
  - `test_discovery_200_on_plain_http_when_insecure_true`.
- `backend/tests/unit/test_oauth_store.py` *(ID2, TS2; ECM-side, merged with `buiqr.2`)*
  - `test_*_hashed_at_rest`: DB rows contain SHA-256 hashes, not the raw token/code.
  - refresh-rotation + reuse-detection: a replayed refresh token raises and kills the family (the AS-side revocation enforcement point; `buiqr.3` wires this into `/token` so a revoked refresh cannot mint a new access token). The RS performs **no** store read, so there is no RS-side "revoke then validate → 401" test; access-token revocation is bounded by the short TTL.
- `test_rate_limit.py` *(D1, D2)*
  - `test_token_endpoint_rate_limited` / `test_authorize_endpoint_rate_limited`.
- `test_dispatcharr_isolation.py` *(O1)*
  - `test_no_dispatcharr_api_key_in_oauth_paths`: CI grep guard / static assertion.

---

## 6. Assumptions & Trust Boundaries (PO decisions where flagged)

This preamble states what the model assumes; deviations change the risk picture and need PO confirmation.

- **TB1. TLS is operator-supplied, not ECM-managed.** ECM does not terminate TLS for the OAuth surface; the operator fronts MCP with a reverse proxy (Caddy/nginx/Traefik) or ECM's `:6143`. In-transit confidentiality/integrity (T1, ID3, HT1) **depends on the operator doing this.** PO-locked (ADR-009 §4/§8). The `oauth_allow_insecure` flag is the explicit acknowledgment that an operator may run without it.
- **TB2. Both containers co-locate on `/config`, with asymmetric access.** The AS/RS split assumes both containers see the `/config` volume, but **ECM mounts it read-write and the MCP RS mounts it read-only** (Option A). The RS reads only the HS256 secret + `oauth_allow_insecure` flag from `settings.json` for offline verify; `mcp_oauth.db` is **ECM-only** (the RS never opens it). HS256 (symmetric) is justified by this co-location (ADR-009 §1). A future multi-host split is the documented RS256/JWKS exit path, **out of scope** here.
- **TB3. `/config/settings.json` write access ≈ full trust.** Anyone who can write `settings.json` already controls the HS256 secret, so threats whose precondition is `settings.json` write access (T5, SR1) are bounded by the same trust level as secret compromise. The mitigation is filesystem perms on `/config`, which is an operational/deployment control, not OAuth code.
- **TB4. Dispatcharr is the existing admin-configured, trusted boundary.** The OAuth token does not change the MCP→Dispatcharr relationship; Dispatcharr is reached with `dispatcharr_api_key` as today (O1). Cross-instance / untrusted-Dispatcharr scenarios are out of scope (consistent with the existing model).
- **TB5. Single ECM admin identity.** v1 binds tokens to the single admin (`sub`). **Multi-tenant per-user grants are out of scope** (ADR-009 §8). If multi-admin/multi-user ECM auth lands later, this model must be revisited for per-user token scoping. **PO to confirm** single-admin is the v1 posture (it is, per epic; flagged for traceability).
- **A1. Offline-verification revocation gap is acceptable.** Tokens are verified offline; under Option A the RS does not read the store, so access-token revocation is bounded **solely by the short TTL**, while refresh-token revocation is enforced at the AS `/token` (renewal stops immediately) (TS2, ADR-009 §1/§5). **PO to confirm** that a bounded access-token revocation gap (≤ TTL, ≤ 15 min recommended) is acceptable for v1 in exchange for the latency (AC#11) and failure-isolation properties. *(This is the central security/perf trade: the PO accepted offline verification at grooming and Option A on 2026-05-21; restated here for the AC#5 sign-off.)*
- **A2. Access-token TTL value.** The model assumes a short TTL. **Under Option A the access-token TTL is the *sole* backstop for access-token revocation** (the RS does not read the store), so it should sit at the short end: **≤ 15 min recommended**, tighter than ECM's 30-min `ACCESS_TOKEN_EXPIRE_MINUTES`. The exact value is `buiqr.3`'s call; a longer TTL widens HT1 (replay) and TS2 (revocation gap). **PO to ratify** the TTL ceiling.
- **A3. Rate-limit thresholds.** Per-IP + per-user limits on `/authorize` and `/token` are mandated (D1, D2); the concrete numbers are `buiqr.4`'s call. **PO to ratify** thresholds sized to single-admin usage.
- **A4. Token-store reaper deferred.** Expired/revoked rows in `mcp_oauth.db` are not auto-pruned in v1 (D4); single-admin volume keeps this small. An additive reaper is a backlog candidate if the table grows. **PO to confirm** deferral is acceptable.

---

## 7. AC#4 Coverage Map (every grooming concern → STRIDE row)

AC#4 of `buiqr.1`: *every concern from grooming must appear as a row.* Coverage:

| Grooming concern | STRIDE row(s) | Section |
|---|---|---|
| **Confused-deputy via dual-auth (fail-cascade bypass, headline)** | **CD1** | §3.6 |
| **Redirect-URI validation** | **RD1** (+ T6) | §3.6 / §3.2 |
| **Open-redirect via consent `return_to`** | **OR1** (+ T6) | §3.6 / §3.2 |
| **Token-replay over plain HTTP** | **HT1** (+ ID3, T1) | §3.7 |
| **Consent-phishing (clickjacking/framing, copy, client pinning)** | **CP1** | §3.6 |
| **Dispatcharr trust boundary / `dispatcharr_api_key` isolation** | **O1** | §3.6 |
| **HTTP-only deploy (discovery 404 unless opt-in, downgrade)** | **HT1, HT2** | §3.7 |
| **Discovery-endpoint information leakage** | **ID1** (+ SP5) | §3.4 / §3.1 |
| *Security-Engineer-added:* PKCE downgrade / `plain` | T3 | §3.2 |
| *Security-Engineer-added:* JWT alg-confusion / `alg:none` | SP2 | §3.1 |
| *Security-Engineer-added:* token-store at-rest compromise | ID2, TS1 | §3.4 / §3.9 |
| *Security-Engineer-added:* revocation gap (offline verify) | TS2 | §3.9 |
| *Security-Engineer-added:* rate-limit bypass / brute-force | D1, D2 | §3.5 |
| *Security-Engineer-added:* shared HS256 secret compromise + rotation | SR1, SR2 | §3.8 |
| *Security-Engineer-added:* audit/auth-mode distinction | R1–R4 | §3.3 |

All eight grooming-named concerns from AC#4 are present, plus seven Security-Engineer-added rows the architecture's shape warranted.

---

## 8. Residual Risks Accepted for v1

These remain after the §4 mitigations; each is bounded and noted for the PO's AC#5 sign-off.

- **Residual: offline-verification revocation gap (Medium, accepted, A1/TS2).** A revoked *access* token is honored until its TTL expires; under Option A the RS does not read the store, so the access-token gap is **purely TTL-bounded** (refresh-token revocation, by contrast, is enforced immediately at the AS `/token`). Not closeable without a per-request callback or an RS store read, both of which the architecture deliberately rejects (latency AC#11 + failure isolation + the MCP `/config:ro` mount). The narrower the TTL (≤ 15 min), the smaller the gap.
- **Residual: token-replay on opt-in HTTP (Medium, operator-accepted, HT1).** When `oauth_allow_insecure=true`, tokens traverse cleartext and can be replayed within their TTL. Bounded by the explicit opt-in (default fails closed) and short TTL. The operator chose this; the safe default is HTTPS.
- **Residual: OAuth-secret compromise → MCP-scope token forgery (Medium impact / Low likelihood, accepted with compensating controls, SR1).** Whoever can read the **dedicated** `mcp_oauth_signing_secret` can forge `mcp`-scope tokens, but **not** ECM admin sessions, since that secret is separate from the user-session `jwt.secret_key` (amended 2026-05-21; this is the deliberate blast-radius bound). Likelihood is gated by `/config` filesystem perms and export redaction; impact is recoverable by rotation (which invalidates live OAuth tokens, leaving admin sessions intact). No KMS/HSM for v1 (consistent with the existing `settings.json`-stored credentials posture).
- **Residual: authenticated-admin abuse (Low, inherent).** The single ECM admin can authorize Claude Desktop and then drive any MCP tool: that is the feature. The OAuth layer authenticates *that the admin* granted access; it does not constrain what the admin (or an agent acting on their behalf) may do beyond the single `mcp` scope. Bounded by single-admin identity (TB5) and the consent audit trail (R1). Per-tool scopes (a future epic) would narrow this.
- **Residual: `mcp_api_key` is a full admin credential (Low likelihood / High impact, accepted by design, EP3).** The static MCP key authenticates an admin-equivalent service principal that can drive **every** `/api/*` route, **including ECM user-account management** (`/api/auth/admin/users*`, covering create/modify/delete of accounts). Possession of the key == possession of an ECM admin. This is inherent to the PO-locked static-key design (the operator sets one trusted key for one trusted automation). Bounded by: the key being credential-class and **redacted from backups/exports** (`_SETTINGS_CREDENTIAL_FIELDS`, confirmed); `/config` filesystem perms; constant-time compare (no timing oracle, `bd-i3axt`); and the self-mutation 403 guard (`bd-1wq7z.24` (c)) preventing the transient principal from corrupting account state. Per-credential scoping (narrowing the static key below full-admin) is a future epic, not v0.17.2.
- **Residual: SDK/transport behavior on HTTP (Low, HT2).** The MCP SDK 1.27.0 http-issuer rejection means HTTP-only operators get a 404, which is intentional fail-closed but can read as "OAuth broken." Mitigated by docs (`buiqr.11`); not a security exposure, an operability one.

---

## 9. Related Work & References

- `docs/adr/ADR-009-mcp-oauth-authorization-server-split.md`: **the architecture this model secures; every mitigation cites an ADR-009 §section.**
- Epic `enhancedchannelmanager-buiqr`: PO-locked OAuth decisions (the source of truth).
- `enhancedchannelmanager-buiqr.2`–`buiqr.9`: implementation children; the §4 checklist items are their acceptance criteria.
- `enhancedchannelmanager-ak7xa` (closed): CI gate for `mcp-server/tests/`; the precondition that keeps the §5 tests from rotting.
- `docs/architecture.md` → MCP Server: current static-key auth baseline + `settings.json` credential schema (the `_SETTINGS_CREDENTIAL_FIELDS` redaction rule §3.4/ID5 reuses).
- `backend/auth/tokens.py`: HS256 (`ALGORITHM = "HS256"`), `jti` revocation set, `revoke_token(jti)`, `hash_token()` (SHA-256); the primitives this model's mitigations mirror.
- `docs/security/threat_model_dbas_import.md`: template mirrored (sections, table shape, status/severity columns, residual-risk closing).
- `docs/adr/ADR-005-code-security-gating-strategy.md`: CodeQL delta-zero gate covering the new OAuth routers at PR time.
- RFC 8414 (AS metadata), RFC 9728 (protected-resource metadata), RFC 7636 (PKCE), RFC 6749/9700 (OAuth 2.0 / 2.1 security BCP), RFC 7591 (DCR, **not** implemented).
