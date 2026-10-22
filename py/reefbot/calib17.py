"""Calibration 17: embedded-coral shedding. Provoke failures (coral across the heading, centred, slow contact),
detect a coral inside the bumper footprint, then try maneuvers until it is free. Logs which maneuver worked."""
from reefbot import RUNS  # repo-relative paths
import json, math, sys
from pathlib import Path
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot, wrap180, MatchOver
from reefbot.geom import Field
from reefbot.skills import Skills
from reefbot.client import INTAKE, PLACE, STOW
OUT = RUNS
g = ReefGame(port=47601); g.launch()
f = Field()
results = []
def embedded(b):
    yaw = math.radians(b.yaw); fx, fz = math.sin(yaw), math.cos(yaw); rx, rz = fz, -fx
    out = []
    for c in b.s["coral"]:
        if c[3] or c[4] or c[2] > 0.3: continue
        dx, dz = c[0] - b.x, c[1] - b.z
        fw, lt = dx * fx + dz * fz, dx * rx + dz * rz
        if abs(fw) < 0.47 and abs(lt) < 0.47:
            out.append((round(fw, 2), round(lt, 2), round(c[2], 2)))
    return out
MANEUVERS = ["reverse", "spin", "strafe", "brake", "intake_creep", "intake_reverse"]
def maneuver(b, name):
    yaw = math.radians(b.yaw); fx, fz = math.sin(yaw), math.cos(yaw); rx, rz = fz, -fx
    if name == "reverse":
        for _ in range(9): b.step(-fx * 1.5, -fz * 1.5, 0)
    elif name == "spin":
        for _ in range(12): b.step(0, 0, 600)
    elif name == "strafe":
        for _ in range(9): b.step(rx * 1.5, rz * 1.5, 0)
    elif name == "brake":
        for _ in range(6): b.step(fx * 2.5, fz * 2.5, 0)
        for _ in range(4): b.step(0, 0, 0)
    elif name == "intake_creep":
        b.hold = {INTAKE}
        for _ in range(12): b.step(fx * 0.3, fz * 0.3, 0)
        b.hold = set()
    elif name == "intake_reverse":
        b.hold = {INTAKE}
        for _ in range(10): b.step(-fx * 0.6, -fz * 0.6, 0)
        b.hold = set()
    for _ in range(3): b.step()
for ep in range(2):
    b = Bot(g.client, OUT / f"calib17-{ep}.jsonl")
    b.reset(); k = Skills(b, f)
    try:
        k.place(4, "L", "L4", back=False)
        while b.t > 8:
            k.stow()
            if b.has_coral():
                b.step(tap=(PLACE,)); b.idle(0.3)
            # provoke: target any lying coral but approach ACROSS its axis, centred, at 1.2 m/s
            cs = [c for c in b.s["coral"] if not c[3] and not c[4] and c[2] < 0.1 and 3.0 < c[0] < 8.2 and abs(c[1]) < 3.5
                  and math.hypot(c[0] - f.reef[0], c[1] - f.reef[1]) > 1.6]
            if not cs: b.idle(0.5); continue
            c = min(cs, key=lambda c: math.hypot(c[0] - b.x, c[1] - b.z))
            axis = math.degrees(math.atan2(c[5], c[6])); hdg = axis + 90
            ux, uz = math.sin(math.radians(hdg)), math.cos(math.radians(hdg))
            for sgn in (1, -1):
                sx, sz = c[0] - sgn * ux * 1.4, c[1] - sgn * uz * 1.4
                if abs(sz) < 3.4 and sx < 8.2 and math.hypot(sx - f.reef[0], sz - f.reef[1]) > 1.6: break
            ux, uz = sgn * ux, sgn * uz; hdg = math.degrees(math.atan2(ux, uz))
            b.goto(sx, sz, hdg, tol=0.05, ytol=2, timeout=3)
            b.hold = {INTAKE}
            g0 = b.gt
            while b.gt - g0 < 1.6 and not b.has_coral():
                b.step(ux * 1.2, uz * 1.2, 0)
            b.hold = set()
            for _ in range(3): b.step()
            em = embedded(b)
            rec = dict(ep=ep, t=round(b.t, 1), got=b.has_coral(), embedded=em, tried=[])
            if em and not b.has_coral():
                for m in MANEUVERS:
                    maneuver(b, m)
                    em2 = embedded(b)
                    rec["tried"].append((m, em2))
                    if not em2: break
            results.append(rec); print(rec, flush=True)
            if b.has_coral():
                b.step(tap=(PLACE,)); b.idle(0.3)
    except MatchOver:
        pass
json.dump(results, open(OUT / "calib17.json", "w"))
g.stop()
