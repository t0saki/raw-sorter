# syntax=docker/dockerfile:1
#
# Multi-arch (linux/amd64 + linux/arm64) image. There is no project compilation: the native
# work (libheif/x265/aom, libraw) comes from pillow-heif / rawpy wheels, so building the other
# arch under QEMU is just wheel downloads — no slow C builds. uv installs from the lockfile.

FROM ghcr.io/astral-sh/uv:0.8-python3.12-bookworm-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
# Resolve & install dependencies first for layer caching, without the project itself.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=README.md,target=README.md \
    uv sync --frozen --no-install-project --no-dev
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm
# libgomp1: OpenMP runtime used by the x265/aom encoders bundled in the wheels.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
# Run as non-root; on DSM set `user:` in compose to a uid/gid that can read INPUT and write ALBUM/ARCHIVE.
USER 1000:1000
ENTRYPOINT ["raw-sorter"]

LABEL org.opencontainers.image.source="https://github.com/t0saki/raw-sorter" \
      org.opencontainers.image.description="Watch RAW+JPG; emit compact HEIF to an album folder and move RAW masters to a cold archive." \
      org.opencontainers.image.licenses="MIT"
