"""Calibration 4: placement matrix (face x side x level x front/back) with ground-intake cycles."""
from reefbot import RUNS  # repo-relative paths
import json, sys
from pathlib import Path
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot
from reefbot.geom import Field
from reefbot.skills import Skills
from reefbot.client import STOW
OUT = RUNS
TESTS = [
    (0, "L", "L4", True), (0, "R", "L4", True), (1, "L", "L4", True), (2, "R", "L4", True),
    (0, "L", "L3", True), (0, "R", "L2", True), (1, "R", "L3", True), (1, "L", "L2", True),
    (2, "L", "L3", True), (2, "R", "L2", True),
    (0, "R", "L3", False), (0, "L", "L2", False), (1, "R", "L4", False), (1, "L", "L3", False),
    (2, "L", "L4", False), (1, "R", "L2", False), (5, "L", "L4", True), (3, "R", "L4", True),
]
g = ReefGame(port=47600); g.launch()
b = Bot(g.client, OUT / "calib4.jsonl")
f = Field(); k = Skills(b, f)
b.reset()
k.place(4, "L", "L4", back=False)
for (face, side, level, back) in TESTS:
    if b.s.get("done") or b.t < 3: break
    k.stow()
    st = f.stations[0] if f.pose(face, side, back)[1] > 0 else f.stations[1]
    tgt = k.loose_coral(near=(st["trig"]["c"][0], st["trig"]["c"][2]))
    if not k.intake_ground(target=tgt): continue
    k.wait_handoff()
    k.place(face, side, level, back)
for e in k.events: print(e)
print("final", b.s["blue"], b.s["red"], b.s["bd"], b.s["reefB"])
json.dump(k.events, open(OUT / "calib4-events.json", "w"))
g.stop()
