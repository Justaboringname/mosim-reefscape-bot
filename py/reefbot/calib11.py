"""Calibration 11: watch the coral intake's request/hasGamePiece flags and the pull target while creeping onto
a floor coral at 0.5 m/s with the intake already deployed."""
from reefbot import RUNS  # repo-relative paths
import json, math, sys
from pathlib import Path
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot, wrap180
from reefbot.geom import Field
from reefbot.skills import Skills
from reefbot.client import INTAKE, PLACE, STOW
OUT = RUNS
V = float(sys.argv[1]) if len(sys.argv) > 1 else 0.5
g = ReefGame(port=47600); g.launch()
b = Bot(g.client, OUT / "calib11.jsonl", decision_steps=4)
f = Field(); k = Skills(b, f)
b.reset()
k.place(4, "L", "L4", back=False)
k.stow()
def lying():
    return [c for c in b.s["coral"] if not c[3] and c[2] < 0.1 and c[0] > 5.5]
def loc(c):
    yaw = math.radians(b.yaw); fx, fz = math.sin(yaw), math.cos(yaw); rx, rz = fz, -fx
    dx, dz = c[0] - b.x, c[1] - b.z
    return round(dx * fx + dz * fz, 3), round(dx * rx + dz * rz, 3), round(c[2], 3)
for trial in range(3):
    k.stow()
    cs = lying()
    if not cs: b.idle(1.0); continue
    c = min(cs, key=lambda c: math.hypot(c[0] - b.x, c[1] - b.z))
    ax, az = c[0] - b.x, c[1] - b.z; d = math.hypot(ax, az); hdg = math.degrees(math.atan2(ax, az))
    b.goto(c[0] - ax / d * 1.2, c[1] - az / d * 1.2, hdg, tol=0.03, ytol=1.5, timeout=4)
    b.hold = {INTAKE}
    b.idle(0.5)
    g0 = b.gt
    print("trial", trial, "coral", [round(v, 3) for v in c[:3]], "axis-rel", round(wrap180(math.degrees(math.atan2(c[5], c[6])) - hdg)), flush=True)
    while b.gt - g0 < 3.0 and not b.has_coral():
        b.step(math.sin(math.radians(hdg)) * V, math.cos(math.radians(hdg)) * V, 0)
        fr = b.c.field()["robot"]["intakes"]["coralIntake"]
        near = [loc(cc) for cc in b.s["coral"] if math.hypot(cc[0] - b.x, cc[1] - b.z) < 0.9 and not cc[3]]
        print(round(b.gt - g0, 3), "req", fr["req"], "hasGP", fr["hasGP"], "tgt", [round(v, 3) for v in fr["tgt"]], "box", [round(v, 3) for v in fr["cols"][0]["local"]],
              "maxD", fr["maxDist"], "acc", fr["acc"], "planar", fr["planar"], "near", near, "mech", b.mech["coral"], flush=True)
    b.hold = set()
    print("trial", trial, "got", b.has_coral(), flush=True)
    if b.has_coral():
        b.wait_until(lambda s: s["mech"]["coral"]["st"] == 3, 2)
        b.goto(6.0, 0.0, 0, tol=0.1, timeout=3)
        b.step(tap=(PLACE,)); b.idle(0.5)
g.stop()
