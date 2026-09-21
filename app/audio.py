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
        return _probe_packet_duration(path)


def _probe_packet_duration(path: str) -> float:
    """Fallback for live-style WebM (MediaRecorder) with no Duration element.

    Chrome/Electron's MediaRecorder writes a WebM segment without a duration, so
    ffprobe reports ``N/A`` and the file looks zero-length. Sum the last packet
    time plus its duration on the video (or audio) stream instead.
    """
    best = 0.0
    for stream in ("v:0", "a:0"):
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", stream,
             "-show_entries", "packet=pts_time,duration_time", "-of", "csv=p=0", path],
            capture_output=True, text=True,
        )
        for line in (result.stdout or "").splitlines():
            fields = line.strip().split(",")
            try:
                pts = float(fields[0])
                dur = float(fields[1]) if len(fields) > 1 and fields[1] else 0.0
            except (ValueError, IndexError):
                continue
            best = max(best, pts + dur)
        if best:
            break
    return best


def extract_audio(video_path: str, out_path: str) -> bool:
    """Extract mono 16k wav. Returns True if the output file exists and is non-empty."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000",
         out_path],
        capture_output=True, text=True,
    )
    return os.path.isfile(out_path) and os.path.getsize(out_path) > 0
