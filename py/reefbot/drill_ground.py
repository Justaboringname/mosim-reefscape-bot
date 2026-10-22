"""Ground-intake drill: station mode off (each station keeps ~3 coral on its floor); repeatedly pick a lying coral
near the stations with Skills.ground_pick and place it (L4 on faces 0-2, then anywhere) to measure pickup rate,
under-bumper rate, time per pickup and penalties. argv: port, episodes, tag"""
from reefbot import RUNS  # repo-relative paths
import json, math, os, sys
from pathlib import Path
from collections import Counter
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot, MatchOver, SP_ID
from reefbot.geom import Field
from reefbot.skills import Skills
from reefbot.client import STOW
OUT = RUNS
port, n, tag = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
g = ReefGame(port=port); g.launch(); f = Field()
tot = Counter(); times = []
slots = [(fc, sd) for fc in (0, 1, 2) for sd in ("L", "R")]
for m in range(n):
    b = Bot(g.client, OUT / f"drillg-{tag}-{m}.jsonl"); b.reset(); k = Skills(b, f); k.fast_leave = True
    k.ground_ff = os.environ.get("GROUND_FF", "1") == "1"
    k.ground_v_touch = float(os.environ.get("GROUND_V_TOUCH", "0.8"))      # as configure_auto(g2_hyb) sets it
    k.FF_LINEUP = float(os.environ.get("FF_LINEUP", "0.9"))
    k.ground_wait_slide = True
    i = 0
    placed = set()
    try:
        while b.t > 3:
            fc, sd = slots[i % len(slots)]; i += 1
            px, pz, _ = f.pose(fc, sd, True)
            if b.mech.get("sp") not in (SP_ID["Stow"], SP_ID["Intake"]):
                b.step(tap=(STOW,))
            if k.coral_in_footprint() and not b.has_coral():
                k.shed_coral()
                tot["shed"] += 1
            opts = [o for o in k.ground_choices((b.x, b.z), (px, pz)) if o[1][0] > 5.5]
            if not opts:
                tot["none_available"] += 1
                b.goto(6.6, 2.3 if pz >= 0 else -2.3, None, tol=0.3, timeout=0.8); b.step(); continue
            t0 = b.gt
            ok = k.ground_pick_ff(opts[0][1], opts[0][2]) if k.ground_ff else k.ground_pick(opts[0][1], opts[0][2])
            tot["attempts"] += 1; tot["acquired"] += ok
            if ok:
                times.append(b.gt - t0)
                free = [(f2, s2, lv) for lv in ("L4", "L2") for f2 in (0, 1, 2, 5, 3, 4) for s2 in ("L", "R")
                        if b.s["reefB"][f.bit(f2, s2, lv)] == "0" and (f2, s2, lv) not in placed
                        and (lv == "L4" or not f.low_algae[f2])]
                if free:
                    f2, s2, lv = free[0]; placed.add((f2, s2, lv))
                    k.place_align(f2, s2, lv, not (f2 in (3, 4, 5) and lv == "L4"))
                else:
                    tot["no_free_slot"] += 1
                    break                         # reef full: end this episode (no dumping coral on the floor)
    except MatchOver:
        pass
    json.dump(k.events, open(OUT / f"drillg-{tag}-{m}.events.json", "w"))
    c = Counter(e[2] for e in k.events)
    tot["under_bumper"] += c["intake: under bumper"]
    tot["pen"] += b.s["bd"]["pen"]
    print(m, dict(tot), flush=True)
print("FINAL", tag, dict(tot), "rate", round(tot["acquired"] / max(1, tot["attempts"]), 3),
      "mean_pick_s", round(sum(times) / max(1, len(times)), 2))
g.stop()
