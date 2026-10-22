"""Calibration 8: station-mode catch sweep. Robot holds Intake at a grid of standoff distances / lateral offsets /
headings; every drop's trajectory is logged at 18 ms. Output runs/reef/calib8.json."""
from reefbot import RUNS  # repo-relative paths
import json, math
from pathlib import Path
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot, wrap180
from reefbot.geom import Field
from reefbot.skills import Skills
from reefbot.client import INTAKE, SPECIAL, PLACE, STOW, L1
OUT = RUNS
g = ReefGame(port=47600); g.launch()
b = Bot(g.client, OUT / "calib8.jsonl", decision_steps=4)
f = Field(); k = Skills(b, f)
b.reset()
b.tap(SPECIAL, confirm=lambda s: s["mech"].get("station") == 1)
st = next(s for s in f.stations if s["p"][2] > 0)
sx, sz = st["spawn"][0], st["spawn"][2]
fx, fz = st["spawnFwd"][0], st["spawnFwd"][2]; h = math.hypot(fx, fz); fx, fz = fx / h, fz / h
rx, rz = fz, -fx                                   # lateral (station right)
face = math.degrees(math.atan2(-fx, -fz)) % 360    # yaw facing the station
grid = []
for dist in (0.62, 0.7, 0.78, 0.86, 0.95, 1.05):
    for lat in (0.0,):
        for yaw in (face, (face + 180) % 360):
            grid.append((dist, lat, yaw))
res = []
# dump the preload first: score it so the robot is empty
k.place(4, "L", "L4", back=False)
k.stow()
for dist, lat, yaw in grid:
    if b.t < 4: break
    if b.has_coral():                     # drop a caught coral into the trough area quickly: L1 + place
        b.tap(L1, confirm=lambda s: s["mech"]["sp"] in (3, 4)); b.step(tap=(PLACE,)); b.idle(0.3)
        k.stow()
    tx, tz = sx + fx * dist + rx * lat, sz + fz * dist + rz * lat
    b.goto(tx, tz, yaw, tol=0.03, ytol=2, timeout=4, vmax=3)
    b.hold = {INTAKE}
    g0 = b.gt; traj = []; caught = None
    seen0 = {round(c[0], 2) for c in b.s["coral"]}
    while b.gt - g0 < 2.6:
        vx, vz, w, d = b.drive_cmd(tx, tz, yaw)
        b.step(vx, vz, w)
        new = [c for c in b.s["coral"] if c[2] > 0.25 and not c[3] and math.hypot(c[0] - tx, c[1] - tz) < 1.6]
        traj.append((round(b.gt - g0, 3), [[round(v, 3) for v in c[:3]] for c in new], round(b.x, 3), round(b.z, 3), round(b.yaw, 1)))
        if b.has_coral():
            caught = round(b.gt - g0, 3); break
    b.hold = set()
    r = dict(dist=dist, lat=lat, yaw=round(yaw, 1), caught=caught, robot=(round(b.x, 3), round(b.z, 3)), traj=traj)
    res.append(r)
    print({k2: v for k2, v in r.items() if k2 != "traj"}, flush=True)
json.dump(dict(station=st, res=res), open(OUT / "calib8.json", "w"))
print("final", b.s["blue"], b.s["red"], b.s["bd"]["pen"])
g.stop()
