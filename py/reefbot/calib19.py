"""Calibration 19: lollipop algae (staged coral at x=7.555 carrying an algae at y~0.51). Algae mode + Stack (L1) +
Intake; dump the algae-intake box in that setpoint, then try to take the algae from the z=0 lollipop at a few
standoffs, facing +x."""
from reefbot import RUNS  # repo-relative paths
import json, math
from pathlib import Path
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot, SP_ID, MatchOver
from reefbot.geom import Field
from reefbot.skills import Skills
from reefbot.client import INTAKE, PLACE, STOW, L1, MODE
OUT = RUNS
g = ReefGame(port=47601); g.launch()
f = Field()
b = Bot(g.client, OUT / "calib19.jsonl"); b.reset(); k = Skills(b, f)
res = []
try:
    k.place(4, "L", "L4", back=False); k.stow()
    b.tap(MODE, confirm=lambda s: s["mech"]["mode"] == 1)
    print("mode", b.mech["mode"], flush=True)
    b.goto(6.2, 0.9, 90, tol=0.05, ytol=2, timeout=4)
    b.tap(L1, confirm=lambda s: s["mech"]["sp"] == SP_ID["Stack"])
    b.idle(0.8)
    fr = b.c.field()["robot"]["intakes"]["algaeIntake"]
    print("stack-algae box", fr["cols"][0]["local"], fr["cols"][0]["half"], "tgt", fr["tgt"], "sp", b.sp, flush=True)
    for lz, standoff in ((0.0, 0.70), (1.83, 0.62), (-1.83, 0.78)):
        if b.t < 10: break
        al = [a for a in b.s["algae"] if math.hypot(a[0] - 7.555, a[1] - lz) < 0.3]
        if not al: print("no lollipop algae at", lz); continue
        a = al[0]
        if b.mech["sp"] != SP_ID["Stack"]:
            b.tap(L1, confirm=lambda s: s["mech"]["sp"] == SP_ID["Stack"]); b.idle(0.5)
        b.goto(a[0] - standoff - 0.6, a[1], 90, tol=0.04, ytol=2, timeout=4)
        b.hold = {INTAKE}
        g0 = b.gt
        while b.gt - g0 < 2.5 and not b.has_algae():
            vx, vz, w, d = b.drive_cmd(a[0] - standoff, a[1], 90, vmax=1.0)
            b.step(vx, vz, w)
        b.hold = set()
        r = dict(lz=lz, standoff=standoff, got=b.has_algae(), dt=round(b.gt - g0, 2), pos=(round(b.x, 3), round(b.z, 3)), algae=[round(v, 2) for v in a[:3]])
        res.append(r); print(r, flush=True)
        if b.has_algae():
            k.score_net(z=-2.0 if lz <= 0 else -1.0)
            print("net", b.s["bd"]["net"], flush=True)
            if b.mech["mode"] != 1:
                b.tap(MODE, confirm=lambda s: s["mech"]["mode"] == 1)
except MatchOver:
    pass
json.dump(res, open(OUT / "calib19.json", "w"))
g.stop()
