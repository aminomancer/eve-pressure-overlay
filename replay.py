"""Replay a real fight from your game logs, for testing the overlay. Not part of the zip.

Starts the overlay with "Simulate a fight" switched on, but instead of the made-up fight it
rewrites the lines of a real fight with current timestamps, at the original pace, looping.

The fight is found from a landmark: the most recent combat line naming PILOT (any part of the
line, case-insensitive), or the most recent combat line at all. From there it grows in both
directions through every combat line within GAP seconds of the previous one, so it can take in
other pilots fought alongside.

Usage: python replay.py ["Pilot Name"] [--preview]
"""

import re
import sys
import time
from datetime import UTC, datetime
from html import unescape

from pressure_overlay import __main__ as app
from pressure_overlay import overlay, simulation
from pressure_overlay.combat import FORMATTING
from pressure_overlay.logs import log_encoding
from pressure_overlay.windows import game_log_folder

LOGS = game_log_folder()
GAP = 60  # seconds without a combat line that end a fight
LEAD = 5  # seconds of log kept before the first and after the last combat line
PAUSE = 15  # seconds of silence before the replay loops
LINE = re.compile(r"\[ (\d{4}\.\d\d\.\d\d \d\d:\d\d:\d\d) \] (.*)")


def read(path):
    data = path.read_bytes()
    lines = []
    for raw in data.decode(log_encoding(data[:4]), errors="replace").splitlines():
        match = LINE.match(raw)
        if match:
            when = datetime.strptime(match[1], simulation.STAMP).timestamp()
            lines.append((when, match[2]))
    return lines


def find(pilot):
    """(log path, [(offset seconds, rest of line)]) for the fight, or None."""
    needle = pilot.casefold()
    for path in sorted(LOGS.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            lines = read(path)
        except OSError:
            continue
        combat = [when for when, rest in lines if rest.startswith("(combat)")]
        hits = [
            when
            for when, rest in lines
            if rest.startswith("(combat)")
            and needle in " ".join(unescape(FORMATTING.sub("", rest)).split()).casefold()
        ]
        if not hits:
            continue
        start = end = hits[-1]
        for when in reversed([w for w in combat if w < start]):
            if start - when > GAP:
                break
            start = when
        for when in (w for w in combat if w > end):
            if when - end > GAP:
                break
            end = when
        fight = [(w, r) for w, r in lines if start - LEAD <= w <= end + LEAD]
        return path, [(w - fight[0][0], r) for w, r in fight]
    return None


def player(lines):
    def run(self):
        try:
            with self.path.open("a", encoding="utf-8") as log:
                while not self.stopped.is_set():
                    begin = time.time()
                    for offset, rest in lines:
                        if self.stopped.wait(max(0, begin + offset - time.time())):
                            break
                        log.write(f"[ {datetime.now(UTC):{simulation.STAMP}} ] {rest}\n")
                        log.flush()
                    self.stopped.wait(PAUSE)
        except OSError:
            pass
        self.clear()

    return run


def main():
    names = [arg for arg in sys.argv[1:] if not arg.startswith("-")]
    pilot = " ".join(names)
    found = find(pilot)
    if found is None:
        sys.exit(f"No combat line mentioning {pilot!r} in {LOGS}")
    path, lines = found
    first = datetime.strptime(path.name[:15], "%Y%m%d_%H%M%S") if path.name[:8].isdigit() else None
    print(
        f"Replaying {len(lines)} lines, {lines[-1][0] / 60:.1f} min, from {path.name}"
        + (f" (session started {first:%Y-%m-%d %H:%M})" if first else ""),
        file=sys.stderr,
    )
    simulation.Simulation.run = player(lines)
    original_init = overlay.Overlay.__init__

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.simulate(True)
        self.configure_logs()  # the Settings dialog does this after simulate(); follow the replay

    overlay.Overlay.__init__ = init
    sys.argv[1:] = [arg for arg in sys.argv[1:] if arg.startswith("-")]
    app.main()


if __name__ == "__main__":
    main()
