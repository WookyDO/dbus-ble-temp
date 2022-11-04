#!/bin/sh
#BASE=/data/drivers/ble-mqtt
BASE=$(dirname $(dirname $(realpath "$0")))

echo "Uninstall ble-mqtt from $BASE"

rm -f /service/ble-mqtt
rm -r $BASE
echo "Uninstall ble-mqtt complete"