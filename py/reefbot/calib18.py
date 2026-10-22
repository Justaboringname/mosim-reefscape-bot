"""Calibration 18: station-mode catch rate per pose. Robot faces the +z blue station, holds Intake at a grid of
(standoff along the drop direction, lateral offset, yaw offset); each drop is tracked (spawned near the station's
spawn point) and counted caught if the robot holds a coral within 0.6 s. Caught coral are dropped on the floor far
away (they don't matter: the station keeps dropping for a robot in station mode)."""
from reefbot import RUNS  # repo-relative paths
import json, math, sys, itertools
from pathlib import Path
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot, wrap180, MatchOver
from reefbot.geom import Field
from reefbot.skills import Skills
from reefbot.client import INTAKE, PLACE, STOW, SPECIAL
OUT = RUNS
g = ReefGame(port=47601); g.launch()
f = Field()
st = next(s for s in f.stations if s["p"][2] > 0)
sx, sz = st["spawn"][0], st["spawn"][2]
fx, fz = st["spawnFwd"][0], st["spawnFwd"][2]; h = math.hypot(fx, fz); fx, fz = fx / h, fz / h
rx, rz = fz, -fx
face = math.degrees(math.atan2(-fx, -fz)) % 360
grid = list(itertools.product([0.60, 0.66, 0.72, 0.78], [-0.15, 0.0, 0.15], [0.0, -12.0, 12.0]))
res = []
gi = 0
for ep in range(3):
    b = Bot(g.client, OUT / f"calib18-{ep}.jsonl", decision_steps=4)
    b.reset(); k = Skills(b, f)
    try:
        b.step(tap=(PLACE,)); b.idle(0.3)            # drop the preload
        b.tap(SPECIAL, confirm=lambda s: s["mech"].get("station") == 1)
        while gi < len(grid) and b.t > 6:
            dist, lat, dyaw = grid[gi]; gi += 1
            tx, tz = sx + fx * dist + rx * lat, sz + fz * dist + rz * lat
            yaw = (face + dyaw) % 360
            k.stow()
            b.goto(tx + fx * 0.6, tz + fz * 0.6, yaw, tol=0.08, ytol=3, timeout=3)
            b.hold = {INTAKE}
            g0 = b.gt; drops = []; seen = set(); caught_t = None
            while b.gt - g0 < 3.2:
                vx, vz, w, d = b.drive_cmd(tx, tz, yaw, vmax=1.5)
                b.step(vx, vz, w)
                for c in b.s["coral"]:
                    if c[2] > 0.9 and math.hypot(c[0] - sx, c[1] - sz) < 0.35:
                        key = (round(b.gt, 1))
                        if not drops or b.gt - drops[-1] > 0.5:
                            drops.append(b.gt)
                if b.has_coral() and caught_t is None:
                    caught_t = b.gt
                    break
            b.hold = set()
            pos = (round(b.x, 3), round(b.z, 3))
            r = dict(dist=dist, lat=lat, dyaw=dyaw, drops=[round(t - g0, 2) for t in drops],
                     caught=round(caught_t - g0, 2) if caught_t else None, pos=pos, wall_gap=round(dist, 2))
            res.append(r); print(r, flush=True)
            if b.has_coral():
                # throw it away near mid-field
                b.goto(5.5, 3.0, None, tol=0.3, timeout=2)
                b.step(tap=(PLACE,)); b.idle(0.25)
        while not b.s.get("done"):
            b.step(n=200)
    except MatchOver:
        pass
    if gi >= len(grid): break
json.dump(res, open(OUT / "calib18.json", "w"))
g.stop()
