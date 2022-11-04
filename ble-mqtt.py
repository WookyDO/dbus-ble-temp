#!/usr/bin/env python

from bluepy.btle import Scanner, DefaultDelegate # pylint: disable=import-error
from time import strftime, sleep
import paho.mqtt.client as mqtt

import struct
import json
import copy

global sensors
global portalId

clientid = "thermogw3"
version = "v1.0 ALPHA"
portalId = ""

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
        "CustomName": "Innenraum",
        "deviceId":""
    },
    "t2":
    {
        "devaddr": "19:c4:00:00:20:c8",
        "CustomName": "Kuehlschrank",
        "deviceId":""
    },
    "t3":
    {
        "devaddr": "19:c4:00:00:20:c9",
        "CustomName": "Doppelboden",
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
            #print ("Discovered device", dev.addr)
            pass
        elif isNewData:
            #print ("Received new data from", dev.addr)
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



def on_message(client, userdata, msg):
    global portalId
    global sensors
    print(msg.topic+" "+str(msg.payload))

    dbus_msg = json.loads(msg.payload)
    portalId = dbus_msg.get("portalId")
    deviceIds = dbus_msg.get("deviceInstance")
    for deviceId in deviceIds:
        sensors.get(deviceId)["deviceId"]=deviceIds[deviceId]

    for sensor in sensors:
        #print ("W/{0}/temperature/{1}/CustomName".format(portalId,sensors[sensor]["deviceId"]))
        client.publish("W/{0}/temperature/{1}/CustomName".format(portalId,sensors[sensor]["deviceId"]),json.dumps({"value":sensors[sensor]["CustomName"]}))


def on_connect(client, userdata, flags, rc):

    global registration
    for sensor in sensors:
        registration["services"][sensor]="temperature"
    print("Connected with result code "+str(rc))
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe("device/{}/DBus".format(clientid))
    client.publish("device/{}/Status".format(clientid), json.dumps(registration))

mqclient = mqtt.Client(clientid)
mqclient.on_message = on_message
mqclient.on_connect = on_connect
mqclient.will_set("device/{}/Status".format(clientid), json.dumps(unregister))
print("Enable mqtt...")
mqclient.connect("127.0.0.1", 1883, 60)
mqclient.loop_start()

print("Establishing scanner...")
scanner = Scanner().withDelegate(ScanDelegate())

while True:
    try:
        devices = scanner.scan(2.0)
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

                        print ("Device: {} Temperature: {} degC Humidity: {}% Uptime: {} sec Voltage: {}V".format(CurrentDevLoc,temperature_C,humidity_pct,uptime,voltage))

                        topic = "W/{0}/temperature/{1}/Temperature".format(portalId,CurrentDevInstance)
                        payload = json.dumps({"value":temperature_C})
                        mqclient.publish(topic,payload)
                        topic = "W/{0}/temperature/{1}/Humidity".format(portalId,CurrentDevInstance)
                        payload = json.dumps({"value":humidity_pct})
                        mqclient.publish(topic,payload)
        sleep(5)
    except KeyboardInterrupt:
       print("Program terminated manually!")
       mqclient.loop_stop()
       raise SystemExit