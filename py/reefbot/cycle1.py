"""Cycle test 1: realistic coral cycles (isolated-coral pickup + back-side L4 on the three faces facing the
stations) with timing breakdown; no algae handling yet."""
from reefbot import RUNS  # repo-relative paths
import json, math, sys
from pathlib import Path
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot
from reefbot.geom import Field
from reefbot.skills import Skills
OUT = RUNS
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 47600
SPEED = float(sys.argv[2]) if len(sys.argv) > 2 else 2.5
g = ReefGame(port=PORT); g.launch()
b = Bot(g.client, OUT / f"cycle1-{PORT}.jsonl")
f = Field(); k = Skills(b, f)
b.reset()
k.place(4, "L", "L4", back=False)
targets = [(0, "L"), (2, "R"), (0, "R"), (2, "L"), (1, "L"), (1, "R")]
cycles = []
i = 0
while b.t > 4 and not b.s.get("done"):
    face, side = targets[i % len(targets)]
    level = ["L4", "L3", "L2"][(i // len(targets)) % 3]
    px, pz, _ = f.pose(face, side, True)
    t0 = b.gt
    k.stow()
    c = k.best_coral(to=(px, pz))
    if c is None:
        # nothing pickable yet: wait near the closer station's drop area
        st = f.stations[0] if pz > 0 else f.stations[1]
        b.goto(st["trig"]["c"][0] - 1.3, st["trig"]["c"][2] * 0.75, None, tol=0.2, timeout=0.5)
        b.idle(0.15); continue
    ok = k.intake_ground(speed=SPEED, target=c)
    t1 = b.gt
    if not ok:
        cycles.append(dict(i=i, picked=False, t=round(t1 - t0, 3), pen=b.s["bd"]["pen"])); print(cycles[-1], flush=True); continue
    k.wait_handoff(); t2 = b.gt
    sc = k.place(face, side, level, True); t3 = b.gt
    cycles.append(dict(i=i, face=face, side=side, level=level, scored=sc, t_pick=round(t1 - t0, 3), t_hand=round(t2 - t1, 3),
                       t_place=round(t3 - t2, 3), total=round(t3 - t0, 3), blue=b.s["blue"], red=b.s["red"]))
    print(cycles[-1], flush=True)
    i += 1
print("final", b.s["blue"], b.s["red"], b.s["bd"], b.s["reefB"])
json.dump(dict(cycles=cycles, events=k.events), open(OUT / f"cycle1-{PORT}.json", "w"))
g.stop()
