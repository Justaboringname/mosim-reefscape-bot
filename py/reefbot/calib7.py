"""Calibration 7: wrist (station) intake box while holding Intake in station mode; TargetTransform of the station mode."""
from reefbot import RUNS  # repo-relative paths
import json
from pathlib import Path
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot
from reefbot.geom import Field
from reefbot.skills import Skills
from reefbot.client import INTAKE, SPECIAL
OUT = RUNS
g = ReefGame(port=47600); g.launch()
b = Bot(g.client, OUT / "calib7.jsonl")
f = Field(); k = Skills(b, f)
b.reset()
k.place(4, "L", "L4", back=False)
k.stow()
b.tap(SPECIAL, confirm=lambda s: s["mech"].get("station") == 1)
b.goto(4.0, 2.6, 0, tol=0.05, timeout=4)
def dump(tag):
    fr = b.c.field()["robot"]
    d = fr["intakes"]
    w = d["wristIntake"]["cols"][0]
    print(tag, round(b.gt, 3), b.sp, round(b.mech.get("elev", 0), 2), "wrist", [round(v, 3) for v in w["local"]], [round(v, 3) for v in w["half"]],
          [round(v, 1) for v in w["rotLocal"]], fr.get("stationMode"), flush=True)
dump("stowed")
b.hold = {INTAKE}
for i in range(14):
    b.step(); dump(f"intake+{i}")
g.stop()
