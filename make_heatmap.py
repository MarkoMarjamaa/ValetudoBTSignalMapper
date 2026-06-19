#!/usr/bin/env python3
"""
make_heatmap.py — turn rssi_log.csv into an interpolated coverage heatmap.

Reads the CSV from rssi_logger.py, interpolates RSSI over the area the robot
covered, and writes a PNG. Coordinates are in the robot's native map units
(centimetres), so strong/weak regions land where they physically are.

Requires: pip install numpy scipy matplotlib

Usage:
	python make_heatmap.py rssi_log.csv
	python make_heatmap.py rssi_log.csv --mac A4:C1:38:XX:XX:XX --flip-y
	python make_heatmap.py rssi_log.csv --walls walls.csv --wall-scale 5
"""

import argparse
import csv

import numpy as np
import matplotlib.pyplot as plt
from scipy.interpolate import griddata


def load(path, mac_filter, name_filter):
	macs = {m.upper() for m in mac_filter} if mac_filter else None
	names = set(name_filter) if name_filter else None
	xs, ys, rs = [], [], []
	with open(path, newline="") as fh:
		for row in csv.DictReader(fh):
			if macs is not None or names is not None:
				hit = (macs is not None and row.get("mac", "").upper() in macs) \
					or (names is not None and row.get("name", "") in names)
				if not hit:
					continue
			try:
				xs.append(float(row["x_cm"]))
				ys.append(float(row["y_cm"]))
				rs.append(float(row["rssi"]))
			except (ValueError, KeyError):
				continue
	return np.array(xs), np.array(ys), np.array(rs)


def main():
	p = argparse.ArgumentParser(description=__doc__,
								formatter_class=argparse.RawDescriptionHelpFormatter)
	p.add_argument("csv", help="log file from rssi_logger.py")
	p.add_argument("--mac", action="append", default=[],
				   help="only use rows from this beacon MAC (repeatable)")
	p.add_argument("--name", action="append", default=[],
				   help="only use rows with this label, e.g. SRS-NB10 (repeatable)")
	p.add_argument("--out", default="heatmap.png")
	p.add_argument("--res", type=int, default=300,
				   help="grid resolution per axis (default 300)")
	p.add_argument("--flip-y", action="store_true",
				   help="flip Y if the map looks upside-down vs your house")
	p.add_argument("--walls", help="optional CSV of wall x,y points to overlay")
	p.add_argument("--wall-scale", type=float, default=1.0,
				   help="multiply wall coords to match RSSI units (try your "
						"map's pixelSize, often 5, if walls don't line up)")
	args = p.parse_args()

	x, y, r = load(args.csv, args.mac, args.name)
	if len(r) < 4:
		raise SystemExit(f"Only {len(r)} usable points — drive around more first.")
	if args.flip_y:
		y = -y

	# Interpolate onto a regular grid (linear inside the sampled hull).
	xi = np.linspace(x.min(), x.max(), args.res)
	yi = np.linspace(y.min(), y.max(), args.res)
	gx, gy = np.meshgrid(xi, yi)
	grid = griddata((x, y), r, (gx, gy), method="linear")

	fig, ax = plt.subplots(figsize=(10, 8))
	im = ax.imshow(grid, origin="lower",
				   extent=(x.min(), x.max(), y.min(), y.max()),
				   aspect="equal", cmap="RdYlGn")  # red = weak, green = strong
	fig.colorbar(im, ax=ax, label="RSSI (dBm)")

	# Show where you actually measured — interpolation between is just a guess.
	ax.scatter(x, y, c=r, cmap="RdYlGn", edgecolors="black",
			   linewidths=0.3, s=12, zorder=3)

	# Optional floor-plan context. The scale knob exists because Valetudo layer
	# pixels and entity points may use different units; nudge --wall-scale until
	# the walls line up with your sampled path.
	if args.walls:
		wx, wy = [], []
		with open(args.walls, newline="") as fh:
			for row in csv.reader(fh):
				try:
					wx.append(float(row[0]) * args.wall_scale)
					wy.append(float(row[1]) * args.wall_scale * (-1 if args.flip_y else 1))
				except (ValueError, IndexError):
					continue
		ax.scatter(wx, wy, s=1, c="dimgray", zorder=2)

	ax.set_xlabel("x (cm)")
	ax.set_ylabel("y (cm)")
	label = ", ".join(args.name + args.mac)
	ax.set_title(f"BLE coverage — {len(r)} samples"
				 + (f" — {label}" if label else ""))
	fig.tight_layout()
	fig.savefig(args.out, dpi=130)
	print(f"Saved -> {args.out}   (RSSI range {r.min():.0f} .. {r.max():.0f} dBm)")


if __name__ == "__main__":
	main()
