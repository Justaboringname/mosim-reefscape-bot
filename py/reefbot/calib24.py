"""Calibration 24 (to run with the user): algae ground intake.
1) dump the algae intake box (robot frame) in Algae mode with Intake held;
2) knock the staged algae off by picking the standing coral (pick_vertical), place that coral, then pick the
   fallen algae from the floor with pick_floor_algae and score it in the net. Prints each step. argv: port"""
from reefbot import RUNS  # repo-relative paths
import json, math, sys
from pathlib import Path
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot, MatchOver
from reefbot.geom import Field
from reefbot.skills import Skills
from reefbot.client import INTAKE
OUT = RUNS
g = ReefGame(port=int(sys.argv[1]) if len(sys.argv) > 1 else 47601); g.launch(); f = Field()
b = Bot(g.client, OUT / "calib24.jsonl"); b.reset(); k = Skills(b, f); k.fast_leave = True
def log(m): print(f"t={b.t:6.2f} {m}", flush=True)
try:
    k.place_align(5, "R", "L4", False); log("preload placed")
    print("mode switch to algae:", k.ensure_mode(True), "mode", b.mech.get("mode"))
    b.hold = {INTAKE}; b.idle(0.8)
    fj = g.client.field()
    print("INTAKES (algae mode, Intake held):", json.dumps(fj.get("robot", {}).get("intakes", fj.get("intakes")), indent=1)[:3000])
    b.hold = set(); b.idle(0.3); k.ensure_mode(False)
    for (lx, lz, yaw, face, side) in ((7.555, 1.826, 30.0, 0, "L"), (7.555, 0.0, 90.0, 1, "R"),
                                      (7.555, -1.826, 150.0, 2, "R")):
        ok = k.pick_vertical(lx, lz, yaw); log(f"vertical {lz}: {ok}")
        if ok:
            k.place_align(face, side, "L4", True); log("placed")
        b.idle(0.5)
        for _ in range(15):                     # let knocked-off algae stop rolling (<= 3 s)
            p0 = [tuple(a[:2]) for a in b.s["algae"]]
            b.idle(0.2)
            if all(math.hypot(a[0] - q[0], a[1] - q[1]) < 0.02 for a, q in zip(b.s["algae"], p0)):
                break
        loose = [a for a in b.s["algae"] if k.floor_algae_settled(a) and a[0] > 5.0]
        log(f"loose algae: {[[round(v, 2) for v in a[:3]] for a in loose]}")
        if loose:
            a = min(loose, key=lambda a: (a[0] - b.x) ** 2 + (a[1] - b.z) ** 2)
            got = k.pick_floor_algae(a[0], a[1]); log(f"floor algae pickup: {got}")
            if got:
                ok = k.score_net(z=-1.75); log(f"net: {ok}")
            k.ensure_mode(False)
except MatchOver:
    pass
json.dump(dict(events=k.events), open(OUT / "calib24.json", "w"))
g.stop()
