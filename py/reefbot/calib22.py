"""Calibration 22: is driving limited while the elevator is up, in open field (no reef contact)? argv: port"""
from reefbot import RUNS  # repo-relative paths
import json, math, sys
from pathlib import Path
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot
from reefbot.client import L4, STOW
OUT = RUNS
g = ReefGame(port=int(sys.argv[1]) if len(sys.argv) > 1 else 47601); g.launch()
b = Bot(g.client, OUT / "calib22.jsonl"); b.reset()
def trial(tag, raise_l4):
    b.goto(1.5, 0.0, 270.0, tol=0.1, ytol=5, timeout=4)
    for _ in range(10): b.step()
    if raise_l4:
        b.step(tap=(L4,))
        b.wait_until(lambda s: s["mech"]["elev"] > 45, 3)
        for _ in range(6): b.step()
    e0 = b.mech["elev"]; g0 = b.gt
    vs = []
    while b.gt - g0 < 0.9:
        b.step(0.0, -4.4, 600.0)            # full speed along -z while spinning
        vs.append((round(b.gt - g0, 2), round(math.hypot(b.s["robot"]["vx"], b.s["robot"]["vz"]), 2), round(b.s["robot"]["wy"])))
    print(tag, "elev", round(e0, 1), "->", round(b.mech["elev"], 1), "v/wy:", vs[::3], flush=True)
    b.step(tap=(STOW,)); b.idle(1.0)
trial("elevator down", False)
trial("elevator at L4", True)
g.stop()
