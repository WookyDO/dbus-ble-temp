# dbus-ble-temp

*** Important to know!!! ***
NOT TESTED ON CCGX

This tool needs additional libraries (gcc, make, bluez5-dev) which may use a big amount of storage, it may be necessary to extend your partition before usage: 
```
/opt/victronenergy/swupdate-scripts/resize2fs.sh
```

It pulls data from ThermoBeacon (Oria and other Clones )and publishes it to Victron Dbus

## Contents
1. [Install and Setup](#Install-and-Setup)
2. [Configuration](#Configuration)
3. [Troubleshooting](#Troubleshooting)
4. [To Do](#To-Do)
5. [Thanks](#thanks)


## Install and Setup 

### Install and setup (not CCGX)

To get the driver up and running, download the latest release from github and then run the setup script.

1. ssh into venus device (as root)

2. Download the latest zip from github and extract contents

```
$ mkdir -p /data/drivers/dbus-ble-temp
$ cd /data/drivers/dbus-ble-temp
$ wget -O dbus-ble-temp.zip https://github.com/wolfganghuse/dbus-ble-temp/releases/download/v0.4.0/dbus-ble-temp.zip
$ unzip dbus-ble-temp.zip
```

3. Run the set up script
```
$ ./bin/setup.sh
```

4. Check the contents of /data/rc.local to ensure dbus-ble-temp automatically starts on reboot
```
$ cat /data/rc.local
ln -s /data/drivers/dbus-ble-temp/bin/service /service/dbus-ble-temp
```

5. Reboot (recommended)
```
$ reboot
```


## Configuration
	

## Troubleshooting
1) First thing to check is that the dbus-ble-temp service is running, from the ssh command line use
```
$ svstat /service/dbus-ble-temp
```
More info on deamontools that VenusOs uses here: https://cr.yp.to/daemontools.html

2) If the service is not running then ensure that your rc.local script has execute permissions.
```
$ ls -l /data/rc.local
...
$ chmod +x /data/rc.local
```
3) If the service is running, then next thing to check is the log with the command:
```
$ more /var/log/dbus-ble-temp/current
```
It should contain something like this:
```
abc
```

3) If you have re-installed more than once, make sure there is only one line in your rc.local for dbus-mqtt-devices.
```
$ more /data/rc.local 
```

4) If you are still having a problem feel free to open an issue on the Github project here: https://github.com/wolfganghuse/dbus-ble-temp/issues
I get email alerts from Github which I don't seem to get from the Victron community forum.


## To Do
- add check for extended storage partition
- external config file

## Thanks
Based on the thermobeacon decoder from https://github.com/rnlgreen/thermobeacon
