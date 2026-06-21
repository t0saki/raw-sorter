# raw-sorter

[English](README.md) · [简体中文](README.zh-CN.md)

监控一个存放相机 **RAW + JPG + 视频** 的目录，把每张照片/每段视频拆分到两个去处：

- 机内直出的 **Fine JPG** → 转码为体积很小的 **HEIF**（HEVC），写入**相册目录**（即 Synology Photos / Google Photos 同步的那个目录）；
- **视频** → 转码为体积很小的 **1080p HEVC + AAC 的 MP4**，写入同一个相册目录；
- **RAW / 原始视频母片** → 移动到**冷存档目录**（长期保存，不参与同步）。

这就是经典的 **母片 / 衍生片** 模式，并且全程自动、持续运行。你的云端相册保持小而快（约 1 MB 的 HEIF、轻量的 1080p 短片），而全质量的 RAW 与原始视频母片廉价地躺在冷存储里。设计为在 Synology DSM（或任意 Linux 主机）上以 Docker 容器运行。

```
                              ┌────────► ALBUM/   （精简 HEIF + 1080p HEVC MP4，同步到云端）
INPUT/  RAW + JPG + 视频  ─────┤
   （被监控）                  └────────► ARCHIVE/ （RAW + 原始视频母片，默认连同原始 JPG）
```

## 特性

- **进程内、高压缩率 HEIF**：基于 libheif/x265（`pillow-heif`），默认 质量 50、`preset=slow`、4:2:0（约为源 JPG 的 1/8 ~ 1/12 体积）。也支持 AVIF。
- **保留元数据**：GPS、拍摄时间及全部 EXIF 一并写入 HEIF。
- **方向正确**：旋转直接烘焙进像素，竖拍照片在任何看图软件里都不会被“二次旋转”。
- **颜色正确**：很多相机拍 Adobe RGB 时并不嵌入 ICC 配置；本工具会把它们转换为 sRGB 并权威标记，避免在云端显示发灰。广色域原图保留在 RAW 存档里。
- **保持目录结构**：`INPUT/` 下的子目录会在 `ALBUM/` 和 `ARCHIVE/` 中原样重建。NAS 系统目录（`@eaDir`、`#recycle`、`#snapshot`、`@Recycle`、`lost+found`、点目录）会自动跳过。
- **持续且健壮**：实时文件监控 + 周期性全量重扫（兜底漏掉的事件）；文件稳定性检测（不会去碰还在通过 SMB 拷贝的文件）；原子发布（相册目录永远看不到写了一半的文件）；幂等、可随时重启；单文件失败隔离。
- **只有 RAW 的帧**：没有同名 JPG 的 RAW 也会出一张 HEIF——从相机内嵌的（已套 LUT 的）预览里提取。
- **视频**：用 ffmpeg 转码为精简的 **1080p HEVC + AAC 的 MP4**，参数针对相机素材优化：10-bit Main10（更高效、无色带）、Lanczos 缩放、自适应量化、正确的 BT.709 标注、`hvc1` 标签 + faststart（到处都能播、能渐进预览）。原片归档到照片旁边。默认 CRF 30、`preset=fast`，全部可配置。

## 快速开始（Docker / 群晖 DSM）

拉取镜像：

```bash
docker pull ghcr.io/t0saki/raw-sorter:latest
```

`docker-compose.yml`（见 `docker-compose.example.yml`）：

```yaml
services:
  raw-sorter:
    image: ghcr.io/t0saki/raw-sorter:latest
    container_name: raw-sorter
    restart: unless-stopped
    user: "1026:100"            # 一个能读 INPUT、写 ALBUM 和 ARCHIVE 的 DSM 用户/组
    # 容器内路径默认就是 /input、/album、/archive，直接挂到这三个点即可：
    volumes:
      - /volume1/photo/incoming:/input        # 你倒相机卡的地方（RAW+JPG）
      - /volume1/photo/Album:/album           # 一个 Synology Photos / 同步目录
      - /volume1/cold/raw-archive:/archive    # RAW 母片的冷存储
    # environment:                            # 全部可选；默认值见下表
    #   QUALITY: "50"
    #   PRESET: medium                        # NAS CPU 较弱时调低
```

```bash
docker compose up -d
docker compose logs -f
```

在 DSM 上也可以用 **Container Manager** 添加容器，在界面里设置三个目录挂载和环境变量。

## 配置

每个选项都既是环境变量（方便在 DSM 界面里填），又有对应的命令行参数（命令行优先）。时间支持 `30s`、`5m`、`2h`。

| 环境变量 / 参数 | 默认值 | 说明 |
|---|---|---|
| `INPUT_DIR` / `--input` | `/input` | 被监控的 RAW+JPG 根目录（递归） |
| `ALBUM_DIR` / `--album` | `/album` | 仅 HEIF 的输出目录（被同步） |
| `ARCHIVE_DIR` / `--archive` | `/archive` | RAW 冷存档目录 |
| `FORMAT` / `--format` | `heif` | `heif` 或 `avif` |
| `QUALITY` / `--quality` | `50` | 0–100 |
| `PRESET` / `--preset` | `slow` | x265 preset（NAS 弱就用 `medium`/`fast`） |
| `TUNE` / `--tune` | `ssim` | x265 tune |
| `CHROMA` / `--chroma` | `420` | `420`、`422` 或 `444` |
| `COLOR` / `--color` | `srgb` | `srgb`（转换广色域以求最大兼容）或 `preserve` |
| `WORKERS` / `--workers` | `1` | 并行转码数（NAS 上建议小） |
| `SETTLE_SECONDS` / `--settle-seconds` | `10` | 文件须保持不变这么久才会被处理 |
| `RESCAN_INTERVAL` / `--rescan-interval` | `5m` | 兜底漏掉事件的全量重扫间隔 |
| `ENCODE_TIMEOUT` / `--encode-timeout` | `5m` | 放弃卡住的转码 |
| `JPG_DISPOSITION` / `--jpg-disposition` | `archive` | 成功后：`archive`（原始 JPG 连同 RAW 一起归档）或 `delete`（删除） |
| `RAW_WITHOUT_JPG` / `--raw-without-jpg` | `preview` | 无同名 JPG 的 RAW：`preview`（内嵌预览→HEIF）、`archive`（只移动）或 `skip` |
| `VIDEO` / `--video` | `transcode` | `transcode`（转 1080p HEVC/AAC 进相册）、`copy`（原片原样进相册）或 `ignore` |
| `VIDEO_DISPOSITION` / `--video-disposition` | `archive` | 相册短片发布后：`archive`（原片连同照片一起归档）或 `delete`（删除） |
| `VIDEO_CRF` / `--video-crf` | `30` | x265 质量（越大越小），28–32 是常用区间 |
| `VIDEO_PRESET` / `--video-preset` | `fast` | x265 preset；NAS 弱就用 `faster`/`ultrafast` |
| `VIDEO_HEIGHT` / `--video-height` | `1080` | 输出高度上限（只降不升） |
| `VIDEO_BITDEPTH` / `--video-bitdepth` | `10` | `10`（Main10，更高效）或 `8`（兼容老硬解） |
| `VIDEO_X265_PARAMS` / `--video-x265-params` | `aq-mode=3:aq-strength=1.0:psy-rd=2.0` | 额外 x265 参数（BT.709 色彩标注会自动追加） |
| `VIDEO_ACODEC` / `--video-acodec` | `aac` | `aac`（MP4 里最稳）或 `copy` |
| `VIDEO_ABITRATE` / `--video-abitrate` | `128k` | 音频码率（`VIDEO_ACODEC=copy` 时忽略） |
| `VIDEO_TIMEOUT` / `--video-timeout` | `2h` | 放弃卡住的转码 |
| `MAX_RETRIES` / `--max-retries` | `3` | 放弃前的重试次数（之后原样保留并记录日志） |
| `ONCE` / `--once` | `false` | 处理完现有文件即退出（适合 cron） |
| `DRY_RUN` / `--dry-run` | `false` | 只记录将要做的操作，不实际改动 |
| `LOG_LEVEL` / `--log-level` | `info` | `debug`/`info`/`warn`/`error` |

## 本地运行（不用 Docker）

需要 [uv](https://docs.astral.sh/uv/)。

```bash
uv run raw-sorter --input ./IN --album ./ALBUM --archive ./ARCHIVE --once
# 持续监控：
uv run raw-sorter --input ./IN --album ./ALBUM --archive ./ARCHIVE
```

## 工作原理与安全性

对每张照片（同名的一组文件），严格按顺序：转码 JPG → 原子地把 HEIF 发布到相册 → 把 RAW 移到存档 → 处置原始 JPG。视频同理：转码 → 原子地把 1080p MP4 发布到相册 → 把原始视频移到存档。RAW / 原始视频母片和原始 JPG 在其替代物落盘确认之前绝不会被删除，所以中断的运行不会丢任何东西，重启后会从断点继续。完全处理完的照片/视频在输入目录里不留任何文件，因此不会被重复处理。

> **建议**：第一次使用时保持 `JPG_DISPOSITION=archive`（不删除）。等你信任结果后再改成 `delete`。

## 许可证

MIT © t0saki
