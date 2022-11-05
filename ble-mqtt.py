#!/usr/bin/env python

from bluepy.btle import Scanner, DefaultDelegate # pylint: disable=import-error
from time import strftime, sleep
import paho.mqtt.client as mqtt
import logging
import argparse
import struct
import json
import copy
import sys
import os

# Victron packages
sys.path.insert(1, os.path.join(os.path.dirname(__file__), 'ext', 'velib_python'))
from logger import setup_logging



global sensors
global portalId

clientid = "thermo_gw4"
version = "v1.0 ALPHA"
softwareVersion = "v0.2.0"
portalId = "notSet"

registration = {
    "clientId": clientid,
    "connected": 1,
    "version": version,
    "services": {}
    }

unregister = copy.deepcopy(registration)
unregister["connected"] = 0

sensors = {
    "t1":
    {
        "devaddr": "19:c4:00:00:20:c5",
        "CustomName": "Room2",
        "deviceId":""
    },
    "t2":
    {
        "devaddr": "19:c4:00:00:20:c8",
        "CustomName": "Fridge2",
        "deviceId":""
    }
}




class DecodeErrorException(Exception):
     def __init__(self, value):
         self.value = value
     def __str__(self):
         return repr(self.value)

class ScanDelegate(DefaultDelegate):
    def __init__(self):
        DefaultDelegate.__init__(self)

    def handleDiscovery(self, dev, isNewDev, isNewData):
        if isNewDev:
            logging.debug ("Discovered device {}".format(dev.addr))
            pass
        elif isNewData:
            logging.debug ("Received new data from  {}".format(dev.addr))
            pass


def convert_uptime(t):
    # convert seconds to day, hour, minutes and seconds
    days = t // (24 * 3600)
    t = t % (24 * 3600)
    hours = t // 3600
    t %= 3600
    minutes = t // 60
    t %= 60
    seconds = t
    return "{} Days {} Hours {} Minutes {} Seconds".format(days,hours,minutes,seconds)


def on_disconnect(client, userdata, rc):
    logging.info("disconnecting reason {}".format(rc))
    client.connected_flag=False
    client.disconnect_flag=True

def on_message(client, userdata, msg):
    global portalId
    global sensors
    logging.info("New Message, Topic: {0}, Content: {1}".format(msg.topic,msg.payload))

    dbus_msg = json.loads(msg.payload)
    portalId = dbus_msg.get("portalId")
    deviceIds = dbus_msg.get("deviceInstance")
    for deviceId in deviceIds:
        sensors.get(deviceId)["deviceId"]=deviceIds[deviceId]

    for sensor in sensors:
        logging.debug ("W/{0}/temperature/{1}/CustomName".format(portalId,sensors[sensor]["deviceId"]))
        client.publish("W/{0}/temperature/{1}/CustomName".format(portalId,sensors[sensor]["deviceId"]),json.dumps({"value":sensors[sensor]["CustomName"]}))


def on_connect(client, userdata, flags, rc):

    global registration
    if rc==0:
        logging.debug("on_connect_flag: {}".format(client.connected_flag))
        client.connected_flag=True
        logging.info ("MQTT connected, Status: {0}".format(rc))
    else:
        logging.info ("Bad connection Returned code={}".format(rc))
    for sensor in sensors:
        registration["services"][sensor]="temperature"
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe("device/{}/DBus".format(clientid))
    client.publish("device/{}/Status".format(clientid), json.dumps(registration))


def main():
    parser = argparse.ArgumentParser(description='Publishes values from ble-devices to dbus-mqtt-devices')
    parser.add_argument('-d', '--debug', help='set logging level to debug', action='store_true')
    args = parser.parse_args()

    logging.info("-------- ble-mqtt, v{} is starting up --------".format(version))
    logger = setup_logging(args.debug)

    mqclient = mqtt.Client(clientid)
    mqclient.connected_flag = False
    mqclient.on_message = on_message
    mqclient.on_connect = on_connect
    mqclient.on_disconnect = on_disconnect

    mqclient.will_set("device/{}/Status".format(clientid), json.dumps(unregister))
    logging.info("Enable mqtt...")
    mqclient.connect("127.0.0.1", 1883, 60)
    mqclient.loop_start()

    logging.info("Establishing scanner...")
    scanner = Scanner().withDelegate(ScanDelegate())

    while not mqclient.connected_flag: #wait in loop
        logging.info("wait for mqtt...")
        sleep(1)
    ti=30
    while portalId=="notSet":
        ti-=1
        if ti==0:
            logging.error("no portalId assigned, dbus-mqtt-devices working ?")
            quit()
        logging.info("wait for portalId ({}sec)...".format(ti))
        sleep(1)
    while True:
        try:
            logging.info("Scanning 2.0sec for Devices")
            devices = scanner.scan(2.0, passive=True)
            for dev in devices:
                for sensor in sensors:
                    if dev.addr in sensors[sensor]["devaddr"]:
                        CurrentDevInstance = sensors[sensor]["deviceId"]
                        CurrentDevLoc = sensors[sensor]["CustomName"]
                        manufacturer_hex = next(value for _, desc, value in dev.getScanData() if desc == 'Manufacturer')
                        manufacturer_bytes = bytes.fromhex(manufacturer_hex)

                        if len(manufacturer_bytes) == 20:
                            e6, e5, e4, e3, e2, e1, voltage, temperature_raw, humidity_raw, uptime_seconds = struct.unpack('xxxxBBBBBBHHHI', manufacturer_bytes)

                            temperature_C = temperature_raw / 16.
                            temperature_F = temperature_C * 9. / 5. + 32.
                            humidity_pct = humidity_raw / 16.

                            voltage = voltage / 1000

                            uptime = convert_uptime(uptime_seconds)

                            uptime_days = uptime_seconds / 86400

                            logging.info ("Device: {} Temperature: {} degC Humidity: {}% Uptime: {} sec Voltage: {}V".format(CurrentDevLoc,temperature_C,humidity_pct,uptime,voltage))

                            topic = "W/{0}/temperature/{1}/Temperature".format(portalId,CurrentDevInstance)
                            payload = json.dumps({"value":temperature_C})
                            mqclient.publish(topic,payload)
                            topic = "W/{0}/temperature/{1}/Humidity".format(portalId,CurrentDevInstance)
                            payload = json.dumps({"value":humidity_pct})
                            mqclient.publish(topic,payload)
            sleep(30)
        except KeyboardInterrupt:
            logging.info("Program terminated manually!")
            mqclient.loop_stop()
            SystemExit()

if __name__ == '__main__':
    # Argument parsing
	parser = argparse.ArgumentParser(
		description='Reads from BLE Thermometers and send to dbus-mqtt-devices'
	)

	parser.add_argument("-d", "--debug", help="set logging level to debug",
					action="store_true")

	args = parser.parse_args()

	print("-------- dbus_systemcalc, v" + softwareVersion + " is starting up --------")
	logger = setup_logging(args.debug)

	main()