"""Generate all PNG visual assets required by the Windows MSIX package and Microsoft Store.

Renders directly from ``src/adv_tg/ui/assets/adv-tg.svg`` using PyQt6's ``QtSvg`` (no extra
dependencies). Run from the repository root::

    QT_QPA_PLATFORM=offscreen python packaging/windows/generate_msix_assets.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QGuiApplication, QImage, QPainter  # noqa: E402
from PyQt6.QtSvg import QSvgRenderer  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SVG_PATH = ROOT / "src" / "adv_tg" / "ui" / "assets" / "adv-tg.svg"
ASSETS_DIR = ROOT / "packaging" / "windows" / "msix" / "Assets"
LISTING_DIR = ROOT / "packaging" / "windows" / "msix" / "StoreListing"

# Background tone of adv-tg.svg (used to pad the wide 310x150 tile cleanly)
TILE_BG = QColor("#091b35")

# (relative_path_under_msix, width, height, background_color_or_None)
TARGETS: list[tuple[Path, int, int, QColor | None]] = [
    # Package / Store logo inside MSIX (50x50 @ 100%, 100x100 @ 200%)
    (Path("Assets/StoreLogo.scale-100.png"), 50, 50, None),
    (Path("Assets/StoreLogo.scale-200.png"), 100, 100, None),
    # App list / Start / Taskbar logo (44x44 base + scale-200 + targetsize + unplated)
    (Path("Assets/Square44x44Logo.scale-100.png"), 44, 44, None),
    (Path("Assets/Square44x44Logo.scale-200.png"), 88, 88, None),
    (Path("Assets/Square44x44Logo.targetsize-16.png"), 16, 16, None),
    (Path("Assets/Square44x44Logo.targetsize-24.png"), 24, 24, None),
    (Path("Assets/Square44x44Logo.targetsize-32.png"), 32, 32, None),
    (Path("Assets/Square44x44Logo.targetsize-48.png"), 48, 48, None),
    (Path("Assets/Square44x44Logo.targetsize-256.png"), 256, 256, None),
    (Path("Assets/Square44x44Logo.targetsize-16_altform-unplated.png"), 16, 16, None),
    (Path("Assets/Square44x44Logo.targetsize-24_altform-unplated.png"), 24, 24, None),
    (Path("Assets/Square44x44Logo.targetsize-32_altform-unplated.png"), 32, 32, None),
    (Path("Assets/Square44x44Logo.targetsize-48_altform-unplated.png"), 48, 48, None),
    (Path("Assets/Square44x44Logo.targetsize-256_altform-unplated.png"), 256, 256, None),
    # Medium tile (150x150 @ 100%, 300x300 @ 200%)
    (Path("Assets/Square150x150Logo.scale-100.png"), 150, 150, None),
    (Path("Assets/Square150x150Logo.scale-200.png"), 300, 300, None),
    # Wide tile (310x150 @ 100%, 620x300 @ 200%)
    (Path("Assets/Wide310x150Logo.scale-100.png"), 310, 150, TILE_BG),
    (Path("Assets/Wide310x150Logo.scale-200.png"), 620, 300, TILE_BG),
    # Partner Center listing logo (300x300 PNG)
    (Path("StoreListing/AppIcon-300x300.png"), 300, 300, None),
]


def render_png(
    renderer: QSvgRenderer, out_path: Path, width: int, height: int, bg: QColor | None
) -> None:
    img = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(bg if bg is not None else Qt.GlobalColor.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    if width == height:
        rect = QRectF(0.0, 0.0, float(width), float(height))
    else:
        # Center the square emblem cleanly inside the wide tile with balanced margin
        side = min(width, height) * 0.84
        x0 = (width - side) * 0.5
        y0 = (height - side) * 0.5
        rect = QRectF(x0, y0, side, side)
    renderer.render(painter, rect)
    painter.end()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not img.save(str(out_path), "PNG"):
        raise RuntimeError(f"Failed to save {out_path}")


def main() -> int:
    if not SVG_PATH.is_file():
        print(f"SVG source not found: {SVG_PATH}", file=sys.stderr)
        return 1
    _app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    renderer = QSvgRenderer(str(SVG_PATH))
    if not renderer.isValid():
        print(f"Invalid SVG: {SVG_PATH}", file=sys.stderr)
        return 1
    base = ROOT / "packaging" / "windows" / "msix"
    for rel, w, h, bg in TARGETS:
        dest = base / rel
        render_png(renderer, dest, w, h, bg)
        print(f"Generated {rel} ({w}x{h})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
