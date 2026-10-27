#!/bin/sh
# Install / update dbus-ble-temp as a daemontools service on Venus OS.
# Safe to run repeatedly.
set -e

BASE=$(dirname "$(dirname "$(realpath "$0")")")
SERVICE=/service/dbus-ble-temp
RCLOCAL=/data/rc.local
LINK_CMD="ln -sfn $BASE/bin/service $SERVICE"

echo "Setup dbus-ble-temp in $BASE"

# --- Pre-flight: Victron's BLE sensor service with UDP gateway socket -------
if ! command -v dbus >/dev/null 2>&1; then
    echo "ERROR: 'dbus' tool not found - this does not look like Venus OS." >&2
    exit 1
fi

PORT=$(dbus -y com.victronenergy.settings /Settings/BleSensors/Socket/Port GetValue 2>/dev/null || true)
BIND=$(dbus -y com.victronenergy.settings /Settings/BleSensors/Socket/BindAddress GetValue 2>/dev/null || true)
if [ -z "$PORT" ] || [ "$PORT" = "None" ]; then
    echo "ERROR: dbus-ble-sensors on this device has no UDP gateway socket" >&2
    echo "       (/Settings/BleSensors/Socket/Port missing). Please update Venus OS." >&2
    exit 1
fi
BIND=$(echo "$BIND" | tr -d "'\"")
if [ -z "$BIND" ]; then
    echo "WARNING: the dbus-ble-sensors UDP socket is disabled (BindAddress empty)."
    echo "         Enable it with:"
    echo "         dbus -y com.victronenergy.settings /Settings/BleSensors/Socket/BindAddress SetValue 127.0.0.1"
fi
echo "dbus-ble-sensors UDP socket: ${BIND:-disabled}:$PORT"

# --- Config ----------------------------------------------------------------
if [ ! -f "$BASE/config.ini" ]; then
    cp "$BASE/config.ini.example" "$BASE/config.ini"
    echo "Created $BASE/config.ini"
fi

# --- Service ---------------------------------------------------------------
chmod +x "$BASE/dbus-ble-temp.py" "$BASE/bin/"*.sh "$BASE/bin/"*.py "$BASE/bin/service/log/run"
awk -v base="$BASE" '{gsub(/\$\{BASE\}/,base);}1' "$BASE/bin/service/run.tmpl" >"$BASE/bin/service/run"
chmod 755 "$BASE/bin/service/run"

if [ -L "$SERVICE" ] && [ "$(readlink "$SERVICE")" = "$BASE/bin/service" ]; then
    svc -u "$SERVICE" 2>/dev/null || true   # bring up if it was stopped
    svc -t "$SERVICE" 2>/dev/null || true
    echo "Service (re)started"
else
    $LINK_CMD
    echo "Service linked to $SERVICE"
fi

# --- Autostart after reboot (/service is a tmpfs on Venus OS) ---------------
if [ ! -f "$RCLOCAL" ]; then
    printf '#!/bin/sh\n' >"$RCLOCAL"
fi
# drop entries of older versions of this script, then add ours exactly once
sed -i '\#/service/dbus-ble-temp#d' "$RCLOCAL"
echo "$LINK_CMD" >>"$RCLOCAL"
chmod +x "$RCLOCAL"

echo "Setup complete. Log: tail -F /var/log/dbus-ble-temp/current | tai64nlocal"
echo "Then enable the sensors in Settings > Integrations > Bluetooth sensors."
