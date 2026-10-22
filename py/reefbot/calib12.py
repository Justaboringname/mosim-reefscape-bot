"""Calibration 12 (static geometry): held-coral position in the robot frame at L2/L3/L4 front/back, and the
algae intake box at LowAlgae/HighAlgae front/back. Robot stands in open field; front/back = facing toward/away
from reefbot import RUNS  # repo-relative paths
from the blue reef (FacingReef is evaluated at the tap)."""
import json, math
from pathlib import Path
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot, wrap180, SP_ID
from reefbot.geom import Field
from reefbot.skills import Skills
from reefbot.client import L2, L3, L4, STOW, PLACE
OUT = RUNS
g = ReefGame(port=47600); g.launch()
b = Bot(g.client, OUT / "calib12.jsonl")
f = Field(); k = Skills(b, f)
b.reset()
P = (1.6, -2.4)
to_reef = math.degrees(math.atan2(f.reef[0] - P[0], f.reef[1] - P[1]))
def local(c):
    yaw = math.radians(b.yaw); fx, fz = math.sin(yaw), math.cos(yaw); rx, rz = fz, -fx
    dx, dz = c[0] - b.x, c[1] - b.z
    return [round(dx * fx + dz * fz, 3), round(dx * rx + dz * rz, 3), round(c[2], 3)]
out = {"coral": {}, "algae": {}}
for lvl, btn, spn in (("L4", L4, "L4"), ("L3", L3, "L3"), ("L2", L2, "L2")):
    for orient in ("front", "back"):
        yaw = to_reef if orient == "front" else (to_reef + 180) % 360
        k.stow(); b.goto(P[0], P[1], yaw % 360, tol=0.03, ytol=1, timeout=4)
        b.tap(btn, confirm=lambda s: s["mech"]["sp"] == SP_ID[spn])
        b.idle(1.4)
        held = [c for c in b.s["coral"] if c[3]]
        fr = b.c.field()["robot"]["intakes"]
        out["coral"][f"{lvl}-{orient}"] = dict(local=local(held[0]) if held else None, facing=b.mech["facing"], elev=round(b.mech["elev"], 2),
                                               axis=[round(v, 3) for v in held[0][5:8]] if held else None)
        print(lvl, orient, out["coral"][f"{lvl}-{orient}"], flush=True)
        b.tap(btn, confirm=lambda s: s["mech"]["sp"] == SP_ID["Stow"]); b.idle(0.8)
# empty the robot: place the coral on the floor
k.stow(); b.step(tap=(PLACE,)); b.idle(0.5); k.stow(); b.goto(P[0] - 0.8, P[1], None, tol=0.1, timeout=2)
for lvl, btn, spn in (("High", L3, "HighAlgae"), ("Low", L2, "LowAlgae")):
    for orient in ("front", "back"):
        yaw = to_reef if orient == "front" else (to_reef + 180) % 360
        k.stow(); b.goto(P[0] - 0.8, P[1], yaw % 360, tol=0.03, ytol=1, timeout=4)
        b.tap(btn, confirm=lambda s: s["mech"]["sp"] == SP_ID[spn])
        b.idle(1.4)
        fr = b.c.field()["robot"]["intakes"]["algaeIntake"]
        out["algae"][f"{lvl}-{orient}"] = dict(box=fr["cols"][0]["local"], half=fr["cols"][0]["half"], tgt=fr["tgt"], facing=b.mech["facing"],
                                               elev=round(b.mech["elev"], 2), planar=fr["planar"], maxD=fr["maxDist"], acc=fr["acc"])
        print(lvl, orient, out["algae"][f"{lvl}-{orient}"], flush=True)
        b.tap(btn, confirm=lambda s: s["mech"]["sp"] == SP_ID["Stow"]); b.idle(0.8)
json.dump(out, open(OUT / "calib12.json", "w"), indent=1)
g.stop()
