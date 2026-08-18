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

## FFmpeg and ffprobe bounded design

FFmpeg and ffprobe perform DNS resolution and redirect handling inside their
subprocess libraries and do not expose ECM's HTTPX transport hook. ECM therefore
validates the input URL immediately before every subprocess start, including
each retry. HTTP(S), UDP, RTP, and RTMP destinations receive the same address
policy; other direct input schemes are rejected. This rejects literal forbidden
addresses, DNS answers containing any forbidden address, and destinations
disallowed by the configured LAN policy before the process is created.

A bounded residual window remains between validation and the subprocess's own
DNS lookup, and FFmpeg-managed redirects cannot be revalidated by ECM. Protocol
allowlists continue to limit subprocess inputs to media-network protocols. The
HTTPX paths, which ECM controls end to end, do not have this residual window.
