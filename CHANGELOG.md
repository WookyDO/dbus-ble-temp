# Changelog

## 1.2.1

### Added
- `setup.sh` warns when Bluetooth sensors are disabled and notes when
  continuous scanning is off.
- README: continuous scanning listed as recommended prerequisite.

### Fixed
- `sensors.py list`: `SEEN` lagged up to 5 minutes behind (record file is
  written only every 5 minutes); it now uses the age reported by Victron's
  service for sensors currently in range.

## 1.2.0

### Added
- Listens on **all Bluetooth adapters** (built-in and USB dongles), like
  Victron's own service. Previously only `hci0` was used, so sensors that
  only a second adapter could receive were missed.
- Adapters plugged in later are picked up within a minute; adapters that
  fail or are unplugged are dropped and reopened automatically.
- `sensors.py list` shows which adapters receive a sensor (`VIA`, with RSSI).

### Changed
- `hci_device` defaults to `all`; a list such as `0, 1` is accepted.

## 1.1.0

Complete rewrite. Not compatible with 0.x installations - install on a
GX device without the old version (or uninstall it first).

### Changed
- Readings are handed to Victron's own Bluetooth sensor service
  (`dbus-ble-sensors`) through its UDP gateway socket instead of publishing
  own D-Bus services. Sensors are enabled, renamed and monitored in
  Settings → Integrations → Bluetooth sensors and get timeout handling and a
  low battery alarm.
- Passive listening on a raw HCI socket instead of scanning with `bluepy`.
- No dependencies: `bluepy`, `gcc`, `bluez5-dev`, `pip` and the
  `velib_python` submodule are no longer needed; no partition resize.
- Configuration in `config.ini` instead of a MAC list in the code.

### Added
- Automatic detection of all ThermoBeacons in range; new sensors appear
  disabled until enabled in the GUI.
- Enabled sensors are named `ThermoBeacon XXXX` unless they already have a name.
- `bin/sensors.py` to list sensors and to ignore, unignore or forget them.
- Automatic cleanup of never-enabled sensors not seen for 7 days.
- `setup.sh` checks for the gateway socket, can be re-run safely and keeps a
  single autostart line; `uninstall.sh` stops the service and removes the
  autostart line.
- Unit tests and CI (tests, pyflakes, shellcheck, release zip check).

### Fixed (compared to 0.x)
- Crash loop after firmware updates (`No module named 'bluepy'`).
- One sensor without manufacturer data aborted the whole scan cycle.
- Values of a sensor that went out of range were shown forever.

## 0.4.0 and older

Own D-Bus services via `velib_python`, scanning with `bluepy`.
