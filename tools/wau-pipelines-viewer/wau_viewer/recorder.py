"""Capture rendered frames and encode them into an MP4 or animated GIF.

MP4 encoding shells out to ``ffmpeg``. GIF encoding prefers ffmpeg's two-pass
palette pipeline for quality, but transparently falls back to Pillow when
ffmpeg is missing or broken, so demo GIFs can still be produced on machines
without a working ffmpeg install.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPixmap

GIF_MAX_WIDTH = 1000
GIF_COLORS = 96


class FrameRecorder:
    def __init__(self, framerate: int = 10, gif_max_width: int = GIF_MAX_WIDTH) -> None:
        self.framerate = max(1, int(framerate))
        self.gif_max_width = max(100, int(gif_max_width))
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

        if out_path.suffix.lower() == ".gif":
            self._encode_gif(out_path)
        else:
            self._encode_ffmpeg_mp4(out_path)

        # leave frames around if encode failed; clean only on success
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        self._tmpdir = None
        return out_path

    # ----- encoders -----

    def _encode_ffmpeg_mp4(self, out_path: Path) -> None:
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("ffmpeg not found on PATH — cannot encode MP4")
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

    def _encode_gif(self, out_path: Path) -> None:
        if shutil.which("ffmpeg") is not None and self._encode_ffmpeg_gif(out_path):
            return
        self._encode_pillow_gif(out_path)

    def _encode_ffmpeg_gif(self, out_path: Path) -> bool:
        """Two-pass palette GIF via ffmpeg. Returns False to allow fallback."""
        vf = (
            f"scale='min({self.gif_max_width},iw)':-1:flags=lanczos,"
            "split[s0][s1];[s0]palettegen=stats_mode=diff[p];"
            "[s1][p]paletteuse=dither=bayer:bayer_scale=3"
        )
        cmd = [
            "ffmpeg", "-y",
            "-framerate", str(self.framerate),
            "-i", str(self._tmpdir / "%06d.png"),
            "-filter_complex", vf,
            str(out_path),
        ]
        cp = subprocess.run(cmd, capture_output=True, text=True)
        return cp.returncode == 0

    def _encode_pillow_gif(self, out_path: Path) -> None:
        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "GIF encoding needs a working ffmpeg or the Pillow package"
            ) from exc

        frame_paths = sorted(self._tmpdir.glob("*.png"))
        first = Image.open(frame_paths[0])
        scale = min(1.0, self.gif_max_width / first.width)
        size = (round(first.width * scale), round(first.height * scale))

        def _prep(p: Path) -> "Image.Image":
            img = Image.open(p).convert("RGB")
            if scale < 1.0:
                img = img.resize(size, Image.LANCZOS)
            return img.quantize(colors=GIF_COLORS, dither=Image.Dither.NONE)

        frames = [_prep(p) for p in frame_paths]
        duration_ms = round(1000 / self.framerate)
        frames[0].save(
            out_path,
            save_all=True,
            append_images=frames[1:],
            duration=duration_ms,
            loop=0,
            optimize=True,
        )
