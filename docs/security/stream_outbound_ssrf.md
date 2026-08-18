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

The only remaining resolution race is on direct UDP, RTP, and RTMP inputs:
their subprocess libraries resolve again after ECM validates the destination.
HTTP(S), including HLS manifests, child playlists, keys, and segments, has no
subprocess-owned provider DNS or redirect window.
