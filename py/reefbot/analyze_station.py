"""Station-drop analysis of match/drill logs: every drop (coral appearing at a station's drop point), whether it
was caught, and the robot's state at that moment (along/lateral distance from the drop point, closing speed, yaw
error, riders already on the robot, floor coral in the catch corridor, recent spin = bumped into something).
usage: python -m reefbot.analyze_station <glob> [...] [--all]"""
from reefbot import FIELD_JSON  # repo-relative paths
import glob
import json
import math
import sys
from pathlib import Path

F = json.loads((FIELD_JSON).read_text())
ST = [s for s in F["stations"] if s["alliance"] == 0]


def frame(st):
    sx, sz = st["spawn"][0], st["spawn"][2]
    fx, fz = st["spawnFwd"][0], st["spawnFwd"][2]
    h = math.hypot(fx, fz)
    return sx, sz, fx / h, fz / h


FR = [frame(s) for s in ST]


def loc(fr, x, z):
    sx, sz, fx, fz = fr
    return (x - sx) * fx + (z - sz) * fz, -(x - sx) * fz + (z - sz) * fx


def riders(s):
    r = s["robot"]
    yaw = math.radians(r["yaw"])
    fx, fz = math.sin(yaw), math.cos(yaw)
    n = 0
    for c in s["coral"]:
        if c[3] or not (0.2 < c[2] < 0.8):
            continue
        dx, dz = c[0] - r["x"], c[1] - r["z"]
        if abs(dx * fx + dz * fz) < 0.5 and abs(dx * fz - dz * fx) < 0.5:
            n += 1
    return n


def drops(rows):
    prev = set()
    for i, s in enumerate(rows):
        r = s["robot"]
        for k, fr in enumerate(FR):
            # the drop point slides along the station to the robot's lateral position (+-0.8 m)
            hi = [c for c in s["coral"] if not c[3] and c[2] > 1.0 and abs(loc(fr, c[0], c[1])[0]) < 0.25
                  and abs(loc(fr, c[0], c[1])[1]) < 0.9]
            if hi and k not in prev and s["t"] < 149.5:
                prev.add(k)
                along, lat = loc(fr, r["x"], r["z"])
                vc = r["vx"] * fr[2] + r["vz"] * fr[3]
                tyaw = math.degrees(math.atan2(-fr[2], -fr[3])) % 360
                yerr = (r["yaw"] - tyaw + 180) % 360 - 180
                caught = any(rows[j]["mech"]["coral"]["has"] for j in range(i + 1, min(i + 40, len(rows))))
                floor = []
                for c in s["coral"]:
                    if c[3] or c[2] > 0.25:
                        continue
                    a, l = loc(fr, c[0], c[1])
                    if -0.3 < a < 1.3 and abs(l) < 0.6:
                        floor.append((round(a, 2), round(l, 2)))
                spin = max(abs(rows[j]["robot"]["wy"]) for j in range(max(0, i - 10), i + 1))
                yield dict(t=s["t"], st=k, caught=caught, along=along, lat=lat, vc=vc, yerr=yerr, rider=riders(s),
                           floor=floor, spin=spin)
            if not hi:
                prev.discard(k)


def main():
    show_all = "--all" in sys.argv
    pats = [a for a in sys.argv[1:] if not a.startswith("--")]
    for pat in pats:
        for p in sorted(glob.glob(pat)):
            rows = [r for r in (json.loads(l) for l in open(p)) if "robot" in r]
            ds = list(drops(rows))
            miss = [d for d in ds if not d["caught"]]
            print(f"== {Path(p).name} drops={len(ds)} caught={len(ds) - len(miss)} miss={len(miss)}")
            for d in (ds if show_all else miss):
                cause = []
                if d["rider"]:
                    cause.append(f"rider{d['rider']}")
                if d["floor"]:
                    cause.append(f"floor{d['floor']}")
                if d["spin"] > 30:
                    cause.append(f"spin{d['spin']:.0f}")
                if abs(d["yerr"]) > 4:
                    cause.append(f"yerr{d['yerr']:.0f}")
                if d["vc"] > -0.6:
                    cause.append(f"slow{d['vc']:.2f}")
                print(f"  t={d['t']:6.2f} st{d['st']} {'ok  ' if d['caught'] else 'MISS'} along={d['along']:.2f} "
                      f"lat={d['lat']:5.2f} vc={d['vc']:5.2f} yerr={d['yerr']:5.1f} " + " ".join(cause))


if __name__ == "__main__":
    main()
