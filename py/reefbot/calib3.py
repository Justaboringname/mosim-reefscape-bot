"""Calibration 3: score preload with own controller at the AutoAlign pose, then ground-intake coral near the
blue +z station (Intake setpoint) and score it; dump intake geometry stowed and deployed."""
from reefbot import RUNS  # repo-relative paths
import json, math, sys
from pathlib import Path
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot, SP, wrap180
from reefbot.client import L4, L3, L2, L1, PLACE, STOW, INTAKE, ALIGN_L, ALIGN_R
OUT = RUNS
SPEED = float(sys.argv[1]) if len(sys.argv) > 1 else 1.5
g = ReefGame(port=47600); g.launch()
b = Bot(g.client, OUT / "calib3.jsonl")
ev = []
def mark(tag):
    m = b.mech
    ev.append((round(b.gt, 3), round(b.t, 2), tag, round(b.x, 3), round(b.z, 3), round(b.yaw, 1), b.sp, round(m.get("elev", 0), 2), m.get("coral"), b.s["blue"], b.s["red"], b.s["reefB"]))
    print(ev[-1], flush=True)
b.reset(); mark("start")
f0 = b.c.field(); json.dump(f0["robot"]["intakes"], open(OUT / "intakes-stowed.json", "w"), indent=1)
# preload: own controller to the AutoAlign pose of node L on face (3.467,0)
b.goto(2.867, 0.170, 90.0, tol=0.01, ytol=1.0, timeout=5, tap=(L4,))
mark("at pose (L4 tapped en route)")
b.wait_until(lambda s: s['mech'].get('elev', 0) > 49.5, 2)
mark("elev up")
b.step(tap=(PLACE,)); mark("place")
b.wait_until(lambda s: s['reefB'].count('1') >= 1, 1.0); mark("scored?")
# go toward the +z blue station floor
def loose_coral():
    cs = [c for c in b.s['coral'] if not c[3] and c[2] < 0.25 and c[0] > 5.5 and c[1] > 0.8]
    return min(cs, key=lambda c: math.hypot(c[0]-b.x, c[1]-b.z)) if cs else None
b.tap(STOW, confirm=lambda s: s['mech']['sp'] == 0)
c = loose_coral(); mark(f"target coral {c}")
# approach point 1.0 m before the coral along the line from the robot
ax, az = c[0] - b.x, c[1] - b.z; d = math.hypot(ax, az)
b.goto(c[0] - ax/d*1.0, c[1] - az/d*1.0, math.degrees(math.atan2(ax, az)), tol=0.08, ytol=4, timeout=4)
mark("pre-intake")
b.hold = {INTAKE}
b.step(); b.step(); b.step(); b.step()
fd = b.c.field(); json.dump(fd["robot"]["intakes"], open(OUT / "intakes-deployed.json", "w"), indent=1)
mark("intake held (deployed dump)")
g0 = b.gt
while b.gt - g0 < 4:
    c = loose_coral() or c
    ax, az = c[0] - b.x, c[1] - b.z; d = math.hypot(ax, az)
    tyaw = math.degrees(math.atan2(ax, az))
    w = max(-600, min(600, 8 * wrap180(tyaw - b.yaw)))
    b.step(ax/max(d,1e-3)*SPEED, az/max(d,1e-3)*SPEED, w)
    if b.has_coral(): break
mark(f"coral acquired? ({b.gt-g0:.2f}s)")
b.hold = set()
g1 = b.gt
while b.gt - g1 < 3:
    b.step()
    m = b.mech['coral']
    if m['st'] == 3 and m['at']: break
mark(f"handoff done? ({b.gt-g1:.2f}s)")
# score at the nearest face node: face (4.715,0.72) yaw 30 -> robot faces reef (yaw 210), use L node (5.209,1.236)
# AutoAlign pose: 1.432 from reef centre along the face normal, 0.17 lateral
rc = (4.2989, 0.0)
def align_pose(face_yaw, left):
    a = math.radians(face_yaw)          # face outward normal direction in (x,z): (sin, cos)
    nx, nz = math.sin(a), math.cos(a)
    lx, lz = nz, -nx                     # lateral
    s = 0.17 if left else -0.17
    return rc[0] + 1.432*nx + s*lx, rc[1] + 1.432*nz + s*lz, (face_yaw + 180) % 360
px, pz, pyaw = align_pose(30, True)
print("pose", px, pz, pyaw)
b.goto(px, pz, pyaw, tol=0.01, ytol=1.0, timeout=5, tap=(L4,))
mark("at second pose")
b.wait_until(lambda s: s['mech'].get('elev', 0) > 49.5, 2); mark("elev up")
b.step(tap=(PLACE,)); mark("place")
b.wait_until(lambda s: s['reefB'].count('1') >= 2, 1.0); mark("scored 2?")
for _ in range(10): b.step()
mark("end")
json.dump(ev, open(OUT / "calib3-events.json", "w"))
g.stop()
