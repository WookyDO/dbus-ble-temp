# Development

## Layout

```
dbus-ble-temp.py          daemon entry point
thermobeacon_bridge.py    all logic: HCI listeners (one per adapter), decoder, Ruuvi encoder,
                          UDP sender, naming, seen-sensor record, cleanup
bin/sensors.py            command line tool (list / ignore / unignore / forget)
bin/setup.sh              install + pre-flight checks, idempotent
bin/uninstall.sh          remove service, autostart line and files
bin/service/              daemontools service (run.tmpl -> run, log/run)
config.ini.example        documented default configuration
tests/                    unit tests (unittest, no dependencies)
docs/                     documentation
```

No third-party packages. Code must run on the Python shipped with Venus OS
(3.12 on v3.85) and is tested on 3.9 and newer. Note that Venus OS' Python
lacks some standard modules (e.g. `unittest`) and has no Bluetooth support in
`socket`.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

The tests run on any machine, no Bluetooth or D-Bus needed. They cover

- the ThermoBeacon decoder, checked against the original 0.x implementation,
- the RuuviTag encoding, checked against a Python port of the register table
  in `dbus-ble-sensors/src/ruuvi.c`,
- the UDP packet layout, checked against `ble_socket_parse()` in
  `dbus-ble-sensors/src/ble-socket.c`,
- HCI event parsing (legacy and extended reports, malformed input),
- several adapters: discovery, hot-plug, removal, read errors, `VIA` record,
- rate limiting, device and ignore lists, naming, seen-sensor record,
  cleanup decisions and scheduling,
- `config.ini` editing (comments preserved) and MAC shortcuts of `sensors.py`.

Code talking to D-Bus (`DbusNamer`, `SettingsJanitor`, `bin/sensors.py`) is
kept thin; the decisions behind it (`find_stale_sensors`, ...) are plain
functions with tests.

## Trying it on a GX device

Copy the working tree to a test device and run in the foreground:

```sh
rsync -av --exclude .git --exclude tests --exclude config.ini \
      ./ root@<gx>:/data/drivers/dbus-ble-temp/
ssh root@<gx>
cd /data/drivers/dbus-ble-temp
sh bin/setup.sh                       # first time / after changing bin/
svc -d /service/dbus-ble-temp         # stop the service ...
python3 dbus-ble-temp.py -d           # ... and run with debug output
```

Useful commands on the device:

```sh
dbus -y | grep -E "temperature|ble"                              # services
dbus -y com.victronenergy.ble / GetItems | tr ',' '\n' | grep Devices/
dbus -y com.victronenergy.temperature.ruuvi_<mac> /Temperature GetValue
dbus -y com.victronenergy.settings /Settings/BleSensors/Socket/Port GetValue
```

A sensor can be simulated without hardware by sending a packet to the UDP
socket, e.g. from Python on the device:

```python
import socket, struct, time, thermobeacon_bridge as tb
mac = "c0:ff:ee:00:00:01"
mfg = struct.pack("<HH6sHhHI", 0x10, 0, tb.bdaddr_from_mac(mac), 3000, 12 * 16, 80 * 16,
                  int(time.time()) & 0xFFFFFF)
r = tb.decode_thermobeacon(mac, mfg)
pkt = tb.build_udp_packet(mac, tb.MFG_ID_RUUVI, tb.encode_ruuvi_rawv2(r, r.uptime), -90)
socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(pkt, ("127.0.0.1", 18542))
```

Remove it again with `bin/sensors.py forget 0001`.

## Continuous integration

`.github/workflows/main.yml` runs on every push and pull request:

- unit tests on Python 3.9 and 3.12,
- `pyflakes` on all Python files,
- `shellcheck` on the shell scripts,
- a test build of the release zip, checking that it contains everything
  needed and nothing else.

## Releases

1. Update `__version__` in `dbus-ble-temp.py` and `CHANGELOG.md`.
2. Tag and push: `git tag v1.2.0 && git push origin v1.2.0`.

The workflow then builds `dbus-ble-temp.zip` (program files, `bin/`,
`config.ini.example`, `README.md`, `LICENSE`, `CHANGELOG.md`) and attaches it
to a GitHub release. The install instructions always download the latest
release.
