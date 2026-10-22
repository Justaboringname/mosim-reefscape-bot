"""Calibration 14: algae to the net (Barge setpoint geometry + drop), algae to the processor, and the endgame climb.
argv: port, which ('net'|'proc')."""
from reefbot import RUNS  # repo-relative paths
import json, math, sys
from pathlib import Path
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot, SP_ID, wrap180
from reefbot.geom import Field
from reefbot.skills import Skills
from reefbot.client import L4, L1, PLACE, STOW, CLIMB, INTAKE
OUT = RUNS
PORT, WHICH = int(sys.argv[1]), sys.argv[2]
g = ReefGame(port=PORT); g.launch()
b = Bot(g.client, OUT / f"calib14-{PORT}.jsonl")
f = Field(); k = Skills(b, f)
b.reset()
log = []
def note(*a):
    log.append(a); print(*a, flush=True)
def local(p):
    yaw = math.radians(b.yaw); fx, fz = math.sin(yaw), math.cos(yaw); rx, rz = fz, -fx
    dx, dz = p[0] - b.x, p[1] - b.z
    return [round(dx * fx + dz * fz, 3), round(dx * rx + dz * rz, 3), round(p[2], 3)]
k.place(4, "L", "L4", back=False)
k.stow()
for attempt in range(3):
    face = [4, 3, 5][attempt]
    k.stow()
    if k.remove_algae(face, back=False, r=1.40): break
note("algae", b.has_algae(), k.events[-1])
held = [a for a in b.s["algae"] if a[3]]
note("algae held local at stow", local(held[0]) if held else None)
if WHICH == "net":
    # geometry at the Barge setpoint, facing -x (toward the barge) in open field
    b.goto(2.2, -2.0, 270, tol=0.05, ytol=2, timeout=4)
    b.tap(L4, confirm=lambda s: s["mech"]["sp"] == SP_ID["Barge"])
    b.idle(1.5)
    held = [a for a in b.s["algae"] if a[3]]
    note("barge setpoint: algae local", local(held[0]) if held else None, "elev", b.mech.get("elev"), "fbarge", b.mech.get("fbarge"))
    # creep toward the barge until blocked, logging x
    xs = []
    g0 = b.gt
    while b.gt - g0 < 3.0:
        b.step(-0.8, 0, 0)
        xs.append(round(b.x, 3))
        if len(xs) > 6 and abs(xs[-1] - xs[-6]) < 0.005: break
    held = [a for a in b.s["algae"] if a[3]]
    note("blocked at x", b.x, "algae", held[0][:3] if held else None)
    b.step(tap=(PLACE,)); b.idle(1.5)
    note("after place: net pts", b.s["bd"]["net"], "nAlgae", b.s["bd"]["nAlgae"], "algae near", [a[:3] for a in b.s["algae"] if abs(a[0]) < 1.2 and a[1] < 0])
    b.idle(2.0)
    note("2s later: net pts", b.s["bd"]["net"], "red", b.s["red"])
else:
    proc = f.processor["trig"]["c"]
    note("processor trigger", proc)
    # stand in front of the processor facing it (+z wall), Processor setpoint = L1 with algae
    b.goto(proc[0], proc[2] - 0.75, 0, tol=0.05, ytol=2, timeout=4)
    b.tap(L1, confirm=lambda s: s["mech"]["sp"] == SP_ID["Processor"])
    b.idle(1.2)
    held = [a for a in b.s["algae"] if a[3]]
    note("processor setpoint: algae local", local(held[0]) if held else None)
    xs = []; g0 = b.gt
    while b.gt - g0 < 2.5:
        b.step(0, 0.8, 0); xs.append(round(b.z, 3))
        if len(xs) > 6 and abs(xs[-1] - xs[-6]) < 0.005: break
    note("blocked at z", b.z)
    b.step(tap=(PLACE,))
    for i in range(12):
        b.idle(0.5)
        note(f"t+{0.5*(i+1):.1f}s blue", b.s["blue"], "red", b.s["red"], "bd.net", b.s["bd"]["net"], "rd.net", b.s["rd"]["net"])
# climb at the end: wait for endgame
k.stow()
cage_z = -2.10
b.goto(1.4, cage_z, 90, tol=0.05, ytol=2, timeout=5)
while b.t > 18 and not b.s.get("done"):
    b.step()
note("endgame t", b.t)
b.tap(CLIMB, confirm=lambda s: s["mech"]["sp"] == SP_ID["Climb"])
g0 = b.gt
while b.gt - g0 < 6 and not b.s.get("done"):
    sp = b.mech["sp"]
    if sp == SP_ID["Climb"]:
        b.step(-0.8, 0, 0)
    else:
        b.step()
    if int((b.gt - g0) / 0.25) != int((b.gt - g0 - 0.045) / 0.25):
        note(round(b.gt - g0, 2), "sp", b.sp, "x", round(b.x, 3), "y", round(b.s["robot"]["y"], 3), "ground", b.s["robot"]["ground"], "cage", b.mech.get("cage"), "climb", b.s["bd"]["climb"], "park", b.s["bd"]["park"])
while not b.s.get("done"):
    b.step()
note("final", b.s["blue"], b.s["red"], b.s["bd"], b.s["rd"])
json.dump(log, open(OUT / f"calib14-{WHICH}.json", "w"), default=str)
g.stop()
