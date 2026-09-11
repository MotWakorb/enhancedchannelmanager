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
