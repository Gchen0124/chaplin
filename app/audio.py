import os
import subprocess


def has_audio(video_path: str) -> bool:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", video_path],
        capture_output=True, text=True,
    )
    return "audio" in (result.stdout or "")


def probe_duration(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    try:
        return float((result.stdout or "").strip())
    except ValueError:
        return 0.0


def extract_audio(video_path: str, out_path: str) -> bool:
    """Extract mono 16k wav. Returns True if the output file exists and is non-empty."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000",
         out_path],
        capture_output=True, text=True,
    )
    return os.path.isfile(out_path) and os.path.getsize(out_path) > 0
