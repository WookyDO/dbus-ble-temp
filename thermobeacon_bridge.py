"""
ThermoBeacon -> Victron dbus-ble-sensors bridge.

Listens passively on a raw HCI socket for the LE advertising reports that
Venus OS' own BLE scanner (dbus-ble-sensors) already produces, decodes
ThermoBeacon (Oria, Brifit, SensorBlue and other clones) manufacturer data,
re-encodes the readings as Ruuvi RAWv2 (data format 5) and hands them to
dbus-ble-sensors through its UDP gateway socket (packet format v1).

dbus-ble-sensors then publishes the sensor as a regular
com.victronenergy.temperature service, with enable/disable switch in the GUI,
custom name, timeout handling and low-battery alarm.

No third party Python packages are needed.
"""

import configparser
import ctypes
import ctypes.util
import errno
import json
import logging
import os
import select
import socket
import struct
import time

log = logging.getLogger("dbus-ble-temp")

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

AF_BLUETOOTH = 31
BTPROTO_HCI = 1
SOL_HCI = 0
HCI_FILTER = 2

HCI_EVENT_PKT = 0x04
EVT_LE_META_EVENT = 0x3E
EVT_LE_ADVERTISING_REPORT = 0x02
EVT_LE_EXT_ADVERTISING_REPORT = 0x0D

AD_TYPE_MANUFACTURER = 0xFF

MFG_ID_RUUVI = 0x0499

UDP_PACKET_V1 = 1
UDP_FLAG_RSSI = 0x01

DEFAULT_UDP_HOST = "127.0.0.1"
DEFAULT_UDP_PORT = 18542  # BLE_SOCKET_DEFAULT_PORT in dbus-ble-sensors

THERMOBEACON_LEN = 20  # manufacturer data incl. 2 byte company id


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def normalize_mac(mac):
    """'DB:5C:8D:A0:0F:21' / 'db5c8da00f21' / 'db-5c-..' -> 'db:5c:8d:a0:0f:21'"""
    h = mac.strip().lower().replace(":", "").replace("-", "")
    if len(h) != 12 or any(c not in "0123456789abcdef" for c in h):
        raise ValueError("invalid MAC address: %r" % mac)
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


def mac_from_bdaddr(b):
    """6 bytes bdaddr_t (LSB first) -> 'aa:bb:cc:dd:ee:ff'"""
    return ":".join("%02x" % x for x in reversed(b))


def bdaddr_from_mac(mac):
    """'aa:bb:cc:dd:ee:ff' -> 6 bytes bdaddr_t (LSB first)"""
    return bytes(reversed(bytes.fromhex(normalize_mac(mac).replace(":", ""))))


def iter_ad_structures(data):
    """Yield (ad_type, ad_data) for raw advertising data. Stops on malformed data."""
    i = 0
    n = len(data)
    while i < n:
        length = data[i]
        if length == 0 or i + 1 + length > n:
            return
        yield data[i + 1], data[i + 2:i + 1 + length]
        i += 1 + length


# --------------------------------------------------------------------------
# ThermoBeacon decoding
# --------------------------------------------------------------------------

class Reading(object):
    __slots__ = ("mac", "temperature", "humidity", "voltage_mv", "uptime", "button")

    def __init__(self, mac, temperature, humidity, voltage_mv, uptime, button):
        self.mac = mac
        self.temperature = temperature
        self.humidity = humidity
        self.voltage_mv = voltage_mv
        self.uptime = uptime
        self.button = button

    def __repr__(self):
        return ("Reading(%s, %.2f degC, %.2f %%, %d mV, uptime %d s)"
                % (self.mac, self.temperature, self.humidity, self.voltage_mv, self.uptime))


def decode_thermobeacon(addr, mfg):
    """
    Decode ThermoBeacon manufacturer data.

    addr: advertiser address 'aa:bb:cc:dd:ee:ff'
    mfg:  complete manufacturer specific data (incl. 2 byte company id)

    Layout (20 bytes, little endian):
      0-1   company id (varies by model, e.g. 0x0010 / 0x0011 / 0x0015)
      2-3   flags (byte 2 bit 7 = button pressed)
      4-9   device MAC, LSB first
      10-11 battery voltage [mV]
      12-13 temperature [1/16 degC], signed
      14-15 humidity [1/16 %]
      16-19 uptime [s]

    ThermoBeacons also send a 22 byte min/max packet which is ignored.
    Returns a Reading or None.
    """
    if len(mfg) != THERMOBEACON_LEN:
        return None

    flags, embedded, voltage, temp_raw, hum_raw, uptime = struct.unpack("<2xH6sHhHI", mfg)

    # The embedded MAC must match the advertiser - this is the signature that
    # tells ThermoBeacons apart from any other 20 byte manufacturer data.
    # (LSB first on all known models; MSB first is accepted as well.)
    if addr not in (mac_from_bdaddr(embedded), mac_from_bdaddr(embedded[::-1])):
        return None

    return Reading(
        mac=addr,
        temperature=temp_raw / 16.0,
        humidity=hum_raw / 16.0,
        voltage_mv=voltage,
        uptime=uptime,
        button=bool(flags & 0x80),
    )


# --------------------------------------------------------------------------
# Ruuvi RAWv2 encoding + dbus-ble-sensors UDP packet
# --------------------------------------------------------------------------

def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def encode_ruuvi_rawv2(reading, seqno):
    """
    Encode a reading as Ruuvi data format 5 (24 bytes, big endian).
    Fields the ThermoBeacon does not have are sent as 'invalid', so
    dbus-ble-sensors does not publish them.
    """
    temp = int(round(_clamp(reading.temperature, -163.835, 163.835) * 200))
    hum = int(round(_clamp(reading.humidity, 0.0, 100.0) * 400))

    # 11 bit battery field: mV - 1600 (1.600 .. 3.646 V)
    batt = int(_clamp(reading.voltage_mv, 1600, 3646)) - 1600
    # dbus-ble-sensors treats raw 0x3ff as invalid (ruuvi.c) - avoid it
    if batt == 0x3FF:
        batt = 0x3FE
    power = (batt << 5) | 0x1F  # tx power: invalid

    return struct.pack(
        ">BhHHhhhHBH6s",
        5,
        temp,
        hum,
        0xFFFF,                       # pressure: invalid
        -0x8000, -0x8000, -0x8000,    # acceleration x/y/z: invalid
        power,
        0xFF,                         # movement counter: invalid
        seqno & 0xFFFF,
        bytes.fromhex(reading.mac.replace(":", "")),
    )


def build_udp_packet(mac, mfg_id, payload, rssi=None):
    """dbus-ble-sensors UDP socket packet, format v1."""
    flags = UDP_FLAG_RSSI if rssi is not None else 0
    pkt = struct.pack("<BB", UDP_PACKET_V1, flags)
    pkt += bdaddr_from_mac(mac)
    pkt += struct.pack("<HB", mfg_id, len(payload))
    pkt += payload
    if rssi is not None:
        pkt += struct.pack("b", _clamp(int(rssi), -128, 127))
    return pkt


# --------------------------------------------------------------------------
# HCI event parsing
# --------------------------------------------------------------------------

def parse_hci_event(pkt):
    """
    Parse one raw HCI event packet as read from an HCI socket.
    Yields (addr, adv_data, rssi) for every LE advertising report.
    Supports legacy (0x02) and extended (0x0D) advertising reports.
    """
    if len(pkt) < 4 or pkt[0] != HCI_EVENT_PKT or pkt[1] != EVT_LE_META_EVENT:
        return
    sub = pkt[3]
    body = pkt[4:]
    if not body:
        return
    num = body[0]
    i = 1

    if sub == EVT_LE_ADVERTISING_REPORT:
        # evt_type(1) addr_type(1) addr(6) len(1) data(len) rssi(1)
        for _ in range(num):
            if i + 9 > len(body):
                return
            addr = mac_from_bdaddr(body[i + 2:i + 8])
            dlen = body[i + 8]
            if i + 9 + dlen + 1 > len(body):
                return
            data = body[i + 9:i + 9 + dlen]
            rssi = struct.unpack("b", body[i + 9 + dlen:i + 10 + dlen])[0]
            i += 10 + dlen
            yield addr, data, rssi

    elif sub == EVT_LE_EXT_ADVERTISING_REPORT:
        # evt_type(2) addr_type(1) addr(6) prim_phy(1) sec_phy(1) sid(1)
        # tx_power(1) rssi(1) interval(2) daddr_type(1) daddr(6) len(1) data
        for _ in range(num):
            if i + 24 > len(body):
                return
            addr = mac_from_bdaddr(body[i + 3:i + 9])
            rssi = struct.unpack("b", body[i + 13:i + 14])[0]
            dlen = body[i + 23]
            if i + 24 + dlen > len(body):
                return
            data = body[i + 24:i + 24 + dlen]
            i += 24 + dlen
            yield addr, data, rssi


# --------------------------------------------------------------------------
# Raw HCI socket (passive - never touches scan parameters)
# --------------------------------------------------------------------------

def _hci_filter():
    # struct hci_ufilter { u32 type_mask; u32 event_mask[2]; __le16 opcode; }
    # sizeof() is 16 incl. tail padding; newer kernels reject shorter optlen.
    return struct.pack("<IIIHxx", 1 << HCI_EVENT_PKT, 0, 1 << (EVT_LE_META_EVENT - 32), 0)


class HciListener(object):
    """
    Opens a raw HCI socket on hciN and only receives LE meta events.
    Uses Python's socket module when it has Bluetooth support, otherwise
    falls back to libc via ctypes (Venus OS' Python may lack AF_BLUETOOTH).
    """

    def __init__(self, dev_id=0):
        self.dev_id = dev_id
        self._sock = None
        self._fd = None
        self._open()

    def _open(self):
        if hasattr(socket, "AF_BLUETOOTH") and hasattr(socket, "BTPROTO_HCI"):
            s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)
            s.setsockopt(getattr(socket, "SOL_HCI", SOL_HCI),
                         getattr(socket, "HCI_FILTER", HCI_FILTER), _hci_filter())
            s.bind((self.dev_id,))
            self._sock = s
            self._fd = s.fileno()
            log.debug("HCI socket opened via socket module")
            return

        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        fd = libc.socket(AF_BLUETOOTH, socket.SOCK_RAW | getattr(socket, "SOCK_CLOEXEC", 0),
                         BTPROTO_HCI)
        if fd < 0:
            e = ctypes.get_errno()
            raise OSError(e, "HCI socket: " + os.strerror(e))
        try:
            flt = _hci_filter()
            if libc.setsockopt(fd, SOL_HCI, HCI_FILTER, flt, len(flt)) < 0:
                e = ctypes.get_errno()
                raise OSError(e, "HCI setsockopt: " + os.strerror(e))
            # struct sockaddr_hci { sa_family_t family; u16 dev; u16 channel; }
            addr = struct.pack("<HHH", AF_BLUETOOTH, self.dev_id, 0)
            if libc.bind(fd, addr, len(addr)) < 0:
                e = ctypes.get_errno()
                raise OSError(e, "HCI bind hci%d: %s" % (self.dev_id, os.strerror(e)))
        except Exception:
            os.close(fd)
            raise
        self._fd = fd
        log.debug("HCI socket opened via ctypes")

    def fileno(self):
        return self._fd

    def read(self):
        return os.read(self._fd, 1024)

    def close(self):
        if self._sock is not None:
            self._sock.close()
        elif self._fd is not None:
            os.close(self._fd)
        self._sock = None
        self._fd = None


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

class Config(object):
    def __init__(self, path=None):
        cp = configparser.ConfigParser()
        if path and os.path.exists(path):
            cp.read(path)
            log.info("config loaded from %s", path)
        else:
            log.info("no config file found (%s), using defaults", path)

        g = cp["general"] if cp.has_section("general") else {}
        self.loglevel = g.get("loglevel", "INFO").upper()
        self.hci_device = int(g.get("hci_device", 0))
        self.udp_host = g.get("udp_host", DEFAULT_UDP_HOST)
        self.udp_port = int(g.get("udp_port", DEFAULT_UDP_PORT))
        self.min_interval = float(g.get("min_interval", 5))
        self.idle_warning = float(g.get("idle_warning", 120))
        # Name given to new sensors whose CustomName is still empty:
        # "<name_prefix> <last 4 hex digits of the MAC>". Empty = disabled.
        self.name_prefix = g.get("name_prefix", "ThermoBeacon").strip()
        # Remove the leftover Victron settings of sensors that were never
        # enabled and have not been seen for N days. 0 = off. Max 29, because
        # the record of seen sensors forgets them after 30 days.
        self.cleanup_days = min(float(g.get("cleanup_days", 7)), 29.0)

        # Where seen sensors are recorded for 'bin/sensors.py list'. Empty = off.
        base = os.path.dirname(os.path.abspath(path)) if path else os.getcwd()
        self.state_file = g.get("state_file", os.path.join(base, "sensors.json")).strip()

        s = cp["sensors"] if cp.has_section("sensors") else {}
        self.devices = parse_mac_list(s.get("devices", ""))
        self.ignore = parse_mac_list(s.get("ignore", ""))


def parse_mac_list(raw):
    return set(normalize_mac(m) for m in raw.replace("\n", ",").split(",") if m.strip())


# --------------------------------------------------------------------------
# Default names
# --------------------------------------------------------------------------

BLE_SERVICE = "com.victronenergy.ble"
BUSITEM_IFACE = "com.victronenergy.BusItem"


def default_name(prefix, mac):
    """'ThermoBeacon', 'bb:ab:00:00:05:9b' -> 'ThermoBeacon 059B'"""
    return "%s %s" % (prefix, mac.replace(":", "")[-4:].upper())


def ble_device_id(mac):
    """dbus-ble-sensors device id for our Ruuvi encoded packets."""
    return "ruuvi_" + mac.replace(":", "")


class DbusNamer(object):
    """
    Gives a sensor a default CustomName in dbus-ble-sensors, but only once
    the user has enabled it (foreign sensors stay unnamed) and only while its
    CustomName is empty - a name set by the user is never touched.
    Returns True when done (named, or already has a name), False to retry.
    """

    def __init__(self):
        self._bus = None

    def __call__(self, mac, name):
        import dbus  # only available on the GX device
        if self._bus is None:
            self._bus = dbus.SystemBus()
        dev = "/Devices/%s/" % ble_device_id(mac)
        try:
            enabled = self._bus.get_object(BLE_SERVICE, dev + "Enabled", introspect=False)
            if int(enabled.GetValue(dbus_interface=BUSITEM_IFACE)) != 1:
                return False  # not (yet) enabled - check again later
            item = self._bus.get_object(BLE_SERVICE, dev + "CustomName", introspect=False)
            current = str(item.GetValue(dbus_interface=BUSITEM_IFACE))
        except dbus.exceptions.DBusException:
            return False  # device not created yet - try again later
        if current:
            return True
        # velib (C) insists on a variant, a plain string is rejected
        item.SetValue(dbus.String(name, variant_level=1), dbus_interface=BUSITEM_IFACE)
        log.info("sensor %s named '%s'", mac, name)
        return True


# --------------------------------------------------------------------------
# Record of seen sensors (read by bin/sensors.py)
# --------------------------------------------------------------------------

class SeenStore(object):
    """
    Keeps {mac: {first_seen, last_seen, rssi, temperature, humidity,
    voltage}} in a small JSON file. To spare the flash it is written at most
    every FLUSH_INTERVAL seconds, except right away when a new sensor shows up.
    """
    FLUSH_INTERVAL = 300
    EXPIRE = 30 * 86400  # drop sensors not seen for 30 days

    def __init__(self, path, wallclock=time.time):
        self.path = path
        self.wallclock = wallclock
        self.data = {}
        self._dirty = False
        self._last_flush = 0.0
        try:
            with open(path) as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            pass
        limit = self.wallclock() - self.EXPIRE
        for mac in [m for m, e in self.data.items() if e.get("last_seen", 0) < limit]:
            del self.data[mac]
            self._dirty = True

    def remove(self, mac):
        if self.data.pop(mac, None) is not None:
            self._dirty = True
            self.flush()

    def update(self, reading, rssi):
        now = self.wallclock()
        entry = self.data.get(reading.mac)
        new = entry is None
        if new:
            entry = self.data[reading.mac] = {"first_seen": int(now)}
        entry.update(last_seen=int(now), rssi=rssi,
                     temperature=round(reading.temperature, 2),
                     humidity=round(reading.humidity, 2),
                     voltage=round(reading.voltage_mv / 1000.0, 3))
        self._dirty = True
        if new or now - self._last_flush >= self.FLUSH_INTERVAL:
            self.flush()

    def flush(self):
        if not self._dirty:
            return
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(self.data, f, indent=1, sort_keys=True)
            os.replace(tmp, self.path)
            self._dirty = False
        except OSError as e:
            log.warning("could not write %s: %s", self.path, e)
        self._last_flush = self.wallclock()


# --------------------------------------------------------------------------
# Cleanup of never-enabled sensors
# --------------------------------------------------------------------------

SETTINGS_SERVICE = "com.victronenergy.settings"
SETTINGS_IFACE = "com.victronenergy.Settings"


def find_stale_sensors(settings, present, seen, now, max_age):
    """
    Decide which sensors' leftover Victron settings can go.

    settings: {path: value} of com.victronenergy.settings
    present:  set of device ids currently known to dbus-ble-sensors
    seen:     SeenStore.data
    Returns {mac: [setting paths]} for sensors that
      - this bridge has recorded (genuine RuuviTags are never touched),
      - were never enabled (no ClassAndVrmInstance, Enabled != 1),
      - have no custom name,
      - are no longer listed by dbus-ble-sensors,
      - were last seen more than max_age seconds ago.
    """
    out = {}
    for mac, entry in seen.items():
        if now - entry.get("last_seen", now) <= max_age:
            continue
        dev = ble_device_id(mac)
        if dev in present:
            continue
        prefix = "/Settings/Devices/%s/" % dev
        mine = {p[len(prefix):]: v for p, v in settings.items() if p.startswith(prefix)}
        if not mine:
            continue
        if "ClassAndVrmInstance" in mine or str(mine.get("Enabled", 0)) == "1":
            continue
        if str(mine.get("CustomName", "")):
            continue
        out[mac] = sorted(prefix + k for k in mine)
    return out


class SettingsJanitor(object):
    """D-Bus side of find_stale_sensors(). Returns the list of removed MACs."""

    def __init__(self):
        self._bus = None

    def __call__(self, seen, now, max_age):
        import dbus
        if self._bus is None:
            self._bus = dbus.SystemBus()

        def items(service):
            obj = self._bus.get_object(service, "/", introspect=False)
            return {str(k): v.get("Value") for k, v in
                    obj.GetItems(dbus_interface=BUSITEM_IFACE).items()}

        settings = {p: v for p, v in items(SETTINGS_SERVICE).items()
                    if p.startswith("/Settings/Devices/ruuvi_")}
        present = set()
        for p in items(BLE_SERVICE):
            if p.startswith("/Devices/ruuvi_"):
                present.add(p.split("/")[2])

        stale = find_stale_sensors(settings, present, seen, now, max_age)
        if stale:
            obj = self._bus.get_object(SETTINGS_SERVICE, "/", introspect=False)
            obj.RemoveSettings(sum(stale.values(), []), dbus_interface=SETTINGS_IFACE)
        return list(stale)


# --------------------------------------------------------------------------
# Bridge
# --------------------------------------------------------------------------

class Bridge(object):
    NAME_RETRY = 30            # seconds
    CLEANUP_INTERVAL = 3600    # seconds
    CLEANUP_FIRST = 120        # first run after start, once sensors are known

    def __init__(self, config, sender=None, clock=time.monotonic, namer=None, seen=None,
                 janitor=None):
        self.cfg = config
        self.clock = clock
        self._last_sent = {}
        self._known = set()
        self._ignored_logged = set()
        if seen is None and getattr(config, "state_file", ""):
            seen = SeenStore(config.state_file)
        self._seen = seen
        self._named = set()
        self._name_tried = {}
        if namer is None and getattr(config, "name_prefix", ""):
            namer = DbusNamer()
        self._namer = namer
        if janitor is None and self._seen is not None and getattr(config, "cleanup_days", 0) > 0:
            janitor = SettingsJanitor()
        self._janitor = janitor
        self._next_cleanup = clock() + self.CLEANUP_FIRST
        if sender is None:
            self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            dest = (config.udp_host, config.udp_port)
            sender = lambda pkt: self._udp.sendto(pkt, dest)
        self._send = sender

    def accepts(self, mac):
        if mac in getattr(self.cfg, "ignore", ()):
            return False
        return not self.cfg.devices or mac in self.cfg.devices

    def handle_adv(self, addr, adv_data, rssi):
        """Process one advertisement. Returns True if a packet was forwarded."""
        for ad_type, ad in iter_ad_structures(adv_data):
            if ad_type != AD_TYPE_MANUFACTURER:
                continue
            reading = decode_thermobeacon(addr, ad)
            if reading is None:
                continue
            if self._seen is not None:
                self._seen.update(reading, rssi)
            if not self.accepts(addr):
                if addr not in self._ignored_logged:
                    self._ignored_logged.add(addr)
                    log.info("ThermoBeacon %s ignored (config)", addr)
                return False
            return self._forward(reading, rssi)
        return False

    def _forward(self, reading, rssi):
        now = self.clock()
        if reading.mac not in self._known:
            self._known.add(reading.mac)
            log.info("ThermoBeacon %s found: %r (enable it once in Settings > "
                     "Integrations > Bluetooth sensors)", reading.mac, reading)

        last = self._last_sent.get(reading.mac)
        if last is not None and now - last < self.cfg.min_interval:
            return False
        self._last_sent[reading.mac] = now

        # The sensor's uptime is used as sequence number: it increases with
        # every new measurement and survives restarts of this bridge, so
        # dbus-ble-sensors' duplicate detection never drops fresh data.
        payload = encode_ruuvi_rawv2(reading, reading.uptime)
        pkt = build_udp_packet(reading.mac, MFG_ID_RUUVI, payload, rssi)
        try:
            self._send(pkt)
        except OSError as e:
            log.warning("UDP send to dbus-ble-sensors failed: %s", e)
            return False
        log.debug("forwarded %r rssi %d", reading, rssi)
        self._maybe_name(reading.mac, now)
        return True

    def maybe_cleanup(self, now):
        if self._janitor is None or self._seen is None or now < self._next_cleanup:
            return
        self._next_cleanup = now + self.CLEANUP_INTERVAL
        try:
            removed = self._janitor(self._seen.data, self._seen.wallclock(),
                                    self.cfg.cleanup_days * 86400)
        except Exception as e:
            log.warning("cleanup of unused sensors failed: %s", e)
            return
        for mac in removed:
            log.info("removed never-enabled sensor %s (not seen for more than "
                     "%g days)", mac, self.cfg.cleanup_days)
            self._seen.remove(mac)

    def close(self):
        if self._seen is not None:
            self._seen.flush()

    def _maybe_name(self, mac, now):
        if self._namer is None or mac in self._named:
            return
        last = self._name_tried.get(mac)
        if last is not None and now - last < self.NAME_RETRY:
            return
        self._name_tried[mac] = now
        try:
            done = self._namer(mac, default_name(self.cfg.name_prefix, mac))
        except Exception as e:  # never let naming break data forwarding
            log.warning("could not set name for %s: %s", mac, e)
            done = False
        if done:
            self._named.add(mac)

    def run(self, listener):
        last_adv = self.clock()
        warned = False
        while True:
            try:
                r, _, _ = select.select([listener], [], [], 10)
            except InterruptedError:
                continue
            now = self.clock()
            self.maybe_cleanup(now)
            if not r:
                if not warned and now - last_adv > self.cfg.idle_warning:
                    log.warning("no BLE advertisements received for %d s - is "
                                "'Bluetooth sensors' enabled on this GX device?",
                                now - last_adv)
                    warned = True
                continue
            try:
                pkt = listener.read()
            except OSError as e:
                if e.errno in (errno.EAGAIN, errno.EINTR):
                    continue
                raise
            for addr, data, rssi in parse_hci_event(pkt):
                last_adv = now
                if warned:
                    log.info("BLE advertisements received again")
                    warned = False
                self.handle_adv(addr, data, rssi)
