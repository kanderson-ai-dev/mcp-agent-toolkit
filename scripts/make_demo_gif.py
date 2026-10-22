"""Convert a Playwright run recording into ``docs/web-demo.gif``.

Pipeline (all local, ffmpeg only):

1. The e2e suite records every run to ``test-results/videos/*.webm``
   (``record_video_dir`` in ``tests/e2e/conftest.py``). Raw video is
   never committed — the directory is gitignored.
2. This script picks the newest recording (or a path passed on the
   command line), trims it, and renders a 1280px-wide GIF at 12 fps with
   a generated 256-colour palette — the standard two-pass ffmpeg GIF
   pipeline for reasonable quality at reasonable size.

Usage::

    uv run playwright install chromium          # once
    uv run pytest tests/e2e -m e2e              # records the videos
    uv run python -m scripts.make_demo_gif      # newest video → docs/web-demo.gif
    uv run python -m scripts.make_demo_gif path/to/page@….webm
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIDEO_DIR = ROOT / "test-results" / "videos"
OUT = ROOT / "docs" / "web-demo.gif"

# Two-pass pipeline knobs: 12 fps + a generated palette is the sweet
# spot between size and legibility for UI recordings.
FPS = 12
WIDTH = 960
TRIM_START_S = 0.4  # skip the first paint flashes


def _newest_video() -> Path:
    videos = sorted(VIDEO_DIR.glob("*.webm"), key=lambda p: p.stat().st_mtime)
    if not videos:
        raise SystemExit(
            f"no recordings under {VIDEO_DIR} — run `uv run pytest tests/e2e -m e2e` first"
        )
    return videos[-1]


def make_gif(source: Path, out: Path) -> None:
    """Two-pass ffmpeg palette pipeline, local files only."""
    out.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise SystemExit("ffmpeg not found on PATH — required for the GIF pipeline")
    with tempfile.TemporaryDirectory() as tmp:
        palette = Path(tmp) / "palette.png"
        vf = f"fps={FPS},scale={WIDTH}:-1:flags=lanczos"
        base = [ffmpeg, "-y", "-ss", str(TRIM_START_S), "-i", str(source)]
        subprocess.run(  # noqa: S603 — local media pipeline, fixed args
            [*base, "-vf", f"{vf},palettegen=max_colors=192", str(palette)],
            check=True,
            capture_output=True,
        )
        subprocess.run(  # noqa: S603 — local media pipeline, fixed args
            [
                *base,
                "-i",
                str(palette),
                "-lavfi",
                f"{vf} [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=4",
                "-loop",
                "0",
                str(out),
            ],
            check=True,
            capture_output=True,
        )
    print(f"{source.name} -> {out} ({out.stat().st_size / 1024:.0f} KiB)")


def main(argv: list[str]) -> int:
    source = Path(argv[1]) if len(argv) > 1 else _newest_video()
    if not source.exists():
        raise SystemExit(f"recording not found: {source}")
    make_gif(source, OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
