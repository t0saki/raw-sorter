# raw-sorter

[English](README.md) · [简体中文](README.zh-CN.md)

Watch a folder of camera **RAW + JPG + video** files and split each shot into two destinations:

- the in-camera **Fine JPG** → re-encoded to a compact **HEIF** (HEVC) and written to a **photo-album folder** (the one Synology Photos / Google Photos sync from);
- a **video** → transcoded to a compact **1080p HEVC + AAC MP4** and written to the same album folder;
- the **RAW / original-video master** → moved to a **cold archive** (kept long-term, not synced).

This is the classic *master / derivative* split, automated and continuous. Your cloud library stays small and fast (≈1 MB HEIFs, lightweight 1080p clips) while the full-quality RAW and original-video masters live cheaply in cold storage. Designed to run as a Docker container on a Synology DSM (or any Linux host).

```
                              ┌────────► ALBUM/   (compact HEIF + 1080p HEVC MP4, synced to the cloud)
INPUT/  RAW + JPG + video ────┤
   (watched)                  └────────► ARCHIVE/ (RAW + original-video masters, + the original JPG by default)
```

## Features

- **In-process, high-compression HEIF** via libheif/x265 (`pillow-heif`) — quality 50, `preset=slow`, 4:2:0 by default (~8–12× smaller than the source JPG). AVIF available too.
- **Metadata preserved** — GPS, capture date and all EXIF ride along into the HEIF.
- **Orientation done right** — rotation is baked into the pixels, so portrait shots never double-rotate in any viewer.
- **Correct colour** — Adobe RGB frames (which many cameras shoot without an embedded profile) are converted to sRGB and tagged authoritatively, so they don't look desaturated in the cloud. The wide-gamut original is preserved in the RAW archive.
- **Mirrors your folder structure** — subfolders under `INPUT/` are recreated under `ALBUM/` and `ARCHIVE/`. NAS system folders (`@eaDir`, `#recycle`, `#snapshot`, `@Recycle`, `lost+found`, dotfolders) are skipped automatically.
- **Continuous & robust** — live filesystem watching + a periodic rescan that backstops missed events; file-stability detection (won't touch a file still being copied over SMB); atomic publish (the album folder never sees a half-written file); idempotent and restart-safe; per-file failure isolation.
- **RAW-only frames** — a RAW with no sibling JPG still gets a HEIF, extracted from the camera's embedded (LUT-baked) preview.
- **Video** — transcoded with ffmpeg to a compact **1080p HEVC + AAC MP4**, tuned for camera footage: 10-bit Main10 (more efficient, no banding), Lanczos downscale, adaptive quantisation, correct BT.709 tagging, `hvc1` tag + faststart so it plays and previews everywhere. The original is archived beside the photos. Defaults: CRF 30, `preset=fast` — all configurable.

## Quick start (Docker / Synology DSM)

Pull the image:

```bash
docker pull ghcr.io/t0saki/raw-sorter:latest
```

`docker-compose.yml` (see `docker-compose.example.yml`):

```yaml
services:
  raw-sorter:
    image: ghcr.io/t0saki/raw-sorter:latest
    container_name: raw-sorter
    restart: unless-stopped
    user: "1026:100"            # a DSM user/group that can read INPUT and write ALBUM + ARCHIVE
    # The in-container paths default to /input, /album, /archive — just mount to them:
    volumes:
      - /volume1/photo/incoming:/input        # where you dump the camera card (RAW+JPG)
      - /volume1/photo/Album:/album           # a Synology Photos / synced folder
      - /volume1/cold/raw-archive:/archive    # cold storage for RAW masters
    # environment:                            # all optional; defaults shown in the table below
    #   QUALITY: "50"
    #   PRESET: medium                        # lower if your NAS CPU is weak
```

```bash
docker compose up -d
docker compose logs -f
```

On DSM you can also add the container through **Container Manager** and set the three folder mounts + the env vars in the UI.

## Configuration

Every option is an environment variable (handy for the DSM UI) and an equivalent CLI flag (CLI wins). Durations accept `30s`, `5m`, `2h`.

| Env / flag | Default | Description |
|---|---|---|
| `INPUT_DIR` / `--input` | `/input` | watched RAW+JPG root (recursive) |
| `ALBUM_DIR` / `--album` | `/album` | HEIF-only output (synced) |
| `ARCHIVE_DIR` / `--archive` | `/archive` | cold RAW archive |
| `FORMAT` / `--format` | `heif` | `heif` or `avif` |
| `QUALITY` / `--quality` | `50` | 0–100 |
| `PRESET` / `--preset` | `slow` | x265 preset (`medium`/`fast` on a weak NAS) |
| `TUNE` / `--tune` | `ssim` | x265 tune |
| `CHROMA` / `--chroma` | `420` | `420`, `422` or `444` |
| `COLOR` / `--color` | `srgb` | `srgb` (convert wide-gamut for max compatibility) or `preserve` |
| `MAX_MEGAPIXELS` / `--max-megapixels` | `25` | downscale album derivatives whose source exceeds this many MP (`0` disables). Guards against a huge panorama/upscale ballooning encode memory and OOM-killing the container; the full-res master is still archived |
| `TARGET_MEGAPIXELS` / `--target-megapixels` | `24` | …down to about this many MP (aspect preserved, Lanczos) |
| `WORKERS` / `--workers` | `1` | parallel encodes (keep low on a NAS) |
| `SETTLE_SECONDS` / `--settle-seconds` | `10` | a file must be unchanged this long before processing |
| `RESCAN_INTERVAL` / `--rescan-interval` | `5m` | full rescan safety net for missed events |
| `ENCODE_TIMEOUT` / `--encode-timeout` | `5m` | abandon a stuck encode |
| `JPG_DISPOSITION` / `--jpg-disposition` | `archive` | after success: `archive` the original JPG beside its RAW, or `delete` it |
| `RAW_WITHOUT_JPG` / `--raw-without-jpg` | `preview` | RAW with no sibling JPG: `preview` (embedded preview → HEIF), `archive` (just move), or `skip` |
| `VIDEO` / `--video` | `transcode` | `transcode` (1080p HEVC/AAC to album), `copy` (original to album as-is), or `ignore` |
| `VIDEO_DISPOSITION` / `--video-disposition` | `archive` | after the album clip is published: `archive` the original beside its photos, or `delete` it |
| `VIDEO_CRF` / `--video-crf` | `30` | x265 quality (higher = smaller); 28–32 is a good range |
| `VIDEO_PRESET` / `--video-preset` | `fast` | x265 preset; `faster`/`ultrafast` on a weak NAS |
| `VIDEO_HEIGHT` / `--video-height` | `1080` | output height cap (never upscales) |
| `VIDEO_BITDEPTH` / `--video-bitdepth` | `10` | `10` (Main10, more efficient) or `8` (max compatibility on old hardware decoders) |
| `VIDEO_X265_PARAMS` / `--video-x265-params` | `aq-mode=3:aq-strength=1.0:psy-rd=2.0` | extra x265 params (BT.709 tagging is always appended) |
| `VIDEO_ACODEC` / `--video-acodec` | `aac` | `aac` (max MP4 compatibility) or `copy` |
| `VIDEO_ABITRATE` / `--video-abitrate` | `128k` | audio bitrate (ignored when `VIDEO_ACODEC=copy`) |
| `VIDEO_TIMEOUT` / `--video-timeout` | `2h` | abandon a stuck transcode |
| `MAX_RETRIES` / `--max-retries` | `3` | attempts before a unit is left in place and logged |
| `ONCE` / `--once` | `false` | process what's there and exit (for cron) |
| `DRY_RUN` / `--dry-run` | `false` | log intended actions, change nothing |
| `LOG_LEVEL` / `--log-level` | `info` | `debug`/`info`/`warn`/`error` |

## Run locally (without Docker)

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv run raw-sorter --input ./IN --album ./ALBUM --archive ./ARCHIVE --once
# continuous:
uv run raw-sorter --input ./IN --album ./ALBUM --archive ./ARCHIVE
```

## How it works & safety

For each shot (files sharing a basename), in strict order: encode the JPG → atomically publish the HEIF to the album → move the RAW to the archive → dispose of the original JPG. Video runs the same way: transcode → atomically publish the 1080p MP4 to the album → move the original video to the archive. The RAW / original-video master and original JPG are never removed until their replacements are confirmed on disk, so an interrupted run loses nothing and simply resumes. A fully-processed shot leaves nothing in the input tree, so it's never reprocessed.

> **Tip:** the first time, leave `JPG_DISPOSITION=archive` (non-destructive). Switch to `delete` only once you trust the results.

## License

MIT © t0saki
