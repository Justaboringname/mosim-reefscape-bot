"""Calibration 5: intake box kinematics while holding Intake (standing still), and the Stack (L1) ground mode."""
from reefbot import RUNS  # repo-relative paths
import json
from pathlib import Path
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot
from reefbot.geom import Field
from reefbot.skills import Skills
from reefbot.client import INTAKE, L1, STOW, PLACE
OUT = RUNS
g = ReefGame(port=47600); g.launch()
b = Bot(g.client, OUT / "calib5.jsonl")
f = Field(); k = Skills(b, f)
b.reset()
k.place(4, "L", "L4", back=False)
k.stow()
b.goto(5.5, 2.0, 60, tol=0.05, timeout=4)
for _ in range(10): b.step()
def dump(tag):
    d = b.c.field()["robot"]["intakes"]
    ci = d["coralIntake"]["cols"][0]; ee = d["EECoralIntake"]["cols"][0]
    print(tag, round(b.gt, 3), b.sp, "coral", [round(v, 3) for v in ci["local"]], [round(v, 3) for v in ci["half"]], [round(v, 1) for v in ci["rotLocal"]],
          "EE", [round(v, 3) for v in ee["local"]], [round(v, 3) for v in ee["half"]], flush=True)
dump("stowed")
b.hold = {INTAKE}
for i in range(16):
    b.step(); dump(f"intake+{i}")
b.hold = set()
for i in range(8):
    b.step(); dump(f"release+{i}")
b.tap(L1, confirm=lambda s: s["mech"]["sp"] == 4)
for i in range(12):
    b.step(); dump(f"stack+{i}")
b.hold = {INTAKE}
for i in range(6):
    b.step(); dump(f"stack+intake+{i}")
g.stop()
