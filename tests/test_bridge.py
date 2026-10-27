#!/usr/bin/env python3
"""Unit tests - run with:  python3 -m unittest discover -s tests"""

import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import thermobeacon_bridge as tb  # noqa: E402

MAC = "fa:ac:00:00:17:21"


def make_thermobeacon(mac=MAC, temp_c=21.5, hum=55.25, mv=3010, uptime=123456,
                      cid=0x0010, flags=0):
    embedded = tb.bdaddr_from_mac(mac)
    return struct.pack("<HH6sHhHI", cid, flags, embedded, mv,
                       int(round(temp_c * 16)), int(round(hum * 16)), uptime)


def make_adv(mfg, name=None):
    data = bytes([len(mfg) + 1, 0xFF]) + mfg
    if name:
        n = name.encode()
        data = bytes([2, 0x01, 0x06]) + bytes([len(n) + 1, 0x09]) + n + data
    return data


def make_hci_legacy(mac, adv, rssi=-60):
    rep = bytes([0x00, 0x00]) + tb.bdaddr_from_mac(mac) + bytes([len(adv)]) + adv + struct.pack("b", rssi)
    body = bytes([0x02, 0x01]) + rep
    return bytes([0x04, 0x3E, len(body)]) + body


def make_hci_ext(mac, adv, rssi=-70):
    rep = (struct.pack("<H", 0x0013) + bytes([0]) + tb.bdaddr_from_mac(mac) +
           bytes([1, 0, 0xFF, 0x7F]) + struct.pack("b", rssi) + struct.pack("<H", 0) +
           bytes([0]) + bytes(6) + bytes([len(adv)]) + adv)
    body = bytes([0x0D, 0x01]) + rep
    return bytes([0x04, 0x3E, len(body)]) + body


def original_decode(mfg):
    """Decoder of the previous dbus-ble-temp.py (bluepy based), as oracle."""
    e6, e5, e4, e3, e2, e1, voltage, temperature_raw, humidity_raw, uptime_seconds = \
        struct.unpack('<xxxxBBBBBBHHHI', mfg)
    t = temperature_raw / 16.
    if t > 4000:
        t = t - 4096
    return t, humidity_raw / 16., voltage / 1000, uptime_seconds


# --- Python port of dbus-ble-sensors' ruuvi.c (format 5) + ble_dbus_set_regs ---
RUUVI_RAWV2 = [
    # name, signed, size, offset, shift, bits, scale, bias, inval
    ("Temperature", True, 2, 1, 0, 16, 200, 0, 0x8000),
    ("Humidity", False, 2, 3, 0, 16, 400, 0, 0xFFFF),
    ("Pressure", False, 2, 5, 0, 16, 100, 500, 0xFFFF),
    ("AccelX", True, 2, 7, 0, 16, 1000, 0, 0x8000),
    ("AccelY", True, 2, 9, 0, 16, 1000, 0, 0x8000),
    ("AccelZ", True, 2, 11, 0, 16, 1000, 0, 0x8000),
    ("BatteryVoltage", False, 2, 13, 5, 11, 1000, 1.6, 0x3FF),
    ("TxPower", False, 1, 14, 0, 5, 0.5, -40, 0x1F),
    ("SeqNo", False, 2, 16, 0, 16, 1, 0, 0xFFFF),
]


def ruuvi_parse(buf):
    out = {}
    for name, signed, size, off, shift, bits, scale, bias, inval in RUUVI_RAWV2:
        raw = int.from_bytes(buf[off:off + size], "big")
        raw = (raw >> shift) & ((1 << bits) - 1)
        if raw == inval:
            out[name] = None
            continue
        if signed and raw & (1 << (bits - 1)):
            raw -= 1 << bits
        out[name] = raw / scale + bias
    return out


class DecodeTests(unittest.TestCase):
    def test_matches_original_decoder(self):
        for temp in (21.5, -5.25, -0.0625, 0.0, 37.9375, -30.0):
            mfg = make_thermobeacon(temp_c=temp, hum=48.5, mv=2950, uptime=999)
            r = tb.decode_thermobeacon(MAC, mfg)
            ot, oh, ov, ou = original_decode(mfg)
            self.assertAlmostEqual(r.temperature, ot)
            self.assertAlmostEqual(r.temperature, temp)
            self.assertAlmostEqual(r.humidity, oh)
            self.assertAlmostEqual(r.voltage_mv / 1000, ov)
            self.assertEqual(r.uptime, ou)

    def test_rejects_wrong_length(self):
        self.assertIsNone(tb.decode_thermobeacon(MAC, make_thermobeacon() + b"\0\0"))  # min/max packet
        self.assertIsNone(tb.decode_thermobeacon(MAC, make_thermobeacon()[:-1]))

    def test_rejects_foreign_mac(self):
        mfg = make_thermobeacon(mac="11:22:33:44:55:66")
        self.assertIsNone(tb.decode_thermobeacon(MAC, mfg))

    def test_button_flag(self):
        self.assertTrue(tb.decode_thermobeacon(MAC, make_thermobeacon(flags=0x80)).button)


class RuuviEncodingTests(unittest.TestCase):
    def roundtrip(self, **kw):
        r = tb.decode_thermobeacon(MAC, make_thermobeacon(**kw))
        buf = tb.encode_ruuvi_rawv2(r, r.uptime)
        self.assertEqual(len(buf), 24)        # ruuvi_handle_mfg requires len 24
        self.assertEqual(buf[0], 5)
        self.assertEqual(buf[18:], bytes.fromhex(MAC.replace(":", "")))
        return r, ruuvi_parse(buf)

    def test_values(self):
        r, p = self.roundtrip(temp_c=21.5, hum=55.25, mv=3010, uptime=70000)
        self.assertAlmostEqual(p["Temperature"], 21.5, places=3)
        self.assertAlmostEqual(p["Humidity"], 55.25, places=3)
        self.assertAlmostEqual(p["BatteryVoltage"], 3.010, places=3)
        self.assertEqual(p["SeqNo"], 70000 & 0xFFFF)
        for k in ("Pressure", "AccelX", "AccelY", "AccelZ", "TxPower"):
            self.assertIsNone(p[k], k)

    def test_negative_temperature(self):
        _, p = self.roundtrip(temp_c=-12.3125)
        self.assertAlmostEqual(p["Temperature"], -12.3125, places=2)

    def test_battery_clamping_and_reserved_value(self):
        _, p = self.roundtrip(mv=1200)
        self.assertAlmostEqual(p["BatteryVoltage"], 1.6)
        _, p = self.roundtrip(mv=4000)
        self.assertAlmostEqual(p["BatteryVoltage"], 3.646)
        _, p = self.roundtrip(mv=1600 + 0x3FF)    # would be 'invalid' in ruuvi.c
        self.assertIsNotNone(p["BatteryVoltage"])

    def test_humidity_clamped(self):
        _, p = self.roundtrip(hum=120)
        self.assertAlmostEqual(p["Humidity"], 100.0)


class UdpPacketTests(unittest.TestCase):
    def test_v1_layout_matches_ble_socket_parse(self):
        payload = bytes(range(24))
        pkt = tb.build_udp_packet(MAC, tb.MFG_ID_RUUVI, payload, rssi=-71)
        # mirrors ble_socket_parse() in dbus-ble-sensors/src/ble-socket.c
        self.assertEqual(pkt[0], 1)
        self.assertEqual(pkt[1], tb.UDP_FLAG_RSSI)
        self.assertEqual(tb.mac_from_bdaddr(pkt[2:8]), MAC)
        self.assertEqual(pkt[8] | (pkt[9] << 8), 0x0499)
        self.assertEqual(pkt[10], 24)
        self.assertEqual(pkt[11:35], payload)
        self.assertEqual(struct.unpack("b", pkt[35:36])[0], -71)
        self.assertEqual(len(pkt), 36)
        self.assertLessEqual(len(pkt), 128)   # BLE_SOCKET_MAX_PACKET


class HciParseTests(unittest.TestCase):
    def test_legacy_report(self):
        adv = make_adv(make_thermobeacon(), name="ThermoBeacon")
        res = list(tb.parse_hci_event(make_hci_legacy(MAC, adv, -55)))
        self.assertEqual(res, [(MAC, adv, -55)])

    def test_extended_report(self):
        adv = make_adv(make_thermobeacon())
        res = list(tb.parse_hci_event(make_hci_ext(MAC, adv, -80)))
        self.assertEqual(res, [(MAC, adv, -80)])

    def test_garbage_is_ignored(self):
        for pkt in (b"", b"\x04", b"\x04\x3e\x02\x02", b"\x04\x0e\x04\x01\x02\x03\x00",
                    make_hci_legacy(MAC, make_adv(make_thermobeacon()))[:-5]):
            self.assertEqual(list(tb.parse_hci_event(pkt)), [])

    def test_truncated_ad_structure(self):
        self.assertEqual(list(tb.iter_ad_structures(b"\x05\xff\x01")), [])


class FakeClock(object):
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Cfg(object):
    def __init__(self, devices=()):
        self.devices = set(devices)
        self.min_interval = 5
        self.idle_warning = 120


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.clock = FakeClock()

    def bridge(self, devices=()):
        return tb.Bridge(Cfg(devices), sender=self.sent.append, clock=self.clock)

    def test_forwards_and_rate_limits(self):
        b = self.bridge()
        adv = make_adv(make_thermobeacon())
        self.assertTrue(b.handle_adv(MAC, adv, -60))
        self.assertFalse(b.handle_adv(MAC, adv, -60))   # within min_interval
        self.clock.t += 5
        self.assertTrue(b.handle_adv(MAC, adv, -60))
        self.assertEqual(len(self.sent), 2)

    def test_device_filter(self):
        b = self.bridge(devices=["11:22:33:44:55:66"])
        self.assertFalse(b.handle_adv(MAC, make_adv(make_thermobeacon()), -60))
        self.assertEqual(self.sent, [])

    def test_non_thermobeacon_ignored(self):
        b = self.bridge()
        ruuvi_like = struct.pack("<H", 0x0499) + bytes(24)
        self.assertFalse(b.handle_adv(MAC, make_adv(ruuvi_like), -60))

    def test_end_to_end_values(self):
        b = self.bridge()
        b.handle_adv(MAC, make_adv(make_thermobeacon(temp_c=-3.5, hum=81.0, mv=2870)), -60)
        pkt = self.sent[0]
        p = ruuvi_parse(pkt[11:35])
        self.assertAlmostEqual(p["Temperature"], -3.5, places=3)
        self.assertAlmostEqual(p["Humidity"], 81.0, places=3)
        self.assertAlmostEqual(p["BatteryVoltage"], 2.87, places=3)


class NamingTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.calls = []
        self.result = True

    def namer(self, mac, name):
        self.calls.append((mac, name))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def bridge(self):
        cfg = Cfg()
        cfg.name_prefix = "ThermoBeacon"
        return tb.Bridge(cfg, sender=lambda p: None, clock=self.clock, namer=self.namer)

    def send(self, b):
        self.clock.t += 10
        return b.handle_adv(MAC, make_adv(make_thermobeacon()), -60)

    def test_default_name(self):
        self.assertEqual(tb.default_name("ThermoBeacon", "bb:ab:00:00:05:9b"), "ThermoBeacon 059B")
        self.assertEqual(tb.ble_device_id("bb:ab:00:00:05:9b"), "ruuvi_bbab0000059b")

    def test_named_once(self):
        b = self.bridge()
        for _ in range(5):
            self.send(b)
        self.assertEqual(self.calls, [(MAC, "ThermoBeacon 1721")])

    def test_retry_until_device_exists(self):
        b = self.bridge()
        self.result = False
        self.send(b)                 # t+10: tried, device not there yet
        self.send(b)                 # t+20: within retry interval
        self.assertEqual(len(self.calls), 1)
        self.send(b); self.send(b)   # t+40: retried
        self.assertEqual(len(self.calls), 2)
        self.result = True
        for _ in range(6):
            self.send(b)
        self.assertEqual(len(self.calls), 3)

    def test_naming_error_does_not_break_forwarding(self):
        b = self.bridge()
        self.result = RuntimeError("dbus down")
        self.assertTrue(self.send(b))

    def test_disabled_without_prefix(self):
        b = tb.Bridge(Cfg(), sender=lambda p: None, clock=self.clock)
        self.assertIsNone(b._namer)


class ConfigTests(unittest.TestCase):
    def test_parse(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False) as f:
            f.write("[general]\nmin_interval = 2\n[sensors]\ndevices = DB:5C:8D:A0:0F:21,\n  fa-ac-00-00-17-21\n")
        try:
            c = tb.Config(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(c.min_interval, 2)
        self.assertEqual(c.udp_port, 18542)
        self.assertEqual(c.devices, {"db:5c:8d:a0:0f:21", "fa:ac:00:00:17:21"})

    def test_defaults_without_file(self):
        c = tb.Config("/nonexistent.ini")
        self.assertEqual(c.devices, set())
        self.assertEqual(c.udp_host, "127.0.0.1")
        self.assertEqual(c.name_prefix, "ThermoBeacon")


if __name__ == "__main__":
    unittest.main()
