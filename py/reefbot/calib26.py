"""Calibration 26: climb drill. One match; after auto, repeatedly: drive to the cage pose, run MatchBot.climb()
(the retry loop under test), record lifted / slip events, cycle the Climb button back to Stow, drive forward,
repeat until the match ends. argv: port"""
from reefbot import RUNS  # repo-relative paths
import json, sys
from pathlib import Path
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot, MatchOver, SP_ID
from reefbot.client import CLIMB
from reefbot.geom import Field
from reefbot.matchbot import MatchBot, CAGE_Z
OUT = RUNS
g = ReefGame(port=int(sys.argv[1]) if len(sys.argv) > 1 else 47606); g.launch(); f = Field()
b = Bot(g.client, OUT / "calib26.jsonl"); b.reset(); mb = MatchBot(b, f, log=lambda m: None)
res = []
try:
    b.wait_until(lambda s: s.get("gs", 0) != 0, timeout=20)
    while b.t > 6:
        t0 = b.t
        mb.climbed = False
        mb.climb()
        lifted = b.s["robot"].get("ground") == 0
        slips = [e for e in mb.events if e[2] == "climb: slipped, retry" and e[1] <= t0]
        res.append(dict(t=round(t0, 1), lifted=lifted, dt=round(t0 - b.t, 2), slips=[e[-1].get("why") for e in slips[-3:]],
                        sp=b.mech["sp"], cage=b.mech.get("cage")))
        print(res[-1], flush=True)
        for _ in range(3):
            if b.mech["sp"] == SP_ID["Stow"]:
                break
            cur = b.mech["sp"]
            b.tap(CLIMB, confirm=lambda s, cur=cur: s["mech"]["sp"] != cur)
        b.idle(0.3)
        b.goto(1.6, CAGE_Z, 90.0, tol=0.1, ytol=5, timeout=3)
except MatchOver:
    pass
print("SUMMARY lifted", sum(1 for r in res if r["lifted"]), "/", len(res), "mean dt", round(sum(r["dt"] for r in res) / max(1, len(res)), 2), flush=True)
json.dump(res, open(OUT / "calib26.json", "w"))
g.stop()
