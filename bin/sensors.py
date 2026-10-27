#!/usr/bin/env python3
"""
Manage the ThermoBeacons seen by dbus-ble-temp.

  sensors.py list                 show all detected ThermoBeacons
  sensors.py ignore MAC [MAC..]   block foreign/unwanted sensors and remove them
  sensors.py unignore MAC [MAC..] allow them again
  sensors.py forget MAC [MAC..]   reset a sensor (name, enabled, instance),
                                  it shows up again as new on the next reading

MAC may be given as 'bb:ab:00:00:05:9b', 'bbab0000059b' or as the last digits
shown in the name, e.g. '059B', as long as that is unique.

ignore/forget restart dbus-ble-sensors so the sensor disappears from the GUI
at once; all Bluetooth sensors are briefly reconnected. Use --no-restart to
skip that (the sensor then drops out of the list within 30 minutes).
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, BASE)

from thermobeacon_bridge import (  # noqa: E402
    BLE_SERVICE, BUSITEM_IFACE, Config, ble_device_id, normalize_mac)

SETTINGS_SERVICE = "com.victronenergy.settings"
SETTINGS_IFACE = "com.victronenergy.Settings"
BRIDGE_SERVICE = "/service/dbus-ble-temp"
BLE_SENSORS_SERVICE = "/service/dbus-ble-sensors"

CONFIG = os.path.join(BASE, "config.ini")


# --------------------------------------------------------------------------
# config.ini editing (keeps comments and layout)
# --------------------------------------------------------------------------

def set_config_list(path, section, key, values):
    """Set 'key = a, b, c' in [section], keeping everything else untouched."""
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []

    value = ", ".join(sorted(values))
    new_line = "%s = %s" % (key, value)
    sec_re = re.compile(r"^\s*\[(.+?)\]\s*$")
    key_re = re.compile(r"^\s*%s\s*[=:]" % re.escape(key))

    start = end = None
    for i, line in enumerate(lines):
        m = sec_re.match(line)
        if m:
            if start is not None and end is None:
                end = i
            if m.group(1).strip() == section:
                start = i
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += ["[%s]" % section, new_line]
    else:
        if end is None:
            end = len(lines)
        for i in range(start + 1, end):
            if key_re.match(lines[i]):
                # drop continuation lines of a multi-line value
                j = i + 1
                while j < end and lines[j][:1] in (" ", "\t") and lines[j].strip():
                    j += 1
                lines[i:j] = [new_line]
                break
        else:
            # insert after the last non-empty line of the section
            k = end
            while k > start + 1 and not lines[k - 1].strip():
                k -= 1
            lines.insert(k, new_line)

    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# D-Bus helpers
# --------------------------------------------------------------------------

_bus = None


def bus():
    global _bus
    if _bus is None:
        import dbus
        _bus = dbus.SystemBus()
    return _bus


def get_items(service):
    try:
        obj = bus().get_object(service, "/", introspect=False)
        return obj.GetItems(dbus_interface=BUSITEM_IFACE)
    except Exception:
        return {}


def ble_devices():
    """{device_id: {Enabled, CustomName, Name, Age}} from dbus-ble-sensors."""
    out = {}
    for path, item in get_items(BLE_SERVICE).items():
        m = re.match(r"^/Devices/(ruuvi_[0-9a-f]{12})/(\w+)$", str(path))
        if m:
            out.setdefault(m.group(1), {})[m.group(2)] = item.get("Value")
    return out


def device_settings(dev_id):
    prefix = "/Settings/Devices/%s/" % dev_id
    return sorted(str(p) for p in get_items(SETTINGS_SERVICE) if str(p).startswith(prefix))


def remove_settings(paths):
    if not paths:
        return
    obj = bus().get_object(SETTINGS_SERVICE, "/", introspect=False)
    obj.RemoveSettings(paths, dbus_interface=SETTINGS_IFACE)


def svc(flag, service):
    subprocess.call(["svc", flag, service])


def wait_down(service, timeout=10):
    """Wait until daemontools reports the service as down."""
    end = time.time() + timeout
    while time.time() < end:
        out = subprocess.run(["svstat", service], stdout=subprocess.PIPE,
                             universal_newlines=True).stdout
        if ": down" in out:
            return True
        time.sleep(0.5)
    return False


def drop_from_seen(cfg, macs):
    """Remove sensors from the record file - only while the bridge is stopped."""
    data = load_seen(cfg)
    if not any(m in data for m in macs):
        return
    for m in macs:
        data.pop(m, None)
    tmp = cfg.state_file + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, cfg.state_file)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def load_seen(cfg):
    try:
        with open(cfg.state_file) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def ago(ts):
    if not ts:
        return "-"
    d = int(time.time() - ts)
    if d < 120:
        return "%ds" % d
    if d < 7200:
        return "%dm" % (d // 60)
    if d < 172800:
        return "%dh" % (d // 3600)
    return "%dd" % (d // 86400)


def last_seen(entry, device, now):
    """
    Time of the last reading. The record file is only written every few
    minutes, so for sensors dbus-ble-sensors currently lists, its 'Age'
    (updated every 10 s) is more recent.
    """
    ts = entry.get("last_seen")
    age = device.get("Age")
    if age not in (None, ""):
        try:
            ts = max(ts or 0, now - int(age))
        except (TypeError, ValueError):
            pass
    return ts


def via(entry, recent=600):
    """Adapters that heard the sensor recently, strongest first: 'hci0 -52, hci1 -71'."""
    now = time.time()
    ads = [(a.get("rssi", -999), name) for name, a in entry.get("adapters", {}).items()
           if now - a.get("seen", 0) <= recent]
    return ", ".join("%s %d" % (n, r) for r, n in sorted(ads, reverse=True))


def resolve(macs, known):
    """Expand short forms ('059B') against the known sensors."""
    out = []
    for m in macs:
        try:
            out.append(normalize_mac(m))
            continue
        except ValueError:
            pass
        tail = m.lower().replace(":", "")
        hits = [k for k in known if k.replace(":", "").endswith(tail)]
        if len(hits) != 1:
            sys.exit("'%s' is %s" % (m, "ambiguous: " + ", ".join(hits) if hits else
                                     "not a MAC address and matches no known sensor"))
        out.append(hits[0])
    return out


def cmd_list(cfg, args):
    seen = load_seen(cfg)
    ble = ble_devices()
    macs = set(seen) | cfg.ignore | cfg.devices
    # Ruuvi entries in dbus-ble-sensors are only ours if we have seen them;
    # genuine RuuviTags are left alone.
    if not macs:
        print("No ThermoBeacons seen yet.")
        return

    now = time.time()
    last = {m: last_seen(seen.get(m, {}), ble.get(ble_device_id(m), {}), now) for m in macs}

    rows = []
    for mac in sorted(macs, key=lambda m: -(last[m] or 0)):
        s = seen.get(mac, {})
        d = ble.get(ble_device_id(mac), {})
        if mac in cfg.ignore:
            status = "ignored"
        elif cfg.devices and mac not in cfg.devices:
            status = "not in devices"
        elif not d:
            status = "gone" if s else "never seen"
        else:
            status = "enabled" if int(d.get("Enabled") or 0) == 1 else "NEW (disabled)"
        name = str(d.get("CustomName") or d.get("Name") or "")
        temp = "%.1f°C %.0f%%" % (s["temperature"], s["humidity"]) if "temperature" in s else ""
        rows.append((mac, status, name, ago(last[mac]),
                     str(s.get("rssi", "")), via(s), temp, ago(s.get("first_seen"))))

    hdr = ("MAC", "STATUS", "NAME", "SEEN", "RSSI", "VIA", "READING", "FIRST")
    w = [max(len(r[i]) for r in rows + [hdr]) for i in range(len(hdr))]
    for r in [hdr] + rows:
        print("  ".join(c.ljust(w[i]) for i, c in enumerate(r)).rstrip())

    if any(r[1].startswith("NEW") for r in rows):
        print("\nNEW sensors: enable wanted ones in Settings > Integrations > "
              "Bluetooth sensors,\nremove foreign ones with:  %s ignore <MAC>" % sys.argv[0])


def _remove_from_victron(macs):
    for mac in macs:
        paths = device_settings(ble_device_id(mac))
        remove_settings(paths)
        print("%s: removed %d Victron setting(s)" % (mac, len(paths)))


def _restart(args, ble_too):
    svc("-t", BRIDGE_SERVICE)
    if ble_too and not args.no_restart:
        print("restarting dbus-ble-sensors (all Bluetooth sensors reconnect)...")
        svc("-t", BLE_SENSORS_SERVICE)


def cmd_ignore(cfg, args):
    macs = resolve(args.mac, set(load_seen(cfg)) | cfg.devices)
    set_config_list(CONFIG, "sensors", "ignore", cfg.ignore | set(macs))
    if cfg.devices & set(macs):
        set_config_list(CONFIG, "sensors", "devices", cfg.devices - set(macs))
    print("added to ignore list: %s" % ", ".join(macs))
    svc("-t", BRIDGE_SERVICE)          # stop forwarding before cleaning up
    time.sleep(2)
    _remove_from_victron(macs)
    _restart(args, ble_too=True)


def cmd_unignore(cfg, args):
    macs = resolve(args.mac, cfg.ignore)
    missing = [m for m in macs if m not in cfg.ignore]
    if missing:
        print("not ignored: %s" % ", ".join(missing))
    set_config_list(CONFIG, "sensors", "ignore", cfg.ignore - set(macs))
    print("allowed again: %s - it shows up as NEW sensor on its next reading"
          % ", ".join(m for m in macs if m not in missing))
    _restart(args, ble_too=False)


def cmd_forget(cfg, args):
    macs = resolve(args.mac, set(load_seen(cfg)) | cfg.devices)
    svc("-d", BRIDGE_SERVICE)
    if not wait_down(BRIDGE_SERVICE):
        print("warning: bridge did not stop in time")
    drop_from_seen(cfg, macs)       # bridge has written its state on exit
    _remove_from_victron(macs)
    if not args.no_restart:
        print("restarting dbus-ble-sensors (all Bluetooth sensors reconnect)...")
        svc("-t", BLE_SENSORS_SERVICE)
    svc("-u", BRIDGE_SERVICE)
    print("done - the sensor(s) show up as NEW on the next reading")


def main():
    global CONFIG
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--config", default=CONFIG, help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list", help="show detected sensors")
    for name, fn in (("ignore", cmd_ignore), ("unignore", cmd_unignore), ("forget", cmd_forget)):
        p = sub.add_parser(name)
        p.add_argument("mac", nargs="+")
        p.add_argument("--no-restart", action="store_true",
                       help="do not restart dbus-ble-sensors")
        p.set_defaults(fn=fn)
    args = ap.parse_args()

    CONFIG = args.config
    import logging
    logging.disable(logging.INFO)  # silence 'config loaded' messages
    cfg = Config(CONFIG)

    if args.cmd in (None, "list"):
        cmd_list(cfg, args)
    else:
        args.fn(cfg, args)


if __name__ == "__main__":
    main()
