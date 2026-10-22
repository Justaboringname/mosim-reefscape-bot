"""Calibration 16: Stack vs Intake pickup cycle time (alternating), and back-side L2 standoff sweep on face 1
(high algae: L2 is not blocked). argv: port"""
from reefbot import RUNS  # repo-relative paths
import json, math, sys
from pathlib import Path
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot
from reefbot.geom import Field
from reefbot.skills import Skills
OUT = RUNS
PORT = int(sys.argv[1])
g = ReefGame(port=PORT); g.launch()
b = Bot(g.client, OUT / f"calib16-{PORT}.jsonl")
f = Field(); k = Skills(b, f)
b.reset()
k.place(4, "L", "L4", back=False)
slots = [(0, "L", "L4", None), (2, "R", "L4", None), (0, "R", "L4", None), (2, "L", "L4", None),
         (1, "L", "L2", 1.35), (1, "R", "L2", 1.40), (1, "L", "L4", None), (1, "R", "L4", None)]
res = []
i = 0
while b.t > 5 and not b.s.get("done") and i < 40:
    face, side, level, r = slots[i % len(slots)]
    if i >= len(slots):
        # second lap: L2 sweep on face 1 if still empty, else fill L4s again on other branches
        face, side, level, r = [(1, "L", "L2", 1.45), (1, "R", "L2", 1.45), (1, "L", "L2", 1.40), (1, "R", "L2", 1.35)][(i - len(slots)) % 4]
    bit = f.bit(face, side, level)
    if b.s["reefB"][bit] == "1" and i >= len(slots):
        i += 1; continue
    method = "stack" if i % 2 else "intake"
    px, pz, _ = f.pose(face, side, True)
    t0 = b.gt
    if method == "intake":
        k.stow()
    c = k.best_coral(to=(px, pz))
    if c is None:
        b.idle(0.3); continue
    ok = k.intake_ground(target=c, method=method)
    t1 = b.gt
    if not ok:
        res.append(dict(i=i, method=method, picked=False)); print(res[-1], flush=True); i += 1; k.stow(); continue
    k.wait_handoff(); t2 = b.gt
    sc = k.place(face, side, level, True, r=r); t3 = b.gt
    res.append(dict(i=i, method=method, face=face, side=side, level=level, r=r, scored=sc, t_pick=round(t1 - t0, 3),
                    t_ready=round(t2 - t1, 3), t_place=round(t3 - t2, 3), total=round(t3 - t0, 3)))
    print(res[-1], flush=True)
    i += 1
print("final", b.s["blue"], b.s["red"], b.s["bd"], b.s["reefB"])
json.dump(dict(res=res, events=k.events), open(OUT / f"calib16-{PORT}.json", "w"))
g.stop()
