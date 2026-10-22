"""Calibration 9: full cycles = ground intake (speed sweep) + placement matrix; timing breakdown per cycle."""
from reefbot import RUNS  # repo-relative paths
import json, math, sys
from pathlib import Path
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot
from reefbot.geom import Field
from reefbot.skills import Skills
OUT = RUNS
SPEEDS = [2.0, 3.0, 4.0]
TESTS = [
    (0, "L", "L4", True), (2, "R", "L4", True), (0, "R", "L4", True), (2, "L", "L4", True), (1, "L", "L4", True), (1, "R", "L4", True),
    (0, "L", "L3", True), (2, "R", "L3", True), (0, "R", "L3", True), (2, "L", "L3", True), (1, "L", "L3", True), (1, "R", "L3", True),
    (0, "L", "L2", True), (2, "R", "L2", True), (0, "R", "L2", True), (2, "L", "L2", True), (1, "L", "L2", True), (1, "R", "L2", True),
    (5, "L", "L4", False), (3, "R", "L4", False), (5, "R", "L3", False), (3, "L", "L3", False), (5, "L", "L2", False), (3, "R", "L2", False),
]
g = ReefGame(port=47600); g.launch()
b = Bot(g.client, OUT / "calib9.jsonl")
f = Field(); k = Skills(b, f)
b.reset()
k.place(4, "L", "L4", back=False)
cycles = []
for i, (face, side, level, back) in enumerate(TESTS):
    if b.s.get("done") or b.t < 4: break
    sp = SPEEDS[i % len(SPEEDS)]
    t0 = b.gt
    k.stow()
    px, pz, _ = f.pose(face, side, back)
    st = f.stations[0] if pz > 0 else f.stations[1]
    tgt = k.loose_coral(near=(st["trig"]["c"][0], st["trig"]["c"][2]))
    ok = k.intake_ground(speed=sp, target=tgt)
    t1 = b.gt
    if not ok:
        cycles.append(dict(i=i, speed=sp, picked=False, t_pick=round(t1 - t0, 3))); continue
    k.wait_handoff()
    t2 = b.gt
    scored = k.place(face, side, level, back)
    t3 = b.gt
    cycles.append(dict(i=i, speed=sp, picked=True, face=face, side=side, level=level, back=back, scored=scored,
                       t_pick=round(t1 - t0, 3), t_hand=round(t2 - t1, 3), t_place=round(t3 - t2, 3), total=round(t3 - t0, 3),
                       blue=b.s["blue"], red=b.s["red"]))
    print(cycles[-1], flush=True)
print("final", b.s["blue"], b.s["red"], b.s["bd"], b.s["reefB"])
json.dump(dict(cycles=cycles, events=k.events), open(OUT / "calib9.json", "w"))
g.stop()
