#!/usr/bin/env python3

from gi.repository import GLib
import platform
import argparse
import struct
import logging
import sys
import os
import dbus
from bluepy.btle import Scanner, DefaultDelegate # pylint: disable=import-error



# our own packages
AppDir = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(1, os.path.join(AppDir, 'ext', 'velib_python'))
from vedbus import VeDbusService
from settingsdevice import SettingsDevice
from vedbus import VeDbusItemImport


devicelist = {
    "db:5c:8d:a0:0f:21",
    "fa:ac:00:00:17:21",
    "dc:12:00:00:12:62",
    "bc:13:00:00:0b:e1",
    "bb:ab:00:00:05:9b",
    "00:00:00:00:00:de"
}

sensors = {}

def devconnection(devAddr):
    rvalue = devAddr.lower()
    rvalue = rvalue.replace(':','')
    return rvalue

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
            pass
        elif isNewData:
            pass


class DbusBLETempService(object):
    def __init__(self, servicename, servicetype,  deviceinstance,  paths, productname, connection):
        logging.debug("Init new Device: %s_%s" % (servicename, connection))
        self._dbus_conn = (dbus.SessionBus(private=True) if 'DBUS_SESSION_BUS_ADDRESS' in os.environ else dbus.SystemBus(private=True))
        self._dbusservice = VeDbusService('com.victronenergy.{}.{}_{}'.format(servicetype,servicename,connection),self._dbus_conn)

        self._servicetype = servicetype
        self._paths = paths
        self._connection = connection

        local_settings = {
            '/CustomName': ["/Settings/Devices/{}_{}/CustomName".format(servicename, connection), 'My {} Sensor'.format(self._servicetype.capitalize()), 0, 0],
            '/TemperatureType': ["/Settings/Devices/{}_{}/TemperatureType".format(servicename, connection), 2, 0, 2],
        }

        self._settings = SettingsDevice(
            bus=self._dbus_conn,
            supportedSettings=local_settings,
            eventCallback=self._handle_changed_setting)

        settings_device_path = "/Settings/Devices/{}_{}/ClassAndVrmInstance".format(servicename, connection)
        requested_device_instance = "{}:{}".format(servicetype,deviceinstance)
        r = self._settings.addSetting(settings_device_path, requested_device_instance, "", "")
        s, self.device_instance = r.get_value().split(':')

        # Create the management objects, as specified in the ccgx dbus-api document
        self._dbusservice.add_path('/Mgmt/ProcessName', __file__)
        self._dbusservice.add_path('/Mgmt/ProcessVersion', 'Unkown version, and running on Python ' + platform.python_version())
        self._dbusservice.add_path('/Mgmt/Connection', connection)

        # Create the mandatory objects
        self._dbusservice.add_path('/DeviceInstance', self.device_instance)
        self._dbusservice.add_path('/ProductId', 41314)
        self._dbusservice.add_path('/ProductName', productname)
        self._dbusservice.add_path('/FirmwareVersion', 0)
        self._dbusservice.add_path('/HardwareVersion', 0)
        self._dbusservice.add_path('/Connected', 1)

        for path, settings in self._paths.items():
            logging.debug('init: {} {}'.format(path, settings))
            if path in self._settings._values:
                logging.debug ('found settings for {}'.format(path))
                logging.debug (self._settings.__getitem__(path))
                settings['initial']=self._settings.__getitem__(path)
            self._dbusservice.add_path(
                path, settings['initial'], writeable=True, onchangecallback=self._handlechangedvalue)

    def _handle_changed_setting(self, setting, oldvalue, newvalue):
        logging.debug("Settings: {} {} {}".format(setting, oldvalue, newvalue))
        if oldvalue != newvalue:
            self._dbusservice[setting]=newvalue

    def _handlechangedvalue(self, path, value):
        logging.debug("someone else updated %s to %s" % (path, value))
        self._settings[path]=value
        return True # accept the change

def scan():
    scanner = Scanner().withDelegate(ScanDelegate())

    #logging.debug("Scanning 3.0sec for Devices")
    try:
        devices = scanner.scan(3.0, passive=True)
    except:
        return True
    for dev in devices:
        if (dev.addr in devicelist):
            try:
                manufacturer_hex = next(value for _, desc, value in dev.getScanData() if desc == 'Manufacturer')
            except:
                logging.debug("catched data error")
                return True
            manufacturer_bytes = bytes.fromhex(manufacturer_hex)

            if len(manufacturer_bytes) == 20:
                e6, e5, e4, e3, e2, e1, voltage, temperature_raw, humidity_raw, uptime_seconds = struct.unpack('xxxxBBBBBBHHHI', manufacturer_bytes)

                temperature_C = temperature_raw / 16.

                if temperature_C > 4000:
                    temperature_C = temperature_C - 4096
                humidity_pct = humidity_raw / 16.

                voltage = voltage / 1000

                uptime = convert_uptime(uptime_seconds)
                
                devaddr = devconnection(dev.addr)
                if (not devaddr in sensors):
                    logging.info ("Known device found")
                    logging.info ("new device {} added to dbus".format(devaddr))

                    sensors[devaddr] = DbusBLETempService(
                    servicename='dbus_ble_3',
                    servicetype='temperature',
                    productname='Generic Temperature Input',
                    connection=devaddr,
                    deviceinstance=0,
                    paths={
                        '/Temperature': {'initial': 0},
                        '/TemperatureType': {'initial': 2},
                        '/Humidity': {'initial': 0},
                        '/BatteryVoltage': {'initial': 0},
                        '/CustomName': {'initial': 'Tempsensor'},
                    })
                    
                logging.info ("Device: {} Temperature: {} degC Humidity: {}% Uptime: {} sec Voltage: {}V".format(devaddr,temperature_C,humidity_pct,uptime,voltage))
                sensors[devaddr]._dbusservice["/Temperature"]=temperature_C
                sensors[devaddr]._dbusservice["/Humidity"]=humidity_pct
                sensors[devaddr]._dbusservice["/BatteryVoltage"]=voltage
            

    return True

# === All code below is to simply run it from the commandline for debugging purposes ===

def main():

    logging.basicConfig(format='%(asctime)s %(levelname)-8s %(message)s', level=logging.DEBUG, datefmt='%Y-%m-%d %H:%M:%S')

    from dbus.mainloop.glib import DBusGMainLoop
    # Have a mainloop, so we can send/receive asynchronous calls to and from dbus
    DBusGMainLoop(set_as_default=True)

    temp_output = {}


    GLib.timeout_add(10000,scan)
    logging.info('Connected to dbus, and switching over to GLib.MainLoop() (= event based)')
    mainloop = GLib.MainLoop()
    mainloop.run()


if __name__ == "__main__":
    main()