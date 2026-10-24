# Build frontend
FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci

# Cache busting - invalidate cache when git commit changes
ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=$GIT_COMMIT

COPY frontend/ ./
RUN npm run build

# Build the reviewed source-pinned FFmpeg/gosu products on native CI runners.
FROM registry.opensuse.org/opensuse/tumbleweed@sha256:c807eabd03acb6f5dfb6a23fd58ed93700d11283a255cd18184f57815e19fc55 AS native-builder
COPY scripts/build_native_runtime.py /tmp/build_native_runtime.py
COPY docker/native/ /tmp/native/
RUN zypper --non-interactive install --no-recommends python312-base \
    && case "$(uname -m)" in x86_64) arch=amd64 ;; aarch64) arch=arm64 ;; *) exit 1 ;; esac \
    && python3.12 /tmp/build_native_runtime.py --manifest /tmp/native/manifest.json --work-dir /tmp/native-work --output-dir /tmp/native-output --arch "$arch"

# Resolve one runtime graph, without retaining compilers or development headers.
FROM registry.opensuse.org/opensuse/tumbleweed@sha256:c807eabd03acb6f5dfb6a23fd58ed93700d11283a255cd18184f57815e19fc55 AS runtime-base
COPY --from=native-builder /tmp/native-output/runtime-rpms/ /tmp/runtime-rpms/
COPY --from=native-builder /tmp/native-output/runtime-packages.txt /tmp/runtime-packages.txt
COPY --from=native-builder /tmp/native/packman.asc /tmp/packman.asc
RUN case "$(uname -m)" in \
        x86_64) repository=https://download.opensuse.org/tumbleweed/repo/oss/ ;; \
        aarch64) repository=https://download.opensuse.org/ports/aarch64/tumbleweed/repo/oss/ ;; \
        *) exit 1 ;; \
    esac \
    && rm -f /etc/zypp/repos.d/*.repo \
    && zypper --non-interactive addrepo --check --gpgcheck --refresh "$repository" oss \
    && zypper --non-interactive refresh \
    && zypper --non-interactive update --no-recommends \
    && rpm --import /tmp/packman.asc \
    && rpm --checksig /tmp/runtime-rpms/*.rpm \
    && zypper --non-interactive install --no-recommends /tmp/runtime-rpms/*.rpm \
        $(cat /tmp/runtime-packages.txt) python312 python312-base shadow coreutils bash timezone ca-certificates \
    && zypper clean --all
COPY --from=native-builder /tmp/native-output/root/ /
RUN ldconfig \
    && for package in gcc gcc-c++ make go1.26 go1.27 libcjson1 libsndfile1; do \
        if rpm -q "$package"; then exit 1; fi; \
    done \
    && rpm -qa --qf '%{NAME}\t%{VERSION}\t%{RELEASE}\t%{ARCH}\t%{VENDOR}\t%{SOURCERPM}\t%{LICENSE}\n' > /tmp/runtime-inventory.tsv

# Install the locked Python dependencies without carrying build tools at runtime.
FROM runtime-base AS python-builder

COPY --from=ghcr.io/astral-sh/uv:latest@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 /uv /usr/local/bin/uv

# Compile Python packages into a virtual env we can copy to the final image
COPY backend/requirements.txt /tmp/requirements.txt
RUN uv venv --python /usr/bin/python3.12 /opt/venv \
    && uv pip install --python /opt/venv/bin/python --no-cache -r /tmp/requirements.txt

# Build resdet from a pinned source commit for both supported image architectures.
FROM runtime-base AS resdet-builder
COPY scripts/generate_sbom.py scripts/build_resdet.py sbom/native-dependencies.json /tmp/
RUN zypper --non-interactive install --no-recommends gcc make pkgconf-pkg-config gawk \
    && python3.12 /tmp/build_resdet.py --manifest /tmp/native-dependencies.json --work-dir /tmp/resdet-work --output-dir /tmp/resdet-output

# Retain exact sources for the runtime inventory, not the expanded builder graph.
FROM native-builder AS source-builder
COPY --from=runtime-base /tmp/runtime-inventory.tsv /tmp/runtime-inventory.tsv
COPY scripts/collect_native_sources.py /tmp/collect_native_sources.py
RUN rpm -q --qf '%{DESCRIPTION}\n' gpg-pubkey-29b700a4 > /tmp/suse.asc \
    && python3.12 /tmp/collect_native_sources.py --inventory /tmp/runtime-inventory.tsv --arch "$(uname -m)" --out /tmp/rpm-source-output --suse-key /tmp/suse.asc --packman-key /tmp/native/packman.asc

# Production image
FROM runtime-base

# Build args - MUST be declared early in the stage to receive build arg
ARG GIT_COMMIT=unknown
ARG ECM_VERSION=unknown
ARG RELEASE_CHANNEL=latest
ENV GIT_COMMIT=$GIT_COMMIT
ENV ECM_VERSION=$ECM_VERSION
ENV RELEASE_CHANNEL=$RELEASE_CHANNEL

WORKDIR /app

# Explicit identity avoids distribution-specific useradd defaults. Entrypoint
# still remaps this account to the configured PUID/PGID before dropping root.
RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --create-home --shell /bin/bash appuser

# Copy pre-built Python packages from builder stage (no build tools needed)
COPY --from=python-builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy backend code
COPY backend/ ./

# Copy built frontend to static directory
COPY --from=frontend-builder /app/frontend/dist ./static

# Matching source accompanies each binary image, before its final verification.
COPY --from=source-builder /tmp/rpm-source-output/ /usr/share/ecm/sources/rpm/
COPY --from=native-builder /tmp/native-output/sources/ /usr/share/ecm/sources/owned/
COPY --from=native-builder /tmp/native-output/build-output.json /usr/share/ecm/provenance/native-build.json
COPY --from=native-builder /tmp/native-output/source-files.json /usr/share/ecm/provenance/native-sources.json
COPY --from=resdet-builder /tmp/resdet-work/resdet.tar.gz /usr/share/ecm/sources/resdet/resdet.tar.gz
COPY scripts/build_resdet.py scripts/generate_sbom.py sbom/native-dependencies.json /usr/share/ecm/sources/resdet/
COPY docker/python-third-party/notices/ /usr/share/doc/ecm/python-third-party/
COPY docker/python-third-party/sources/ /usr/share/ecm/sources/python/
COPY docker/python-third-party/component-manifest.json docker/python-third-party/SOURCE-AVAILABILITY.txt /usr/share/doc/ecm/python-third-party/
COPY docker/python-third-party/artifact-index/ /usr/share/doc/ecm/python-third-party/artifact-index/

# Create config and TLS directories with proper permissions
# Convert entrypoint line endings (handles Windows CRLF -> Unix LF)
RUN mkdir -p /config /config/tls /config/uploads/logos /run/ecm \
    && chown -R appuser:appuser /config /app \
    && chown appuser:appuser /run/ecm \
    && chmod 700 /run/ecm \
    && chmod 700 /config/tls \
    && sed -i 's/\r$//' /app/entrypoint.sh \
    && chmod +x /app/entrypoint.sh

# Copy immutable native outputs after every other filesystem-mutating final-stage step.
COPY --from=resdet-builder /tmp/resdet-output/resdet /usr/local/bin/resdet
COPY --from=resdet-builder /tmp/resdet-output/licenses/ /usr/share/doc/resdet/
RUN test "$(resdet -V)" = "$(printf 'resdet version 2.4.3\nlibresdet version 3.2.0\nBuilt with image readers: Y4M')"

# Environment
ENV PUID=1000
ENV PGID=1000
ENV CONFIG_DIR=/config
ENV ECM_PORT=6100
ENV ECM_HTTPS_PORT=6143

# Expose default ports (HTTP: 6100, HTTPS: 6143)
# Note: Actual ports are configurable at runtime via ECM_PORT and ECM_HTTPS_PORT.
EXPOSE 6100 6143

# Add healthcheck (respects runtime ECM_PORT).
# The interpreter is the absolute venv path, not a bare `python` resolved
# through PATH (bead enhancedchannelmanager-0oi96): a runtime PATH override
# would otherwise silently move the healthcheck onto the system interpreter.
# urllib is stdlib, so this happens to survive that today — pinning it keeps
# the healthcheck honest if it ever grows a dependency, and keeps every
# interpreter invocation in the image consistent.
# Long-running installs may hit slow first-run migrations against bloated
# SQLite WAL files. WAL checkpoint at startup (bd-ej995) addresses the
# common case; this start-period absorbs the edge case where a particularly
# large migration still runs longer than the default. Operators on installs
# with consistent fast startups can lower this; the default favors safety
# over startup time.
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
  CMD /opt/venv/bin/python -c "import urllib.request, os; port = os.environ.get('ECM_PORT', '6100'); urllib.request.urlopen(f'http://localhost:{port}/api/health')" || exit 1

# Entrypoint sets UID/GID from PUID/PGID, fixes permissions, then drops to non-root via gosu
ENTRYPOINT ["/app/entrypoint.sh"]
