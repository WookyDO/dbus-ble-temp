#!/usr/bin/env python3
"""Tests for the ignore list, the seen-sensor record and bin/sensors.py."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

import thermobeacon_bridge as tb  # noqa: E402
from test_bridge import MAC, Cfg, FakeClock, make_adv, make_thermobeacon  # noqa: E402

spec = importlib.util.spec_from_file_location("sensors", os.path.join(HERE, "..", "bin", "sensors.py"))
sensors = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sensors)

OTHER = "c0:ff:ee:00:00:01"


class IgnoreTests(unittest.TestCase):
    def test_ignored_sensor_not_forwarded_but_recorded(self):
        sent = []
        cfg = Cfg()
        cfg.ignore = {OTHER}
        recorded = []

        class Seen(object):
            def update(self, r, rssi, adapter=None):
                recorded.append(r.mac)

        b = tb.Bridge(cfg, sender=sent.append, clock=FakeClock(), seen=Seen())
        self.assertFalse(b.handle_adv(OTHER, make_adv(make_thermobeacon(mac=OTHER)), -90))
        self.assertTrue(b.handle_adv(MAC, make_adv(make_thermobeacon()), -50))
        self.assertEqual(len(sent), 1)
        self.assertEqual(recorded, [OTHER, MAC])

    def test_config_ignore(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.ini")
            with open(p, "w") as f:
                f.write("[sensors]\nignore = C0:FF:EE:00:00:01\n")
            c = tb.Config(p)
            self.assertEqual(c.ignore, {OTHER})
            self.assertEqual(c.state_file, os.path.join(d, "sensors.json"))


class SeenStoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "sensors.json")
        self.t = 1000000.0

    def tearDown(self):
        self.dir.cleanup()

    def reading(self, mac=MAC):
        return tb.decode_thermobeacon(mac, make_thermobeacon(mac=mac, temp_c=20.5, hum=50, mv=2900))

    def disk(self):
        with open(self.path) as f:
            return json.load(f)

    def test_new_sensor_written_at_once_then_throttled(self):
        s = tb.SeenStore(self.path, wallclock=lambda: self.t)
        s.update(self.reading(), -60)
        self.assertEqual(self.disk()[MAC]["rssi"], -60)
        self.t += 10
        s.update(self.reading(), -70)
        self.assertEqual(self.disk()[MAC]["rssi"], -60)       # not yet flushed
        self.t += 300
        s.update(self.reading(), -75)
        d = self.disk()[MAC]
        self.assertEqual(d["rssi"], -75)
        self.assertEqual(d["first_seen"], 1000000)
        self.assertEqual(d["temperature"], 20.5)
        self.assertEqual(d["voltage"], 2.9)

    def test_flush_on_close_and_reload(self):
        s = tb.SeenStore(self.path, wallclock=lambda: self.t)
        s.update(self.reading(), -60)
        self.t += 5
        s.update(self.reading(), -61)
        s.flush()
        self.assertEqual(tb.SeenStore(self.path, wallclock=lambda: self.t).data[MAC]["rssi"], -61)

    def test_old_entries_expire(self):
        s = tb.SeenStore(self.path, wallclock=lambda: self.t)
        s.update(self.reading(), -60)
        s.update(self.reading("c0:ff:ee:00:00:01"), -90)
        self.t += 20 * 86400
        s.update(self.reading(), -60)
        s.flush()
        self.t += 11 * 86400       # MAC seen 11 days ago, OTHER 31 days ago
        self.assertEqual(set(tb.SeenStore(self.path, wallclock=lambda: self.t).data), {MAC})

    def test_drop_from_seen(self):
        s = tb.SeenStore(self.path, wallclock=lambda: self.t)
        s.update(self.reading(), -60)
        s.update(self.reading("c0:ff:ee:00:00:01"), -90)

        class C(object):
            state_file = self.path
        sensors.drop_from_seen(C, ["c0:ff:ee:00:00:01", "11:22:33:44:55:66"])
        self.assertEqual(set(self.disk()), {MAC})

    def test_corrupt_file_ignored(self):
        with open(self.path, "w") as f:
            f.write("{broken")
        self.assertEqual(tb.SeenStore(self.path).data, {})


DAY = 86400
NOW = 2000000000


def settings_for(dev, **kv):
    return {"/Settings/Devices/%s/%s" % (dev, k): v for k, v in kv.items()}


class CleanupDecisionTests(unittest.TestCase):
    FOREIGN = "c0:ff:ee:00:00:01"
    FOREIGN_ID = "ruuvi_c0ffee000001"

    def stale(self, settings, present=(), last_seen_days=8, max_days=7):
        seen = {self.FOREIGN: {"last_seen": NOW - last_seen_days * DAY}}
        return tb.find_stale_sensors(settings, set(present), seen, NOW, max_days * DAY)

    def test_never_enabled_old_sensor_is_removed(self):
        st = settings_for(self.FOREIGN_ID, Enabled=0, CustomName="")
        self.assertEqual(self.stale(st), {self.FOREIGN: sorted(st)})

    def test_recently_seen_is_kept(self):
        self.assertEqual(self.stale(settings_for(self.FOREIGN_ID, Enabled=0, CustomName=""),
                                    last_seen_days=6), {})

    def test_still_listed_by_victron_is_kept(self):
        self.assertEqual(self.stale(settings_for(self.FOREIGN_ID, Enabled=0, CustomName=""),
                                    present=[self.FOREIGN_ID]), {})

    def test_ever_enabled_is_kept(self):
        # once enabled, a device instance exists - even if disabled again later
        st = settings_for(self.FOREIGN_ID, Enabled=0, CustomName="",
                          ClassAndVrmInstance="temperature:31")
        self.assertEqual(self.stale(st), {})
        self.assertEqual(self.stale(settings_for(self.FOREIGN_ID, Enabled=1)), {})

    def test_named_by_user_is_kept(self):
        self.assertEqual(self.stale(settings_for(self.FOREIGN_ID, Enabled=0,
                                                 CustomName="Nachbar")), {})

    def test_unknown_ruuvi_is_never_touched(self):
        st = settings_for("ruuvi_aabbccddeeff", Enabled=0, CustomName="")
        self.assertEqual(self.stale(st), {})

    def test_no_settings_nothing_to_do(self):
        self.assertEqual(self.stale({}), {})


class BridgeCleanupTests(unittest.TestCase):
    def test_schedule_and_seen_removal(self):
        clock = FakeClock()
        calls = []

        class Seen(object):
            data = {"c0:ff:ee:00:00:01": {}}
            removed = []
            wallclock = staticmethod(lambda: NOW)

            def update(self, r, rssi, adapter=None):
                pass

            def remove(self, mac):
                self.removed.append(mac)

            def flush(self):
                pass

        def janitor(seen, now, max_age):
            calls.append((now, max_age))
            return ["c0:ff:ee:00:00:01"]

        cfg = Cfg()
        cfg.cleanup_days = 7
        seen = Seen()
        b = tb.Bridge(cfg, sender=lambda p: None, clock=clock, seen=seen, janitor=janitor)
        b.maybe_cleanup(clock.t + 60)            # too early after start
        self.assertEqual(calls, [])
        b.maybe_cleanup(clock.t + 120)
        self.assertEqual(calls, [(NOW, 7 * DAY)])
        self.assertEqual(seen.removed, ["c0:ff:ee:00:00:01"])
        b.maybe_cleanup(clock.t + 1000)          # within the hour
        self.assertEqual(len(calls), 1)
        b.maybe_cleanup(clock.t + 120 + 3600)
        self.assertEqual(len(calls), 2)

    def test_janitor_error_is_contained(self):
        def janitor(*a):
            raise RuntimeError("dbus down")
        cfg = Cfg()
        cfg.cleanup_days = 7

        class Seen(object):
            data = {}
            wallclock = staticmethod(lambda: NOW)
        b = tb.Bridge(cfg, sender=lambda p: None, clock=FakeClock(), seen=Seen(), janitor=janitor)
        b.maybe_cleanup(1e9)   # must not raise

    def test_config_cap(self):
        with tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False) as f:
            f.write("[general]\ncleanup_days = 90\n")
        try:
            self.assertEqual(tb.Config(f.name).cleanup_days, 29)
        finally:
            os.unlink(f.name)


class ConfigEditTests(unittest.TestCase):
    EXAMPLE = open(os.path.join(HERE, "..", "config.ini.example")).read()

    def edit(self, text, key, values, section="sensors"):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.ini")
            with open(p, "w") as f:
                f.write(text)
            sensors.set_config_list(p, section, key, values)
            with open(p) as f:
                return f.read()

    def test_replaces_value_and_keeps_comments(self):
        out = self.edit(self.EXAMPLE, "ignore", {OTHER, MAC})
        self.assertIn("ignore = %s, %s\n" % (OTHER, MAC), out)
        self.assertIn("# Sensors that are never forwarded", out)
        self.assertEqual(out.count("ignore ="), 1)
        self.assertEqual(out.count("[sensors]"), 1)

    def test_roundtrip_through_config_parser(self):
        out = self.edit(self.EXAMPLE, "ignore", {OTHER})
        with tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False) as f:
            f.write(out)
        try:
            c = tb.Config(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(c.ignore, {OTHER})
        self.assertEqual(c.devices, set())
        self.assertEqual(c.name_prefix, "ThermoBeacon")

    def test_clear(self):
        out = self.edit("[sensors]\nignore = a, b\n  c\ndevices =\n", "ignore", set())
        self.assertEqual(out, "[sensors]\nignore = \ndevices =\n")

    def test_adds_missing_key_and_section(self):
        out = self.edit("[general]\nloglevel = INFO\n\n[sensors]\ndevices =\n\n", "ignore", {OTHER})
        self.assertIn("[sensors]\ndevices =\nignore = %s\n" % OTHER, out)
        out = self.edit("[general]\nloglevel = INFO\n", "ignore", {OTHER})
        self.assertTrue(out.endswith("[sensors]\nignore = %s\n" % OTHER))

    def test_only_touches_right_section(self):
        out = self.edit("[general]\nignore = keep\n[sensors]\nignore = x\n", "ignore", {OTHER})
        self.assertIn("[general]\nignore = keep\n", out)
        self.assertIn("[sensors]\nignore = %s\n" % OTHER, out)


class ViaTests(unittest.TestCase):
    def test_via(self):
        import time
        now = time.time()
        e = {"adapters": {"hci0": {"rssi": -71, "seen": now}, "hci1": {"rssi": -52, "seen": now},
                          "hci2": {"rssi": -40, "seen": now - 3600}}}
        self.assertEqual(sensors.via(e), "hci1 -52, hci0 -71")
        self.assertEqual(sensors.via({}), "")


class ResolveTests(unittest.TestCase):
    KNOWN = {"bb:ab:00:00:05:9b", "bc:13:00:00:0b:e1", "dc:12:00:00:12:62"}

    def test_full_and_short(self):
        self.assertEqual(sensors.resolve(["BB-AB-00-00-05-9B", "059b", "1262"], self.KNOWN),
                         ["bb:ab:00:00:05:9b", "bb:ab:00:00:05:9b", "dc:12:00:00:12:62"])

    def test_unknown_or_ambiguous(self):
        with self.assertRaises(SystemExit):
            sensors.resolve(["ffff"], self.KNOWN)
        with self.assertRaises(SystemExit):
            sensors.resolve(["00"], self.KNOWN)


if __name__ == "__main__":
    unittest.main()
