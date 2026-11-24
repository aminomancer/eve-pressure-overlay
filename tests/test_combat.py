import unittest
from datetime import UTC, datetime, timedelta

from pressure_overlay.combat import (
    DRAINING,
    DRY,
    MISSILE_GAP,
    PINNED,
    RECOVERED,
    Damage,
    DroneHit,
    Hit,
    MissileHit,
    MissileReferences,
    Neut,
    PodHit,
    average_quality,
    drain_state,
    parse_combat,
    snapshot,
    split_labels,
    weapon_class,
)
from pressure_overlay.logs import placeholder_snapshot

NOW = datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)


def line(text, seconds_ago=0):
    stamp = NOW - timedelta(seconds=seconds_ago)
    return f"[ {stamp:%Y.%m.%d %H:%M:%S} ] (combat) {text}"


class CombatTest(unittest.TestCase):
    def test_neut_direction_is_preserved_from_amount_color(self):
        module = "<color=0x77ffffff><font size=10> - Heavy Energy Neutralizer II</font>"
        sent = "<color=0xff7fffff><b>0 GJ</b><color=0x77ffffff> energy neutralized <b>Rook</b>"
        taken = "<color=0xffe57f7f>1,260.5 GJ<color=0x77ffffff> energy neutralized "
        taken += "<color=0xffffffff>Aster Vale [TICK]&lt;ALLY&gt;(Bhaalgorn)"
        heavy = "Heavy Energy Neutralizer II"
        self.assertEqual(parse_combat(line(sent + module)), Neut(NOW, "Rook", 0, False, heavy))
        self.assertEqual(
            parse_combat(line(taken + module)), Neut(NOW, "Aster Vale", 1260.5, True, heavy)
        )
        self.assertEqual(
            parse_combat(line("30 GJ energy neutralized from Red - Heavy Neutralizer")),
            Neut(NOW, "Red", 30, True, "Heavy Neutralizer"),
        )
        self.assertIsNone(parse_combat(line("30 GJ energy neutralized Red - Heavy Neutralizer")))

    def test_nosferatu_drains_count_as_capacitor_pressure(self):
        nos = "Corpum C-Type Medium Energy Nosferatu"
        drained = (
            "<color=0xffe57f7f><b>-63 GJ</b><color=0x77ffffff><font size=10> energy drained to "
            "</font><b><color=0xffffffff><font size=10><color=0xFFFFFFFF>Vel4n Dusk</color>"
            "</font> <font size=12><color=0xFF40FF70><b>Akoman</b></color></font></b>"
            f"<color=0x77ffffff><font size=10> - {nos}</font>"
        )
        self.assertEqual(
            parse_combat(line(drained)), Neut(NOW, "Vel4n Dusk", 63, True, nos, "Akoman")
        )
        mine = "<color=0xff7fffff><b>+12 GJ</b> energy drained from Cyclone"
        mine += " - Small Energy Nosferatu II"
        self.assertEqual(
            parse_combat(line(mine)), Neut(NOW, "Cyclone", 12, False, "Small Energy Nosferatu II")
        )
        self.assertEqual(
            parse_combat(line("-30 GJ energy drained to Red - Energy Nosferatu II")),
            Neut(NOW, "Red", 30, True, "Energy Nosferatu II"),
        )

    def test_hit_quality_counts_outgoing_turrets_only(self):
        self.assertEqual(
            parse_combat(line("60 to Rook[TICK](Sabre) - 425mm AutoCannon II - Smashes")),
            Hit(NOW, "smashes", "Rook[TICK](Sabre)", "425mm AutoCannon II", 60),
        )
        miss = "Your group of Mega Anode Particle Stream I misses Rook completely - Mega Anode"
        self.assertEqual(
            parse_combat(line(miss)), Hit(NOW, "misses", "Rook", "Mega Anode Particle Stream I")
        )
        for ignored in (
            "5 to Rook - Small EMP Smartbomb II - Hits",
            "Your Heavy Assault Missile Launcher II misses Rook completely",
        ):
            self.assertIsNone(parse_combat(line(ignored)))

    def test_turret_meter_follows_the_current_pilot_and_ship(self):
        def hit(seconds, grade, target):
            return Hit(NOW - timedelta(seconds=seconds), grade, target)

        events = [
            hit(9, "wrecks", "Rook[TICK](Raven)"),
            hit(8, "hits", "Aster[TICK](Sabre)"),
            hit(7, "smashes", "Rook[TICK](Capsule)"),
            hit(6, "misses", "Rook"),
        ]
        result = snapshot(events, NOW)
        [stream] = result.turrets
        self.assertEqual([h.grade for h in stream.results], ["smashes", "misses"])
        self.assertAlmostEqual(average_quality(stream.results), 1.37 / 2)
        # Some overview settings log "Pilot Ship" with no brackets; misses still name the pilot.
        spaced = [*events, hit(5, "hits", "Vel4n Dusk Akoman"), hit(4, "misses", "Vel4n Dusk")]
        [stream] = snapshot(spaced, NOW).turrets
        self.assertEqual([h.grade for h in stream.results], ["hits", "misses"])

    def test_split_fire_gets_a_second_meter_but_a_target_switch_does_not(self):
        def hit(seconds, target):
            return Hit(NOW - timedelta(seconds=seconds), "hits", target, "425mm AutoCannon II")

        switched = [hit(s, "Rook") for s in (30, 25, 20)] + [hit(s, "Aster") for s in (15, 10)]
        self.assertEqual([s.latest.target for s in snapshot(switched, NOW).turrets], ["Aster"])
        split = [hit(s, "Rook") for s in (12, 8, 4)] + [hit(s, "Aster") for s in (10, 6, 2)]
        # Both keep firing, so both show, the one started on most recently first.
        streams = snapshot(split, NOW).turrets
        self.assertEqual([s.latest.target for s in streams], ["Aster", "Rook"])
        third = [*split, *(hit(s, "Third") for s in (9, 5, 1))]
        self.assertEqual(len(snapshot(third, NOW).turrets), 2)

    def test_split_meters_are_labelled_by_ship_then_pilot(self):
        def hit(seconds, target, weapon="425mm AutoCannon II"):
            return Hit(NOW - timedelta(seconds=seconds), "hits", target, weapon)

        split = [hit(s, "Rook[A](Sabre)") for s in (9, 5)] + [
            hit(s, "Aster[B](Cerberus)") for s in (7, 3)
        ]
        self.assertEqual(split_labels(snapshot(split, NOW).turrets), ["Cerberus", "Sabre"])
        same = [hit(s, "Rook[A](Sabre)") for s in (9, 5)] + [
            hit(s, "Aster[B](Sabre)") for s in (7, 3)
        ]
        self.assertEqual(split_labels(snapshot(same, NOW).turrets), ["Aster", "Rook"])
        # Two weapons on one target are told apart by their icon and colour, not a label.
        dual = "Dual 180mm AutoCannon II"
        one = [hit(s, "Rook[A](Sabre)") for s in (9, 5)] + [
            hit(s, "Rook[A](Sabre)", dual) for s in (7, 3)
        ]
        self.assertEqual(split_labels(snapshot(one, NOW).turrets), [None, None])

    def test_drone_hits_keep_grade_and_damage(self):
        self.assertEqual(
            parse_combat(line("60 to Rook - Acolyte II - Wrecks")),
            DroneHit(NOW, "wrecks", "Acolyte II", 60, "Rook"),
        )
        miss = "Your Mosquito EV-600-I misses Rook completely - Mosquito EV-600-I"
        self.assertEqual(
            parse_combat(line(miss)), DroneHit(NOW, "misses", "Mosquito EV-600-I", 0, "Rook")
        )
        structure = "QX-1AB » ZK-7YT - Z11 - Quiet Harbor[-HRBR](Ansiblex Jump Bridge)"
        self.assertEqual(
            parse_combat(line(f"40 to {structure} - Vespa II - Grazes")),
            DroneHit(NOW, "grazes", "Vespa II", 40, structure),
        )

    def test_drone_dps_and_quality(self):
        events = [DroneHit(NOW - timedelta(seconds=61), "wrecks", "Ogre II", 6000, "Rook")]
        events += [
            DroneHit(NOW - timedelta(seconds=i), "hits", "Ogre II", 60, "Rook") for i in range(10)
        ]
        result = snapshot(events, NOW)
        # 600 damage over the ten seconds since the first of these hits.
        self.assertEqual(result.drone_dps, 60)
        self.assertAlmostEqual(result.drone_quality, (3 + 10 * 0.875) / 11)
        self.assertIsNone(snapshot([], NOW).drone_quality)

    def test_turret_and_missile_dps_average_from_first_shot(self):
        events = [
            Hit(NOW - timedelta(seconds=i), "hits", "Rook", "425mm AutoCannon II", 100)
            for i in range(5)
        ]
        events += [
            MissileHit(NOW - timedelta(seconds=2 * i), "Rook", "Scourge Heavy Missile", 300)
            for i in range(3)
        ]
        result = snapshot(events, NOW)
        # Five one-second volleys, and three missile volleys two seconds apart.
        self.assertEqual(result.turrets[0].dps, 100)
        self.assertEqual(result.missiles[0].dps, 150)
        self.assertEqual(result.missiles[0].gap, 2)
        self.assertIsNone(snapshot(events[-1:], NOW).missiles[0].gap)

    def test_drain_states_follow_each_pulse(self):
        neut, nos = "Medium Abyssal Energy Neutralizer", "Small Energy Nosferatu II"

        def pulse(seconds, amount, module=neut):
            return Neut(NOW - timedelta(seconds=seconds), "Vexor", amount, False, module)

        # Two staggered neuts, then one comes back empty and the target is dry, for every zero.
        pulses = [pulse(40, 332), pulse(37, 336)]
        self.assertEqual(drain_state(pulses), (DRAINING, 336))
        pulses.append(pulse(34, 0))
        self.assertEqual(drain_state(pulses), (DRY, 0))
        pulses.append(pulse(31, 0))
        self.assertEqual(drain_state(pulses), (DRY, 0))
        pulses.append(pulse(28, 28))
        self.assertEqual(drain_state(pulses), (PINNED, 28))
        pulses.append(pulse(25, 0))
        self.assertEqual(drain_state(pulses)[0], DRY)
        # Once low, anything under half keeps it pinned.
        pulses.append(pulse(22, 150))
        self.assertEqual(drain_state(pulses), (PINNED, 150))
        pulses.append(pulse(19, 330))
        self.assertEqual(drain_state(pulses), (RECOVERED, 330))
        self.assertEqual(snapshot(pulses, NOW).outgoing[0].state, RECOVERED)
        # It stays recovered until a full pulse lands 1.5 cycles (18s by default) after that.
        pulses += [pulse(10, 330), pulse(4, 336)]
        self.assertEqual(drain_state(pulses)[0], RECOVERED)
        pulses.append(pulse(0, 330))
        self.assertEqual(drain_state(pulses)[0], DRAINING)
        # A fifth of the last full pulse or less means there was little cap left to take.
        self.assertEqual(drain_state([pulse(20, 332), pulse(8, 60)]), (PINNED, 60))
        # Falloff or a resist change can weaken a pulse without the target running dry.
        self.assertEqual(drain_state([pulse(20, 336), pulse(8, 124)])[0], DRAINING)
        halved = [pulse(30, 400), pulse(20, 200), pulse(10, 400)]
        self.assertEqual(drain_state(halved), (DRAINING, 400))
        # A module's first pulse on a low target can't be judged against its own full pulse.
        small = "Small Energy Neutralizer II"
        self.assertEqual(drain_state([pulse(30, 300), pulse(20, 0), pulse(10, 40, small)])[0], DRY)
        # A nos finding their cap below yours says nothing while a neut is working on them.
        self.assertEqual(drain_state([pulse(10, 300), pulse(8, 0, nos)])[0], DRAINING)
        self.assertEqual(drain_state([pulse(10, 0, nos)])[0], DRY)

    def test_drain_state_clears_when_modules_stop(self):
        neut = "Medium Energy Neutralizer II"
        pulses = [Neut(NOW - timedelta(seconds=30), "Vexor", 0, False, neut)]
        # Burnt out or out of range: nothing for two cycles (12s each by default).
        self.assertEqual(drain_state(pulses, NOW), (None, 0))
        self.assertEqual(drain_state(pulses, NOW - timedelta(seconds=10)), (DRY, 0))
        # A slower module keeps its state longer.
        self.assertEqual(drain_state(pulses, NOW, lambda module: 24)[0], DRY)
        self.assertIsNone(snapshot(pulses, NOW).outgoing[0].state)

    def test_neut_targets_list_newest_engagement_first(self):
        def pulses(target, *seconds):
            neut = "Medium Energy Neutralizer II"
            return [Neut(NOW - timedelta(seconds=s), target, 300, False, neut) for s in seconds]

        events = pulses("Long", 48, 36, 24, 12, 0) + pulses("Fresh", 20, 8)
        # A target left alone for over a minute starts a new engagement when you return.
        events += pulses("Back", 200, 188, 5)
        rows = snapshot(events, NOW).outgoing
        self.assertEqual([row.entity for row in rows], ["Back", "Fresh", "Long"])

    def test_missile_hits_keep_target_weapon_and_damage(self):
        raw = "<color=0xff00ffff><b>1,204</b> <color=0x77ffffff><font size=10>to</font> "
        raw += "<b><color=0xffffffff>Rook [TICK](Cerberus)</b><font size=10><color=0x77ffffff>"
        raw += " - Scourge Rage Heavy Assault Missile - Hits"
        hit = parse_combat(line(raw))
        self.assertEqual(
            hit,
            MissileHit(NOW, "Rook [TICK](Cerberus)", "Scourge Rage Heavy Assault Missile", 1204),
        )
        self.assertEqual(hit.damage_type, "Kinetic")
        rocket = parse_combat(line("88 to Rook - Caldari Navy Nova Rocket - Hits"))
        self.assertEqual(
            (rocket.weapon, rocket.damage_type), ("Caldari Navy Nova Rocket", "Explosive")
        )
        self.assertIsNone(parse_combat(line("0 to Rook - Inferno Heavy Missile - Hits")))

    def test_missile_meter_shows_the_latest_volley_against_its_target_best(self):
        def hit(seconds, target, weapon, amount):
            return MissileHit(NOW - timedelta(seconds=seconds), target, weapon, amount)

        events = [
            hit(9, "Rook[TICK](Cerberus)", "Scourge Heavy Missile", 300),
            hit(8, "Aster[TICK](Sabre)", "Scourge Heavy Missile", 90),
            hit(7, "Rook[TICK](Cerberus)", "Scourge Heavy Missile", 150),
        ]
        # Fired alongside each other, so each target gets its own meter.
        missiles = {s.latest.target: s.latest for s in snapshot(events, NOW).missiles}
        rook = missiles["Rook[TICK](Cerberus)"]
        self.assertEqual((rook.amount, rook.reference.best, rook.ratio), (150, 300, 0.5))
        self.assertEqual(missiles["Aster[TICK](Sabre)"].ratio, 1.0)
        pod = snapshot([*events, hit(6, "Rook[TICK](Capsule)", "Scourge Heavy Missile", 40)], NOW)
        self.assertEqual([s.latest.ratio for s in pod.missiles], [1.0])
        self.assertEqual(snapshot([], NOW).missiles, ())

    def test_missile_reference_restarts_after_idle_gap_or_clear(self):
        references = MissileReferences()
        first = references.add(MissileHit(NOW, "Rook", "Nova Heavy Missile", 400))
        later = references.add(MissileHit(NOW + MISSILE_GAP, "Rook", "Nova Heavy Missile", 200))
        self.assertEqual((later.ratio, later.reference.hits), (0.5, 2))
        stale = NOW + MISSILE_GAP * 2 + timedelta(seconds=1)
        fresh = references.add(MissileHit(stale, "Rook", "Nova Heavy Missile", 200))
        self.assertEqual((fresh.ratio, fresh.reference.hits, first.ratio), (1.0, 1, 1.0))
        references.clear()
        reshipped = references.add(MissileHit(stale, "Rook", "Nova Heavy Missile", 100))
        self.assertEqual(reshipped.reference.best, 100)

    def test_rolling_pressure_ranks_sources_and_keeps_outgoing_targets(self):
        events = [
            Neut(NOW - timedelta(seconds=60), "Expired", 600, True),
            Neut(NOW - timedelta(seconds=59), "Rook", 300, True),
            Neut(NOW - timedelta(seconds=24), "aster", 360, True),
            Neut(NOW - timedelta(seconds=12), "Aster", 360, True),
            Neut(NOW, "Aster", 360, True),
            Neut(NOW, "Zero", 0, True),
            Neut(NOW + timedelta(seconds=1), "Future", 300, True),
            Neut(NOW, "Willow", 180, False),
            Neut(NOW, "Willow", 180, False),
            Neut(NOW, "Dry", 0, False),
        ]
        result = snapshot(events, NOW)
        self.assertEqual(result.incoming_total, 1380)
        self.assertEqual([row.entity for row in result.incoming], ["Aster", "Rook"])
        # Three pulses 12 s apart cover 36 s of activity; a lone pulse 59 s ago covers 59 s.
        self.assertAlmostEqual(result.incoming[0].rate, 30)
        self.assertAlmostEqual(result.incoming[1].rate, 300 / 59)
        self.assertAlmostEqual(result.incoming_rate, 30 + 300 / 59)
        # A fresh pair of neut cycles reads as one cycle's worth, not a minute's.
        self.assertEqual(
            [(row.entity, row.rate, row.latest) for row in result.outgoing],
            [("Willow", 30, 360), ("Dry", 0, 0)],
        )

    def test_hit_average_uses_last_ten_recent_results(self):
        events = [Hit(NOW - timedelta(seconds=12), "wrecks")]
        events.extend(Hit(NOW - timedelta(seconds=i), "hits") for i in range(9, 0, -1))
        events.append(Hit(NOW, "misses"))
        for now in (NOW, NOW + timedelta(hours=1)):
            results = snapshot(events, now).turrets[0].results
            self.assertAlmostEqual(average_quality(results), 9 * 0.875 / 10)

    def test_incoming_damage_parses_all_weapon_types_and_optional_fields(self):
        raw = "<color=0xffcc0000><b>1,200</b><color=0x77ffffff> from <color=0xffffffff>"
        raw += (
            "Aster Vale [TICK](Cerberus)<color=0x77ffffff> - Inferno Heavy Assault Missile - Hits"
        )
        self.assertEqual(
            parse_combat(line(raw)),
            Damage(NOW, "Aster Vale", 1200, "HAM", "Inferno Heavy Assault Missile", "Cerberus"),
        )
        drone = "45 from Mira Solenne[SOLN](Osprey Navy Issue) - Warrior II - Penetrates"
        self.assertEqual(
            parse_combat(line(drone)),
            Damage(NOW, "Mira Solenne", 45, None, "Warrior II", "Osprey Navy Issue"),
        )
        self.assertEqual(
            parse_combat(line("80 from Field Drone - Hits")), Damage(NOW, "Field Drone", 80)
        )
        self.assertEqual(parse_combat(line("35 from Rook")), Damage(NOW, "Rook", 35))
        self.assertIsNone(parse_combat(line("0 from Rook - Test Weapon - Hits")))
        self.assertIsNone(parse_combat(line("Rook misses you completely")))

    def test_incoming_dps_ranks_sources_and_expires_after_15_seconds(self):
        events = [
            Damage(NOW - timedelta(seconds=15), "Expired", 9000),
            Damage(NOW - timedelta(seconds=14), "Rook", 300),
            Damage(NOW, "rook", 150),
            Damage(NOW, "Aster", 900),
            Neut(NOW - timedelta(seconds=30), "Rook", 60, True),
        ]
        result = snapshot(events, NOW)
        self.assertEqual(result.damage_total, 1350)
        # Rook's two volleys span the whole window; Aster's single volley counts one cycle.
        self.assertEqual(
            [(row.entity, row.rate) for row in result.damage], [("Aster", 180), ("rook", 30)]
        )
        self.assertEqual(result.incoming_total, 60)

    def test_weapon_classes_cover_turret_and_missile_families(self):
        cases = {
            "Mega Pulse Laser II": "Pulse",
            "Dual Light Beam Laser II": "Beam",
            "Neutron Blaster Cannon II": "Blaster",
            "425mm Railgun II": "Rail",
            "220mm Vulcan AutoCannon II": "AC",
            "800mm Heavy 'Scout' Repeating Cannon I": "AC",
            "1400mm Howitzer Artillery II": "Arty",
            "Heavy Entropic Disintegrator II": "Disintegrator",
            "Polarized Torpedo": "Torps",
            "Nova Rage Heavy Assault Missile": "HAM",
            "Mjolnir Precision Cruise Missile": "Cruise",
            "Caldari Navy Scourge Heavy Missile": "HM",
            "Inferno Fury Light Missile": "LM",
            "Nova Javelin Rocket": "Rockets",
            "Medium Breacher Pod Launcher": "Pods",
            "Small EMP Smartbomb II": None,
            "Electron Bomb": None,
            "Warrior II": None,
        }
        for name, expected in cases.items():
            self.assertEqual(weapon_class(name), expected, name)

    def test_top_source_reports_main_weapon_without_drones(self):
        events = [
            Damage(NOW, "Aster", 600, "Pulse", "Mega Pulse Laser II"),
            Damage(NOW, "Aster", 300, None, "Warrior II"),
            Damage(NOW, "Aster", 150),
        ]
        row = snapshot(events, NOW).damage[0]
        self.assertEqual((row.weapon, row.weapon_rate, row.rate), ("Pulse", 120, 210))
        self.assertEqual(row.module, "Mega Pulse Laser II")

    def test_breacher_pods_list_each_target_still_ticking(self):
        pod = "Medium Breacher Pod Launcher"
        self.assertEqual(
            parse_combat(line(f"112 to Corvin Ashe[PODS](Cyclone) - {pod} - Hits")),
            PodHit(NOW, "Corvin Ashe[PODS](Cyclone)", 112, pod),
        )
        events = [
            PodHit(NOW - timedelta(seconds=5), "Gone[A](Rifter)", 50, pod),
            PodHit(NOW - timedelta(seconds=1), "Corvin Ashe[PODS](Cyclone)", 112, pod),
            PodHit(NOW, "Jax0r[PODS](Cyclone)", 100, pod),
        ]
        pods = snapshot(events, NOW).pods
        # The target you started podding most recently comes first.
        self.assertEqual([(p.entity, p.rate) for p in pods], [("Jax0r", 100), ("Corvin Ashe", 112)])

    def test_move_mode_placeholders_fill_every_meter(self):
        sample = placeholder_snapshot(NOW)
        self.assertEqual((len(sample.turrets), len(sample.missiles)), (2, 2))
        self.assertTrue(sample.drones and sample.pods)
        self.assertEqual(len(sample.incoming), 3)
        self.assertEqual(len(sample.damage), 3)
        self.assertEqual(sample.damage[0].weapon, "AC")
        self.assertEqual({row.state for row in sample.outgoing}, {DRAINING, DRY, PINNED, RECOVERED})
        self.assertLess(min(stream.latest.ratio for stream in sample.missiles), 1)
