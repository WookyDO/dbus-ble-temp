#!/bin/sh
#BASE=/data/drivers/ble-mqtt
BASE=$(dirname $(dirname $(realpath "$0")))

echo "Setup ble-mqtt in $BASE started"
cd $BASE

echo "Ensure Python's Pip is installed"
python -m pip --version
piperr=$?
if [ "$piperr" -ne 0 ]; then
    opkg update && opkg install python3-pip
fi

echo "Install gcc"
opkg update && opkg install gcc

echo "Install make"
opkg update && opkg install make
echo "Install bluez5-dev"
opkg update && opkg install bluez5-dev

echo "Pip install module dependencies"
python -m pip install -r requirements.txt

echo "Set up device service to autorun on restart"
chmod +x $BASE/ble-mqtt.py
# Use awk to inject correct BASE path into the run script
awk -v base=$BASE '{gsub(/\$\{BASE\}/,base);}1' $BASE/bin/service/run.tmpl >$BASE/bin/service/run
chmod -R a+rwx $BASE/bin/service
rm -f /service/ble-mqtt
ln -s $BASE/bin/service /service/ble-mqtt

CMD="ln -s $BASE/bin/service /service/ble-mqtt"
if ! grep -q "$CMD" /data/rc.local; then
    echo "$CMD" >> /data/rc.local
fi
chmod +x /data/rc.local
echo "Setup ble-mqtt complete"