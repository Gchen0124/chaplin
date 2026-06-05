from unittest.mock import patch

from app import audio


def test_has_audio_true():
    with patch("app.audio.subprocess.run") as run:
        run.return_value.stdout = "audio\n"
        run.return_value.returncode = 0
        assert audio.has_audio("/clip.webm") is True


def test_has_audio_false_when_no_stream():
    with patch("app.audio.subprocess.run") as run:
        run.return_value.stdout = "\n"
        run.return_value.returncode = 0
        assert audio.has_audio("/clip.webm") is False


def test_probe_duration_parses_seconds():
    with patch("app.audio.subprocess.run") as run:
        run.return_value.stdout = "4.521000\n"
        run.return_value.returncode = 0
        assert abs(audio.probe_duration("/clip.webm") - 4.521) < 1e-3


def test_probe_duration_returns_zero_on_garbage():
    with patch("app.audio.subprocess.run") as run:
        run.return_value.stdout = "N/A\n"
        run.return_value.returncode = 0
        assert audio.probe_duration("/clip.webm") == 0.0


def test_extract_audio_invokes_ffmpeg(tmp_path):
    out = tmp_path / "audio.wav"
    with patch("app.audio.subprocess.run") as run:
        run.return_value.returncode = 0
        out.write_bytes(b"RIFF")  # simulate ffmpeg producing a file
        ok = audio.extract_audio("/clip.webm", str(out))
    assert ok is True
    args = run.call_args[0][0]
    assert args[0] == "ffmpeg" and str(out) in args
