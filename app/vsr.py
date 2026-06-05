import torch

from pipelines.pipeline import InferencePipeline


class VSR:
    """Loads the lip-reading model once and reads video files to text."""

    def __init__(self, config_filename: str, device: str = "cpu"):
        self.model = InferencePipeline(
            config_filename,
            device=torch.device(device),
            detector="mediapipe",
            face_track=True,
        )

    def read_lips(self, video_path: str) -> str:
        try:
            return (self.model(video_path) or "").strip()
        except Exception:
            return ""
