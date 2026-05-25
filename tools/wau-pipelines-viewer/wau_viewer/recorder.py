"""Capture rendered frames and mux them into an MP4 with ffmpeg."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QWidget


class FrameRecorder:
    def __init__(self, framerate: int = 8) -> None:
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("ffmpeg not found on PATH — cannot record video")
        self.framerate = max(1, int(framerate))
        self._tmpdir: Optional[Path] = None
        self._frame_idx = 0
        self._active = False

    def start(self) -> None:
        self._tmpdir = Path(tempfile.mkdtemp(prefix="wau_viewer_frames_"))
        self._frame_idx = 0
        self._active = True

    @property
    def active(self) -> bool:
        return self._active

    def grab(self, widget: QWidget) -> None:
        if not self._active or self._tmpdir is None:
            return
        pix: QPixmap = widget.grab()
        out = self._tmpdir / f"{self._frame_idx:06d}.png"
        pix.save(str(out), "PNG")
        self._frame_idx += 1

    def stop_and_encode(self, out_path: Path) -> Path:
        if not self._active or self._tmpdir is None:
            raise RuntimeError("recorder is not active")
        self._active = False
        if self._frame_idx == 0:
            raise RuntimeError("no frames were captured")
        out_path = Path(out_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg", "-y",
            "-framerate", str(self.framerate),
            "-i", str(self._tmpdir / "%06d.png"),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            str(out_path),
        ]
        cp = subprocess.run(cmd, capture_output=True, text=True)
        if cp.returncode != 0:
            raise RuntimeError(
                f"ffmpeg failed:\n  cmd: {' '.join(cmd)}\n  stderr: {cp.stderr}"
            )
        # leave frames around if encode failed; clean only on success
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        self._tmpdir = None
        return out_path
