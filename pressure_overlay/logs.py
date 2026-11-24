import codecs
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Event, Thread

from .combat import (
    DAMAGE_WINDOW,
    DPS_WINDOW,
    DRONE_LIMIT,
    NEUT_MEMORY,
    WINDOW,
    CombatSnapshot,
    Damage,
    DroneHit,
    Hit,
    MissileHit,
    MissileReferences,
    Neut,
    PodHit,
    parse_combat,
    parse_time,
    snapshot,
)

LISTENER = re.compile(r"^\s*Listener\s*:\s*(.+?)\s*$", re.M)
SESSION_START = re.compile(
    r"^\s*Session Started\s*:\s*(\d{4}\.\d\d\.\d\d \d\d:\d\d:\d\d)\s*$", re.M
)
# Results kept so a target switch still leaves a full sample for the current target.
HISTORY = 2 * DRONE_LIMIT


def log_encoding(data):
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    if data[1:2] == b"\0":
        return "utf-16-le"
    if data[:1] == b"\0":
        return "utf-16-be"
    return "utf-8-sig"


def placeholder_snapshot(now):
    """Made-up data for every meter, so Move mode shows the overlay at full size."""

    def ago(seconds):
        return now - timedelta(seconds=seconds)

    target, other = "Sample Pilot[SMPL](Vagabond)", "Second Pilot[SMPL](Stabber)"
    grades = ("hits", "smashes", "penetrates", "grazes", "hits")
    grades += ("glances off", "penetrates", "misses", "hits", "smashes")
    ac, ham = "425mm AutoCannon II", "Scourge Heavy Assault Missile"
    pod, drones = "Medium Breacher Pod Launcher", ("Hammerhead II", "Hobgoblin II")
    neut, heavy, nos = (
        "Medium Energy Neutralizer II",
        "Heavy Energy Neutralizer II",
        "Medium Energy Nosferatu II",
    )
    events = [
        Hit(ago(i), grade, target, ac, 0 if grade == "misses" else 300)
        for i, grade in enumerate(grades)
    ]
    # Guns split onto a second target, and a second launcher group with other ammo.
    events += [Hit(ago(i), grade, other, ac, 300) for i, grade in enumerate(grades[:5])]
    events += [MissileHit(ago(s), target, ham, amount) for s, amount in ((6, 560), (3, 540))]
    events.append(MissileHit(now, target, ham, 420))
    inferno = "Inferno Heavy Assault Missile"
    events += [MissileHit(ago(s), target, inferno, 500) for s in (7, 4, 1)]
    events += [
        DroneHit(ago(i // 2), grade, drones[i % 2], 60, target)
        for i, grade in enumerate(grades * 2)
    ]
    events += [PodHit(ago(1), target, 112, pod), PodHit(now, other, 100, pod)]
    events += [Neut(ago(s), "Sample Pilot", 600, True, heavy, "Curse") for s in (24, 12, 0)]
    events += [Neut(ago(s), "Second Pilot", 63, True, nos, "Ashimmu") for s in (10, 5, 0)]
    events += [Neut(ago(s), "Third Pilot", 180, True, neut, "Sentinel") for s in (12, 0)]
    # One target per drain state: draining, dry, pinned, recovered.
    for pilot, ship, pulses in (
        ("Sample Pilot", "Vagabond", (292, 292, 292)),
        ("Second Pilot", "Stabber", (292, 292, 0)),
        ("Third Pilot", "Cerberus", (292, 0, 40)),
        ("Fourth Pilot", "Tornado Fleet Issue", (292, 0, 30, 290)),
    ):
        last = len(pulses) - 1
        events += [
            Neut(ago(12 * (last - i)), pilot, amount, False, neut, ship)
            for i, amount in enumerate(pulses)
        ]
    pulse = "Mega Pulse Laser II"
    events += [Damage(ago(s), "Sample Pilot", 900, "AC", ac, "Vagabond") for s in (12, 9, 6, 3, 0)]
    events += [Damage(ago(s), "Sample Pilot", 200, None, drones[0], "Vagabond") for s in (10, 5, 0)]
    events += [Damage(ago(s), "Second Pilot", 600, "HAM", ham, "Cerberus") for s in (9, 4)]
    events += [Damage(ago(s), "Third Pilot", 450, "Pulse", pulse, "Oracle") for s in (8, 0)]
    return snapshot(events, now)


@dataclass(frozen=True)
class Session:
    path: Path
    pilot: str
    started: datetime
    identity: tuple[int, int]


class Tail:
    def __init__(self):
        self.offset = 0
        self.decoder = None
        self.pending = ""
        self.events = []
        self.hits = deque(maxlen=HISTORY)
        # Recent volleys for DPS; the last one stays for the meter until it goes stale.
        self.missiles = []
        self.references = MissileReferences()
        self.drones = []
        # Your neut results, kept long enough to remember a target running dry.
        self.drains = []
        self.context = None

    def set_context(self, context):
        """Clear the hit meters after ESI reports a dock or a different ship."""
        ship, docks = context
        if self.context is not None:
            old_ship, old_docks = self.context
            reshipped = ship is not None and old_ship is not None and ship != old_ship
            if docks != old_docks or reshipped:
                self.hits.clear()
                self.drones.clear()
                self.missiles.clear()
                self.references.clear()
            if ship is None:
                ship = old_ship
        self.context = ship, docks

    def read(self, session, now):
        cutoff = now - WINDOW
        self.events = [
            event
            for event in self.events
            if event.time > (now - DAMAGE_WINDOW if isinstance(event, Damage) else cutoff)
        ]
        # Keep the drone window for DPS and enough results for the current target.
        self.drones = [
            hit
            for index, hit in enumerate(self.drones)
            if hit.time > now - DPS_WINDOW or index >= len(self.drones) - HISTORY
        ]
        self.missiles = [
            hit
            for index, hit in enumerate(self.missiles)
            if hit.time > now - DPS_WINDOW or index == len(self.missiles) - 1
        ]
        self.drains = [pulse for pulse in self.drains if pulse.time > now - NEUT_MEMORY]
        with session.path.open("rb") as stream:
            size = stream.seek(0, 2)
            if size < self.offset:
                self.__init__()
            if size == self.offset:
                return
            stream.seek(self.offset)
            if self.decoder is None:
                head = stream.read(min(4, size))
                self.decoder = codecs.getincrementaldecoder(log_encoding(head))(errors="replace")
                stream.seek(0)
            after = (now - NEUT_MEMORY).strftime("%Y.%m.%d %H:%M:%S")
            while self.offset < size:
                chunk = stream.read(min(65536, size - self.offset))
                if not chunk:
                    break
                self.offset += len(chunk)
                lines = (self.pending + self.decoder.decode(chunk)).split("\n")
                self.pending = lines.pop()
                for line in lines:
                    try:
                        parsed = parse_combat(line.rstrip("\r"), after)
                    except Exception:
                        # One line EVE writes in an unexpected shape shouldn't stop the overlay.
                        continue
                    if isinstance(parsed, Hit):
                        self.hits.append(parsed)
                    elif isinstance(parsed, MissileHit):
                        self.missiles.append(self.references.add(parsed))
                    elif isinstance(parsed, DroneHit):
                        self.drones.append(parsed)
                    elif isinstance(parsed, Neut) and not parsed.incoming:
                        self.drains.append(parsed)
                    elif parsed is not None and (
                        not isinstance(parsed, Damage) or parsed.time > now - DAMAGE_WINDOW
                    ):
                        self.events.append(parsed)


@dataclass(frozen=True)
class Frame:
    pilots: tuple[str, ...] = ()
    pilot: str = ""
    status: str = "Looking for EVE game logs…"
    combat: CombatSnapshot = CombatSnapshot()


class LogMonitor:
    """Find sessions by their Listener header and tail only the selected pilot."""

    def __init__(self, folder):
        self.folder = folder
        self.sessions = {}
        self.next_scan = 0
        self.tails = {}
        self.status = ""

    def discover(self):
        discovered = {}
        try:
            for path in self.folder.iterdir():
                if path.suffix.lower() != ".txt":
                    continue
                try:
                    info = path.stat()
                    identity = (info.st_dev, info.st_ino)
                    cached = self.sessions.get(path)
                    if cached and cached.identity == identity:
                        discovered[path] = cached
                        continue
                    with path.open("rb") as stream:
                        head = stream.read(4096)
                except OSError:
                    continue
                header = head.decode(log_encoding(head), errors="replace")
                pilot, start = LISTENER.search(header), SESSION_START.search(header)
                if pilot and start:
                    try:
                        started = parse_time(start[1])
                    except ValueError:
                        continue
                    discovered[path] = Session(path, pilot[1], started, identity)
        except FileNotFoundError:
            self.status = "Game log folder missing. Open Settings to choose it."
        except OSError:
            self.status = "Game log folder could not be read."
        else:
            self.status = "Waiting for an EVE character log."
        self.sessions = discovered

    def poll(self, now, selected="", context=(None, 0), *, refresh=False, cycle=None):
        clock = time.monotonic()
        if refresh or clock >= self.next_scan:
            self.discover()
            self.next_scan = clock + 2
        sessions = sorted(
            self.sessions.values(), key=lambda s: (s.started, s.path.name), reverse=True
        )
        pilots = tuple(sorted({session.pilot for session in sessions}, key=str.casefold))
        candidates = (
            [session for session in sessions if session.pilot.casefold() == selected.casefold()]
            if selected
            else sessions
        )
        if not candidates:
            self.tails.clear()
            status = f"Waiting for {selected}'s log." if selected and pilots else self.status
            return Frame(pilots, selected, status)

        latest = candidates[0]
        # A session that ended in the current window can still contribute pressure.
        active = [latest]
        for session in candidates[1:]:
            if session.pilot == latest.pilot:
                try:
                    if session.path.stat().st_mtime > (now - WINDOW).timestamp():
                        active.append(session)
                except OSError:
                    continue

        tails = {}
        status = "Watching game log"
        for session in reversed(active):
            tail = self.tails.get(session) or Tail()
            try:
                tail.read(session, now)
            except OSError:
                if session == latest:
                    status = "Waiting to read game log…"
            if session == latest:
                # Nothing is fired while docked, so every line read so far predates the dock.
                tail.set_context(context)
            tails[session] = tail
        self.tails = tails
        events = [event for tail in tails.values() for event in (*tail.events, *tail.drains)]
        events.extend(tails[latest].hits)
        events.extend(tails[latest].drones)
        events.extend(tails[latest].missiles)
        return Frame(pilots, latest.pilot, status, snapshot(events, now, cycle))


class LogWorker:
    def __init__(self):
        self.requests = SimpleQueue()
        self.result = None
        self.generation = 0
        # (ship item ID, dock count) from ESI, written by the UI thread.
        self.context = None, 0
        # Module name -> base cycle in seconds or None, set by the UI thread.
        self.cycle = None
        self.stopped = Event()
        Thread(target=self.run, daemon=True).start()

    def configure(self, folder, pilot):
        self.generation += 1
        self.requests.put((self.generation, folder, pilot))

    def run(self):
        monitor = None
        while not self.stopped.is_set():
            try:
                while True:
                    generation, folder, pilot = self.requests.get_nowait()
                    if monitor is None or monitor.folder != folder:
                        monitor = LogMonitor(folder)
            except Empty:
                pass
            if monitor is not None:
                self.result = (
                    generation,
                    monitor.poll(datetime.now(UTC), pilot, self.context, cycle=self.cycle),
                )
            self.stopped.wait(0.25)
