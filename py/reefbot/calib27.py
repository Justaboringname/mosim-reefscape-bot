"""Calibration 27: algae into the PROCESSOR (6 pts, scored into bd.net by ProcessorScorer.AddScore). calib14's
single try (robot against the wall at z 3.56, Processor setpoint = L1 with algae, ball 0.29 m ahead at y 0.76) did
not score. Here: six reef algae, one per variant, logging the ball's path for 2.5 s after the release.
variants: standoff from the wall (0 = pushed against it, 0.15, 0.3), release while creeping forward, and the Barge
(net) setpoint as a high toss. argv: port"""
from reefbot import RUNS  # repo-relative paths
import json, math, sys
from pathlib import Path
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot, MatchOver, SP_ID
from reefbot.client import L1, L4, PLACE
from reefbot.geom import Field
from reefbot.matchbot import MatchBot
OUT = RUNS
g = ReefGame(port=int(sys.argv[1]) if len(sys.argv) > 1 else 47606); g.launch(); f = Field()
b = Bot(g.client, OUT / "calib27.jsonl"); b.reset(); mb = MatchBot(b, f, log=lambda m: None)
PX = f.processor["trig"]["c"][0]
notes = []
def note(*a): notes.append(a); print(*a, flush=True)
VARIANTS = [dict(standoff=0.0, creep=0.0, sp="proc"), dict(standoff=0.15, creep=0.0, sp="proc"),
            dict(standoff=0.30, creep=0.0, sp="proc"), dict(standoff=0.25, creep=0.6, sp="proc"),
            dict(standoff=0.0, creep=0.0, sp="barge"), dict(standoff=0.4, creep=0.0, sp="barge")]
try:
    mb.place_any(5, "R", "L4", False)          # the preload coral: remove_algae needs empty hands
    b.wait_until(lambda s: s.get("gs", 0) != 0, timeout=20)
    mb.stow()
    for v, face in zip(VARIANTS, (5, 0, 4, 1, 3, 2)):
        if not (mb.remove_algae(face, back=False, r=1.40) or mb.remove_algae(face, back=False, r=1.45)):
            note("variant", v, "no algae from face", face); continue
        z_wall = 4.02 - 0.46 - v["standoff"]
        b.goto(PX, min(z_wall, 3.3), 0.0, tol=0.06, ytol=3, timeout=5)
        btn = L1 if v["sp"] == "proc" else L4
        want = SP_ID["Processor"] if v["sp"] == "proc" else SP_ID["Barge"]
        b.tap(btn, confirm=lambda s, w=want: s["mech"]["sp"] == w)
        b.idle(1.0)
        g0 = b.gt
        while b.gt - g0 < 2.0 and b.z < z_wall - 0.02:
            b.step(0.0, 0.8, 0.0)
        b.idle(0.3)
        held = [a for a in b.s["algae"] if a[3]]
        hid = b.s["algae"].index(held[0]) if held else None
        net0 = b.s["bd"]["net"]
        note("variant", v, "robot", (round(b.x, 2), round(b.z, 2), round(b.yaw)), "sp", b.mech["sp"], "elev", round(b.mech.get("elev", 0), 1),
             "ball", [round(c, 3) for c in held[0][:3]] if held else None)
        if v["creep"]:
            b.step(0.0, v["creep"], 0.0, tap=(PLACE,))
        else:
            b.step(tap=(PLACE,))
        traj = []
        g1 = b.gt
        while b.gt - g1 < 2.5:
            b.step(0.0, v["creep"] if b.gt - g1 < 0.3 else 0.0, 0.0)
            if hid is not None and hid < len(b.s["algae"]) and int((b.gt - g1) / 0.045) % 4 == 0:
                a = b.s["algae"][hid]; traj.append((round(b.gt - g1, 2), round(a[0], 2), round(a[1], 2), round(a[2], 2)))
        note("   result: bd.net", net0, "->", b.s["bd"]["net"], "traj", traj[:14])
        mb.stow()
except MatchOver:
    pass
json.dump(notes, open(OUT / "calib27.json", "w"))
g.stop()
