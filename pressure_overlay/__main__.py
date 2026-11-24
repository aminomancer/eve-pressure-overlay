import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description="EVE Online combat-log overlay for Windows")
    parser.add_argument(
        "--preview", action="store_true", help="show the overlay without a focused EVE client"
    )
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("EVE Pressure Overlay requires Windows.")
    import ctypes
    import tkinter as tk

    try:
        import PIL  # noqa: F401
    except ImportError:
        message = "EVE Pressure needs Pillow. Install it with:\n\npython -m pip install pillow"
        ctypes.windll.user32.MessageBoxW(None, message, "EVE Pressure", 0x10)
        raise SystemExit(1) from None

    from .overlay import Overlay

    # Without this, Windows draws the overlay at 96 DPI and stretches it, which blurs text.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()
    root = tk.Tk()
    Overlay(root, preview=args.preview)
    root.mainloop()


if __name__ == "__main__":
    main()
