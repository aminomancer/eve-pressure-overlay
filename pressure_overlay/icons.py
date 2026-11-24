import json
import re
import urllib.request
from queue import SimpleQueue
from threading import Thread

from .esi import COMPATIBILITY_DATE, ESI_URL, USER_AGENT

IMAGE_URL = "https://images.evetech.net/types/{}/icon?size=32"
# The dogma attribute holding a module's cycle time, in milliseconds.
DURATION = 73
ABYSSAL = re.compile(r"\babyssal\s+", re.I)


class IconCache:
    """Item icons from CCP's image server and module cycle times from ESI, by item name, kept
    on disk."""

    def __init__(self, folder):
        self.folder = folder
        self.index = folder / "index.json"
        try:
            self.ids = json.loads(self.index.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.ids = {}
        self.cycles_file = folder / "cycles.json"
        try:
            self.cycles = json.loads(self.cycles_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cycles = {}
        self.requested = set()
        self.cycles_requested = set()
        self.queue = SimpleQueue()
        Thread(target=self.run, daemon=True).start()

    def path(self, name):
        """The icon file for an item name, or None while it downloads or if there's none."""
        key = name.casefold()
        type_id = self.ids.get(key)
        if type_id:
            path = self.folder / f"{type_id}.png"
            if path.exists():
                return path
        if name and type_id != 0 and key not in self.requested:
            self.requested.add(key)
            self.queue.put((self.fetch_icon, name))
        return None

    def cycle(self, name):
        """A module's base cycle in seconds, or None until it's known. Safe from any thread."""
        key = name.casefold()
        seconds = self.cycles.get(key)
        if seconds is None and name and key not in self.cycles_requested:
            self.cycles_requested.add(key)
            self.queue.put((self.fetch_cycle, name))
        return seconds or None

    def type_id(self, name):
        key = name.casefold()
        if key not in self.ids:
            self.ids[key] = self.lookup(name)
            self.index.write_text(json.dumps(self.ids), encoding="utf-8")
        return self.ids[key]

    def lookup(self, name):
        request = urllib.request.Request(
            f"{ESI_URL}/universe/ids",
            json.dumps([name]).encode(),
            {
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
                "X-Compatibility-Date": COMPATIBILITY_DATE,
            },
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            types = json.load(response).get("inventory_types", [])
        return next((int(t["id"]) for t in types if t["name"].casefold() == name.casefold()), 0)

    def duration(self, name):
        """Seconds per cycle from the item's type, 0 if it has none."""
        type_id = self.type_id(name)
        if type_id:
            request = urllib.request.Request(
                f"{ESI_URL}/universe/types/{type_id}",
                headers={"User-Agent": USER_AGENT, "X-Compatibility-Date": COMPATIBILITY_DATE},
            )
            with urllib.request.urlopen(request, timeout=15) as response:
                attributes = json.load(response).get("dogma_attributes", [])
            for attribute in attributes:
                if attribute["attribute_id"] == DURATION:
                    return attribute["value"] / 1000
        # Abyssal modules roll their own stats, so the type has none; use the same size's T2.
        if ABYSSAL.search(name):
            return self.duration(f"{ABYSSAL.sub('', name)} II")
        return 0

    def fetch_icon(self, name):
        type_id = self.type_id(name)
        if type_id:
            self.download(type_id)

    def fetch_cycle(self, name):
        self.cycles[name.casefold()] = self.duration(name)
        self.cycles_file.write_text(json.dumps(self.cycles), encoding="utf-8")

    def download(self, type_id):
        path = self.folder / f"{type_id}.png"
        if path.exists():
            return
        request = urllib.request.Request(
            IMAGE_URL.format(type_id), headers={"User-Agent": USER_AGENT}
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            data = response.read()
        temp = path.with_suffix(".tmp")
        temp.write_bytes(data)
        temp.replace(path)

    def run(self):
        while True:
            fetch, name = self.queue.get()
            try:
                self.folder.mkdir(parents=True, exist_ok=True)
                fetch(name)
            except (OSError, ValueError, KeyError, TypeError):
                # Offline or an unknown name; try again next launch.
                continue
