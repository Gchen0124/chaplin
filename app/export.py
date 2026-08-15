import os
import subprocess

from pydantic import BaseModel

MAX_ATEMPO = 1.15


class Alignment(BaseModel):
    idx: int
    start_s: float
    speed: float
    duration_s: float


def plan_alignment(*, starts: list[float], clip_durations: list[float],
                   video_duration: float, max_atempo: float = MAX_ATEMPO) -> list[Alignment]:
    out = []
    for i, dur in enumerate(clip_durations):
        start = starts[i] if i < len(starts) else 0.0
        nxt = starts[i + 1] if i + 1 < len(starts) else video_duration
        window = max(0.0, nxt - start)
        speed = 1.0
        placed = dur
        if window > 0 and dur > window:
            speed = min(max_atempo, dur / window)
            placed = dur / speed
            if placed > window:
                placed = window
        out.append(Alignment(idx=i, start_s=start, speed=speed, duration_s=placed))
    return out


def wrap_caption(text: str, width: int = 42) -> str:
    text = (text or "").replace("\n", " ").strip()
    if len(text) <= width:
        return text
    lines, buf = [], ""
    for ch in text:
        buf += ch
        if len(buf) >= width:
            cut = max(buf.rfind(" "), buf.rfind("，"), buf.rfind("。"), buf.rfind(","))
            if cut >= width // 2:
                lines.append(buf[:cut].strip())
                buf = buf[cut + 1:].lstrip()
            else:
                lines.append(buf.strip())
                buf = ""
    if buf.strip():
        lines.append(buf.strip())
    return "\\N".join(lines)


def ass_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    cs = int(round(seconds * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H33000000,0,0,0,0,100,100,0,0,4,0,0,2,40,40,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def write_ass(utterances: list[dict], path: str) -> None:
    lines = [_ASS_HEADER]
    for u in utterances:
        text = wrap_caption(u.get("english_text") or "").replace("{", "\\{")
        start = ass_timestamp(float(u["start_s"]))
        end = ass_timestamp(float(u["end_s"]))
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n")
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)


def mux_demo(*, screen_path: str, voice_clips: list[tuple[str, Alignment]],
             ass_path: str, out_path: str, video_duration: float) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    cmd = ["ffmpeg", "-y", "-i", screen_path]
    for path, _al in voice_clips:
        cmd += ["-i", path]
    filters = []
    mix_labels = []
    for i, (_path, al) in enumerate(voice_clips):
        delay_ms = max(0, int(round(al.start_s * 1000)))
        chain = f"[{i + 1}:a]"
        if abs(al.speed - 1.0) > 1e-3:
            chain += f"atempo={al.speed:.4f},"
        chain += f"atrim=0:{al.duration_s:.4f},adelay={delay_ms}|{delay_ms}[a{i}]"
        filters.append(chain)
        mix_labels.append(f"[a{i}]")
    if mix_labels:
        filters.append(
            f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=longest:normalize=0[a]"
        )
        audio_map = "[a]"
    else:
        filters.append("anullsrc=r=44100:cl=stereo[a]")
        audio_map = "[a]"
    ass_escaped = ass_path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    filters.append(f"[0:v]ass='{ass_escaped}'[v]")
    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "[v]", "-map", audio_map,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-t", f"{video_duration:.3f}",
        "-movflags", "+faststart",
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg mux failed: {result.stderr[-800:]}")


def _mp3_duration(path: str) -> float:
    from app.audio import probe_duration
    return probe_duration(path) or 0.5


async def export_demo(*, utterances: list[dict], video_duration: float,
                      screen_path: str, demo_dir: str,
                      synthesize_fn, voice_id: str, tts_key: str, model_id: str) -> str:
    voice_dir = os.path.join(demo_dir, "voice")
    os.makedirs(voice_dir, exist_ok=True)
    clip_paths, durs, starts = [], [], []
    for u in utterances:
        idx = u["idx"]
        dest = os.path.join(voice_dir, f"{idx:03d}.mp3")
        if not (os.path.isfile(dest) and os.path.getsize(dest) > 0):
            try:
                data = await synthesize_fn(u["english_text"], voice_id, tts_key, model_id)
            except Exception as exc:
                raise RuntimeError(f"tts:{idx}:{exc}") from exc
            with open(dest, "wb") as fh:
                fh.write(data)
        clip_paths.append(dest)
        starts.append(float(u["start_s"]))
        durs.append(_mp3_duration(dest))
    plans = plan_alignment(starts=starts, clip_durations=durs, video_duration=video_duration)
    ass_path = os.path.join(demo_dir, "captions.ass")
    write_ass(utterances, ass_path)
    out_path = os.path.join(demo_dir, "export.mp4")
    mux_demo(screen_path=screen_path, voice_clips=list(zip(clip_paths, plans)),
             ass_path=ass_path, out_path=out_path, video_duration=video_duration)
    return out_path
