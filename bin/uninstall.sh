#!/bin/sh
#BASE=/data/drivers/dbus-ble-temp
BASE=$(dirname $(dirname $(realpath "$0")))

echo "Uninstall dbus-ble-temp from $BASE"

rm -f /service/dbus-ble-temp
rm -r $BASE
echo "Uninstall dbus-ble-temp complete"