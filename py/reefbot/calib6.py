"""Calibration 6: station-mode coral intake (RobotSpecial toggles stationCoral): where to stand, which way to face,
catch time; then score it back-side on face 0."""
from reefbot import RUNS  # repo-relative paths
import json, math, sys
from pathlib import Path
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot, wrap180
from reefbot.geom import Field
from reefbot.skills import Skills
from reefbot.client import INTAKE, SPECIAL, STOW
OUT = RUNS
g = ReefGame(port=47600); g.launch()
b = Bot(g.client, OUT / "calib6.jsonl")
f = Field(); k = Skills(b, f)
b.reset()
k.place(4, "L", "L4", back=False)
k.stow()
b.tap(SPECIAL, confirm=lambda s: s["mech"].get("station") == 1)
print("station flag", b.mech.get("station"), "sp", b.sp, flush=True)
st = f.stations[0] if f.stations[0]["p"][2] > 0 else f.stations[1]
sx, sz = st["spawn"][0], st["spawn"][2]
fx, fz = st["spawnFwd"][0], st["spawnFwd"][2]; h = math.hypot(fx, fz); fx, fz = fx / h, fz / h
face_station = math.degrees(math.atan2(-fx, -fz))       # robot yaw that faces the station
trials = [(0.75, face_station), (0.75, face_station + 180), (0.60, face_station), (0.90, face_station + 180), (0.60, face_station + 180)]
res = []
for dist, yaw in trials:
    if b.t < 8: break
    k.stow()
    # step back out, then come in
    b.goto(sx + fx * 2.0, sz + fz * 2.0, yaw % 360, tol=0.1, ytol=5, timeout=4)
    tx, tz = sx + fx * dist, sz + fz * dist
    g0 = b.gt
    b.hold = {INTAKE}
    got_t = None
    while b.gt - g0 < 5:
        vx, vz, w, d = b.drive_cmd(tx, tz, yaw % 360)
        b.step(vx, vz, w)
        if b.has_coral() and got_t is None:
            got_t = b.gt - g0
        if got_t is not None and b.mech["coral"]["st"] == 3 and b.mech["coral"]["at"]:
            break
    b.hold = set()
    r = dict(dist=dist, yaw=round(yaw % 360, 1), got=got_t and round(got_t, 3), ready=round(b.gt - g0, 3),
             st=b.mech["coral"], pos=(round(b.x, 3), round(b.z, 3)), sp=b.sp)
    print(r, flush=True); res.append(r)
    if b.has_coral():
        back = abs(wrap180(b.yaw - f.pose(0, "L", True)[2])) < 90
        side = "L" if len([x for x in res if x["got"]]) % 2 else "R"
        k.place(0, side, "L4", back=back)
        print(k.events[-2:], flush=True)
json.dump(res, open(OUT / "calib6.json", "w"))
print("final", b.s["blue"], b.s["red"], b.s["bd"])
g.stop()
