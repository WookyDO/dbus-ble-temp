#!/usr/bin/env python3
"""
dbus-ble-temp - feed ThermoBeacon BLE thermometers into Venus OS.

The readings are handed to Victron's own BLE sensor service
(dbus-ble-sensors) via its UDP gateway socket. See README.md.
"""

import argparse
import logging
import os
import signal
import sys

AppDir = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, AppDir)

from thermobeacon_bridge import Bridge, Config, HciHub, describe_adapters, log  # noqa: E402

__version__ = "1.2.1"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-c", "--config", default=os.path.join(AppDir, "config.ini"),
                    help="config file (default: %(default)s)")
    ap.add_argument("-d", "--debug", action="store_true", help="debug logging")
    args = ap.parse_args()

    logging.basicConfig(format="%(levelname)-8s %(message)s", level=logging.INFO)
    cfg = Config(args.config)
    logging.getLogger().setLevel("DEBUG" if args.debug else cfg.loglevel)

    log.info("dbus-ble-temp v%s starting: %s -> udp %s:%d, %s%s",
             __version__, describe_adapters(cfg.hci_devices), cfg.udp_host, cfg.udp_port,
             "sensors %s" % ", ".join(sorted(cfg.devices)) if cfg.devices
             else "auto-detecting all ThermoBeacons",
             ", ignoring %s" % ", ".join(sorted(cfg.ignore)) if cfg.ignore else "")

    hub = HciHub(cfg.hci_devices)
    bridge = Bridge(cfg)
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))  # svc -t / -d
    try:
        bridge.run(hub)
    except KeyboardInterrupt:
        pass
    finally:
        bridge.close()
        hub.close()


if __name__ == "__main__":
    main()
