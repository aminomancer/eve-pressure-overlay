import os
from pathlib import Path

# Default launcher and Steam install folders.
EVE_ROOTS = (
    Path("C:/CCP/EVE"),
    Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
    / "Steam/steamapps/common/Eve Online",
)
WINDOWS_FONTS = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
EVE_FONTS = {"regular": "evesansneue-regular.otf", "bold": "evesansneue-bold.otf"}
FALLBACK_FONTS = {"regular": "segoeui.ttf", "bold": "segoeuib.ttf"}
SYMBOL_FONT = WINDOWS_FONTS / "seguisym.ttf"


def eve_fonts(roots=EVE_ROOTS):
    """EVE's UI font files, found through a local install's shared resource index."""
    for root in roots:
        # Prefer Tranquility's index over Singularity's.
        for index in sorted(
            root.glob("*/resfileindex.txt"), key=lambda path: path.parent.name != "tq"
        ):
            found = {}
            try:
                with index.open(encoding="utf-8", errors="replace") as lines:
                    for line in lines:
                        name, _, rest = line.partition(",")
                        for style, file in EVE_FONTS.items():
                            if name == f"res:/ui/fonts/{file}":
                                path = root / "ResFiles" / rest.split(",")[0]
                                if path.is_file():
                                    found[style] = path
            except OSError:
                continue
            if len(found) == len(EVE_FONTS):
                return found
    return None


def ui_fonts():
    return eve_fonts() or {style: WINDOWS_FONTS / file for style, file in FALLBACK_FONTS.items()}
