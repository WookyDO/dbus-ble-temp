#!/bin/sh
# Remove dbus-ble-temp. Use --keep-files to keep the installation directory.
BASE=$(dirname "$(dirname "$(realpath "$0")")")
SERVICE=/service/dbus-ble-temp
RCLOCAL=/data/rc.local

echo "Uninstall dbus-ble-temp from $BASE"

if [ -e "$SERVICE" ]; then
    svc -dx "$SERVICE" "$SERVICE/log" 2>/dev/null || true
    rm -f "$SERVICE"
fi

if [ -f "$RCLOCAL" ]; then
    sed -i '\#/service/dbus-ble-temp#d' "$RCLOCAL"
fi

if [ "$1" != "--keep-files" ]; then
    cd / || exit 1
    rm -rf "$BASE"
fi

echo "Uninstall complete. Sensors already known to dbus-ble-sensors can be"
echo "removed in Settings > Integrations > Bluetooth sensors."
