# Changelog

All notable changes to Enhanced Channel Manager are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `docs/adr/ADR-004-release-cut-promotion-discipline.md` — mandates short-lived `release/vX.Y.Z` branches + merge-commit PR + a seven-item pre-cut gate (G1a–G7) for all `dev`→`main` promotions. Narrow hotfix carve-out with a mechanical ceiling (>2 hotfixes between cuts triggers a mandatory incident review). Directly closes the 0.16.0-rollback root cause (zero-open-P0/P1 gate) and the PR #82 scope-sprawl pattern (non-release PRs to `main` forbidden) (`bd-4lk1q`).
- `docs/adr/ADR-005-code-security-gating-strategy.md` — extends CodeQL to run on PR-to-`dev` and push-to-`dev` (currently only fires on `main`-adjacent events); enforces delta-zero via GitHub Code Scanning merge protection rules on both `dev` and `main`; disables "Allow administrators to bypass required status checks" on both branches. Three-week phased rollout (advisory → dirty-base remediation → enforcement). Dismiss-with-comment policy with only two Phase-1 categories (false-positive / test-only); "won't fix" intentionally excluded until a mechanical co-sign enforcer exists (`bd-sm3n3`).
- Structured JSON logging on stdout with `trace_id` correlation middleware (honors inbound `X-Request-ID`, generates UUIDv4 otherwise, echoes in response header). `bind_context()` helper for attaching structured fields to long-running tasks (`bd-ak1db`, PR #80).
- Prometheus `/metrics` endpoint exposing `ecm_http_requests_total`, `ecm_http_request_duration_seconds`, `ecm_health_ready_ok`, and `ecm_health_ready_check_duration_seconds`. HTTP middleware labels by matched FastAPI route pattern so cardinality stays bounded. `/metrics` is unauthenticated by design (Prometheus scrapers have no session context) — hardening via reverse-proxy allowlist or bearer-token scrape credential is a separate future bead (`bd-ak1db`, PR #80).
- Alembic schema migration system with baseline revision `0001` capturing the current SQLAlchemy metadata (36 tables). `alembic upgrade head` wired into app startup; legacy pre-Alembic DBs are `stamp`ed rather than re-DDLed (`bd-c5wf5`, PR #81).
- `GET /api/health/schema` public endpoint returning `current_revision`, `head_revision`, `up_to_date`, `foreign_keys_enabled`, and `journal_mode` — required by DBAS restore/sync to gate backup imports on schema version (`bd-c5wf5`, PR #81).
- `docs/database_migrations.md` authoring conventions (`bd-c5wf5`, PR #81).
- `docs/runbooks/` scaffold with template and v0.16.0 hard-rollback exemplar (`bd-bwly4`).

### Changed
- `docs/backend_architecture.md` expanded with an Observability section covering logging schema, metric naming conventions, cardinality rules, `bind_context` usage, and the process for adding new metrics (`bd-ak1db`, PR #80).

## [0.16.0] — Yanked 2026-04-20

**This release was rolled back** before any external consumer pulled the tag. The GitHub Release, git tag, and GHCR image for `0.16.0` were deleted; `:latest` was retagged back to the `v0.15.2` multi-arch index. Rollback executed per `bd-vgm4l`; see `docs/runbooks/v0.16.0-rollback.md` for the procedure. The PO chose rollback over a forward hotfix because open P0/P1 bugs needed to clear before a release could ship. Work originally tagged `0.16.0` will re-ship in a later version once blockers are cleared.

## [0.15.2] — 2026-04-16

### Security
- Upgraded `vite` out of the vulnerable 7.0.0–7.3.1 range, resolving three high-severity advisories affecting the frontend dev tooling:
  - [GHSA-4w7w-66w2-5vf9](https://github.com/advisories/GHSA-4w7w-66w2-5vf9) — path traversal in optimized-deps `.map` handling.
  - [GHSA-v2wj-q39q-566r](https://github.com/advisories/GHSA-v2wj-q39q-566r) — `server.fs.deny` bypass with queries.
  - [GHSA-p9ff-h696-f583](https://github.com/advisories/GHSA-p9ff-h696-f583) — arbitrary file read via dev server WebSocket.
- No runtime code changes; only the frontend build tooling is affected. Dev server users should upgrade promptly.

## [0.15.1] — 2026-03-31

### Added
- Login rate limiting — 5 attempts per minute per IP via slowapi on both local and Dispatcharr login endpoints.
- Security headers: `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`.
- Client IP in login-failure logs (includes `X-Forwarded-For` for forensics).

### Changed
- **Password policy now follows NIST 800-63B** — dropped composition rules (uppercase/lowercase/number), replaced the 33-word list with the 10k common-passwords list, uniform 8-character minimum for all users including admin-created.
- CORS tightened — explicit method and header allow-lists instead of wildcards.

### Fixed
- EPG matching: channel names with a number prefix (e.g. `535 | ESPN`) were normalizing to `535espn` instead of `espn`, causing zero matches against EPG data.

### Security
- Injection / SSRF (OWASP A03/A10):
  - ffmpeg protocol allow-list on all invocations — blocks `file://`, `data://`, `concat:` and other dangerous protocols.
  - URL-scheme validation at input time on M3U, Xtream Codes, and EPG source endpoints — only `http://` and `https://` allowed.
  - Zip backup path canonicalization — defense-in-depth `resolve()` check alongside existing traversal guards.
- Logging hardening (OWASP A09):
  - 500-response scrubbing — global handler returns generic `Internal server error` to clients; real error stays in server logs.
  - Validation-error log redaction — `Authorization` / `Cookie` headers and auth-endpoint request bodies redacted from logs.

## [0.15.0] — 2026-03-29

### Added
- **Export & Publish** — generate M3U/XMLTV playlists from channel profiles and publish to cloud targets (S3, WebDAV, etc.) on a cron schedule. Includes channel selector, playlist preview, cloud-target management, and publish history.
- **Low-FPS detection** — streams below a configurable threshold (5/10/15/20 FPS) are flagged with an amber icon and deprioritized in Smart Sort. Zero-overhead, always-on, configurable in Settings → Maintenance.
- **Black-screen detection** — optional ffmpeg `signalstats` check after each probe flags dark/blank streams with a purple icon. Configurable sample duration (3–30s) in Settings → Maintenance.
- **Normalize Names engine** — tag-based rules engine for cleaning up stream names during bulk channel creation. YAML import/export, edit/revert controls, drag-and-drop priority ordering.
- **Backup & Restore** — full configuration backup/restore from Settings, with restore option during the first-run setup wizard.
- **Merge Channels** — combine two or more channels into one, consolidating their streams.
- Auto-creation enhancements — EPG logo-source action, Probe Streams action, Set Channel Profile action; Smart Sort option in rule sort dropdown; rule selection in Auto-Create schedule; separate stream sort from channel sort per rule.
- PUID/PGID support for Docker container user identity.
- TV Guide print view.
- JWT authentication in Swagger UI with `/swagger` redirect.
- Debug bundle for troubleshooting auth issues.

### Changed
- Server-side migration of EPG matching, stream normalization, and edit consolidation (previously client-side).
- Settings UI consistency — compact dropdowns, unified admin section CSS, proper field ordering.
- Scoped reprobe to last scheduled probe's channel groups.
- Capped Dispatcharr HTTP client connection pool.
- All GitHub Actions updated to Node.js 24-compatible versions.
- Migrated 161 `console.log` call sites to the structured logger.

### Fixed
- Persistent 503 when stream prober not initialized — prober now self-heals and auto-creates.
- `assign_logo` from EPG and duplicate rule priorities.
- Radio buttons, undefined CSS variables, and card backgrounds in admin tabs.
- Push-down renumbering, menu overflow, task-card grid.

### Removed
- ~2,000+ lines of dead code across backend and frontend, including duplicate `formatDate` implementations.

### Security
- Secured backup endpoints; improved token handling.
- Fixed npm audit vulnerabilities in `brace-expansion`, `flatted`, `picomatch`.

## [0.14.0] — 2026-03-06

Highlights: black-screen detection during probes; failed-stream re-probe scheduled task; auto-creation per-rule `skip_struck_streams`, provider order, and channel-number sort; pattern-builder UI for normalization rules; configurable HTTP/HTTPS ports via env vars. Numerous auto-creation and notification-center bug fixes. Full notes: [GitHub Release v0.14.0](https://github.com/MotWakorb/enhancedchannelmanager/releases/tag/v0.14.0).

## [0.13.1] — 2026-02-20

Bug-fix release. Full notes: [GitHub Release v0.13.1](https://github.com/MotWakorb/enhancedchannelmanager/releases/tag/v0.13.1).

## [0.13.0] — 2026-02-19

Full notes: [GitHub Release v0.13.0](https://github.com/MotWakorb/enhancedchannelmanager/releases/tag/v0.13.0).

## Earlier versions

Entries for `0.12.x` and earlier have not been backfilled into this file. See the [GitHub Releases page](https://github.com/MotWakorb/enhancedchannelmanager/releases) for the original release notes. Future releases will be recorded here under the appropriate Keep-a-Changelog sections.

[Unreleased]: https://github.com/MotWakorb/enhancedchannelmanager/compare/v0.15.2...HEAD
[0.15.2]: https://github.com/MotWakorb/enhancedchannelmanager/releases/tag/v0.15.2
[0.15.1]: https://github.com/MotWakorb/enhancedchannelmanager/releases/tag/v0.15.1
[0.15.0]: https://github.com/MotWakorb/enhancedchannelmanager/releases/tag/v0.15.0
[0.14.0]: https://github.com/MotWakorb/enhancedchannelmanager/releases/tag/v0.14.0
[0.13.1]: https://github.com/MotWakorb/enhancedchannelmanager/releases/tag/v0.13.1
[0.13.0]: https://github.com/MotWakorb/enhancedchannelmanager/releases/tag/v0.13.0
