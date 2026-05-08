"""Bilibili 视频 Whisper 语音转录 —— 使用 Systran faster-whisper-base 模型。"""

from __future__ import annotations

import subprocess
import tempfile
import os
import time

import structlog

logger = structlog.get_logger()

_MODEL = None


def transcribe_video(bvid: str, model_path: str, cookies_file: str) -> str:
    """下载视频音频并用 Whisper 转录为文本。

    返回转录文本，失败返回空字符串。
    """
    global _MODEL

    url = f"https://www.bilibili.com/video/{bvid}"

    with tempfile.TemporaryDirectory(prefix="bilibot_transcribe_") as tmpdir:
        audio_path = os.path.join(tmpdir, "audio.wav")

        try:
            _download_audio(url, audio_path, cookies_file, max_retries=2)
        except Exception as e:
            logger.warning("transcribe_download_failed", bvid=bvid, error=str(e))
            return ""

        file_size = os.path.getsize(audio_path) if os.path.exists(audio_path) else 0
        if file_size < 1000:
            logger.warning("transcribe_audio_too_small", bvid=bvid, size=file_size)
            return ""

        try:
            if _MODEL is None:
                logger.info("whisper_model_loading", path=model_path)
                from faster_whisper import WhisperModel
                _MODEL = WhisperModel(model_path, device="cpu", compute_type="int8")
                logger.info("whisper_model_loaded")

            segments, _info = _MODEL.transcribe(audio_path, beam_size=5, language="zh")
            texts = [seg.text.strip() for seg in segments if seg.text.strip()]
            transcript = " ".join(texts)

            if not transcript:
                logger.warning("transcribe_empty_result", bvid=bvid)
                return ""

            logger.info("transcribe_done", bvid=bvid, chars=len(transcript))
            return transcript[:8000]

        except Exception as e:
            logger.warning("transcribe_error", bvid=bvid, error=str(e))
            return ""


def _download_audio(url: str, output: str, cookies_file: str, max_retries: int = 2) -> None:
    cmd = [
        "yt-dlp",
        "--cookies", cookies_file,
        "--extract-audio",
        "--audio-format", "wav",
        "--audio-quality", "0",
        "--output", output,
        "--quiet",
        "--no-playlist",
        url,
    ]

    last_error = ""
    for attempt in range(max_retries):
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        if result.returncode == 0:
            return
        last_error = f"yt-dlp 失败 (code={result.returncode}): {result.stderr[:300]}"
        logger.warning("yt-dlp_retry", attempt=attempt+1, max_retries=max_retries, error=last_error)
        if attempt < max_retries - 1:
            time.sleep(3)

    raise RuntimeError(last_error)
