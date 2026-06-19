#!/usr/bin/env python3
"""
rssi_logger.py — log BLE beacon RSSI against Valetudo robot position.

Idea: tape a continuously-advertising BLE device (e.g. a plant meter) to the
robot. This PC listens to its advertising packets and pairs every RSSI reading
with the robot's current position, polled from Valetudo's REST API. Output is a
CSV you can turn into a heatmap with make_heatmap.py.

We never CONNECT to the beacon — only listen to broadcasts — so several beacons
can be logged at once and nothing interferes with their normal operation.

Requires: pip install bleak aiohttp      (bleak is cross-platform: BlueZ/WinRT/CoreBT)

Usage:
	# 1. find out which MAC is your plant meter:
	python rssi_logger.py --discover

	# 2. log it while you drive the robot around:
	python rssi_logger.py --robot-ip 192.168.1.50 --mac A4:C1:38:XX:XX:XX
"""

import argparse
import asyncio
import csv
import sys
import time
from datetime import datetime

import aiohttp
from bleak import BleakScanner


# --------------------------------------------------------------------------
# Shared state: position is updated by one task, read by the scan callback.
# --------------------------------------------------------------------------
class State:
	def __init__(self):
		self.x = None
		self.y = None
		self.angle = None
		self.pos_ts = 0.0     # time.monotonic() of last good position fix
		self.stop = False


state = State()


def extract_robot_position(map_data):
	"""Pull the robot_position entity out of a Valetudo map-state JSON.

	Returns (x, y, angle) in the map's native units (centimetres), or None.
	"""
	for ent in map_data.get("entities", []):
		if ent.get("type") == "robot_position":
			pts = ent.get("points", [])
			if len(pts) >= 2:
				angle = ent.get("metaData", {}).get("angle")
				return float(pts[0]), float(pts[1]), angle
	return None


async def poll_position(robot_ip, interval, session):
	"""Repeatedly fetch /api/v2/robot/state/map and cache the robot position."""
	url = f"http://{robot_ip}/api/v2/robot/state/map"
	while not state.stop:
		t0 = time.monotonic()
		try:
			async with session.get(
				url, timeout=aiohttp.ClientTimeout(total=5)
			) as resp:
				data = await resp.json()
			pos = extract_robot_position(data)
			if pos:
				state.x, state.y, state.angle = pos
				state.pos_ts = time.monotonic()
		except Exception as e:  # network blip, robot rebooting, etc. — keep going
			print(f"[pos] poll error: {e}", file=sys.stderr)
		# hold cadence regardless of how long the request took
		await asyncio.sleep(max(0.0, interval - (time.monotonic() - t0)))


def make_callback(target_macs, target_names, writer, fh, max_pos_age):
	"""Build the bleak detection callback that writes paired rows.

	Matching is by MAC and/or advertised name. Name matching matters for
	devices that use a Resolvable Private Address (e.g. the SRS-NB10): their
	MAC rotates every few minutes, but the name stays put. When we recognise a
	device by name we remember its current random address, so the RSSI-only
	packets that follow (which carry no name) still match — until it rotates,
	at which point the next named packet re-learns the new address.
	"""
	macs = {m.upper() for m in target_macs} if target_macs else set()
	names = set(target_names) if target_names else set()
	learned = {}  # random address -> stable label (the name it matched)

	def matched_label(device, adv):
		"""Return a stable label if this device is a target, else None."""
		addr = device.address.upper()
		name = adv.local_name or device.name
		if addr in learned:
			return learned[addr]
		if name and name in names:
			learned[addr] = name  # latch onto the current rotating address
			return name
		if addr in macs:
			return name or addr
		return None

	def cb(device, adv):
		label = matched_label(device, adv)
		if label is None:
			return

		rssi = adv.rssi
		now = time.monotonic()
		pos_age = (now - state.pos_ts) if state.pos_ts else None

		# Drop readings we can't anchor to a fresh position.
		if state.x is None or pos_age is None or pos_age > max_pos_age:
			reason = "no position yet" if state.x is None else f"stale {pos_age:.1f}s"
			print(f"[skip] {device.address} rssi={rssi} ({reason})")
			return

		writer.writerow([
			datetime.now().isoformat(timespec="milliseconds"),
			device.address,
			label,
			rssi,
			f"{state.x:.1f}",
			f"{state.y:.1f}",
			"" if state.angle is None else state.angle,
			f"{pos_age:.2f}",
		])
		fh.flush()  # crash-safe: every reading is on disk immediately
		print(f"[log] {label}  rssi={rssi:4d}  "
			  f"x={state.x:.0f} y={state.y:.0f}  pos_age={pos_age:.1f}s")

	return cb


async def run(args):
	fh = open(args.out, "a", newline="")
	writer = csv.writer(fh)
	if fh.tell() == 0:
		writer.writerow(["wall_time", "mac", "name", "rssi",
						 "x_cm", "y_cm", "angle", "pos_age_s"])

	async with aiohttp.ClientSession() as session:
		pos_task = asyncio.create_task(
			poll_position(args.robot_ip, args.poll, session)
		)
		scanner = BleakScanner(
			detection_callback=make_callback(
				args.mac, args.name, writer, fh, args.max_pos_age
			)
		)
		await scanner.start()
		print("Scanning — drive the robot around the house. Ctrl-C to stop.\n")
		try:
			while True:
				await asyncio.sleep(1)
		except asyncio.CancelledError:
			pass
		finally:
			state.stop = True
			await scanner.stop()
			pos_task.cancel()
			fh.close()
			print(f"\nSaved -> {args.out}")


async def discover(duration):
	"""Scan briefly and print everything in range so you can spot your meter."""
	seen = {}

	def cb(device, adv):
		seen[device.address] = (device.name, adv.rssi)

	scanner = BleakScanner(detection_callback=cb)
	await scanner.start()
	print(f"Discovering for {duration}s — bring the meter close so it ranks high...\n")
	await asyncio.sleep(duration)
	await scanner.stop()

	print(f"{'MAC':<20} {'RSSI':>5}  Name")
	print("-" * 45)
	for mac, (name, rssi) in sorted(
		seen.items(), key=lambda kv: kv[1][1] if kv[1][1] is not None else -999,
		reverse=True
	):
		print(f"{mac:<20} {rssi:>5}  {name or '(no name)'}")


def main():
	p = argparse.ArgumentParser(description=__doc__,
								formatter_class=argparse.RawDescriptionHelpFormatter)
	p.add_argument("--discover", action="store_true",
				   help="just list nearby BLE devices + RSSI, then exit")
	p.add_argument("--discover-time", type=float, default=10.0,
				   help="seconds to scan in discover mode (default 10)")
	p.add_argument("--robot-ip", help="Valetudo robot IP, e.g. 192.168.1.50")
	p.add_argument("--mac", action="append", default=[],
				   help="beacon MAC to log (repeatable). Skip for devices with "
						"a rotating private address — use --name instead.")
	p.add_argument("--name", action="append", default=[],
				   help="advertised name to log, e.g. SRS-NB10 (repeatable). "
						"Survives MAC rotation.")
	p.add_argument("--out", default="rssi_log.csv", help="output CSV path")
	p.add_argument("--poll", type=float, default=1.0,
				   help="seconds between position polls (default 1.0)")
	p.add_argument("--max-pos-age", type=float, default=3.0,
				   help="discard RSSI if the last position is older than this (s)")
	args = p.parse_args()

	try:
		if args.discover:
			asyncio.run(discover(args.discover_time))
		else:
			if not args.robot_ip or not (args.mac or args.name):
				p.error("--robot-ip and at least one --mac or --name are "
						"required (or use --discover)")
			asyncio.run(run(args))
	except KeyboardInterrupt:
		pass


if __name__ == "__main__":
	main()
