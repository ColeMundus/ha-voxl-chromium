# syntax=docker/dockerfile:1
FROM rust:1.99.0-bookworm AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl clang pkg-config libx264-dev libyuv-dev libopus-dev \
    libpulse-dev npm python3 && rm -rf /var/lib/apt/lists/*
RUN npm install --global esbuild@0.25.11
# Debian's libyuv-dev omits pkg-config metadata; voxl 0.3.2 requires it.
RUN mkdir -p /usr/local/lib/pkgconfig \
    && printf 'Name: libyuv\nDescription: Debian system libyuv\nVersion: 0\nLibs: -lyuv\n' \
    > /usr/local/lib/pkgconfig/libyuv.pc
COPY upstream.json /build/upstream.json
WORKDIR /build
RUN python3 -c 'import json,subprocess; p=json.load(open("upstream.json")); subprocess.run(["curl","-fL","--retry","3","-o","voxl.tar.gz","https://github.com/tembo/voxl/archive/"+p["commit"]+".tar.gz"],check=True)' \
    && python3 -c 'import hashlib,json; p=json.load(open("upstream.json")); assert hashlib.sha256(open("voxl.tar.gz","rb").read()).hexdigest()==p["sha256"], "Upstream checksum mismatch"' \
    && mkdir source && tar -xzf voxl.tar.gz -C source --strip-components=1
WORKDIR /build/source
COPY patches/ /build/patches/
RUN git apply --check /build/patches/zip-security.patch \
    && git apply /build/patches/zip-security.patch \
    && cargo test --release --locked --lib \
    && cargo build --release --locked --bin voxl \
    && strip target/release/voxl

FROM debian:bookworm-slim
ARG BUILD_VERSION=0.1.0
LABEL org.opencontainers.image.title="Voxl Chromium for Home Assistant" \
    org.opencontainers.image.source="https://github.com/ColeMundus/ha-voxl-chromium" \
    org.opencontainers.image.version="${BUILD_VERSION}" \
    org.opencontainers.image.licenses="GPL-2.0-or-later"
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates chromium fonts-liberation fonts-noto-color-emoji \
    libx264-164 libyuv0 libopus0 libpulse0 xvfb xauth x11-utils \
    openbox pulseaudio pulseaudio-utils nginx-light python3 tini tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home --shell /usr/sbin/nologin browser \
    && mkdir -p /data /opt/addon
COPY --from=builder /build/source/target/release/voxl /usr/local/bin/voxl
COPY --from=builder /build/source/LICENSE /usr/share/doc/voxl/LICENSE
COPY --from=builder /build/source/Cargo.lock /usr/share/doc/voxl/Cargo.lock
COPY upstream.json /usr/share/doc/voxl/upstream.json
COPY patches/ /usr/share/doc/voxl/patches/
COPY MODIFICATIONS.md /usr/share/doc/voxl/MODIFICATIONS.md
COPY LICENSE /usr/share/doc/ha-voxl-chromium/LICENSE
COPY runtime.py nginx.conf /opt/addon/
# tini is our own PID 1; config.yaml therefore sets init: false.
ENTRYPOINT ["/usr/bin/tini", "--", "python3", "/opt/addon/runtime.py"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD ["python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8099/healthz',timeout=4)"]
