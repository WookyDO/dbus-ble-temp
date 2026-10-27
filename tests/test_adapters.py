#!/usr/bin/env python3
"""Tests for listening on several Bluetooth adapters (HciHub)."""

import errno
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

import thermobeacon_bridge as tb  # noqa: E402
from test_bridge import MAC, Cfg, FakeClock, make_adv, make_hci_legacy, make_thermobeacon  # noqa: E402


class FakeListener(object):
    """HCI listener backed by a pipe, so select() works on it."""
    opened = []

    def __init__(self, dev):
        self.dev = dev
        self.r, self.w = os.pipe()
        self.fail = None
        self.closed = False
        FakeListener.opened.append(self)

    def fileno(self):
        return self.r

    def push(self, pkt):
        os.write(self.w, pkt)

    def read(self):
        if self.fail:
            raise OSError(self.fail, os.strerror(self.fail))
        return os.read(self.r, 1024)

    def close(self):
        self.closed = True
        os.close(self.r)
        os.close(self.w)


class HubTests(unittest.TestCase):
    def setUp(self):
        self.sysfs = tempfile.mkdtemp()
        self.clock = FakeClock()
        FakeListener.opened = []

    def tearDown(self):
        shutil.rmtree(self.sysfs)

    def plug(self, *names):
        for n in names:
            os.mkdir(os.path.join(self.sysfs, n))

    def unplug(self, name):
        os.rmdir(os.path.join(self.sysfs, name))

    def hub(self, devices=None, opener=FakeListener):
        h = tb.HciHub(devices, sysfs=self.sysfs, opener=opener, clock=self.clock)
        self.addCleanup(h.close)
        return h

    def test_available_adapters(self):
        self.plug("hci0", "hci2", "hci0:11", "other")
        self.assertEqual(tb.available_adapters(self.sysfs), [0, 2])
        self.assertEqual(tb.available_adapters("/nonexistent"), [])

    def test_listens_on_all_by_default(self):
        self.plug("hci0", "hci1")
        h = self.hub()
        self.assertEqual(sorted(h.listeners), [0, 1])

    def test_device_selection(self):
        self.plug("hci0", "hci1", "hci2")
        self.assertEqual(sorted(self.hub({1}).listeners), [1])

    def test_packets_tagged_with_adapter(self):
        self.plug("hci0", "hci1")
        h = self.hub()
        h.listeners[0].push(b"A")
        h.listeners[1].push(b"B")
        self.assertEqual(sorted(h.wait(0.1)), [(0, b"A"), (1, b"B")])
        self.assertEqual(h.wait(0.01), [])

    def test_hotplug_and_removal(self):
        self.plug("hci0")
        h = self.hub()
        self.assertEqual(sorted(h.listeners), [0])
        self.plug("hci1")                      # dongle plugged in
        self.clock.t += 61
        h.wait(0.01)
        self.assertEqual(sorted(h.listeners), [0, 1])
        self.unplug("hci1")                    # and removed again
        self.clock.t += 61
        h.wait(0.01)
        self.assertEqual(sorted(h.listeners), [0])

    def test_read_error_drops_adapter_until_rescan(self):
        self.plug("hci0", "hci1")
        h = self.hub()
        bad = h.listeners[1]
        bad.fail = errno.ENODEV
        bad.push(b"x")
        h.listeners[0].push(b"ok")
        self.assertEqual(h.wait(0.1), [(0, b"ok")])
        self.assertTrue(bad.closed)
        self.assertEqual(sorted(h.listeners), [0])
        self.clock.t += 61                     # still present in sysfs -> reopened
        h.wait(0.01)
        self.assertEqual(sorted(h.listeners), [0, 1])

    def test_open_failure_is_retried_quietly(self):
        self.plug("hci0", "hci1")
        calls = []

        def opener(dev):
            calls.append(dev)
            if dev == 1:
                raise OSError(errno.EPERM, "no")
            return FakeListener(dev)

        h = self.hub(opener=opener)
        self.assertEqual(sorted(h.listeners), [0])
        self.clock.t += 61
        h.wait(0.01)
        self.assertEqual(calls.count(1), 2)

    def test_no_adapter_at_all(self):
        h = self.hub()
        self.assertEqual(h.wait(0.01), [])


class BridgeStepTests(unittest.TestCase):
    def test_step_forwards_and_records_adapter(self):
        sent, seen = [], []

        class Seen(object):
            data = {}
            wallclock = staticmethod(lambda: 0)

            def update(self, r, rssi, adapter=None):
                seen.append((r.mac, rssi, adapter))

            def flush(self):
                pass

        class Hub(object):
            def wait(self, timeout):
                adv = make_adv(make_thermobeacon())
                return [(0, make_hci_legacy(MAC, adv, -50)), (1, make_hci_legacy(MAC, adv, -70))]

        b = tb.Bridge(Cfg(), sender=sent.append, clock=FakeClock(), seen=Seen())
        b.step(Hub())
        self.assertEqual(seen, [(MAC, -50, "hci0"), (MAC, -70, "hci1")])
        self.assertEqual(len(sent), 1)         # duplicate via 2nd adapter rate limited

    def test_idle_warning(self):
        class Hub(object):
            def wait(self, timeout):
                return []
        clock = FakeClock()
        b = tb.Bridge(Cfg(), sender=lambda p: None, clock=clock)
        clock.t += 121
        with self.assertLogs("dbus-ble-temp", "WARNING"):
            b.step(Hub())


class SeenAdapterTests(unittest.TestCase):
    def test_best_recent_adapter(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        t = [1000000.0]
        s = tb.SeenStore(os.path.join(d, "s.json"), wallclock=lambda: t[0])
        r = tb.decode_thermobeacon(MAC, make_thermobeacon())
        s.update(r, -80, "hci0")
        s.update(r, -55, "hci1")
        self.assertEqual(s.data[MAC]["rssi"], -55)
        t[0] += 700                            # hci1 does not hear it any more
        s.update(r, -82, "hci0")
        self.assertEqual(s.data[MAC]["rssi"], -82)
        self.assertEqual(set(s.data[MAC]["adapters"]), {"hci0", "hci1"})


class ConfigAdapterTests(unittest.TestCase):
    def test_parse(self):
        self.assertIsNone(tb.parse_adapters("all"))
        self.assertIsNone(tb.parse_adapters(""))
        self.assertEqual(tb.parse_adapters("0, hci1"), {0, 1})
        self.assertEqual(tb.parse_adapters("2"), {2})
        with self.assertRaises(ValueError):
            tb.parse_adapters("usb0")
        self.assertEqual(tb.describe_adapters(None), "all adapters")
        self.assertEqual(tb.describe_adapters({1, 0}), "hci0, hci1")

    def test_default_is_all(self):
        self.assertIsNone(tb.Config("/nonexistent.ini").hci_devices)


if __name__ == "__main__":
    unittest.main()
