#!/usr/bin/env python

from bluepy.btle import Scanner, DefaultDelegate # pylint: disable=import-error
from time import strftime
import struct
import json


#Enter the MAC address of the sensors
SENSORS = {"19:c4:00:00:20:c5": "Innenraum"}

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


def on_message(client, userdata, msg):  # The callback for when a PUBLISH message is received from the server.
    global portalId
    global deviceTempInstance

    x = json.loads(msg.payload)
    portalId = x["portalId"]
    deviceTempInstance = x["deviceInstance"]["t1"]

print ("Enable MQTT")
clientid = "thermogw"
import paho.mqtt.client as mqtt
mqttBroker ="127.0.0.1"
mqclient = mqtt.Client(clientid)
mqclient.on_message = on_message
mqclient.connect(mqttBroker)
mqclient.loop_start()

version = "v1.0 ALPHA"
topic = "device/{0}/DBus".format(clientid)
status = "device/{0}/Status".format(clientid)
payload = json.dumps({ "clientId": clientid, "connected": 1, "version": version, "services":{"t1": "temperature"} })
try:
    mqclient.subscribe(topic)
    mqclient.publish(status,payload)
except Exception as e:
    print ("MQ Init failed: {}".format(e))


print("Establishing scanner...")
scanner = Scanner().withDelegate(ScanDelegate())

try:
    while True:
        print ("Initiating scan...")
        devices = scanner.scan(5.0)

        for dev in devices:
            if dev.addr in SENSORS:
                CurrentDevAddr = dev.addr
                CurrentDevLoc = SENSORS[dev.addr]
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

                    topic = "W/{0}/temperature/{1}/Temperature".format(portalId,deviceTempInstance)
                    payload = json.dumps({"value":temperature_C})
                    mqclient.publish(topic,payload)
                    topic = "W/{0}/temperature/{1}/Humidity".format(portalId,deviceTempInstance)
                    payload = json.dumps({"value":humidity_pct})
                    mqclient.publish(topic,payload)

                else:
                    print ("Ignoring invalid data length for {}: {}".format(CurrentDevLoc,len(manufacturer_bytes)))

except DecodeErrorException:
    print("Decode Exception")
    pass