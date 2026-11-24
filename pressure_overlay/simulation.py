"""A made-up fight written to its own log folder in real time, for testing the overlay."""

import random
import time
from datetime import UTC, datetime
from itertools import count
from threading import Event, Thread

STAMP = "%Y.%m.%d %H:%M:%S"
TARGET, SHIP = "Fake Target", "Vexor"
NEUT = "Medium Abyssal Energy Neutralizer"
AC, HAM = "220mm Vulcan AutoCannon II", "Caldari Navy Inferno Heavy Assault Missile"
SECOND_HAM = "Caldari Navy Scourge Heavy Assault Missile"
DRONES = ("Mosquito EV-600-I",) * 3 + ("Huntsman SW-600-I",) * 2
GRADES = ("Grazes", "Glances Off", "Hits", "Hits", "Penetrates", "Smashes")
# The fight repeats every two minutes: draining, dry, pinned, then recovered.
LOOP = 120

OUT_HIT = (
    "<color=0xff00ffff><b>{amount}</b> <color=0x77ffffff><font size=10>to</font> "
    "<b><color=0xffffffff>{target}</b><font size=10><color=0x77ffffff> - {weapon} - {grade}"
)
IN_HIT = (
    "<color=0xffcc0000><b>{amount}</b> <color=0x77ffffff><font size=10>from</font> "
    "<b><color=0xffffffff>{source}</b><font size=10><color=0x77ffffff> - {weapon} - {grade}"
)
NEUT_LINE = (
    "<color={color}><b>{amount} GJ</b><color=0x77ffffff><font size=10> energy neutralized "
    "</font><b><color=0xffffffff><font size=10><color=0xFFFFFFFF>{pilot}</color></font> "
    "<font size=12><color=0xFF40FF70><b>{ship}</b></color></font></b><color=0x77ffffff>"
    "<font size=10> - {module}</font>"
)
HOSTILES = (
    # Source, weapon, seconds between shots, damage range.
    ("Hostile One[HSTL](Vagabond)", AC, 2, (250, 400)),
    ("Hostile One[HSTL](Vagabond)", "Hammerhead II", 3, (40, 60)),
    ("Hostile Two[HSTL](Cerberus)", "Scourge Heavy Assault Missile", 4, (300, 350)),
    ("Hostile Three[HSTL](Oracle)", "Mega Pulse Laser II", 5, (500, 800)),
    ("Hostile Four[HSTL](Wolf)", "150mm Light AutoCannon II", 2, (50, 80)),
)


def your_neut(phase):
    """Two staggered neuts: full, then one empty pulse, then small ones, then full again."""
    if phase < 30:
        return random.randint(326, 338)
    if phase < 36:
        return 0
    if phase < 66:
        return random.randint(0, 40)
    return random.randint(326, 338)


def events(second):
    phase = second % LOOP
    target = f"{TARGET}[FAKE]({SHIP})"
    if phase % 6 == 0:
        yield NEUT_LINE.format(
            color="0xff7fffff", amount=your_neut(phase), pilot=TARGET, ship=SHIP, module=NEUT
        )
    if phase % 2 == 0:
        if random.random() < 0.2:
            yield f"Your group of {AC} misses {TARGET} completely - {AC}"
        else:
            grade = random.choice(GRADES)
            yield OUT_HIT.format(
                amount=random.randint(200, 450), target=target, weapon=AC, grade=grade
            )
    # A gap in the missile volleys shows the row dimming when one is overdue.
    if phase % 3 == 0 and not 40 <= phase < 55:
        amount = random.randint(300, 500)
        yield OUT_HIT.format(amount=amount, target=target, weapon=HAM, grade="Hits")
    # For half a minute a second launcher group fires other ammo, for a second missile meter.
    if phase % 3 == 1 and 70 <= phase < 100:
        amount = random.randint(250, 400)
        yield OUT_HIT.format(amount=amount, target=target, weapon=SECOND_HAM, grade="Hits")
    if phase % 4 == 0:
        for drone in DRONES:
            if random.random() < 0.15:
                yield f"Your {drone} misses {TARGET} completely - {drone}"
            else:
                grade = random.choice(GRADES)
                yield OUT_HIT.format(
                    amount=random.randint(20, 60), target=target, weapon=drone, grade=grade
                )
    if phase < 12:
        yield OUT_HIT.format(
            amount=49, target=target, weapon="Medium Breacher Pod Launcher", grade="Hits"
        )
    for source, weapon, every, (low, high) in HOSTILES:
        if phase % every == 0:
            grade = "Hits" if "Missile" in weapon else random.choice(GRADES)
            yield IN_HIT.format(
                amount=random.randint(low, high), source=source, weapon=weapon, grade=grade
            )
    if phase % 12 == 3:
        yield NEUT_LINE.format(
            color="0xffe57f7f",
            amount=random.randint(560, 600),
            pilot="Hostile Five",
            ship="Curse",
            module="Heavy Energy Neutralizer II",
        )
    if phase % 6 == 1:
        yield NEUT_LINE.format(
            color="0xffe57f7f",
            amount=random.randint(40, 60),
            pilot="Hostile Three",
            ship="Oracle",
            module="Medium Energy Nosferatu II",
        )


class Simulation:
    """Writes the fight once a second until stopped, then deletes its log."""

    def __init__(self, folder, pilot):
        self.folder = folder
        self.stopped = Event()
        folder.mkdir(parents=True, exist_ok=True)
        self.clear()
        started = datetime.now(UTC)
        self.path = folder / f"{started:%Y%m%d_%H%M%S}_simulated.txt"
        rule = "-" * 60
        self.path.write_text(
            f"{rule}\n  Gamelog\n  Listener: {pilot}\n  Session Started: {started:{STAMP}}\n"
            f"{rule}\n",
            encoding="utf-8",
        )
        Thread(target=self.run, daemon=True).start()

    def run(self):
        try:
            with self.path.open("a", encoding="utf-8") as log:
                for second in count():
                    if self.stopped.wait(1 - time.time() % 1):
                        break
                    stamp = f"[ {datetime.now(UTC):{STAMP}} ] (combat) "
                    log.writelines(stamp + line + "\n" for line in events(second))
                    log.flush()
        except OSError:
            pass
        self.clear()

    def clear(self):
        for path in self.folder.glob("*.txt"):
            path.unlink(missing_ok=True)

    def stop(self):
        self.stopped.set()
