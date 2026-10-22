from reefbot import RUNS  # repo-relative paths
import json, sys, time
from pathlib import Path
from reefbot.gamectl import ReefGame
OUT = RUNS
g = ReefGame(port=47600)
g.launch()
c = g.client
t0 = time.time()
s = c.reset()
print("reset in %.1fs" % (time.time() - t0), {k: s.get(k) for k in ("t", "gs", "rs", "blue", "red", "robot", "mech", "cheated")})
f = c.field()
(OUT / "field.json").write_text(json.dumps(f, indent=1))
print("field keys", list(f.keys()), "faces", len(f.get("faces", [])), "robot", {k: v for k, v in f.get("robot", {}).items() if k != "setpoints"})
t0 = time.time(); n = 0
while n < 60:
    s = c.act(0, 0, 0); n += 1
print("60 idle decisions: %.2fs wall, game t=%s" % (time.time() - t0, s["t"]))
print(json.dumps({k: s.get(k) for k in ("t", "gs", "blue", "red", "bd", "rd", "reefB", "trough", "robot", "mech", "bIn")}))
print("coral", s["coral"][:12]); print("algae", s["algae"][:20])
g.stop()
