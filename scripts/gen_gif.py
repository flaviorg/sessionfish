"""Record docs/media/demo.gif from the real viewer of `sessionfish demo`.

Needs playwright with a Chromium build (`pip install playwright && playwright install chromium`)
and ffmpeg. Not a project dependency: only used to refresh the README media.

python scripts/gen_gif.py [path/to/index.html]
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "media"
# (ply, frames to hold): walk the game, linger on the inaccuracy and the blunder
SHOTS = [(0, 3), (1, 1), (2, 1), (3, 2), (4, 4), (5, 2), (6, 10), (7, 1), (8, 2), (9, 4)]


def main() -> int:
    from playwright.sync_api import sync_playwright

    html = Path(sys.argv[1]) if len(sys.argv) > 1 else next(ROOT.glob(".sessionfish/demo/reviews/*/index.html"))
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
        page.goto(html.resolve().as_uri())
        n = 0
        for ply, hold in SHOTS:
            page.evaluate(f"go({ply})")
            page.wait_for_timeout(450)
            path = Path(tmp) / f"f{n:03d}.png"
            page.screenshot(path=str(path))
            for _ in range(hold - 1):
                n += 1
                (Path(tmp) / f"f{n:03d}.png").write_bytes(path.read_bytes())
            n += 1
        page.evaluate("go(6)")
        page.wait_for_timeout(300)
        page.screenshot(path=str(OUT / "viewer.png"), full_page=True)
        browser.close()
        palette = Path(tmp) / "palette.png"
        frames = str(Path(tmp) / "f%03d.png")
        scale = "fps=2,scale=960:-1:flags=lanczos"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-framerate",
                "2",
                "-i",
                frames,
                "-vf",
                f"{scale},palettegen=max_colors=128",
                str(palette),
            ],
            check=True,
        )
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-framerate",
                "2",
                "-i",
                frames,
                "-i",
                str(palette),
                "-lavfi",
                f"{scale}[x];[x][1:v]paletteuse=dither=bayer",
                str(OUT / "demo.gif"),
            ],
            check=True,
        )
    print(f"wrote {OUT / 'demo.gif'} and {OUT / 'viewer.png'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
