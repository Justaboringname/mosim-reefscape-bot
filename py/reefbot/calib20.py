"""Calibration 20: new auto — preload + 3 standing (staged) coral with the end effector (Stack) + station coral,
all to L4 with the game's AutoAlign. Prints a timeline; argv: port"""
from reefbot import RUNS  # repo-relative paths
import json, sys
from pathlib import Path
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot, MatchOver
from reefbot.geom import Field
from reefbot.skills import Skills
from reefbot.client import SPECIAL, STOW
OUT = RUNS
port = int(sys.argv[1]) if len(sys.argv) > 1 else 47601
g = ReefGame(port=port); g.launch(); f = Field()
b = Bot(g.client, OUT / "calib20.jsonl"); b.reset()
k = Skills(b, f); k.fast_leave = True
def log(tag):
    print(f"t={b.t:6.2f} gs={b.s['gs']} blue={b.s['blue']} reef={b.s['reefB'].count('1')} {tag}", flush=True)
try:
    b.tap(SPECIAL, confirm=lambda s: s["mech"].get("station") == 1)
    log("start")
    k.place_align(1, "L", "L4", True); log("preload placed")
    for (lx, lz, yaw, face, side) in ((7.555, 0.0, 90.0, 1, "R"), (7.555, 1.826, 30.0, 0, "L"), (7.555, -1.829, 150.0, 2, "R")):
        ok = k.pick_vertical(lx, lz, yaw); log(f"vertical {lz} -> {ok}")
        if ok:
            k.place_align(face, side, "L4", True); log(f"placed {face}{side} L4")
    for (face, side) in ((0, "R"), (2, "L"), (5, "L"), (3, "R")):
        which = 0 if f.pose(face, side, True)[1] >= 0 else 1
        if b.mech.get("sp") != 0:
            b.step(tap=(STOW,))
        ok = k.intake_station(which); log(f"station {which} -> {ok}")
        if ok:
            k.wait_handoff(); k.place_align(face, side, "L4", True); log(f"placed {face}{side} L4")
    b.idle(1.0); log("end of script")
except MatchOver:
    pass
for e in k.events:
    print(e)
json.dump(dict(events=k.events), open(OUT / "calib20.json", "w"))
g.stop()
