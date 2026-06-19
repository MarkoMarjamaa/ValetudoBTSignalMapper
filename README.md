# ValetudoBTSignalMapper
Attach BT devices on vacuum, reads vacuums location and bt signal strength and makes heatmap

## Install
```pip install bleak aiohttp numpy scipy matplotlib```
## Get BT devices mac 
```python rssi_logger.py --discover```
## Start logger and star vacuum
```python rssi_logger.py --robot-ip 192.168.1.50 --mac A4:C1:38:XX:XX:XX```

Or if you want to use your devices name instead or device is changing it's mac

```python rssi_logger.py --robot-ip 192.168.1.50 --name your_device```
## Create heatmap

You can log several devices at once and filter them when creating heatmap 

```python make_heatmap.py --name your_device --flip-y rssi_log.csv```

## Resulting heatmap

<img width="1300" height="1040" alt="heatmap" src="https://github.com/user-attachments/assets/350a7d16-01bd-4adc-8eac-c1a921bb7ccb" />
