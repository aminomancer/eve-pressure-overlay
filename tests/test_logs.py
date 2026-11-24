import os
import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from test_combat import NOW, line

from pressure_overlay.combat import average_quality
from pressure_overlay.logs import LogMonitor


def header(pilot, started):
    return f"  Gamelog\r\n  Listener: {pilot}\r\n  Session Started: {started:%Y.%m.%d %H:%M:%S}\r\n"


class LogMonitorTest(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.monitor = LogMonitor(self.folder)

    def test_live_tailing_handles_split_utf16_line_without_double_counting(self):
        file = self.folder / "eve.txt"
        file.write_bytes(header("Pilot One", NOW).encode("utf-16"))
        self.assertEqual(self.monitor.poll(NOW).pilot, "Pilot One")
        fresh = (line("300 GJ energy neutralized from Éowyn - Heavy Neutralizer") + "\r\n").encode(
            "utf-16-le"
        )
        with file.open("ab") as stream:
            stream.write(fresh[:-1])
        self.assertEqual(self.monitor.poll(NOW).combat.incoming_total, 0)
        with file.open("ab") as stream:
            stream.write(fresh[-1:])
        frame = self.monitor.poll(NOW)
        self.assertEqual(frame.combat.incoming[0].entity, "Éowyn")
        self.assertEqual(self.monitor.poll(NOW).combat.incoming_total, 300)
        self.assertEqual(self.monitor.poll(NOW + timedelta(seconds=60)).combat.incoming_total, 0)

    def test_character_selection_and_live_log_rotation(self):
        old = self.folder / "old.txt"
        old.write_text(
            header("First", NOW - timedelta(hours=1))
            + line("300 GJ energy neutralized from Rook - Heavy Neutralizer", 20)
            + "\n",
            encoding="utf-8",
        )
        os.utime(old, (NOW.timestamp(), NOW.timestamp()))
        other = self.folder / "other.txt"
        other.write_text(
            header("Second", NOW - timedelta(seconds=30))
            + line("120 GJ energy neutralized from Aster - Heavy Neutralizer", 10)
            + "\n",
            encoding="utf-8",
        )
        os.utime(other, (NOW.timestamp(), NOW.timestamp()))
        self.assertEqual(self.monitor.poll(NOW).pilot, "Second")
        self.assertEqual(self.monitor.poll(NOW, "First").combat.incoming_total, 300)

        current = self.folder / "new.txt"
        current.write_text(
            header("First", NOW)
            + line("180 GJ energy neutralized from Rook - Heavy Neutralizer")
            + "\n",
            encoding="utf-8",
        )
        frame = self.monitor.poll(NOW, refresh=True)
        self.assertEqual(frame.pilots, ("First", "Second"))
        self.assertEqual(frame.pilot, "First")
        self.assertEqual(frame.combat.incoming_total, 480)
        self.assertEqual(self.monitor.poll(NOW, "Second").combat.incoming_total, 120)

    def test_hit_quality_survives_idle_time_and_resets_with_new_session(self):
        previous = self.folder / "old.txt"
        previous.write_text(
            header("Pilot One", NOW - timedelta(hours=1))
            + line("300 to Rook - 425mm AutoCannon II - Wrecks", 90)
            + "\n",
            encoding="utf-8",
        )
        os.utime(previous, (NOW.timestamp(), NOW.timestamp()))

        def quality(combat):
            return average_quality(combat.turrets[0].results) if combat.turrets else None

        self.assertEqual(quality(self.monitor.poll(NOW, "pilot one").combat), 3.0)
        self.assertEqual(quality(self.monitor.poll(NOW + timedelta(hours=1)).combat), 3.0)

        current = self.folder / "new.txt"
        current.write_text(header("Pilot One", NOW), encoding="utf-8")
        self.assertIsNone(quality(self.monitor.poll(NOW, refresh=True).combat))

    def test_missile_reference_resets_on_ship_change_and_dock(self):
        session = self.folder / "missiles.txt"
        lines = [line("400 to Rook - Scourge Heavy Missile - Hits", 200)]
        lines += [line("100 to Rook - Scourge Heavy Missile - Hits", 20 - i) for i in range(12)]
        session.write_text(header("Pilot One", NOW) + "\n".join(lines) + "\n", encoding="utf-8")
        combat = self.monitor.poll(NOW, refresh=True, context=(1, 0)).combat
        self.assertEqual(combat.missiles[0].latest.ratio, 0.25)

        def fire(amount, context):
            with session.open("a", encoding="utf-8") as stream:
                stream.write(line(f"{amount} to Rook - Scourge Heavy Missile - Hits") + "\n")
            return self.monitor.poll(NOW, context=context).combat.missiles[0].latest

        self.assertEqual(fire(50, (1, 0)).ratio, 0.125)
        self.assertEqual(fire(60, (None, 0)).ratio, 0.15)
        self.assertEqual(self.monitor.poll(NOW, context=(2, 0)).combat.missiles, ())
        self.assertEqual(fire(60, (2, 0)).ratio, 1.0)
        self.assertEqual(self.monitor.poll(NOW, context=(2, 1)).combat.missiles, ())

    def test_drones_keep_recent_window_for_dps(self):
        session = self.folder / "drones.txt"
        lines = [line("120 to Rook - Hammerhead II - Hits", 70 - i) for i in range(5)]
        lines += [line("60 to Rook - Hammerhead II - Smashes", 30 - i) for i in range(15)]
        lines += [line("60 to Aster - Hammerhead II - Grazes", 10 - i) for i in range(5)]
        session.write_text(header("Pilot One", NOW) + "\n".join(lines) + "\n", encoding="utf-8")
        combat = self.monitor.poll(NOW, refresh=True).combat
        # 1,200 damage since the first hit inside the window, 30 seconds ago.
        self.assertEqual(combat.drone_dps, 40)
        self.assertEqual([hit.grade for hit in combat.drones], ["grazes"] * 5)
