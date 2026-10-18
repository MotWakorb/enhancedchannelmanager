# Stream outbound SSRF boundary

ECM applies the shared `ssrf_outbound_mode` policy to stream previews, bitrate
measurement, ffprobe metadata collection, and FFmpeg black-screen/transcode
operations.

HTTPX-backed preview and bitrate requests use resolve-once connection pinning.
Every A/AAAA answer is checked, the socket connects to the validated address,
the original hostname remains the HTTP `Host` and TLS SNI name, and every
redirect is independently validated. Redirect chains are capped at five.
Cloud metadata, link-local, multicast, reserved, and malformed destinations
remain denied in both modes. RFC1918, shared-address-space, and loopback targets
remain available only in `lan_friendly` mode so configured local IPTV and
Dispatcharr installations continue to work.

## Redirect scheme downgrades

Redirect re-validation refuses an `https` → `http` downgrade by default.
Provider-media probes and direct stream previews explicitly permit it. Providers commonly answer
`https://<portal>/live/<user>/<pass>/<id>.ts` with a 302 onto a plain-HTTP edge
node. That hop is unencrypted. This compatibility policy does not guarantee
that a provider's redirect URL contains no credentials.

The waivers are scoped to probing (ffprobe metadata, resdet, bitrate measurement,
black-screen detection) and direct stream previews, and cover only the scheme downgrade. The denylist,
resolve-once connection pinning, per-hop re-validation, redirect chain cap, and
cross-origin credential stripping still apply. Authenticated Dispatcharr channel
previews, EPG fetches, cloud backup targets, and sync targets keep refusal. Each
downgrade that is followed is logged.

These boundaries are exercised by `test_probe_scheme_downgrade.py` and
`test_preview_provider_policy.py` under `backend/tests/security/`, including
later HLS resources and the channel proxy's bearer header.

## DNS and preview startup

The stream adapter runs DNS validation in worker threads, with at most two retries
on transient `EAI_AGAIN` only (100 ms then 200 ms). Permanent errors, empty answers,
and denied addresses fail closed. The shared synchronous validator still performs
one lookup per invocation. A successful lookup validates every answer and pins the
connection without a second provider DNS lookup. `test_stream_dns_retry.py` covers
the retry bounds, off-event-loop resolution, and mixed-answer refusal.

Previews open the same upstream response they will consume before sending browser
headers. Policy refusal is HTTP 403, DNS/connect/UA configuration failure is 502,
upstream HTTP rejection is 502 with its status code, and upstream timeout is 504.
Messages omit provider URLs, headers and raw client exceptions. Preview startup
uses a 10-second connect timeout and 30-second I/O timeout (not a viewing limit).
Disconnect cleanup closes responses/relays and terminates/reaps FFmpeg, including
disconnects before the first body read. See `test_preview_startup.py`.

## FFmpeg and ffprobe boundary

FFmpeg and ffprobe do not receive provider HTTP(S) URLs. ECM first resolves and
validates the initial redirect chain through the pinned HTTPX path, then gives
the subprocess a tokenized URL on an ephemeral loopback-only relay. The
subprocess protocol allowlist is reduced to `http,tcp,crypto`: HTTP can reach
only opaque loopback relay URLs, while `crypto` is required internally to
decrypt AES-128 HLS segments. Provider URLs and channel bearer credentials
remain in ECM's HTTP client and never appear in subprocess arguments.

The relay streams bounded chunks with downstream backpressure. It rewrites HLS
playlist resource lines and quoted `URI` attributes (segments, child playlists,
keys, and maps) to fresh opaque relay tokens. Every token fetch independently
uses the pinned redirect-safe client. Authorization is retained for the same
normalized origin and stripped for cross-origin resources. Manifests are capped
at 2 MiB and one subprocess relay may register at most 1,024 resources.
Cancellation terminates (and, if needed, kills) the subprocess before closing
active HTTP responses and the relay. Direct HTTP transport streams and HLS
therefore remain supported
without giving FFmpeg a provider-network path.

UDP, RTP, and RTMP remain direct subprocess inputs because they are not HTTP
redirect protocols. ECM validates their literal or resolved destination under
the configured LAN policy immediately before every spawn and retry. Other
direct schemes are rejected.

### Probe startup budgets and optional sampling

Metadata and resdet relay acquisition have a wall-clock startup deadline equal
to the configured probe timeout; black-screen acquisition has a separate
30-second deadline. These deadlines include DNS validation and the entire
initial redirect chain, before subprocess spawn. Cancelling a DNS worker stops
awaiting its result; it cannot stop an OS resolver already running in a thread.
That discarded result cannot start a provider request.

Each of these subprocess phases passes finite HTTPX connect/write/pool budgets
of 10 seconds. Metadata and resdet read inactivity budgets are the configured
probe timeout plus 5 seconds; black-screen reads use the sample duration plus
30 seconds. They apply to initial and later HLS resources. Startup and read
budgets are separate from the subprocess's sample/decode limit, so relay
acquisition does not consume the intended sample duration.

Metadata probing shares the existing retry count and delay between retryable
subprocess diagnostics and provider HTTP 500/502/503/504/520 or
connection/read/write drops and remote protocol failures. Each retry closes the
previous relay before waiting, and independently validates a fresh destination.
HTTP 401/403/404, other non-selected statuses, timeouts, policy denials and
cancellations do not trigger retries, including failures of later HLS resources.

The owned validated subprocess input retains typed relay failure provenance for
initial body reads and later segments, keys and nested manifests. Probes settle
relay response handlers and check this state before classifying subprocess exit,
metadata or brightness output (including a zero exit with partial output). A
policy refusal takes precedence over an earlier resource network failure;
nonretryable failures take precedence over transient ones. The loopback error
response remains generic, or aborts an already-started body; preview cleanup does not raise raw
provider exceptions into its streaming response handler. These seams and the
independent startup watchdog assertions are covered by
`test_stream_probe_relay_failures.py`.

Expected optional black-screen HTTP/network/DNS inability returns unknown
(`None`); successful metadata and measured bitrate are saved, the success resets
failure strikes, and the prior black-screen flag is retained. Policy denials,
cancellations, configuration errors and process-spawn errors still propagate.
Nonzero FFmpeg exits discard even nonempty partial brightness samples. Logs use
failure types or numeric exit codes, not provider exception text.

The relay cancels a handler when its local consumer disconnects, including while
the provider is stalled. Only expected downstream reset/broken-pipe writes are
suppressed; upstream errors and cancellation retain their semantics. The handler
closes its initial upstream response/context immediately on completion or quit,
rather than retaining it until the subprocess relay context ends. Regression
coverage in `backend/tests/unit/test_stream_probe_relay_failures.py` uses real
loopback consumers and the real relay with a pinned fake provider transport,
plus isolated in-memory persistence tests.

## Probe failure diagnostics

A failed probe reports its cause without reporting the provider URL. ffmpeg and
ffprobe diagnostics may embed the provider URL and its credentials, so
subprocess text is never copied into logs or persisted state. Exceptions ECM's
own guards raise are classified separately, by exception type rather than by
inspecting the message, and their fixed messages are logged and surfaced in the
probe run report. The run report also groups failures by cause, so a wholesale
guard rejection reads as one named reason rather than an unexplained count.

The only remaining resolution race is on direct UDP, RTP, and RTMP inputs:
their subprocess libraries resolve again after ECM validates the destination.
HTTP(S), including HLS manifests, child playlists, keys, and segments, has no
subprocess-owned provider DNS or redirect window.
