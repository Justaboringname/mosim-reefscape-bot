"""Calibration 10: controlled ground-pickup study. Drop our own coral in open floor, back off to a chosen approach
angle relative to its long axis, then drive through it with method (Intake | Stack) at speed v."""
from reefbot import RUNS  # repo-relative paths
import json, math, sys, itertools
from pathlib import Path
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot, wrap180, SP_ID
from reefbot.geom import Field
from reefbot.skills import Skills
from reefbot.client import INTAKE, PLACE, STOW, L1
OUT = RUNS
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 47600
SEED = int(sys.argv[2]) if len(sys.argv) > 2 else 0
g = ReefGame(port=PORT); g.launch()
fd = g.client  # field dump after reset
b = Bot(g.client, OUT / f"calib10-{PORT}.jsonl")
f = Field(); k = Skills(b, f)
b.reset()
info = b.c.field()
print("coralColliders", info.get("coralColliders"), flush=True)
combos = list(itertools.product(["Intake", "Stack"], [1.5, 2.5, 3.5], [0, 45, 90]))
combos = combos[SEED % len(combos):] + combos[:SEED % len(combos)]
spots = [(6.2, 0.0), (6.6, -1.0), (6.6, 1.0), (2.0, -2.5), (2.0, 2.5), (6.0, -2.0), (6.0, 2.0), (2.2, -1.0)]
res = []
def my_coral():
    # the coral nearest the robot that is lying on the floor
    cs = [c for c in b.s["coral"] if not c[3] and c[2] < 0.2]
    return min(cs, key=lambda c: math.hypot(c[0] - b.x, c[1] - b.z)) if cs else None
for ti, (method, v, ang) in enumerate(combos * 3):
    if b.t < 6 or b.s.get("done"): break
    # make sure we hold a coral in the EE
    if not (b.has_coral() and b.mech["coral"]["st"] == 3):
        k.stow()
        st = f.stations[ti % 2]
        tgt = k.loose_coral(near=(st["trig"]["c"][0], st["trig"]["c"][2]))
        ok = k.intake_ground(speed=1.5, target=tgt)
        if not ok: continue
        k.wait_handoff()
    k.stow()
    sx, sz = spots[ti % len(spots)]
    b.goto(sx, sz, 0, tol=0.05, ytol=3, timeout=4)
    b.step(tap=(PLACE,)); b.idle(0.7)
    c = my_coral()
    if c is None or math.hypot(c[0] - b.x, c[1] - b.z) > 1.2:
        res.append(dict(ti=ti, method=method, v=v, ang=ang, err="no dropped coral")); continue
    ax_yaw = math.degrees(math.atan2(c[5], c[6]))     # long-axis direction on the floor
    app_yaw = ax_yaw + ang                             # robot heading for the approach
    ux, uz = math.sin(math.radians(app_yaw)), math.cos(math.radians(app_yaw))
    # start point 1.8 m before the coral along the approach; choose the side that stays inside the field
    for sgn in (1, -1):
        p0 = (c[0] - sgn * ux * 1.8, c[1] - sgn * uz * 1.8)
        if -7.8 < p0[0] < 7.8 and -3.4 < p0[1] < 3.4 and math.hypot(p0[0] - 4.3, p0[1]) > 1.6:
            break
    ux, uz = sgn * ux, sgn * uz
    hdg = math.degrees(math.atan2(ux, uz))
    k.stow()
    b.goto(p0[0], p0[1], hdg, tol=0.04, ytol=2, timeout=4)
    c = my_coral() or c
    if method == "Stack":
        b.tap(L1, confirm=lambda s: s["mech"]["sp"] == SP_ID["Stack"])
        b.idle(0.45)
    g0 = b.gt; start = (b.x, b.z); got = None
    cx, cz = c[0], c[1]
    while b.gt - g0 < 3.0:
        d = math.hypot(cx - b.x, cz - b.z)
        if d < 1.3: b.hold = {INTAKE}
        tx, tz = cx + ux * 1.0, cz + uz * 1.0
        vx, vz, w, _ = b.drive_cmd(tx, tz, hdg, vmax=v)
        b.step(vx, vz, w)
        if b.has_coral(): got = round(b.gt - g0, 3); break
        if (b.x - cx) * ux + (b.z - cz) * uz > 0.9: break
    b.hold = set()
    g1 = b.gt
    if got:
        b.wait_until(lambda s: s["mech"]["coral"]["st"] == 3 and s["mech"]["coral"]["at"] == 1, 2.0)
    r = dict(ti=ti, method=method, v=v, ang=ang, got=got, ready=round(b.gt - g1, 3) if got else None,
             st=b.mech["coral"]["st"], coral_axis=round(ax_yaw, 1), pen=b.s["bd"]["pen"])
    res.append(r); print(r, flush=True)
json.dump(res, open(OUT / f"calib10-{PORT}.json", "w"))
print("final", b.s["blue"], b.s["red"], b.s["bd"]["pen"])
g.stop()
