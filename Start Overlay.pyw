import sys
from pathlib import Path

# Double-clicking a .pyw runs it with pythonw, which never opens a console window.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pressure_overlay.__main__ import main  # noqa: E402

main()
