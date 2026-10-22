"""Ground-intake attempts reconstructed from state logs (no events needed): an attempt is a stretch with the intake
lowered (mech.sp == Intake) and no coral held; it succeeds if mech.coral.has turns on before the intake rises.
For each attempt: the coral closest to the intake at its closest point (robot frame fwd/lat, angle between its
axis and the heading), the robot's speed at that moment, the coral's speed (pushed?), lying coral inside the
footprint (under the bumper), and penalty points gained. usage: python -m reefbot.analyze_ground <glob>..."""
import glob
import json
import math
import sys


def rf(r, x, z):
    yaw = math.radians(r["yaw"])
    fx, fz = math.sin(yaw), math.cos(yaw)
    dx, dz = x - r["x"], z - r["z"]
    return dx * fx + dz * fz, dx * fz - dz * fx


def attempts(rows):
    i, n = 0, len(rows)
    while i < n:
        s = rows[i]
        if s["mech"]["sp"] == 1 and not s["mech"]["coral"]["has"]:
            j = i
            while j < n and rows[j]["mech"]["sp"] == 1 and not rows[j]["mech"]["coral"]["has"]:
                j += 1
            ok = j < n and rows[j]["mech"]["coral"]["has"]
            yield i, min(j, n - 1), ok
            i = j + 1
        else:
            i += 1


def describe(rows, i, j, ok):
    best = None
    for k in range(i, j + 1):
        s, r = rows[k], rows[k]["robot"]
        for ci, c in enumerate(s["coral"]):
            if c[3] or c[4] or c[2] > 0.3:
                continue
            fwd, lat = rf(r, c[0], c[1])
            if fwd < 0.2 or abs(lat) > 0.45:
                continue
            key = fwd + 2 * abs(lat)
            if best is None or key < best[0]:
                yaw = math.radians(r["yaw"])
                rel = abs(math.degrees(math.atan2(c[5], c[6])) - r["yaw"]) % 180
                rel = min(rel, 180 - rel)
                vr = r["vx"] * math.sin(yaw) + r["vz"] * math.cos(yaw)
                cv = 0.0
                if k + 1 <= j:
                    c2 = rows[k + 1]["coral"][ci]
                    dt = rows[k + 1]["gameTime"] - s["gameTime"]
                    cv = math.hypot(c2[0] - c[0], c2[1] - c[1]) / max(dt, 1e-3)
                best = (key, k, fwd, lat, rel, vr, cv)
    under = 0
    for c in rows[j]["coral"]:
        if not (c[3] or c[4]) and c[2] < 0.3:
            fwd, lat = rf(rows[j]["robot"], c[0], c[1])
            if abs(fwd) < 0.46 and abs(lat) < 0.46:
                under += 1
    pen = rows[min(j + 20, len(rows) - 1)]["bd"]["pen"] - rows[i]["bd"]["pen"]
    return best, under, pen


def main():
    tot = dict(n=0, ok=0, under=0, pen=0)
    for pat in sys.argv[1:]:
        for p in sorted(glob.glob(pat)):
            rows = [r for r in (json.loads(l) for l in open(p)) if "robot" in r]
            print(f"== {p.split('/')[-1]}")
            for i, j, ok in attempts(rows):
                best, under, pen = describe(rows, i, j, ok)
                tot["n"] += 1; tot["ok"] += ok; tot["under"] += under > 0; tot["pen"] += pen
                dur = rows[j]["gameTime"] - rows[i]["gameTime"]
                if best:
                    _, k, fwd, lat, rel, vr, cv = best
                    print(f"  t={rows[i]['gameTime']:6.2f} {'OK  ' if ok else 'MISS'} dur={dur:4.2f} closest fwd={fwd:5.2f} "
                          f"lat={lat:5.2f} rel={rel:4.0f} v_robot={vr:5.2f} v_coral={cv:4.2f} under={under} pen={pen}")
                else:
                    print(f"  t={rows[i]['gameTime']:6.2f} {'OK  ' if ok else 'MISS'} dur={dur:4.2f} no coral ahead under={under} pen={pen}")
    print("TOTAL", tot)


if __name__ == "__main__":
    main()
