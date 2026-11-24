import re
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from html import unescape

WINDOW_SECONDS = 60
WINDOW = timedelta(seconds=WINDOW_SECONDS)
DAMAGE_SECONDS = 15
DAMAGE_WINDOW = timedelta(seconds=DAMAGE_SECONDS)
DPS_SECONDS = 60
DPS_WINDOW = timedelta(seconds=DPS_SECONDS)
HIT_LIMIT = 10
# A full flight of five drones produces about ten times as many results as one turret group.
DRONE_LIMIT = 50
# Reshipping needs a dock, so a long pause on one target likely means a new fit.
MISSILE_GAP = timedelta(minutes=5)
# Assumed cycle when a source has pulsed only once.
NEUT_CYCLE = 12
DAMAGE_CYCLE = 5
# How long a target's neut results count, so it's remembered as having run dry.
NEUT_MEMORY = timedelta(minutes=5)
# Against that module's last full pulse on the target: at most a fifth means it's pinned (falloff
# rarely cuts that deep), under half keeps it pinned, and three quarters means cap again.
PIN_DRAIN, HELD_DRAIN, RECOVERY_DRAIN = 0.2, 0.5, 0.75
# Recovered stays until a full pulse lands this many of the module's cycles after the recovery,
# so it shows about two cycles however many neuts are staggered.
RECOVERED_CYCLES = 1.5
# A target's state is dropped once none of your modules on it has pulsed for this many cycles
# (burnt out, out of range, or switched off). Overheating only makes cycles shorter.
STALE_CYCLES = 2
# Longer than a heavy neut's cycle, so a nos result waits on a slow neut too.
NOS_DEFER = timedelta(seconds=30)
DRAINING, DRY, PINNED, RECOVERED = "draining", "dry", "pinned", "recovered"
# Breacher pods tick once a second; a target is still podded if it ticked this recently.
POD_ACTIVE = timedelta(seconds=3)
# A second turret or missile meter shows while its weapon has fired this recently.
CONCURRENT = timedelta(seconds=20)

COMBAT = re.compile(r"^\s*\[\s*(\d{4}\.\d\d\.\d\d \d\d:\d\d:\d\d)\s*\]\s*\(combat\)\s*(.*)$")
# EVE uses unclosed markup tags. Strip just its formatting, preserving alliance tags.
FORMATTING = re.compile(r"</?(?:b|i|u)>|</(?:color|font)>|<(?:color|font)\s*[ =][^>]*>", re.I)
AMOUNT_COLOR = re.compile(r"^\s*<color\s*=\s*[\"']?(?:0x|#)?([0-9a-f]{8})[\"']?\s*>", re.I)
# Neut, nosferatu and tackle lines put the pilot and ship in separate font runs.
PILOT_RUN = re.compile(r"<font size=10><color=[^>]*>([^<]+)</color></font>\s*<font size=12>", re.I)
SHIP_RUN = re.compile(r"<font size=12><color=[^>]*><b>([^<]+)</b>", re.I)
NEUT = re.compile(r"^([+-]?)(\d[\d,]*(?:\.\d+)?)\s*GJ\s+energy (neutralized|drained)\s+(.+)$", re.I)
INCOMING_HIT = re.compile(r"^(\d[\d,]*(?:\.\d+)?)\s+from\s+(.+)$", re.I)
OUTGOING_HIT = re.compile(r"^[\d,.]+\s+to\s+", re.I)
OUTGOING_MISS = re.compile(r"^Your (?:group of )?(.+?) misses (.+?) completely(?: - .+)?$", re.I)
TURRET = re.compile(
    r"\b(?:autocannon|repeating cannon|artillery|howitzer|railgun|gatling rail|blaster|"
    r"(?:pulse|beam|afocal|modal) laser|energy beam|particle stream|afocal maser|"
    r"vorton projector|entropic disintegrator)\b",
    re.I,
)
MISSILE = re.compile(r"\b(?:missiles?|rockets?|torpedo(?:es)?)\b", re.I)
# Graded hits from anything else are drones or fighters, apart from these area weapons.
NOT_DRONE = re.compile(r"\b(?:smartbomb|bomb|breacher|lance|doomsday)\b", re.I)
DAMAGE_TYPES = {"mjolnir": "EM", "inferno": "Thermal", "scourge": "Kinetic", "nova": "Explosive"}
# Order matters: "heavy assault missile" must win over "heavy missile".
WEAPON_CLASSES = tuple(
    (label, re.compile(pattern, re.I))
    for label, pattern in (
        ("Pulse", r"pulse (?:laser|maser)"),
        ("Beam", r"beam laser|energy beam|particle stream|afocal maser"),
        ("Blaster", r"blaster"),
        ("Rail", r"railgun|gatling rail"),
        ("AC", r"autocannon|repeating cannon"),
        ("Arty", r"artillery|howitzer"),
        ("Disintegrator", r"disintegrator"),
        ("Vorton", r"vorton"),
        ("Rockets", r"rocket"),
        ("LM", r"light missile"),
        ("HAM", r"heavy assault missile"),
        ("HM", r"heavy missile"),
        ("Cruise", r"cruise missile"),
        ("Torps", r"torpedo"),
        ("Pods", r"breacher"),
    )
)
SHIP_SUFFIX = re.compile(r"\(([^()]+)\)\s*$")
ENTITY_SUFFIX = re.compile(r"(?:\s*(?:\[[^\[\]]+\]|<[^<>]+>|\([^()]+\)))+\s*$")

# Midpoints of the log's turret damage multiplier bands. A miss is zero.
HIT_QUALITY = {
    "misses": 0.0,
    "grazes": 0.5625,
    "glances off": 0.6875,
    "hits": 0.875,
    "penetrates": 1.125,
    "smashes": 1.37,
    "wrecks": 3.0,
}


@dataclass(frozen=True)
class Neut:
    time: datetime
    entity: str
    amount: float
    incoming: bool
    module: str = ""
    ship: str = ""


@dataclass(frozen=True)
class Hit:
    time: datetime
    grade: str
    # Hits name the pilot with corp and ship; misses name the pilot only.
    target: str = ""
    weapon: str = ""
    amount: float = 0.0


@dataclass(frozen=True)
class DroneHit:
    time: datetime
    grade: str
    weapon: str
    amount: float
    target: str = ""


@dataclass(frozen=True)
class PodHit:
    time: datetime
    target: str
    amount: float
    weapon: str = ""


@dataclass(eq=False)
class Reference:
    best: float
    last: datetime
    hits: int = 1


@dataclass(frozen=True)
class MissileHit:
    time: datetime
    target: str
    weapon: str
    amount: float
    reference: Reference | None = field(default=None, compare=False)

    @property
    def key(self):
        return self.weapon.casefold(), self.target.casefold()

    @property
    def ratio(self):
        return min(1.0, self.amount / self.reference.best) if self.reference else 1.0

    @property
    def damage_type(self):
        name = self.weapon.casefold()
        return next((label for word, label in DAMAGE_TYPES.items() if word in name), None)


class MissileReferences:
    """Best damage per missile and target, restarted after an idle gap or a ship change."""

    def __init__(self):
        self.references = {}

    def add(self, hit):
        reference = self.references.get(hit.key)
        if reference is None or hit.time - reference.last > MISSILE_GAP:
            reference = self.references[hit.key] = Reference(hit.amount, hit.time)
        else:
            reference.best = max(reference.best, hit.amount)
            reference.last = hit.time
            reference.hits += 1
        return replace(hit, reference=reference)

    def clear(self):
        self.references.clear()


@dataclass(frozen=True)
class Damage:
    time: datetime
    entity: str
    amount: float
    # None for drones and lines without a weapon.
    weapon: str | None = None
    module: str = ""
    ship: str = ""


def weapon_class(name):
    return next((label for label, pattern in WEAPON_CLASSES if pattern.search(name)), None)


def is_drone(weapon):
    return not (TURRET.search(weapon) or MISSILE.search(weapon) or NOT_DRONE.search(weapon))


def parse_time(value):
    return datetime.strptime(value, "%Y.%m.%d %H:%M:%S").replace(tzinfo=UTC)


def entity_label(entity):
    return ENTITY_SUFFIX.sub("", entity).strip() or entity.strip()


def ship_label(entity):
    """The ship type from a `Pilot[CORP](Ship)` label, or nothing."""
    found = SHIP_SUFFIX.search(entity)
    return found[1].strip() if found else ""


def parse_combat(line, after=None):
    match = COMBAT.match(line)
    if not match:
        return None
    stamp, raw = match.groups()
    text = " ".join(unescape(FORMATTING.sub("", raw)).split())
    recent = after is None or stamp > after
    neut = NEUT.fullmatch(text) if recent else None
    if neut:
        sign, amount, kind, description = neut.groups()
        # Some clients include the direction in the text. Otherwise EVE encodes
        # it in the amount color, including on zero-GJ cycles.
        color = AMOUNT_COLOR.match(raw)
        direction = color[1].lower() if color else None
        explicit = re.match(r"(from|to)\s+", description, re.I)
        word = explicit[1].lower() if explicit else None
        # Neutralizers take energy "from" you; nosferatus send it "to" the pilot draining you.
        incoming_word = "to" if kind.lower() == "drained" else "from"
        if direction in {"ff7fffff", "ffe57f7f"}:
            incoming = direction == "ffe57f7f"
        elif word:
            incoming = word == incoming_word
        elif sign:
            incoming = sign == "-"
        else:
            return None
        if word and incoming == (word == incoming_word):
            description = description[explicit.end() :]
        entity, separator, module = description.rpartition(" - ")
        if not separator or not entity.strip():
            return None
        pilot = PILOT_RUN.search(raw)
        ship = SHIP_RUN.search(raw)
        # Match the pilot-only names that damage lines use once their tags are stripped.
        entity = unescape(pilot[1]) if pilot else entity_label(entity)
        return Neut(
            parse_time(stamp),
            entity.strip(),
            float(amount.replace(",", "")),
            incoming,
            module.strip(),
            unescape(ship[1]).strip() if pilot and ship else "",
        )

    miss = OUTGOING_MISS.fullmatch(text)
    if miss:
        weapon, target = miss[1], miss[2].strip()
        if TURRET.search(weapon):
            return Hit(parse_time(stamp), "misses", target, weapon)
        if is_drone(weapon):
            return DroneHit(parse_time(stamp), "misses", weapon, 0.0, target)
    if OUTGOING_HIT.match(text):
        parts = text.rsplit(" - ", 2)
        if len(parts) == 3:
            head, weapon, outcome = parts
            grade, weapon = outcome.casefold(), weapon.strip()
            amount, _, target = head.partition(" to ")
            amount, target = float(amount.replace(",", "")), target.strip()
            if grade in HIT_QUALITY and TURRET.search(weapon):
                return Hit(parse_time(stamp), grade, target, weapon, amount)
            if grade in HIT_QUALITY and is_drone(weapon):
                return DroneHit(parse_time(stamp), grade, weapon, amount, target)
            if "breacher" in weapon.casefold() and amount > 0 and target:
                return PodHit(parse_time(stamp), target, amount, weapon)
            if MISSILE.search(weapon) and amount > 0 and target:
                return MissileHit(parse_time(stamp), target, weapon, amount)
    damage = INCOMING_HIT.fullmatch(text) if recent else None
    if damage:
        amount, description = damage.groups()
        amount = float(amount.replace(",", ""))
        if amount > 0:
            # With weapon and hit quality disabled, EVE can log the source alone.
            parts = description.rsplit(" - ", 2)
            weapon, module = None, ""
            if len(parts) == 3 and parts[-1].casefold() not in HIT_QUALITY:
                source = " - ".join(parts[:-1])
            else:
                source = parts[0]
                if len(parts) == 3:
                    weapon, module = weapon_class(parts[1]), parts[1].strip()
            return Damage(
                parse_time(stamp),
                entity_label(source),
                amount,
                weapon,
                module,
                ship_label(source),
            )
    return None


def typical_gap(times):
    """The median seconds between distinct timestamps, or None with fewer than two."""
    stamps = sorted(set(times))
    if len(stamps) < 2:
        return None
    gaps = sorted((b - a).total_seconds() for a, b in zip(stamps, stamps[1:]))
    return gaps[len(gaps) // 2]


def active_seconds(times, now, window, cycle):
    """Seconds to average over: from a source's first pulse, at least one cycle long."""
    stamps = sorted(set(times))
    cycle = typical_gap(stamps) or cycle
    span = max((stamps[-1] - stamps[0]).total_seconds() + cycle, (now - stamps[0]).total_seconds())
    return min(window, max(1.0, span))


@dataclass(frozen=True)
class Pressure:
    entity: str
    total: float
    latest: float
    seconds: float = WINDOW_SECONDS
    # The source's main weapon class and its non-drone damage, for incoming DPS.
    weapon: str | None = None
    weapon_total: float = 0.0
    # The item that did the most, for its icon.
    module: str = ""
    # For your neuts: what the latest pulse says about the target's cap, and its GJ.
    state: str | None = None
    ship: str = ""
    drain: float = 0.0

    @property
    def rate(self):
        return self.total / self.seconds

    @property
    def weapon_rate(self):
        return self.weapon_total / self.seconds


@dataclass(frozen=True)
class Stream:
    """Your recent results from one weapon on one target, for one meter."""

    results: tuple
    dps: float = 0.0
    # Median seconds between recent results, to spot when the next one is overdue.
    gap: float | None = None

    @property
    def latest(self):
        return self.results[-1]


def average_quality(results):
    if results:
        return sum(HIT_QUALITY[hit.grade] for hit in results) / len(results)
    return None


@dataclass(frozen=True)
class CombatSnapshot:
    # Up to two meters each: what you fired at last, and one more firing alongside it.
    turrets: tuple[Stream, ...] = ()
    missiles: tuple[Stream, ...] = ()
    drones: tuple[DroneHit, ...] = ()
    drone_dps: float = 0.0
    outgoing: tuple[Pressure, ...] = ()
    incoming: tuple[Pressure, ...] = ()
    damage: tuple[Pressure, ...] = ()
    # One row per podded target, with its latest per-second tick as the total.
    pods: tuple[Pressure, ...] = ()

    @property
    def drone_quality(self):
        return average_quality(self.drones)

    @property
    def incoming_total(self):
        return sum(row.total for row in self.incoming)

    @property
    def incoming_rate(self):
        return sum(row.rate for row in self.incoming)

    @property
    def damage_total(self):
        return sum(row.total for row in self.damage)

    @property
    def damage_rate(self):
        return sum(row.rate for row in self.damage)


def same_pilot(miss, label):
    """Whether a miss, which names only the pilot, belongs to a fuller hit label."""
    return label == miss or label.startswith((miss + " ", miss + "[", miss + "("))


def on_target(results, limit):
    """The latest results against the most recently hit pilot and ship."""
    if not results:
        return ()
    latest = results[-1].target.casefold()
    full = latest
    if results[-1].grade == "misses":
        for hit in reversed(results):
            label = hit.target.casefold()
            if hit.grade != "misses" and same_pilot(latest, label):
                full = label
                break
    picked = []
    for hit in reversed(results):
        label = hit.target.casefold()
        if label == full or (hit.grade == "misses" and same_pilot(label, full)):
            picked.append(hit)
            if len(picked) == limit:
                break
    return tuple(reversed(picked))


def by_weapon_and_target(results):
    """Results grouped per weapon and target, with each miss joining its pilot's hits."""
    labels = [result.target.casefold() for result in results]
    hits = [i for i, result in enumerate(results) if getattr(result, "grade", "") != "misses"]
    for i, result in enumerate(results):
        if i in hits:
            continue
        # A miss names the pilot only: use their nearest hit label, before it if possible.
        matches = [j for j in hits if same_pilot(labels[i], labels[j])]
        earlier = [j for j in matches if j < i]
        if matches:
            labels[i] = labels[earlier[-1] if earlier else matches[0]]
    groups = defaultdict(list)
    for result, label in zip(results, labels):
        groups[result.weapon.casefold(), label].append(result)
    return list(groups.values())


def concurrent_streams(results, now, limit):
    """The weapon and target fired at last, and another one still firing alongside it."""
    groups = sorted(by_weapon_and_target(results), key=lambda group: group[-1].time)
    if not groups:
        return ()
    latest = groups[-1]
    started = engaged_since([result.time for result in latest], WINDOW)
    shown = [latest]
    for group in reversed(groups[:-1]):
        # A target you switched away from stopped before the new one started.
        if group[-1].time >= started and now - group[-1].time <= CONCURRENT:
            shown.append(group)
            break
    # The one you started on most recently goes first, so the rows keep their places.
    shown.sort(key=lambda group: engaged_since([r.time for r in group], WINDOW), reverse=True)
    return tuple(
        Stream(
            tuple(group[-limit:]),
            dps(group, now),
            typical_gap([result.time for result in group if result.time > now - DPS_WINDOW]),
        )
        for group in shown
    )


def split_labels(streams):
    """Names that tell meters on different targets apart, or None for each."""
    targets = [
        next((r.target for r in reversed(s.results) if ship_label(r.target)), s.latest.target)
        for s in streams
    ]
    if len({target.casefold() for target in targets}) < 2:
        return [None] * len(streams)
    ships = [ship_label(target) for target in targets]
    if all(ships) and len({ship.casefold() for ship in ships}) == len(ships):
        return ships
    return [entity_label(target) for target in targets]


def dps(results, now):
    """Damage per second over the recent window, from the first result in it."""
    recent = [result for result in results if result.time > now - DPS_WINDOW]
    if not recent:
        return 0.0
    seconds = active_seconds([result.time for result in recent], now, DPS_SECONDS, DAMAGE_CYCLE)
    return sum(result.amount for result in recent) / seconds


def drain_state(pulses, now=None, cycle=None):
    """Whether a target still has cap to take, and the pulse that showed it, from log order.

    `cycle(module)` gives a module's base cycle in seconds, or None if unknown. With `now`, the
    state is None once no module has pulsed on the target recently.
    """

    def seconds(module):
        return timedelta(seconds=(cycle and cycle(module)) or NEUT_CYCLE)

    ceilings, latest = {}, {}
    state, amount, last_neut, recovered_until = None, 0.0, None, None
    for pulse in pulses:
        module = pulse.module.casefold()
        latest[pulse.module] = pulse.time
        if "nosferatu" not in module:
            last_neut = pulse.time
        elif last_neut and pulse.time - last_neut <= NOS_DEFER:
            # Most nos take nothing once the target's cap is below yours; a neut says more.
            continue
        ceiling = ceilings.get(module)
        amount = pulse.amount
        held = state in (DRY, PINNED)
        if pulse.amount == 0:
            state = DRY
        elif ceiling is None:
            # A module's first pulse: full by definition, unless the target is already low, when
            # there's no telling what full would be.
            if not held:
                ceilings[module] = pulse.amount
                state = state or DRAINING
        elif pulse.amount <= (HELD_DRAIN if held else PIN_DRAIN) * ceiling:
            state = PINNED
        elif pulse.amount >= RECOVERY_DRAIN * ceiling:
            # Following full pulses lets range and resist changes move the ceiling.
            ceilings[module] = pulse.amount
            if held:
                state = RECOVERED
                recovered_until = pulse.time + RECOVERED_CYCLES * seconds(pulse.module)
            elif state != RECOVERED or pulse.time >= recovered_until:
                state = DRAINING
    if now is not None and all(
        now - time > STALE_CYCLES * seconds(module) for module, time in latest.items()
    ):
        state = None
    return state, amount


def engaged_since(times, gap):
    """When the latest unbroken run of activity began, allowing pauses up to `gap`."""
    times = sorted(times)
    start = times[-1]
    for earlier in reversed(times[:-1]):
        if start - earlier > gap:
            break
        start = earlier
    return start


def snapshot(events, now, cycle=None):
    """`cycle(module)` is a module's base cycle in seconds, or None if unknown."""
    hits, drones, missiles = [], [], []
    pods = {}
    references = MissileReferences()
    incoming, outgoing, damage = defaultdict(list), defaultdict(list), defaultdict(list)
    drains = defaultdict(list)
    pod_ticks = defaultdict(list)
    for event in sorted(events, key=lambda event: event.time):
        if event.time > now:
            continue
        if isinstance(event, Hit):
            hits.append(event)
        elif isinstance(event, DroneHit):
            drones.append(event)
        elif isinstance(event, PodHit):
            pod_ticks[event.target.casefold()].append(event.time)
            if event.time > now - POD_ACTIVE:
                pods[event.target.casefold()] = event
        elif isinstance(event, MissileHit):
            missiles.append(event if event.reference else references.add(event))
        elif isinstance(event, Damage):
            if event.time > now - DAMAGE_WINDOW:
                damage[event.entity.casefold()].append(event)
        elif not event.incoming and event.time > now - NEUT_MEMORY:
            drains[event.entity.casefold()].append(event)
            if event.time > now - WINDOW:
                outgoing[event.entity.casefold()].append(event)
        elif event.incoming and event.amount > 0 and event.time > now - WINDOW:
            incoming[event.entity.casefold()].append(event)

    def summarize(groups, window, cycle):
        rows = []
        for pulses in groups.values():
            latest = pulses[-1]
            weapons, modules = defaultdict(float), defaultdict(float)
            for pulse in pulses:
                if getattr(pulse, "weapon", None):
                    weapons[pulse.weapon] += pulse.amount
                if pulse.module:
                    modules[pulse.module] += pulse.amount
            rows.append(
                Pressure(
                    latest.entity,
                    sum(pulse.amount for pulse in pulses),
                    sum(pulse.amount for pulse in pulses if pulse.time == latest.time),
                    active_seconds([pulse.time for pulse in pulses], now, window, cycle),
                    max(weapons, key=weapons.get) if weapons else None,
                    sum(weapons.values()),
                    max(modules, key=modules.get) if modules else "",
                    ship=next((p.ship for p in reversed(pulses) if p.ship), ""),
                )
            )
        return tuple(sorted(rows, key=lambda row: (-row.rate, row.entity.casefold())))

    # Targets you started on most recently come first, so rows don't swap as rates change.
    drained, started = [], {}
    for row in summarize(outgoing, WINDOW_SECONDS, NEUT_CYCLE):
        pulses = drains[row.entity.casefold()]
        started[row.entity] = engaged_since([pulse.time for pulse in pulses], WINDOW)
        state, drain = drain_state(pulses, now, cycle)
        drained.append(replace(row, state=state, drain=drain))
    drained.sort(key=lambda row: (-started[row.entity].timestamp(), -row.rate))
    podded = sorted(
        pods.values(),
        key=lambda hit: (
            -engaged_since(pod_ticks[hit.target.casefold()], POD_ACTIVE).timestamp(),
            -hit.amount,
        ),
    )
    return CombatSnapshot(
        turrets=concurrent_streams(hits, now, HIT_LIMIT),
        missiles=concurrent_streams(missiles, now, HIT_LIMIT),
        drones=on_target(drones, DRONE_LIMIT),
        drone_dps=dps(drones, now),
        outgoing=tuple(drained),
        incoming=summarize(incoming, WINDOW_SECONDS, NEUT_CYCLE),
        damage=summarize(damage, DAMAGE_SECONDS, DAMAGE_CYCLE),
        pods=tuple(
            Pressure(
                entity_label(hit.target),
                hit.amount,
                hit.amount,
                1,
                module=hit.weapon,
                ship=ship_label(hit.target),
            )
            for hit in podded
        ),
    )
