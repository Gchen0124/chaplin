from unittest.mock import patch

from app.export import (
    Alignment,
    MAX_ATEMPO,
    ass_timestamp,
    mux_demo,
    plan_alignment,
    wrap_caption,
    write_ass,
)


def test_plan_alignment_pads_short_clip():
    plans = plan_alignment(starts=[0.0, 3.0], clip_durations=[1.0, 0.8], video_duration=5.0)
    assert plans[0].start_s == 0.0
    assert plans[0].speed == 1.0
    assert plans[0].duration_s == 1.0
    assert plans[1].start_s == 3.0


def test_plan_alignment_speeds_up_to_max_atempo():
    plans = plan_alignment(starts=[0.0, 2.0], clip_durations=[2.2], video_duration=4.0)
    assert abs(plans[0].speed - 1.1) < 1e-6
    assert plans[0].duration_s <= 2.0 + 1e-6


def test_plan_alignment_hard_cuts_instead_of_overlapping_next():
    plans = plan_alignment(starts=[0.0, 1.0], clip_durations=[2.0], video_duration=3.0)
    assert plans[0].speed == MAX_ATEMPO
    assert abs(plans[0].duration_s - 1.0) < 1e-6
    assert plans[0].start_s + plans[0].duration_s <= 1.0 + 1e-6


def test_wrap_caption_breaks_near_42():
    text = "This is a long product demo sentence that should wrap onto another line."
    wrapped = wrap_caption(text, width=42)
    assert "\\N" in wrapped
    assert "\\N\\N" not in wrapped
    for line in wrapped.split("\\N"):
        assert len(line) <= 48


def test_ass_timestamp_format():
    assert ass_timestamp(0) == "0:00:00.00"
    assert ass_timestamp(65.2) == "0:01:05.20"


def test_write_ass_uses_gladia_end_not_tts(tmp_path):
    path = tmp_path / "c.ass"
    write_ass([
        {"start_s": 1.0, "end_s": 2.5, "english_text": "Open Settings."},
    ], str(path))
    body = path.read_text(encoding="utf-8")
    assert "Dialogue:" in body
    assert "0:00:01.00" in body
    assert "0:00:02.50" in body
    assert "Open Settings." in body
    assert "BorderStyle=4" in body or "BorderStyle,4" in body or ",4," in body


def test_mux_demo_invokes_ffmpeg(tmp_path):
    out = tmp_path / "export.mp4"
    clip = tmp_path / "v0.mp3"
    clip.write_bytes(b"ID3")
    ass = tmp_path / "c.ass"
    ass.write_text("[Script Info]\n")
    with patch("app.export.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stderr = ""
        mux_demo(
            screen_path="/screen.webm",
            voice_clips=[(str(clip), Alignment(idx=0, start_s=1.0, speed=1.0, duration_s=0.8))],
            ass_path=str(ass),
            out_path=str(out),
            video_duration=5.0,
        )
    args = run.call_args[0][0]
    assert args[0] == "ffmpeg"
    assert "ass=" in " ".join(args)
    assert str(out) == args[-1]
