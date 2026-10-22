"""Calibration 13: algae removal (face, front/back, standoff r) then L3/L2 placement standoff sweep on the cleared
face. argv: port, face, mode ('back'|'front')."""
from reefbot import RUNS  # repo-relative paths
import json, math, sys
from pathlib import Path
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot
from reefbot.geom import Field
from reefbot.skills import Skills
from reefbot.client import PLACE, STOW
OUT = RUNS
PORT, FACE, MODE = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
BACK = MODE == "back"
g = ReefGame(port=PORT); g.launch()
b = Bot(g.client, OUT / f"calib13-{PORT}.jsonl")
f = Field(); k = Skills(b, f)
b.reset()
res = []
k.place(4, "L", "L4", back=False)
k.stow()
got = k.remove_algae(FACE, BACK, r=1.40)
res.append(("algae", FACE, BACK, got, k.events[-1]))
print(res[-1], flush=True)
# dispose: drive 1.5 m out from the face and drop it (Place), see where it lands
if got:
    x, z, yaw = f.pose(FACE, "C", BACK, r=2.6)
    b.goto(x, z, yaw, tol=0.1, ytol=5, timeout=3)
    b.step(tap=(PLACE,)); b.idle(0.8)
    al = [a for a in b.s["algae"] if math.hypot(a[0] - b.x, a[1] - b.z) < 2.0]
    print("dropped algae near robot:", al, "algae has", b.has_algae(), flush=True)
k.stow()
plan = []
for lvl in ("L3", "L2"):
    for r in (1.30, 1.35, 1.40, 1.45):
        for side in ("L", "R"):
            plan.append((lvl, r, side))
done = set()
for lvl, r, side in plan:
    if b.t < 5 or b.s.get("done"): break
    bit = f.bit(FACE, side, lvl)
    if b.s["reefB"][bit] == "1":
        continue          # slot already filled by an earlier trial at this level/side
    px, pz, _ = f.pose(FACE, side, BACK)
    k.stow()
    c = k.best_coral(to=(px, pz))
    if c is None:
        b.idle(0.3); continue
    if not k.intake_ground(target=c): continue
    k.wait_handoff()
    ok = k.place(FACE, side, lvl, BACK, r=r)
    res.append(("place", lvl, r, side, BACK, ok, b.s["blue"], b.s["red"]))
    print(res[-1], flush=True)
print("final", b.s["blue"], b.s["red"], b.s["bd"], b.s["reefB"])
json.dump(dict(res=res, events=k.events), open(OUT / f"calib13-{PORT}.json", "w"))
g.stop()
