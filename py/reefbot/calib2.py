"""Calibration 2: where does the game's own AutoAlign put Madtown, and does L4 score from there?"""
from reefbot import RUNS  # repo-relative paths
import json, sys
from pathlib import Path
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot, SP, wrap180
from reefbot.client import L4, L3, L2, PLACE, STOW, ALIGN_L, ALIGN_R
OUT = RUNS
g = ReefGame(port=47600); g.launch()
b = Bot(g.client, OUT / "calib2.jsonl")
ev = []
def mark(tag):
    m = b.mech
    ev.append((round(b.gt, 3), round(b.t, 2), tag, round(b.x, 3), round(b.z, 3), round(b.yaw, 1), b.sp, round(m.get("elev", 0), 2), m.get("coral", {}).get("has"), b.s["blue"], b.s["red"], b.s["reefB"]))
    print(ev[-1], flush=True)

def align_and_place(btn_align, level_btn, level_sp, approach, label):
    b.goto(*approach, tol=0.05, ytol=3, timeout=5)
    mark(f"{label}: approach")
    b.hold = {btn_align}
    g0 = b.gt; last = (b.x, b.z, b.yaw); still = 0
    tapped = False
    while b.gt - g0 < 4:
        tap = ()
        if not tapped and b.gt - g0 > 0.05:
            tap = (level_btn,); tapped = True
        b.step(tap=tap, free=True)
        cur = (b.x, b.z, b.yaw)
        still = still + 1 if max(abs(cur[0]-last[0]), abs(cur[1]-last[1])) < 0.0015 and abs(wrap180(cur[2]-last[2])) < 0.2 else 0
        last = cur
        if still >= 3 and abs(b.mech.get("elev", 0)) > 0 and b.mech["sp"] == level_sp: 
            # also wait for elevator to settle
            break
    mark(f"{label}: aligned ({b.gt-g0:.2f}s)")
    b.wait_until(lambda s: True, 0)
    e0 = None
    while b.gt - g0 < 5:
        b.step(free=True)
        e = b.mech.get("elev")
        if e0 is not None and abs(e - e0) < 0.05: break
        e0 = e
    mark(f"{label}: elevator settled ({b.gt-g0:.2f}s)")
    b.step(tap=(PLACE,), free=True)
    mark(f"{label}: place")
    for _ in range(8): b.step(free=True)
    b.hold = set()
    mark(f"{label}: after place 0.36s")
    x0, z0 = b.x, b.z
    import math
    fx, fz = math.sin(math.radians(b.yaw)), math.cos(math.radians(b.yaw))
    b.goto(x0 - 0.45*fx, z0 - 0.45*fz, b.yaw, tol=0.05, timeout=2)
    for _ in range(10): b.step()
    mark(f"{label}: backed off")
    b.tap(STOW, confirm=lambda s: s['mech']['sp'] == 0)
    for _ in range(15): b.step()

b.reset()
mark("start")
# preload on face (3.467,0): approach ~1.0 m out, facing the reef
align_and_place(ALIGN_L, L4, 9, (2.3, 0.3, 90.0), "L4 alignL")
json.dump(ev, open(OUT / "calib2-events.json", "w"))
g.stop()
