"""Calibration 25: net-shot policy A/B. Each episode: place the preload, then take the six reef algae from the
front (far faces first) and shoot each into the net with the policy from NET_POLICY:
  P1 = arm up early (v36), P2 = arm up only once stopped at the net, P3 = arm up early, final approach 1.0 m/s.
Logs the ball's release-impulse direction (robot frame) and the late judgment. argv: port, episodes, tag"""
from reefbot import RUNS  # repo-relative paths
import json, os, sys
from pathlib import Path
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot, MatchOver
from reefbot.geom import Field
from reefbot.matchbot import MatchBot
OUT = RUNS
port, n, tag = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
pol = os.environ.get("NET_POLICY", "P1")
g = ReefGame(port=port); g.launch(); f = Field()
for m in range(n):
    b = Bot(g.client, OUT / f"calib25-{tag}-{m}.jsonl"); b.reset()
    mb = MatchBot(b, f); mb.fast_leave = True
    mb.net_press_at_stop = pol == "P2"
    mb.net_final_v = 1.0 if pol == "P3" else 2.0
    try:
        mb.place_align(5, "R", "L4", False)
        for face in (4, 3, 5, 2, 0, 1):
            if mb.b.has_coral():
                break
            if mb.remove_algae(face, back=False, r=1.40) or mb.remove_algae(face, back=False, r=1.45):
                mb.score_net(z=mb.pick_net_z())
            mb.stow()
        b.idle(2.0)
        mb.resolve_nets(wait=0.0)
    except MatchOver:
        mb.resolve_nets(wait=0.0)
    json.dump(dict(events=mb.events), open(OUT / f"calib25-{tag}-{m}.json", "w"))
    ev = mb.events
    rel = [e for e in ev if e[2] == "net: released"]
    res = [e[2] for e in ev if e[-1].get("late") if isinstance(e[-1], dict)]
    print(m, pol, "released", len(rel), "scored", res.count("net: scored"), "rd", [e[-1].get("rd") for e in rel], flush=True)
g.stop()
