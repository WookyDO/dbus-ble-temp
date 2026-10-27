# dbus-ble-temp

Shows **ThermoBeacon** Bluetooth thermometers/hygrometers (Oria, Brifit,
SensorBlue and other clones) on Victron **Venus OS** GX devices such as the
Cerbo GX or Ekrano GX, as native temperature sensors:

- temperature, humidity and battery voltage in the GUI, on VRM and in Node-RED
- enable, disable and rename sensors in **Settings → Integrations → Bluetooth sensors**
- automatic detection of new sensors, low battery alarm, "disconnected" when
  a sensor goes out of range
- no compiler, no extra packages, survives firmware updates

dbus-ble-temp does not publish sensors itself. It decodes the ThermoBeacon
broadcasts and hands the readings to Victron's own Bluetooth sensor service
(`dbus-ble-sensors`), which does everything else. See
[How it works](docs/how-it-works.md).

> **Note** — Victron's service has no ThermoBeacon support, so the readings
> are passed on in RuuviTag format. The sensors therefore show the product
> name *RuuviTag*; their name is `ThermoBeacon XXXX` (last digits of the MAC).

## Contents

1. [Requirements](#requirements)
2. [Installation](#installation)
3. [Adding sensors](#adding-sensors)
4. [Foreign and unwanted sensors](#foreign-and-unwanted-sensors)
5. [Configuration](#configuration)
6. [Updating and uninstalling](#updating-and-uninstalling)
7. [Troubleshooting](#troubleshooting)
8. [FAQ](#faq)

## Requirements

- A GX device with built-in Bluetooth (Cerbo GX, Ekrano GX, ...) running a
  Venus OS whose Bluetooth sensor service has the UDP gateway socket.
  Tested on **Venus OS v3.85**; `setup.sh` checks this and stops if it is
  missing.
- **Bluetooth sensors enabled**: Settings → Integrations → Bluetooth
  sensors. Without it nothing scans for Bluetooth devices.
- **Continuous scanning** (same menu) - recommended, not required. Without it
  Victron's service scans in short windows and only about one in six sensor
  broadcasts is received: updates come less often and weak sensors may
  briefly show as disconnected. `setup.sh` prints a note if it is off.
- **Root access via SSH**, see Victron's
  [Root access](https://www.victronenergy.com/live/ccgx:root_access) guide
  (set a root password, enable SSH on LAN).
- One or more ThermoBeacon sensors.

## Installation

Log in as root (`ssh root@<ip-of-your-gx>`) and run:

```sh
mkdir -p /data/drivers/dbus-ble-temp
cd /data/drivers/dbus-ble-temp
wget -O dbus-ble-temp.zip https://github.com/WookyDO/dbus-ble-temp/releases/latest/download/dbus-ble-temp.zip
unzip -o dbus-ble-temp.zip && rm dbus-ble-temp.zip
sh bin/setup.sh
```

`setup.sh`

- checks that Victron's Bluetooth sensor service and its UDP socket exist,
- creates `config.ini` from `config.ini.example` (only the first time),
- installs and starts the service and makes it start again after a reboot
  (via `/data/rc.local`).

It can be run again at any time, e.g. after an update; it never duplicates
anything.

Everything lives in `/data`, so firmware updates do not remove it.

## Adding sensors

Every ThermoBeacon in range is detected automatically. Within a few seconds
it appears **disabled** in

**Settings → Integrations → Bluetooth sensors**

Enable the sensors you want. An enabled sensor

- appears as temperature sensor in the device list, on VRM and in Node-RED,
- is named `ThermoBeacon XXXX` (last four digits of its MAC address), unless
  you have given it a name already. Rename it there or in the device list at
  any time; dbus-ble-temp never overwrites a name you set.

To see what is in range, with signal strength and last reading:

```console
# /data/drivers/dbus-ble-temp/bin/sensors.py list
MAC                STATUS          NAME               SEEN  RSSI  VIA                 READING     FIRST
bb:ab:00:00:05:9b  enabled         Fridge             12s   -52   hci0 -52            4.3°C 58%   3d
bc:13:00:00:0b:e1  enabled         ThermoBeacon 0BE1  15s   -58   hci1 -58, hci0 -71  19.5°C 58%  3d
c0:ff:ee:00:00:01  NEW (disabled)  Ruuvi 0001         40s   -91   hci0 -91            12.0°C 80%  2m
```

`STATUS` is one of `enabled`, `NEW (disabled)`, `ignored` (see below) or
`gone` (not in range at the moment). `VIA` lists the Bluetooth adapters that
received the sensor in the last 10 minutes, strongest first.

### More range with a second adapter

dbus-ble-temp listens on **all** Bluetooth adapters of the GX device, like
Victron's own service. If a sensor is too far away for the built-in adapter,
a USB Bluetooth dongle placed closer to it (e.g. on a USB extension cable)
extends the range; it is picked up automatically within a minute of being
plugged in. `VIA` in `sensors.py list` shows which adapter receives what.

## Foreign and unwanted sensors

Sensors of neighbours (marina, campsite, apartment building) are detected as
well, but they stay **disabled** and never show up anywhere except in the
Bluetooth sensors list. What happens next depends on whether they stay in
range:

| Situation | What happens | Action needed |
|---|---|---|
| Sensor only passes by | Drops out of the list 30 minutes after its last reading. If it was never enabled, its leftover settings are deleted after 7 days (`cleanup_days`). | none |
| Sensor stays in range for good | Stays in the list as disabled. | `sensors.py ignore` |
| You enabled a sensor by mistake | Shows up in the GUI. | `sensors.py forget` or `ignore` |

On the GX device:

```sh
cd /data/drivers/dbus-ble-temp
bin/sensors.py ignore 0001     # never forward it again and remove it from the GUI
bin/sensors.py unignore 0001   # allow it again, it reappears as NEW
bin/sensors.py forget 0BE1     # reset name / enabled / VRM instance, reappears as NEW
```

A sensor can be given as full MAC address (`c0:ff:ee:00:00:01`,
`c0ffee000001`) or as the last digits shown in its name (`0001`), as long as
that is unique.

`ignore` and `forget` restart Victron's Bluetooth sensor service, so the
sensor is gone from the GUI at once; **all** Bluetooth sensors (including
e.g. Mopeka or Ruuvi) reconnect within a few seconds. Add `--no-restart` to
avoid that; the sensor then disappears within 30 minutes.

> **Tip:** the signal strength (`RSSI`) tells your sensors from foreign ones.
> Sensors on board are usually stronger than -80 dBm; values around -90 dBm
> are typically somebody else's.

## Configuration

`/data/drivers/dbus-ble-temp/config.ini`; every option is explained in
[`config.ini.example`](config.ini.example). Restart after a change:

```sh
svc -t /service/dbus-ble-temp
```

| Option | Default | Meaning |
|---|---|---|
| `[general] loglevel` | `INFO` | `DEBUG` logs every reading |
| `[general] hci_device` | `all` | Bluetooth adapters: `all`, or e.g. `0` / `0, 1` |
| `[general] udp_host`, `udp_port` | `127.0.0.1`, `18542` | Victron's Bluetooth sensor service |
| `[general] min_interval` | `5` | at most one update per sensor every N seconds |
| `[general] name_prefix` | `ThermoBeacon` | name for newly enabled sensors; empty = off |
| `[general] cleanup_days` | `7` | delete never-enabled sensors not seen for N days (max 29, 0 = off) |
| `[general] idle_warning` | `120` | log a warning when no Bluetooth traffic at all for N seconds |
| `[general] state_file` | `sensors.json` | record of seen sensors, used by `sensors.py list` |
| `[sensors] devices` | *(empty)* | only forward these MACs; empty = all ThermoBeacons |
| `[sensors] ignore` | *(empty)* | never forward these MACs; managed by `sensors.py ignore` |

## Updating and uninstalling

**Update** — download and unpack the new release over the old one, then run
`setup.sh` again. `config.ini` and the list of seen sensors are kept.

```sh
cd /data/drivers/dbus-ble-temp
wget -O dbus-ble-temp.zip https://github.com/WookyDO/dbus-ble-temp/releases/latest/download/dbus-ble-temp.zip
unzip -o dbus-ble-temp.zip && rm dbus-ble-temp.zip
sh bin/setup.sh
```

**Uninstall**

```sh
sh /data/drivers/dbus-ble-temp/bin/uninstall.sh               # removes everything
sh /data/drivers/dbus-ble-temp/bin/uninstall.sh --keep-files  # keeps the directory
```

The sensors stay in the Bluetooth sensors list until they time out (30
minutes); disabled ones can then be ignored, enabled ones show as
disconnected and can be removed from the device list.

## Troubleshooting

```sh
svstat /service/dbus-ble-temp                          # running?
tail -F /var/log/dbus-ble-temp/current | tai64nlocal   # live log
bin/sensors.py list                                    # what is seen
```

A healthy start looks like this:

```
INFO     dbus-ble-temp v1.2.1 starting: all adapters -> udp 127.0.0.1:18542, auto-detecting all ThermoBeacons
INFO     listening on Bluetooth adapter hci0
INFO     listening on Bluetooth adapter hci1
INFO     ThermoBeacon bb:ab:00:00:05:9b found: Reading(bb:ab:00:00:05:9b, 19.25 degC, 58.00 %, 2880 mV, uptime 2131727 s) (enable it once in Settings > Integrations > Bluetooth sensors)
INFO     sensor bb:ab:00:00:05:9b named 'ThermoBeacon 059B'
```

| Symptom | Cause / fix |
|---|---|
| `setup.sh`: *no UDP gateway socket* | Venus OS too old - update the firmware. |
| Log: *no BLE advertisements received* | Bluetooth sensors are disabled in the GUI, or the adapter is down. |
| Log: *cannot listen on hciN* | The adapter could not be opened; retried every minute. Check `hciconfig -a`. |
| Log: *HCI ... Operation not permitted* | Not running as root. |
| Sensor in the log, but not in the GUI list | Check the socket: `dbus -y com.victronenergy.settings /Settings/BleSensors/Socket/BindAddress GetValue` must be `127.0.0.1`. |
| Sensor appears only now and then | Turn on *Continuous scanning* (see [Requirements](#requirements)). Otherwise weak signal (see `RSSI` in `sensors.py list`): move the sensor, the GX device or add a USB Bluetooth adapter. |
| Values look wrong | Run in the foreground with debug output: `svc -d /service/dbus-ble-temp; python3 dbus-ble-temp.py -d`, stop with Ctrl-C, then `svc -u /service/dbus-ble-temp`. |

Still stuck? Please open an [issue](https://github.com/WookyDO/dbus-ble-temp/issues)
with the output of `sensors.py list` and the last 50 log lines.

## FAQ

**Why "RuuviTag"?** Victron's Bluetooth service supports a fixed set of
sensor types and ThermoBeacon is not one of them. RuuviTag is a supported
temperature/humidity sensor with an open data format, so the readings are
passed on in that format. Only the product name shows this.

**Does it interfere with other Bluetooth sensors or VictronConnect?** No.
dbus-ble-temp never scans or connects; it only listens to the broadcasts
Victron's own scan already receives.

**Does it work with real RuuviTags at the same time?** Yes. They are handled
by Victron directly, and dbus-ble-temp never touches their settings.

**Which ThermoBeacon models?** Every model broadcasting the common 20-byte
ThermoBeacon format (temperature, humidity, battery, uptime); the sensor's
own MAC address inside the data is used to recognise it.

## Documentation

- [How it works](docs/how-it-works.md) — architecture, data formats, design decisions
- [Development](docs/development.md) — tests, deploying to a test device, releases
- [Changelog](CHANGELOG.md)

## Thanks

ThermoBeacon decoding based on [rnlgreen/thermobeacon](https://github.com/rnlgreen/thermobeacon).
Victron Energy for the open source [dbus-ble-sensors](https://github.com/victronenergy/dbus-ble-sensors).

## License

MIT, see [LICENSE](LICENSE).
