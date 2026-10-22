"""Calibration 23: ground pickup grid — approach angle (heading vs coral long axis) x contact speed. Per attempt
records acquired / under-bumper / stuck-after. An episode ends as soon as a stuck coral cannot be shed (keeps
penalty cascades out of the data). argv: port, episodes, tag"""
from reefbot import RUNS  # repo-relative paths
import json, math, sys, itertools
from pathlib import Path
from collections import defaultdict
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot, MatchOver, SP_ID
from reefbot.geom import Field
from reefbot.skills import Skills
from reefbot.client import STOW, PLACE
OUT = RUNS
port, n, tag = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
ANGLES = (0.0, 30.0, 45.0, 60.0, 90.0)
SPEEDS = (1.0, 1.8, 2.4, 3.0)
GRID = list(itertools.product(ANGLES, SPEEDS))
res = defaultdict(lambda: [0, 0, 0])      # (angle, v) -> [attempts, acquired, under]
g = ReefGame(port=port); g.launch(); f = Field()
gi = 0
for m in range(n):
    b = Bot(g.client, OUT / f"calib23-{tag}-{m}.jsonl"); b.reset(); k = Skills(b, f)
    try:
        while b.t > 3:
            if b.has_coral():
                # dump it in the processor corner far away (x 6.0, z -3.3) facing the wall, then continue
                b.goto(5.6, -3.3, 180.0, tol=0.2, ytol=10, timeout=2.0)
                b.step(tap=(PLACE,)); b.idle(0.3)
                if b.mech.get("sp") != SP_ID["Stow"]:
                    b.step(tap=(STOW,))
                continue
            if k.coral_in_footprint():
                if not k.shed_coral():
                    print("stuck coral could not be shed: ending episode", flush=True)
                    break
            ang, v = GRID[gi % len(GRID)]
            k.ground_angle = ang
            opts = [o for o in k.ground_choices((b.x, b.z), None) if o[1][0] > 5.5]
            if not opts:
                b.goto(6.6, 2.3 if b.z >= 0 else -2.3, None, tol=0.3, timeout=0.8); b.step(); continue
            gi += 1
            nu0 = sum(1 for e in k.events if e[2] == "intake: under bumper")
            ok = k.ground_pick(opts[0][1], opts[0][2], v_contact=v)
            nu1 = sum(1 for e in k.events if e[2] == "intake: under bumper")
            r = res[(ang, v)]; r[0] += 1; r[1] += ok; r[2] += (nu1 > nu0)
    except MatchOver:
        pass
    print(m, "pen", b.s["bd"]["pen"], flush=True)
print("angle  v    attempts acquired under")
for (ang, v) in GRID:
    r = res[(ang, v)]
    print(f"{ang:5.0f} {v:4.1f} {r[0]:8d} {r[1]:8d} {r[2]:5d}")
json.dump({f"{a},{v}": res[(a, v)] for (a, v) in GRID}, open(OUT / f"calib23-{tag}.json", "w"))
g.stop()
