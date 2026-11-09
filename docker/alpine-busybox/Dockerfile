# Additional PO-approved source-owned provider; never a published ECM image.
FROM python:3.12-alpine3.24@sha256:e064671ca452259bab50e8e2c7bd896e39e9a343bb06ca25234adf6fe18d0fc7 AS busybox-builder
COPY scripts/alpine_repository_policy.py /proof/scripts/alpine_repository_policy.py
RUN python /proof/scripts/alpine_repository_policy.py \
    && apk upgrade --no-cache \
    && apk add --no-cache abuild build-base linux-headers openssl-dev utmps-dev utmps-static perl zip pkgconf
COPY scripts/build_busybox_provider.py scripts/build_native_runtime.py scripts/build_alpine_codecs.py scripts/verify_musllinux_gnu.py scripts/alpine_apk_v2.py /proof/scripts/
COPY docker/alpine-busybox/ /proof/assets/
COPY --from=busybox-cache / /proof/cache/
RUN --network=none case "$(uname -m)" in x86_64) arch=amd64 ;; aarch64) arch=arm64 ;; *) exit 1 ;; esac \
    && python /proof/scripts/build_busybox_provider.py prepare-build --manifest /proof/assets/manifest.json \
        --arch "$arch" --work-dir /proof/work --output-dir /proof/output --cache-dir /proof/cache
# The unchanged upstream suite requires real HTTP access (original options=net).
# Only this check stage has network access; source compilation above does not.
RUN case "$(uname -m)" in x86_64) arch=amd64 ;; aarch64) arch=arm64 ;; *) exit 1 ;; esac \
    && python /proof/scripts/build_busybox_provider.py check --manifest /proof/assets/manifest.json \
        --arch "$arch" --work-dir /proof/work --output-dir /proof/output
RUN --network=none case "$(uname -m)" in x86_64) arch=amd64 ;; aarch64) arch=arm64 ;; *) exit 1 ;; esac \
    && python /proof/scripts/build_busybox_provider.py package --manifest /proof/assets/manifest.json \
        --arch "$arch" --work-dir /proof/work --output-dir /proof/output

FROM scratch AS busybox-evidence
COPY --from=busybox-builder /proof/output/ /
