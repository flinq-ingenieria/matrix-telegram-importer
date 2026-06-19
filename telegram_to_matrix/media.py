from __future__ import annotations

import mimetypes
import subprocess
from pathlib import Path

from PIL import Image

from .models import PreparedMedia, TelegramMessageNormalized


class MediaConversionError(RuntimeError):
    pass


def prepare_media(message: TelegramMessageNormalized, work_dir: Path) -> PreparedMedia:
    if message.media_path is None or message.media_kind is None:
        raise MediaConversionError("Message has no media")

    source = message.media_path
    if not source.exists():
        raise MediaConversionError(f"Media file does not exist: {source}")

    work_dir.mkdir(parents=True, exist_ok=True)

    if message.media_kind == "image":
        return _convert_image(source, work_dir, message)
    if message.media_kind == "audio":
        return _convert_audio(source, work_dir, message)
    if message.media_kind == "video":
        return _convert_video(source, work_dir, message)
    if message.media_kind == "document":
        mime = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        body = source.name
        info = {"size": source.stat().st_size, "mimetype": mime}
        return PreparedMedia(file_path=source, mime_type=mime, msgtype="m.file", body=body, info=info)

    raise MediaConversionError(f"Unsupported media kind: {message.media_kind}")


def _convert_image(source: Path, work_dir: Path, message: TelegramMessageNormalized) -> PreparedMedia:
    target = work_dir / f"{source.stem}.jpg"
    with Image.open(source) as img:
        rgb = img.convert("RGB")
        rgb.save(target, format="JPEG", quality=90)
        width, height = rgb.size

    info = {
        "size": target.stat().st_size,
        "mimetype": "image/jpeg",
        "w": width,
        "h": height,
    }
    return PreparedMedia(
        file_path=target,
        mime_type="image/jpeg",
        msgtype="m.image",
        body=target.name,
        info=info,
    )


def _convert_audio(source: Path, work_dir: Path, message: TelegramMessageNormalized) -> PreparedMedia:
    if source.suffix.lower() == ".ogg":
        info = {
            "size": source.stat().st_size,
            "mimetype": "audio/ogg",
        }
        if message.media_duration_seconds is not None:
            info["duration"] = message.media_duration_seconds * 1000
        return PreparedMedia(
            file_path=source,
            mime_type="audio/ogg",
            msgtype="m.audio",
            body=source.name,
            info=info,
        )

    target = work_dir / f"{source.stem}.ogg"
    _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-c:a",
            "libopus",
            "-b:a",
            "64k",
            str(target),
        ]
    )
    info = {
        "size": target.stat().st_size,
        "mimetype": "audio/ogg",
    }
    if message.media_duration_seconds is not None:
        info["duration"] = message.media_duration_seconds * 1000
    return PreparedMedia(
        file_path=target,
        mime_type="audio/ogg",
        msgtype="m.audio",
        body=target.name,
        info=info,
    )


def _convert_video(source: Path, work_dir: Path, message: TelegramMessageNormalized) -> PreparedMedia:
    target = work_dir / f"{source.stem}.mp4"
    _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-c:v",
            "libx264",
            "-profile:v",
            "baseline",
            "-level",
            "3.1",
            "-preset",
            "veryfast",
            "-crf",
            "24",
            "-vf",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(target),
        ]
    )
    info = {
        "size": target.stat().st_size,
        "mimetype": "video/mp4",
    }
    if message.media_width is not None:
        info["w"] = message.media_width
    if message.media_height is not None:
        info["h"] = message.media_height
    if message.media_duration_seconds is not None:
        info["duration"] = message.media_duration_seconds * 1000
    return PreparedMedia(
        file_path=target,
        mime_type="video/mp4",
        msgtype="m.video",
        body=target.name,
        info=info,
    )


def _run_ffmpeg(cmd: list[str]) -> None:
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise MediaConversionError("ffmpeg not found in PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise MediaConversionError(exc.stderr.strip() or "ffmpeg conversion failed") from exc
