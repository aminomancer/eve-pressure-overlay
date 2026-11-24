"""Render the README screenshot: every meter with Move mode's sample data, over a made-up space
backdrop. It draws the overlay's own image instead of capturing the screen, using a throwaway
settings folder, so your settings, shortcuts and screen contents aren't involved. The overlay
window shows for a few seconds while item icons load.

The image scales with your display scaling (150% gives the published size).
Usage: python tools/screenshot.py [output.png]   (default: docs/screenshot.png)
"""

import ctypes
import json
import os
import random
import shutil
import sys
import tempfile
import time
import tkinter as tk
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pressure_overlay import overlay  # noqa: E402
from pressure_overlay.logs import Frame, placeholder_snapshot  # noqa: E402


def clouds(width, height, seed, scale):
    """Soft fractal noise in 0..255: several octaves of blurred random noise, blended."""
    random.seed(seed)
    total, used = None, 0.0
    for step, weight in ((96, 0.45), (48, 0.3), (24, 0.15), (12, 0.1)):
        step = max(2, round(step * scale))
        small = Image.effect_noise((width // step + 4, height // step + 4), 64)
        big = small.resize((width + 4 * step, height + 4 * step), Image.BICUBIC)
        dx, dy = random.randrange(step), random.randrange(step)
        big = big.crop((dx, dy, dx + width, dy + height))
        big = big.filter(ImageFilter.GaussianBlur(step / 1.5))
        used += weight
        total = big if total is None else Image.blend(total, big, weight / used)
    return ImageOps.autocontrast(total, cutoff=1)


def fill(size, color):
    return Image.new("RGB", size, color)


def space(width, height, scale):
    size = width, height
    base = fill(size, (4, 6, 12))
    # Two nebula clouds, violet and teal, thinned out so much of the frame stays dark.
    for seed, color, strength in ((3, (120, 50, 170), 1.0), (11, (30, 120, 160), 0.8)):
        cloud = clouds(width, height, seed, scale)
        mask = cloud.point(lambda v, s=strength: max(0, min(255, (v - 120) * 2.2 * s)))
        mask = mask.convert("L").filter(ImageFilter.GaussianBlur(6 * scale))
        base = Image.composite(fill(size, color), base, mask.point(lambda v: v * 0.55))
    # A brighter glow toward the top right, then a vignette.
    core = Image.new("L", size)
    ImageDraw.Draw(core).ellipse((width * 0.45, -height * 0.4, width * 1.3, height * 0.9), fill=90)
    core = core.filter(ImageFilter.GaussianBlur(width / 7))
    base = Image.composite(fill(size, (200, 150, 230)), base, core.point(lambda v: v * 0.5))
    vignette = Image.new("L", size)
    box = (-width * 0.15, -height * 0.35, width * 1.15, height * 1.35)
    ImageDraw.Draw(vignette).ellipse(box, fill=255)
    vignette = vignette.filter(ImageFilter.GaussianBlur(width / 10))
    base = Image.composite(base, fill(size, (2, 3, 6)), vignette)
    # Stars: many faint pinpricks, and a few brighter ones with a glow.
    random.seed(7)
    stars = Image.new("RGBA", size)
    draw = ImageDraw.Draw(stars)
    for _ in range(width * height // 900):
        x, y = random.uniform(0, width), random.uniform(0, height)
        alpha = int(random.betavariate(1.2, 6) * 255)
        tint = random.choice(((255, 255, 255), (200, 220, 255), (255, 235, 210)))
        r = random.choice((0.5, 0.5, 0.7, 1.0)) * scale
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(*tint, alpha))
    bright = Image.new("RGBA", size)
    draw = ImageDraw.Draw(bright)
    for _ in range(max(6, width * height // 60000)):
        x, y = random.uniform(0, width), random.uniform(0, height)
        r = random.uniform(1.0, 1.8) * scale
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(235, 240, 255, 255))
    glow = bright.filter(ImageFilter.GaussianBlur(3 * scale))
    image = base.convert("RGBA")
    for layer in (glow, glow, stars, bright):
        image.alpha_composite(layer)
    return image


def capture():
    """The overlay's composed image with sample data, and its display scale."""
    overlay.Overlay.sync_shortcuts = lambda self: None  # leave the real shortcuts alone
    folder = Path(tempfile.mkdtemp())
    try:
        icons = Path(os.environ["LOCALAPPDATA"]) / "EVE Pressure" / "icons"
        if icons.exists():
            shutil.copytree(icons, folder / "icons")
        (folder / "logs").mkdir()
        settings = {"locked": True, "folder": str(folder / "logs")}
        (folder / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        root = tk.Tk()
        app = overlay.Overlay(root, folder / "settings.json", preview=True)
        images, compose = [], app.compose
        app.compose = lambda height: images.append(compose(height)) or images[-1]

        def render():
            app.frame = Frame(combat=placeholder_snapshot(datetime.now(UTC)))
            app.render()

        # The first render asks for missing icons; give the downloads time, then render again.
        render()
        end = time.time() + 8
        while time.time() < end:
            root.update()
            time.sleep(0.05)
        render()
        root.destroy()
        return images[-1], app.scale
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "screenshot.png"
    # Like the app: without per-monitor DPI awareness the text renders blurry.
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    shot, scale = capture()
    pad_x, pad_y = round(90 * scale), round(50 * scale)
    image = space(shot.width + 2 * pad_x, shot.height + 2 * pad_y, scale)
    image.alpha_composite(shot, (pad_x, pad_y))
    image.convert("RGB").save(out, optimize=True)
    print(f"{out} {image.width}x{image.height}")


if __name__ == "__main__":
    main()
