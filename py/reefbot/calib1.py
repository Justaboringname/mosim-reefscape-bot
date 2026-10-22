"""Calibration 1: omega sign, drive response, preload L4 on the nearest face (own controller), place timing."""
from reefbot import RUNS  # repo-relative paths
import json, math
from pathlib import Path
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot, SP, wrap180
from reefbot.client import L4, PLACE, STOW
OUT = RUNS
g = ReefGame(port=47600); g.launch()
b = Bot(g.client, OUT / "calib1.jsonl")
b.reset()
ev = []
def mark(tag):
    ev.append((round(b.gt, 3), round(b.t, 2), tag, round(b.x, 3), round(b.z, 3), round(b.yaw, 1), b.sp, b.mech.get("elev"), b.mech.get("coral"), b.s["blue"], b.s["red"], b.s["reefB"]))
    print(ev[-1], flush=True)
mark("start")
y0 = b.yaw
for _ in range(5): b.step(0, 0, 200)
mark(f"after w=+200 x5 dyaw={wrap180(b.yaw - y0):.1f}")
for _ in range(10): b.step(0, 0, 0)
# straight-line accel test along -z (start z=2.7 -> toward 0.5), 1 s full speed
xs = []
g0 = b.gt
for _ in range(22):
    b.step(0, -4.4, 0); xs.append((round(b.gt - g0, 3), round(b.z, 3), round(b.s['robot']['vz'], 3)))
print("accel", xs, flush=True)
for _ in range(12):
    b.step(0, 0, 0); xs.append((round(b.gt - g0, 3), round(b.z, 3), round(b.s['robot']['vz'], 3)))
print("decel", xs[22:], flush=True)
mark("after accel/decel")
# face (3.467,0) yaw 270 -> node L (2.774, 0.17), robot yaw 90 facing reef
ok = b.goto(2.774, 0.17, 90.0, tol=0.02, ytol=1.5, timeout=6)
mark(f"at node ok={ok}")
b.tap(L4, confirm=lambda s: s['mech']['sp'] == 9)
mark("L4 tapped")
last = None
while True:
    b.step(0, 0, 0)
    e = b.mech.get("elev")
    if last is not None and abs(e - last) < 0.05 and e > 1: break
    last = e
    if b.gt - ev[-1][0] > 4: break
mark("elevator settled")
b.tap(PLACE)
mark("place tapped")
b.wait_until(lambda s: not s['mech']['coral']['has'], 2.0)
mark("coral released")
for _ in range(6): b.step(0, 0, 0)
mark("held 0.27s")
b.goto(2.35, 0.17, 90.0, tol=0.05, timeout=2)
mark("backed off")
for _ in range(20): b.step(0, 0, 0)
mark("settled")
b.tap(STOW, confirm=lambda s: s['mech']['sp'] == 0)
for _ in range(30): b.step(0, 0, 0)
mark("stowed")
json.dump(ev, open(OUT / "calib1-events.json", "w"))
g.stop()
