import colorsys
import json
import os
import subprocess
import sys
import tempfile
import time
import tkinter as tk
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import Empty
from tkinter import filedialog, font, ttk

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

from . import fonts, windows
from .combat import (
    DRAINING,
    DRY,
    PINNED,
    RECOVERED,
    average_quality,
    entity_label,
    split_labels,
)
from .esi import CLIENT_ID, EsiWorker, Status
from .icons import IconCache
from .logs import Frame, LogWorker, placeholder_snapshot
from .simulation import Simulation

PANEL = "#101419"
# Move mode's backdrop is lighter than the meter tracks so their empty parts still show.
BACKDROP = "#505860"
BORDER = "#6b757e"
# A 20% grey at 60% opacity: visible on black space, and the game still shows through.
TRACK = (48, 54, 60, 153)
SEPARATOR = (190, 200, 210, 70)
CUT = (0, 0, 0, 0)
# Invisible, but enough alpha for Windows to send the mouse to the overlay.
CATCH = (0, 0, 0, 1)
SHADOW = (0, 0, 0)
TEXT = "#f2f6f8"
MUTED = "#aebdc8"
WARN = "#ffc04d"
DANGER = "#ff5a4f"
DAMAGE_COLORS = {
    "EM": "#6aa6ff",
    "Thermal": "#ff6b4f",
    "Kinetic": "#cfd6dc",
    "Explosive": "#ffb547",
}
MIRRORED = {"w": "e", "e": "w"}
# Pillow anchors on the text baseline; rows are centred on digit height instead.
ANCHORS = {"w": "ls", "e": "rs", "center": "ms"}
# Drawn bottom to top. Only the text layer gets a shadow.
LAYERS = ("bars", "icons", "text", "top")
AUTO = "Follow active EVE character"
# Layout in unscaled pixels, measured from the icon side.
# Room past the values for the labels that tell split meters apart.
LABEL_ROOM = 72
WIDTH = 300 + LABEL_ROOM
# Below this, a pilot row drops the ship name rather than squeeze it.
SHIP_MIN = 30
ROW = 22
SOURCE_ROW = 15
GAP = 4
# Bars look closer to the rows below them than they are, so those get a little more room.
METER_GAP = 4
# Space between what you're doing and what's being done to you, with a line across it.
INCOMING_GAP = 16
ICON_X, ICON_SIZE = 12, 18
BAR_LEFT, BAR_RIGHT, VALUE_EDGE = 28, 248, 296
VALUE_GAP = 6
BAR_HEIGHT = 10
# Turret and drone bars end just above the best ordinary grade; band edges become ticks.
GRADE_SCALE = 1.5
GRADE_EDGES = (0.5, 0.625, 0.75, 1.0, 1.25)
# Fill colors from below Grazes up to Penetrates and better.
GRADE_COLORS = ("#ff5a4f", "#ff8c42", "#ffc04d", "#d9e05a", "#6fdc8c", "#6fdc8c")
# Keywords in your ship's ESI group name, and the DPS then GJ/s that turn totals amber and red.
SHIP_LEVELS = (
    (
        ("titan", "carrier", "dreadnought", "force auxiliary", "freighter", "capital"),
        ((3000, 8000), (150, 400)),
    ),
    (("battleship", "black ops", "marauder"), ((700, 1600), (50, 120))),
    (("battlecruiser", "command ship"), ((450, 1000), (30, 70))),
    (("destroyer", "interdictor"), ((150, 350), (8, 20))),
    (
        ("frigate", "interceptor", "bomber", "covert ops", "corvette", "shuttle", "capsule"),
        ((100, 250), (5, 15)),
    ),
)
CRUISER_LEVELS = ((300, 700), (20, 50))
NEUT_SOURCE_LIMIT = 3
DAMAGE_SOURCE_LIMIT = 3
# Dry and pinned mean your neuts did their job; recovered means they have cap again.
DRAIN_COLORS = {DRAINING: TEXT, DRY: "#6fdc8c", PINNED: "#6aa6ff", RECOVERED: WARN}
STALE = timedelta(minutes=2)
# Missiles that never arrive leave no log line, so a late volley is the only sign of them.
MISSILE_OVERDUE = 2
MISSILE_OVERDUE_MIN = 5


APP_NAME = "EVE Pressure"
ICON_FILE = "EVE Pressure.ico"


APP_FOLDER = Path(__file__).resolve().parent.parent


def write_icon(path):
    """The tray icon's three meter bars, drawn large so every size stays sharp."""
    size = 256
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 8, size - 8, size - 8), 36, fill=(0x10, 0x14, 0x19, 255))
    bars = ((56, 88, 208, "#8ccfef"), (112, 144, 160, "#edbb76"), (168, 200, 184, "#f59487"))
    for top, bottom, right, color in bars:
        draw.rectangle((48, top, right, bottom), fill=color)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])


def dimmed(color):
    """The same color with most of its saturation gone and a little of its lightness."""
    red, green, blue = (channel / 255 for channel in ImageColor.getrgb(color))
    hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    rgb = colorsys.hls_to_rgb(hue, lightness * 0.8, saturation * 0.2)
    return "#" + "".join(f"{round(channel * 255):02x}" for channel in rgb)


def short(value):
    return f"{value:,.0f}" if value >= 10 else f"{value:.1f}"


def merged(live, sample, now):
    """Live meters that would show while locked, and made-up ones elsewhere, for Move mode."""

    def recent(event):
        return event is not None and now - event.time < STALE

    names = ("outgoing", "incoming", "damage", "pods")
    changes = {name: getattr(sample, name) for name in names if not getattr(live, name)}
    for name in ("turrets", "missiles"):
        if not any(recent(stream.latest) for stream in getattr(live, name)):
            changes[name] = getattr(sample, name)
    if not (live.drones and recent(live.drones[-1])):
        changes.update(drones=sample.drones, drone_dps=sample.drone_dps)
    return replace(live, **changes)


def ship_levels(group):
    group = group.casefold()
    return next(
        (levels for words, levels in SHIP_LEVELS if any(word in group for word in words)),
        CRUISER_LEVELS,
    )


def paste(image, tile, left, top):
    """Alpha-composite a tile, clipping whatever falls outside the image."""
    skip_x, skip_y = max(0, -left), max(0, -top)
    image.alpha_composite(tile, (left + skip_x, top + skip_y), (skip_x, skip_y))


def paint_text(image, point, value, color, typeface, anchor):
    # ImageDraw on a clear RGBA image darkens antialiased edges, so draw a mask and tint it.
    left, top, right, bottom = typeface.getbbox(value, anchor=anchor)
    if right <= left or bottom <= top:
        return
    mask = Image.new("L", (right - left, bottom - top))
    ImageDraw.Draw(mask).text((-left, -top), value, fill=255, font=typeface, anchor=anchor)
    tile = Image.new("RGBA", mask.size, color)
    tile.putalpha(mask)
    paste(image, tile, point[0] + left, point[1] + top)


def inside(box, x, y):
    return box is not None and box[0] <= x < box[2] and box[1] <= y < box[3]


@dataclass
class Settings:
    folder: str
    pilot: str = ""
    position: tuple[int, int] = (40, 70)
    locked: bool = False
    offset: tuple[int, int] | None = None
    min_drone_dps: int = 0
    esi_client_id: str = CLIENT_ID
    hide_docked: bool = True
    meters_from_right: bool = True
    move_backdrop: bool = True
    start_menu_shortcut: bool = True
    desktop_shortcut: bool = False
    shortcut_target: str = ""

    @classmethod
    def load(cls, path):
        settings = cls(str(windows.game_log_folder()))
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return settings
        if not isinstance(data, dict):
            return settings
        for key in ("folder", "pilot", "shortcut_target"):
            if isinstance(data.get(key), str):
                setattr(settings, key, data[key])
        # An override for anyone who registers their own app.
        if isinstance(data.get("esi_client_id"), str) and data["esi_client_id"].strip():
            settings.esi_client_id = data["esi_client_id"].strip()
        for key in ("position", "offset"):
            point = data.get(key)
            if isinstance(point, list) and len(point) == 2 and all(type(v) is int for v in point):
                setattr(settings, key, tuple(point))
        settings.locked = data.get("locked") is True
        if type(data.get("min_drone_dps")) is int and data["min_drone_dps"] >= 0:
            settings.min_drone_dps = data["min_drone_dps"]
        if isinstance(data.get("hide_docked"), bool):
            settings.hide_docked = data["hide_docked"]
        if isinstance(data.get("meters_from_right"), bool):
            settings.meters_from_right = data["meters_from_right"]
        for key in ("move_backdrop", "start_menu_shortcut", "desktop_shortcut"):
            if isinstance(data.get(key), bool):
                setattr(settings, key, data[key])
        return settings

    def save(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as file:
            data = asdict(self)
            # Saving the default would pin it if the built-in ID ever changes.
            if data["esi_client_id"] == CLIENT_ID:
                del data["esi_client_id"]
            json.dump(data, file, ensure_ascii=False)
        temp = Path(file.name)
        try:
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


class Overlay:
    def __init__(self, root, settings_path=None, *, preview=False):
        self.root = root
        self.root.withdraw()
        app_data = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        self.settings_path = settings_path or app_data / "EVE Pressure" / "settings.json"
        self.settings = Settings.load(self.settings_path)
        self.preview = preview
        self.foreground = windows.EveForeground()
        self.client = None
        self.active_pilot = ""
        self.log_config = None
        self.mapped = False
        self.shown = False
        self.frame = Frame()
        self.locked = self.settings.locked
        self.notice = ""
        self.dialog = None
        self.drag = None
        self.docks = 0
        self.rendered = 0.0
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96)
        self.root.title("EVE Pressure")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        paths = fonts.ui_fonts()
        self.fonts = {
            "small": ImageFont.truetype(paths["regular"], self.px(12)),
            "tiny": ImageFont.truetype(paths["bold"], self.px(11)),
            "value": ImageFont.truetype(paths["bold"], self.px(12)),
            "number": ImageFont.truetype(paths["bold"], self.px(14)),
            "symbol": ImageFont.truetype(fonts.SYMBOL_FONT, self.px(15)),
        }
        # The in-bar label sits exactly mid-bar only if the space left around its digits is even.
        for size in range(self.px(11), 7, -1):
            tiny = ImageFont.truetype(paths["bold"], size)
            if (self.px(BAR_HEIGHT) + tiny.getbbox("0", anchor="ls")[1]) % 2 == 0:
                self.fonts["tiny"] = tiny
                break
        self.half_digit = {
            style: -typeface.getbbox("0", anchor="ls")[1] / 2
            for style, typeface in self.fonts.items()
        }
        windows.private_font(paths["regular"])
        self.tip_font = font.Font(family=self.fonts["small"].getname()[0], size=-self.px(12))
        self.icons = IconCache(self.settings_path.parent / "icons")
        self.images = {}
        self.ops = []
        self.targets = []
        self.settings_box = None
        self.tip_window = None
        self.body = tk.Canvas(
            self.root,
            width=self.px(WIDTH),
            height=1,
            highlightthickness=0,
            borderwidth=0,
            bg=PANEL,
        )
        self.body.pack()
        self.body.bind("<ButtonPress-1>", self.start_drag)
        self.body.bind("<ButtonPress-3>", self.context_menu)
        self.body.bind("<B1-Motion>", self.move)
        self.body.bind("<ButtonRelease-1>", self.finish_drag)
        self.interactive = False
        self.root.update_idletasks()
        self.hwnd = windows.window_handle(self.root)
        self.apply_window_style(self.locked)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.hotkey = windows.LockHotkey()
        self.tray = windows.TrayIcon("EVE Pressure")
        self.tray.moving = not self.locked
        self.logs = LogWorker()
        self.logs.cycle = self.icons.cycle
        self.esi = EsiWorker(self.settings_path.parent / "esi.dat", self.settings.esi_client_id)
        self.esi_status = Status()
        self.esi_controls = None
        self.simulation = None
        self.configure_logs()
        self.sync_shortcuts()
        self.tick()

    def px(self, value):
        return round(value * self.scale)

    @property
    def clear(self):
        return self.locked or not self.settings.move_backdrop

    def configure_logs(self):
        pilot = self.settings.pilot or self.active_pilot
        config = Path(self.settings.folder), pilot
        if self.simulation is not None:
            # The simulated log is the only one in its folder, so follow whatever is there.
            config = self.simulation.folder, ""
        if config != self.log_config:
            self.log_config = config
            self.logs.configure(*config)
            self.frame = Frame(self.frame.pilots, pilot)

    def tick(self):
        client = None if self.preview else self.foreground.current()
        dirty = client != self.client or time.monotonic() - self.rendered > 1
        self.client = client
        self.esi.follow(self.frame.pilot)
        esi = self.esi.status
        if esi.pilot.casefold() != self.frame.pilot.casefold():
            esi = Status(self.frame.pilot)
        if esi != self.esi_status:
            if esi.docked and not self.esi_status.docked:
                self.docks += 1
            self.esi_status = esi
            self.logs.context = esi.ship_id, self.docks
        if self.esi_controls is not None:
            message, linked = self.esi_controls
            message.set(self.esi.message)
            if tuple(linked.cget("values")) != tuple(self.esi.linked):
                linked.configure(values=self.esi.linked)
        docked = self.settings.hide_docked and esi.docked and self.simulation is None
        visible = self.preview or (client is not None and not docked)
        if not visible and self.shown:
            self.drag = None
            windows.hide_window(self.hwnd)
            self.shown = False
        if client is not None and client.pilot != self.active_pilot:
            self.active_pilot = client.pilot
            self.configure_logs()
        try:
            while True:
                # The hotkey also reports which shortcut it registered, as a string.
                if self.hotkey.events.get_nowait() is None and visible:
                    self.toggle_lock()
        except Empty:
            pass
        try:
            while True:
                choice = self.tray.events.get_nowait()
                if choice == windows.TrayIcon.MOVE:
                    self.toggle_lock()
                elif choice == windows.TrayIcon.SETTINGS:
                    self.open_settings()
                elif choice == windows.TrayIcon.QUIT:
                    self.close()
                    return
        except Empty:
            pass
        result = self.logs.result
        if result and result[0] == self.logs.generation and result[1] != self.frame:
            self.frame = result[1]
            dirty = True
        status = self.notice or self.esi_status.error or self.frame.status
        if self.simulation is not None:
            status = f"Simulating a fight · {status}"
        self.tray.set_tooltip(f"EVE Pressure · {status}")
        if visible and not self.mapped:
            # Map the Tk widgets once. Later shows use Win32 without activating the overlay.
            self.root.state("normal")
            self.root.update_idletasks()
            self.apply_window_style(self.locked)
            self.mapped = True
        if visible and (dirty or not self.shown):
            self.render()
        if visible and not self.shown:
            windows.show_window(self.hwnd)
            self.shown = True
        self.hover()
        self.root.after(75, self.tick)

    def apply_window_style(self, locked):
        windows.overlay_style(self.hwnd, click_through=locked)
        self.interactive = False
        self.body.configure(cursor="" if locked else "fleur")

    def render(self):
        self.rendered = time.monotonic()
        self.ops.clear()
        self.targets.clear()
        moving = not self.locked
        metrics = self.frame.combat
        now = datetime.now(UTC)
        if moving:
            metrics = merged(metrics, placeholder_snapshot(now), now)

        def fresh(event):
            return moving or now - event.time < STALE

        dps_levels, neut_levels = ship_levels(self.esi_status.ship_group)
        self.settings_box = None
        y = 2
        turrets = [stream for stream in metrics.turrets if fresh(stream.latest)]
        for stream, label in zip(turrets, split_labels(turrets)):
            hits, last = stream.results, stream.latest
            tip = (
                f"{last.weapon}, {stream.dps:,.0f} DPS\nAverage of your last "
                f"{len(hits)} results on {entity_label(last.target)}\n"
                "100% is about what perfect tracking averages"
            )
            y = self.grade_row(y, last.weapon, average_quality(hits), tip, label)
        missiles = [stream for stream in metrics.missiles if fresh(stream.latest)]
        for stream, label in zip(missiles, split_labels(missiles)):
            missile = stream.latest
            idle = (now - missile.time).total_seconds()
            expected = max(MISSILE_OVERDUE_MIN, MISSILE_OVERDUE * (stream.gap or 5))
            overdue = self.locked and idle > expected
            y = self.missile_row(y, missile, stream.dps, idle if overdue else None, label)
        drones, drone_dps = metrics.drones, metrics.drone_dps
        enough = drone_dps > 0 and drone_dps >= self.settings.min_drone_dps
        if drones and fresh(drones[-1]) and (moving or enough):
            damage = defaultdict(float)
            for hit in drones:
                damage[hit.weapon] += hit.amount
            kinds = sorted(damage, key=damage.get, reverse=True)
            tip = (
                f"{', '.join(kinds)}, {drone_dps:,.0f} DPS\nAverage of your last "
                f"{len(drones)} results on {entity_label(drones[-1].target)}\n"
                "100% is about what perfect tracking averages"
            )
            y = self.grade_row(y, kinds[0], metrics.drone_quality, tip)
        if y > 2 and (metrics.pods or metrics.outgoing):
            y += METER_GAP
        if metrics.pods:
            count = len(metrics.pods)
            pod_dps = sum(row.rate for row in metrics.pods)
            total = f"{count} pod{'s' if count != 1 else ''}, {pod_dps:,.0f} DPS"
            tip = "Breacher pods still ticking\nDamage per second on each target"
            y = self.pressure(y, metrics.pods, total, TEXT, tip)
        if metrics.outgoing:
            tip = "Your neuts and nosferatus\nLatest GJ on each target, and whether it's dry"
            y = self.pressure(y, metrics.outgoing, None, TEXT, tip, value=self.drain_value)
        if y > 2 and (metrics.damage or metrics.incoming):
            # Centre the line between the last outgoing row and the first incoming one.
            line = self.px(y + (INCOMING_GAP - GAP) / 2)
            left = ICON_X - ICON_SIZE / 2
            right = left + (WIDTH - LABEL_ROOM) / 2
            self.rect("bars", self.x(left), line, self.x(right), line + 1, SEPARATOR)
            y += INCOMING_GAP
        if metrics.damage:
            rows = metrics.damage[:DAMAGE_SOURCE_LIMIT]
            total = f"{metrics.damage_rate:,.0f} DPS"
            level = self.level(metrics.damage_rate, dps_levels)
            y = self.pressure(y, rows, total, level, "Damage taken\nDPS from each pilot")
        if metrics.incoming:
            rows = metrics.incoming[:NEUT_SOURCE_LIMIT]
            total = f"{short(metrics.incoming_rate)} GJ/s"
            level = self.level(metrics.incoming_rate, neut_levels)
            tip = "Capacitor drained from you\nGJ/s from each pilot"
            y = self.pressure(y, rows, total, level, tip)
        if moving:
            self.text(ICON_X, y + ROW / 2, "⚙", TEXT, "symbol", "center")
            self.settings_box = self.square(y + ROW / 2)
            self.catch(self.settings_box, "Settings")
            y += ROW
        image = self.compose(max(1, self.px(y + 2)))
        self.body.configure(width=image.width, height=image.height)
        self.place()
        red, green, blue, alpha = image.convert("RGBa").split()
        pixels = Image.merge("RGBA", (blue, green, red, alpha)).tobytes()
        try:
            windows.draw_layered(self.hwnd, image.width, image.height, pixels)
        except OSError:
            self.notice = "Windows could not draw the overlay."

    def compose(self, height):
        width = self.px(WIDTH)
        layers = {name: Image.new("RGBA", (width, height)) for name in LAYERS}
        for name, paint in self.ops:
            paint(layers[name])
        image = Image.new("RGBA", (width, height))
        if not self.clear:
            ImageDraw.Draw(image).rectangle(
                (0, 0, width - 1, height - 1), fill=BACKDROP, outline=BORDER
            )
        image.alpha_composite(layers["bars"])
        image.alpha_composite(layers["icons"])
        if self.clear:
            alpha = layers["text"].getchannel("A")
            # A firm halo hugging the glyphs and a faint wide one, about 50% dark together.
            tight = alpha.filter(ImageFilter.MaxFilter(3))
            tight = tight.filter(ImageFilter.GaussianBlur(0.6 * self.scale))
            image.alpha_composite(self.halo(tight, 2, 90))
            soft = alpha.filter(ImageFilter.GaussianBlur(2 * self.scale))
            image.alpha_composite(self.halo(soft, 1.5, 56))
        image.alpha_composite(layers["text"])
        image.alpha_composite(layers["top"])
        return image

    @staticmethod
    def halo(alpha, gain, limit):
        halo = Image.new("RGBA", alpha.size, SHADOW)
        halo.putalpha(alpha.point(lambda value: min(limit, round(value * gain))))
        return halo

    @staticmethod
    def level(rate, levels):
        return DANGER if rate >= levels[1] else WARN if rate >= levels[0] else TEXT

    def meter_value(self, middle, value, color, label):
        """The value hugs its bar, and a split label follows it at the same gap."""
        x = BAR_RIGHT + VALUE_GAP
        self.text(x, middle, value, color, "number", "w")
        if label:
            x += self.fonts["number"].getlength(value) / self.scale + VALUE_GAP
            text = self.fit(label, self.px(WIDTH - x - 2), "small")
            self.text(x, middle, text, MUTED, "small", "w")

    def grade_row(self, y, item, quality, tip, label=None):
        middle = y + ROW / 2
        self.icon(item, middle, tip)
        band = sum(quality >= edge for edge in GRADE_EDGES)
        color = GRADE_COLORS[band]
        top, bottom = self.bar(middle, min(quality, GRADE_SCALE) / GRADE_SCALE, color)
        width = max(1, self.px(1))
        for edge in GRADE_EDGES:
            x = self.x(BAR_LEFT + (BAR_RIGHT - BAR_LEFT) * edge / GRADE_SCALE) - width // 2
            self.rect("bars", x, top, x + width, bottom, CUT)
        self.meter_value(middle, f"{quality:.0%}", color, label)
        return y + ROW

    def missile_row(self, y, missile, missile_dps, idle, label=None):
        middle = y + ROW / 2
        best = missile.reference.best
        kind = missile.damage_type
        tip = (
            f"{missile.weapon}, {missile_dps:,.0f} DPS\n{missile.amount:,.0f} of {best:,.0f} "
            f"best on {entity_label(missile.target)}"
        )
        if idle is not None:
            tip += f"\nNo volley has landed for {idle:.0f}s"
        self.icon(missile.weapon, middle, tip)
        color = DAMAGE_COLORS.get(kind, TEXT)
        marker_color = TEXT
        if idle is not None:
            color, marker_color = dimmed(color), MUTED
        top, bottom = self.bar(middle, missile.ratio, color)
        center = (top + bottom) / 2
        marker = BAR_LEFT + (BAR_RIGHT - BAR_LEFT) * missile.ratio
        width = self.px(2)
        x = self.x(marker) - width // 2
        self.rect("top", x, self.px(middle - 7), x + width, self.px(middle + 7), marker_color)
        latest = f"{missile.amount:,.0f}"
        if missile.ratio <= 0.75:
            self.text(marker + 4, middle, latest, marker_color, "tiny", "w", center=center)
        else:
            self.text(marker - 4, middle, latest, PANEL, "tiny", "e", center=center, layer="top")
        self.meter_value(middle, f"{best:,.0f}", color, label)
        return y + ROW

    @staticmethod
    def drain_value(row):
        """Your latest pulse on the target with its state, then all your modules' GJ/s."""
        rate = f" · {short(row.rate)}/s"
        if row.state in (None, DRAINING):
            return f"{row.drain:,.0f}{rate}", TEXT
        return f"{row.drain:,.0f} {row.state}{rate}", DRAIN_COLORS[row.state]

    def pressure(self, y, rows, total, color, tip, *, value=None):
        self.icon(rows[0].module, y + ROW / 2, tip)
        if total is not None:
            self.text(BAR_LEFT, y + ROW / 2, total, color, "number", "w")
            y += ROW
        else:
            y += (ROW - SOURCE_ROW) / 2
        for row in rows:
            text, value_color = value(row) if value else (short(row.rate), TEXT)
            self.source(y, row.entity, text, TEXT, value_color=value_color, ship=row.ship)
            y += SOURCE_ROW
            if row is rows[0] and row.weapon:
                # An elbow ties the weapon line to the pilot above it.
                left, right = self.x(BAR_LEFT + 4), self.x(BAR_LEFT + 10)
                top, middle = self.px(y - 2), self.px(y + SOURCE_ROW / 2)
                elbow = [(left, top), (left, middle), (right, middle)]
                self.ops.append(
                    ("text", lambda image, elbow=elbow: ImageDraw.Draw(image).line(elbow, MUTED))
                )
                self.source(y, row.weapon, short(row.weapon_rate), MUTED, indent=14)
                y += SOURCE_ROW
        return y + GAP

    def source(self, y, name, value, color, *, indent=0, value_color=None, ship=""):
        """A name, its ship in grey, and its value right after, starting next to the icon."""
        middle, gap = y + SOURCE_ROW / 2, 6
        measure = self.fonts["small"].getlength
        value_width = self.fonts["value"].getlength(value) / self.scale
        room = self.px(VALUE_EDGE - BAR_LEFT - indent - gap - value_width)
        # The pilot is what gets called, so the ship gives way first.
        ship_room = room - measure(name) - self.px(gap)
        if ship and ship_room >= self.px(SHIP_MIN):
            ship = self.fit(ship, ship_room, "small")
        else:
            ship, name = "", self.fit(name, room, "small")
        name_width = measure(name) / self.scale
        ship_width = measure(ship) / self.scale + gap if ship else 0
        start = BAR_LEFT + indent
        if self.settings.meters_from_right:
            # Right-aligned text still reads left to right, so the value sits by the icon.
            value_x = start
            ship_x = start + value_width + gap
            name_x = ship_x + ship_width
        else:
            name_x, value_x = start, start + name_width + ship_width + gap
            ship_x = start + name_width + gap
        self.text(name_x, middle, name, color, "small", "w")
        if ship:
            self.text(ship_x, middle, ship, MUTED, "small", "w")
        self.text(value_x, middle, value, value_color or color, "value", "w")

    def x(self, value):
        return self.px(WIDTH - value if self.settings.meters_from_right else value)

    def square(self, middle):
        size = self.px(ICON_SIZE)
        left, top = self.x(ICON_X) - size // 2, self.px(middle) - size // 2
        return left, top, left + size, top + size

    def catch(self, box, tip):
        """Hover and click the whole square, including the clear parts of its icon."""
        self.targets.append((box, tip))
        self.ops.append(("icons", lambda image: image.paste(CATCH, box)))

    def icon(self, item, middle, tip):
        image = self.image(item)
        if image is not None:
            box = self.square(middle)
            self.catch(box, tip)
            self.ops.append(("icons", lambda layer: paste(layer, image, *box[:2])))

    def image(self, item):
        if not item:
            return None
        key = item.casefold()
        if key not in self.images:
            path = self.icons.path(item)
            if path is None:
                return None
            size = self.px(ICON_SIZE)
            try:
                with Image.open(path) as source:
                    icon = source.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
            except OSError:
                return None
            self.images[key] = icon
        return self.images[key]

    def text(self, x, middle, value, color, style, anchor, *, center=None, layer="text"):
        if not value:
            return
        if self.settings.meters_from_right:
            anchor = MIRRORED.get(anchor, anchor)
        center = self.scale * middle if center is None else center
        point = self.x(x), round(center + self.half_digit[style])
        typeface, anchor = self.fonts[style], ANCHORS[anchor]
        self.ops.append(
            (layer, lambda image: paint_text(image, point, value, color, typeface, anchor))
        )

    def rect(self, layer, x0, top, x1, bottom, fill):
        left, right = sorted((x0, x1))
        if right > left and bottom > top:
            box = left, top, right - 1, bottom - 1
            self.ops.append((layer, lambda image: ImageDraw.Draw(image).rectangle(box, fill)))

    def bar(self, middle, fraction, color):
        zero, cap = self.x(BAR_LEFT), self.x(BAR_RIGHT)
        top, bottom = self.px(middle - BAR_HEIGHT / 2), self.px(middle + BAR_HEIGHT / 2)
        self.rect("bars", zero, top, cap, bottom, TRACK)
        if fraction > 0:
            end = round(zero + (cap - zero) * min(1.0, fraction))
            self.rect("bars", zero, top, end, bottom, color)
        return top, bottom

    def hover(self):
        """Catch the mouse only over icons and the gear, and show their tooltips."""
        x, y = windows.cursor_position()
        left, top = windows.position(self.hwnd)
        hits = [tip for box, tip in self.targets if inside(box, x - left, y - top)]
        tip = hits[-1] if hits and self.shown else None
        interactive = tip is not None
        if self.locked and interactive != self.interactive:
            try:
                windows.overlay_style(self.hwnd, click_through=not interactive)
            except OSError:
                return
            self.interactive = interactive
        if tip is None:
            self.hide_tip()
            return
        if self.tip_window is None:
            self.tip_window = tk.Toplevel(self.root)
            self.tip_window.overrideredirect(True)
            self.tip_window.attributes("-topmost", True)
            self.tip_label = tk.Label(
                self.tip_window,
                bg=PANEL,
                fg=TEXT,
                font=self.tip_font,
                padx=6,
                pady=3,
                justify="left",
            )
            self.tip_label.pack()
        self.tip_label.configure(text=tip)
        self.tip_window.update_idletasks()
        width = self.tip_window.winfo_reqwidth()
        x, y = windows.cursor_position()
        x = x - width - 14 if self.settings.meters_from_right else x + 14
        self.tip_window.geometry(f"+{x}+{y + 14}")
        self.tip_window.deiconify()

    def hide_tip(self, _event=None):
        if self.tip_window is not None:
            self.tip_window.withdraw()

    def fit(self, value, width, style):
        measure = self.fonts[style].getlength
        if measure(value) <= width:
            return value
        while value and measure(value + "…") > width:
            value = value[:-1]
        return value + "…"

    def place(self):
        self.root.update_idletasks()
        width, height = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        if self.client is not None:
            left, top, right, bottom = self.client.bounds
            if self.settings.offset is None:
                saved_x, saved_y = self.settings.position
                self.settings.offset = (
                    (saved_x - left, saved_y - top)
                    if left <= saved_x < right and top <= saved_y < bottom
                    else (40, 70)
                )
            x = left + self.settings.offset[0]
            y = top + self.settings.offset[1]
        else:
            x, y = self.settings.position
            work = windows.work_area(x, y, width, height)
            left, top, right, bottom = work.left, work.top, work.right, work.bottom
        if not self.drag:
            target = max(left, min(x, right - width)), max(top, min(y, bottom - height))
            if target != windows.position(self.hwnd):
                windows.move_window(self.hwnd, *target)

    def start_drag(self, event):
        if inside(self.settings_box, event.x, event.y):
            self.open_settings()
            return
        if self.locked:
            return
        x, y = windows.position(self.hwnd)
        self.drag = event.x_root - x, event.y_root - y

    def context_menu(self, event):
        if inside(self.settings_box, event.x, event.y):
            self.tray.show_menu()

    def move(self, event):
        if self.drag:
            windows.move_window(self.hwnd, event.x_root - self.drag[0], event.y_root - self.drag[1])

    def finish_drag(self, _event):
        if not self.drag:
            return
        self.drag = None
        self.record_position()
        self.place()
        self.record_position()
        self.save()

    def record_position(self):
        x, y = windows.position(self.hwnd)
        if self.client is not None:
            self.settings.offset = (x - self.client.bounds[0], y - self.client.bounds[1])
        else:
            self.settings.position = x, y

    def toggle_lock(self):
        locked = not self.locked
        try:
            self.apply_window_style(locked)
        except OSError:
            self.notice = "Windows could not change the overlay appearance."
            return
        self.locked = locked
        self.tray.moving = not locked
        self.drag = None
        self.save()
        self.render()

    def open_settings(self):
        if self.dialog is not None:
            self.dialog.lift()
            return
        dialog = self.dialog = tk.Toplevel(self.root)
        dialog.title("EVE Pressure Settings")
        dialog.resizable(False, False)
        dialog.protocol("WM_DELETE_WINDOW", self.close_settings)
        panel = ttk.Frame(dialog, padding=16)
        panel.pack(fill="both", expand=True)
        pilots = list(self.frame.pilots)
        if self.settings.pilot and self.settings.pilot not in pilots:
            pilots.append(self.settings.pilot)
        pilot = tk.StringVar(dialog, self.settings.pilot or AUTO)
        folder = tk.StringVar(dialog, self.settings.folder)
        drone_dps = tk.StringVar(dialog, str(self.settings.min_drone_dps))
        hide_docked = tk.BooleanVar(dialog, self.settings.hide_docked)
        from_right = tk.BooleanVar(dialog, self.settings.meters_from_right)
        move_backdrop = tk.BooleanVar(dialog, self.settings.move_backdrop)
        simulate = tk.BooleanVar(dialog, self.simulation is not None)
        start_menu = tk.BooleanVar(dialog, self.settings.start_menu_shortcut)
        desktop = tk.BooleanVar(dialog, self.settings.desktop_shortcut)
        linked = tk.StringVar(dialog)
        message = tk.StringVar(dialog, self.esi.message)

        ttk.Label(panel, text="Character").grid(row=0, column=0, sticky="w")
        ttk.Combobox(panel, state="readonly", values=(AUTO, *pilots), textvariable=pilot).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(3, 12)
        )
        ttk.Label(panel, text="EVE game log folder").grid(row=2, column=0, sticky="w")
        ttk.Entry(panel, textvariable=folder, width=48).grid(
            row=3, column=0, sticky="ew", pady=(3, 12)
        )
        ttk.Checkbutton(
            panel, text="Meters fill from the right (icons on the right)", variable=from_right
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 12))
        ttk.Checkbutton(panel, text="Dark backdrop in Move mode", variable=move_backdrop).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(0, 12)
        )
        ttk.Label(panel, text="Minimum drone DPS").grid(row=6, column=0, sticky="w")
        ttk.Spinbox(panel, from_=0, to=5000, increment=25, textvariable=drone_dps, width=8).grid(
            row=7, column=0, sticky="w", pady=(3, 12)
        )
        ttk.Checkbutton(panel, text="Hide while docked", variable=hide_docked).grid(
            row=10, column=0, sticky="w", pady=(0, 6)
        )
        characters = ttk.Combobox(
            panel, state="readonly", values=self.esi.linked, textvariable=linked
        )
        characters.grid(row=11, column=0, sticky="ew", pady=(0, 6))
        self.esi_controls = message, characters

        def link():
            self.esi.link()

        def unlink():
            if linked.get():
                self.esi.unlink(linked.get())
                linked.set("")

        ttk.Button(panel, text="Unlink", command=unlink).grid(
            row=11, column=1, padx=(6, 0), pady=(0, 6)
        )
        ttk.Button(panel, text="Link character…", command=link).grid(row=12, column=0, sticky="w")
        ttk.Label(panel, textvariable=message, wraplength=350).grid(
            row=13, column=0, columnspan=2, sticky="w", pady=(3, 12)
        )

        def browse():
            chosen = filedialog.askdirectory(parent=dialog, initialdir=folder.get(), mustexist=True)
            if chosen:
                folder.set(chosen)

        def apply():
            self.settings.pilot = "" if pilot.get() == AUTO else pilot.get()
            self.settings.folder = folder.get().strip() or str(windows.game_log_folder())
            try:
                self.settings.min_drone_dps = max(0, int(float(drone_dps.get())))
            except ValueError:
                pass
            self.settings.hide_docked = hide_docked.get()
            self.settings.meters_from_right = from_right.get()
            self.settings.move_backdrop = move_backdrop.get()
            self.settings.start_menu_shortcut = start_menu.get()
            self.settings.desktop_shortcut = desktop.get()
            self.sync_shortcuts()
            self.simulate(simulate.get())
            self.apply_window_style(self.locked)
            self.configure_logs()
            self.save()
            self.close_settings()
            self.render()

        ttk.Button(panel, text="Browse…", command=browse).grid(
            row=3, column=1, padx=(6, 0), pady=(3, 12)
        )
        shortcuts = ttk.Frame(panel)
        shortcuts.grid(row=14, column=0, columnspan=2, sticky="w", pady=(0, 12))
        ttk.Checkbutton(shortcuts, text="Start menu shortcut", variable=start_menu).pack(
            side="left", padx=(0, 12)
        )
        ttk.Checkbutton(shortcuts, text="Desktop shortcut", variable=desktop).pack(side="left")
        ttk.Checkbutton(
            panel, text="Simulate a fight for testing (until you quit)", variable=simulate
        ).grid(row=15, column=0, columnspan=2, sticky="w", pady=(0, 12))
        ttk.Button(panel, text="Apply", command=apply).grid(row=16, column=1, sticky="e")
        dialog.bind("<Escape>", lambda _event: self.close_settings())
        dialog.wait_visibility()
        dialog.focus_force()

    def sync_shortcuts(self):
        """Point the shortcuts at this copy and Python, rewriting them only when either moves."""
        python = Path(sys.executable).with_name("pythonw.exe")
        if not python.exists():
            python = Path(sys.executable)
        icon = self.settings_path.parent / ICON_FILE
        target = f"{python};{APP_FOLDER}"
        moved = self.settings.shortcut_target != target
        for place, wanted in (
            (windows.START_MENU, self.settings.start_menu_shortcut),
            (windows.DESKTOP, self.settings.desktop_shortcut),
        ):
            link = windows.shell_folder(place, Path.home()) / f"{APP_NAME}.lnk"
            try:
                if not wanted:
                    link.unlink(missing_ok=True)
                elif moved or not link.exists():
                    if not icon.exists():
                        write_icon(icon)
                    windows.write_shortcut(link, python, "-m pressure_overlay", APP_FOLDER, icon)
            except (OSError, subprocess.SubprocessError):
                return
        self.settings.shortcut_target = target
        self.save()

    def close_settings(self):
        self.esi_controls = None
        self.dialog.destroy()
        self.dialog = None

    def simulate(self, enabled):
        if enabled == (self.simulation is not None):
            return
        if enabled:
            pilot = self.settings.pilot or self.active_pilot or "Simulated Pilot"
            folder = self.settings_path.parent / "simulation"
            try:
                self.simulation = Simulation(folder, pilot)
            except OSError:
                self.notice = "The simulated fight could not be started."
        else:
            self.simulation.stop()
            self.simulation = None

    def save(self):
        self.settings.locked = self.locked
        try:
            self.settings.save(self.settings_path)
        except OSError:
            self.notice = "Settings could not be saved."
        else:
            self.notice = ""

    def close(self):
        self.save()
        self.simulate(False)
        self.logs.stopped.set()
        self.esi.stopped.set()
        self.esi.wake.set()
        self.hotkey.close()
        self.tray.close()
        self.root.destroy()
