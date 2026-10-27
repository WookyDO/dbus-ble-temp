# How it works

## Overview

```
 ThermoBeacon ──BLE broadcast──▶ hci0, hci1, ... (Bluetooth adapters of the GX device)
                                   │
            ┌──────────────────────┴──────────────────────┐
            ▼                                             ▼
   dbus-ble-sensors (Victron)                 dbus-ble-temp (this project)
   scans continuously,                        listens passively on a raw
   ignores ThermoBeacon data                  HCI socket, never scans
            ▲                                             │
            │   UDP 127.0.0.1:18542                       │ decode ThermoBeacon
            └──────── RuuviTag format 5 packet ◀──────────┘ encode as RuuviTag
            │
            ▼
   com.victronenergy.temperature.ruuvi_<mac>  ──▶ GUI, VRM, Node-RED, MQTT
```

Venus OS ships `dbus-ble-sensors`, Victron's service for Bluetooth sensors
(RuuviTag, Mopeka, Gobius, SolarSense, ...). It scans for Bluetooth broadcasts,
decodes the ones it knows, and publishes each sensor as a D-Bus service with
enable switch, custom name, VRM instance, timeout and alarms.

Since 2026 it also accepts sensor data from Bluetooth *gateways* through a
local UDP socket (`/Settings/BleSensors/Socket/*`, default
`127.0.0.1:18542`). dbus-ble-temp uses exactly that interface: it acts as a
gateway that delivers ThermoBeacon readings in a format `dbus-ble-sensors`
understands.

Everything a user sees - the sensor list, enabling, names, alarms, VRM - is
Victron's code. dbus-ble-temp is about 1000 lines of Python (including the
command line tool) without any third-party dependency.

## Receiving: passive raw HCI socket

`dbus-ble-sensors` runs a continuous passive LE scan on every Bluetooth
adapter (built-in and USB dongles) through raw HCI sockets. The Linux kernel
delivers every HCI event to *all* raw HCI sockets bound to that adapter, so
a second socket receives the same advertising reports without starting a
scan of its own.

`HciListener` (in `thermobeacon_bridge.py`) handles one adapter:

- opens `AF_BLUETOOTH / SOCK_RAW / BTPROTO_HCI` and binds it to `hciN`,
- sets an HCI filter for *LE Meta events* only,
- never sends a command to the adapter.

A raw HCI socket only receives events of the one adapter it is bound to, so
`HciHub` keeps one `HciListener` per adapter (`hci_device = all`, or a
list):

- adapters are found in `/sys/class/bluetooth` and rescanned every 60
  seconds, so a USB dongle plugged in later is picked up,
- an adapter whose socket fails (unplugged, reset) is dropped and reopened
  on a later rescan,
- an adapter that is present but down (no address) costs nothing; its
  socket simply receives data once it comes up.

A sensor heard by two adapters arrives twice; the per-sensor rate limit
(`min_interval`) forwards only one of them. The seen-sensor record keeps the
RSSI per adapter (`sensors.py list` → `VIA`).

Venus OS' Python is built without Bluetooth support in the `socket` module,
so the socket is created through `libc` via `ctypes`. Kernels from 6.10 on
copy the filter with `copy_safe_from_sockptr()` and require the full
`struct hci_ufilter` including tail padding (16 bytes, not 14).

Both legacy (`0x02`) and extended (`0x0D`) LE advertising reports are parsed.

Consequence: no data arrives when *Bluetooth sensors* is disabled in the GUI,
because then nothing scans. The bridge logs a warning after `idle_warning`
seconds without any Bluetooth traffic.

## Decoding ThermoBeacon

Manufacturer specific data (AD type `0xFF`), 20 bytes, little endian:

| Offset | Size | Field |
|---|---|---|
| 0 | 2 | company id (differs between models, e.g. `0x0010`, `0x0011`, `0x0015`) |
| 2 | 2 | flags (byte 2, bit 7 = button pressed) |
| 4 | 6 | MAC address of the sensor, LSB first |
| 10 | 2 | battery voltage, mV |
| 12 | 2 | temperature, 1/16 °C, signed |
| 14 | 2 | humidity, 1/16 % |
| 16 | 4 | uptime, seconds |

Because the company id is not consistent across models, a packet is
recognised as ThermoBeacon by its length **and** by the embedded MAC address
matching the sender. ThermoBeacons also send a 22-byte min/max packet, which
is ignored.

## Forwarding: RuuviTag data format 5

`dbus-ble-sensors` only dispatches data to its built-in decoders, so the
reading is re-encoded as RuuviTag data format 5 ("RAWv2", 24 bytes, big
endian) - an open format of a supported temperature/humidity sensor:

| Field | Value |
|---|---|
| temperature | from ThermoBeacon, 0.005 °C steps |
| humidity | from ThermoBeacon, clamped to 0-100 %, 0.0025 % steps |
| pressure, acceleration, TX power, movement | *invalid* - not published |
| battery | from ThermoBeacon, clamped to 1.600-3.646 V |
| sequence number | lower 16 bits of the sensor's uptime |
| MAC | sensor MAC |

The sequence number matters: `dbus-ble-sensors` drops packets whose sequence
number goes backwards within a window. The ThermoBeacon uptime increases with
every measurement and survives restarts of dbus-ble-temp, so fresh data is
never mistaken for a duplicate. One quirk is worked around: `dbus-ble-sensors`
treats the raw battery value `0x3FF` (2.623 V) as invalid, so it is sent as
`0x3FE`.

The packet is wrapped in the UDP gateway format v1 of `dbus-ble-sensors`
(version, flags, sensor address, manufacturer id `0x0499`, payload, RSSI) and
sent to `127.0.0.1:18542`. At most one packet per sensor is sent every
`min_interval` seconds.

The resulting D-Bus service is `com.victronenergy.temperature.ruuvi_<mac>`
with `/Temperature`, `/Humidity`, `/BatteryVoltage`, `/Connected`,
`/CustomName`, `/Alarms/LowBattery`, ...

## Names

`dbus-ble-sensors` names a RuuviTag `Ruuvi XXXX` and ignores Bluetooth names
for it. Once a sensor is **enabled** and its custom name is still empty,
dbus-ble-temp sets `/Devices/ruuvi_<mac>/CustomName` on
`com.victronenergy.ble` to `ThermoBeacon XXXX` (`name_prefix` + last four
MAC digits). Disabled - possibly foreign - sensors are never named, and a
name set by the user is never changed. `velib` requires the value to be sent
as a D-Bus variant.

## Sensor lifecycle

```
            in range                 enabled in GUI
  (new) ───────────────▶ NEW ─────────────────────────▶ enabled ──▶ named
                          │ (disabled)                     │
                          │                                │ out of range
          out of range    │                                ▼
          30 min          ▼                          disconnected
                    dropped from list                  (stays in the
                          │                             device list)
         7 days unseen,   │
         never enabled    ▼
                    settings deleted
                    (cleanup_days)
```

- **Seen sensors** are recorded in `sensors.json` (first/last seen, RSSI,
  last reading). The file is written right away for a new sensor and
  otherwise at most every 5 minutes to spare the flash memory; entries not
  seen for 30 days are dropped.
- **Cleanup** runs 2 minutes after start and then hourly. It deletes the
  settings `/Settings/Devices/ruuvi_<mac>/*` of a sensor only if
  1. dbus-ble-temp recorded it as ThermoBeacon (genuine RuuviTags are never
     touched),
  2. it was never enabled (no `ClassAndVrmInstance`, `Enabled` ≠ 1),
  3. it has no custom name,
  4. `dbus-ble-sensors` no longer lists it,
  5. it was last seen more than `cleanup_days` days ago.
- **ignore** adds the MAC to `[sensors] ignore` in `config.ini`; the bridge
  still records the sensor (so `list` can show it) but never forwards it.
  The sensor's settings are deleted and `dbus-ble-sensors` is restarted so
  it disappears at once - `dbus-ble-sensors` itself has no way to remove a
  device, it only expires devices 30 minutes after their last packet.
- **forget** stops the bridge, removes the sensor from `sensors.json` and
  its Victron settings, restarts `dbus-ble-sensors` and starts the bridge
  again. If the sensor is still in range it comes back as NEW.

## Files and processes

| Path | Purpose |
|---|---|
| `/data/drivers/dbus-ble-temp/` | installation directory |
| `dbus-ble-temp.py` | entry point (daemon) |
| `thermobeacon_bridge.py` | HCI listener, decoder, encoder, naming, cleanup |
| `bin/sensors.py` | command line tool |
| `bin/setup.sh`, `bin/uninstall.sh` | install / remove |
| `bin/service/` | daemontools service, linked to `/service/dbus-ble-temp` |
| `config.ini` | configuration (not overwritten by updates) |
| `sensors.json` | record of seen sensors |
| `/var/log/dbus-ble-temp/current` | log (multilog, 4 × 25 kB) |
| `/data/rc.local` | one line re-creating the service link at boot (`/service` is a tmpfs) |

## Design decisions

**Gateway instead of own D-Bus services.** Earlier versions (0.x) created
their own `com.victronenergy.temperature` services with `velib_python` and
scanned with `bluepy`. That meant compiling `bluepy` on the GX device (gcc,
bluez headers, often a resized root partition), being broken by every
firmware update that replaced Python, no timeout handling and no GUI for
enabling sensors. Using Victron's gateway socket removes all of that.

**RuuviTag format instead of a native decoder.** A ThermoBeacon decoder in
`dbus-ble-sensors` itself would be the cleanest solution and would show the
right product name, but it is C code in a system service: it would have to be
cross-compiled and would be overwritten by each firmware update until
accepted upstream. The RuuviTag format carries exactly the fields a
ThermoBeacon has.

**Listening instead of scanning.** Two processes configuring scans on one
adapter disturb each other. Listening on a second raw HCI socket is free.

## Limitations

- Product name and default name come from the RuuviTag decoder of
  `dbus-ble-sensors` (*RuuviTag*, *Ruuvi XXXX* until enabled).
- The low battery alarm uses RuuviTag thresholds (2.5 V, lower when cold).
- No data while *Bluetooth sensors* is disabled in the GUI.
