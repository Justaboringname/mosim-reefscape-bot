"""L1 trough drill (10-21). The trough was never used by the bot; the end-game idle (~5 s before the climb) could take
one coral there (+2). Madtown L1 (ReefscapeRobotBase.cs:259-281, Madtown.cs:585-590): with a coral held the L1 button
sets L1 (l1Setpoint; no front/back variant), Place then releases with force (0, -2.5, 0). The trough counts every
coral inside its trigger boxes (Trough.cs), reported per trough in state "trough" (order can swap between episodes).
Per attempt: station catch + handoff, goto the face-centre pose at radius R with the robot facing the reef (front) or
away (back), tap L1, wait for the elevator to settle, tap Place, read trough[0] 1.0 s later.
argv: port, episodes, tag, orientation (front|back), radii (comma list)"""
from reefbot import RUNS  # repo-relative paths
import json
import math
import sys
from pathlib import Path

from reefbot.bot import Bot, MatchOver, SP_ID
from reefbot.client import SPECIAL, STOW, L1, PLACE
from reefbot.gamectl import ReefGame
from reefbot.geom import Field
from reefbot.matchbot import MatchBot

OUT = RUNS
port, n, tag = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
orient = sys.argv[4] if len(sys.argv) > 4 else "front"
radii = [float(x) for x in (sys.argv[5] if len(sys.argv) > 5 else "1.40").split(",")]
back = orient == "back"
g = ReefGame(port=port)
g.launch()
f = Field()
rows = []
for m in range(n):
    b = Bot(g.client, OUT / f"drillt-{tag}-{m}.jsonl")
    b.reset()
    mb = MatchBot(b, f, log=lambda s: None)
    mb.configure_auto("near")
    mb.place_mode = "direct"
    k = 0
    try:
        b.tap(SPECIAL, confirm=lambda s: s["mech"].get("station") == 1)
        b.wait_until(lambda s: s.get("gs", 0) != 0, timeout=20)
        b.idle(3.5)
        faces = (1, 0, 2, 1, 0, 2, 5, 3, 5, 3)
        while b.t > 6 and k < 40:
            face = faces[k % len(faces)]
            r = radii[k % len(radii)]
            k += 1
            if b.mech.get("sp") not in (SP_ID["Stow"], SP_ID["Intake"]):
                b.step(tap=(STOW,))
            x, z, yaw = f.pose(face, "C", back, r=r)
            which = mb.pick_station(0 if f.stations[0]["p"][2] * z >= 0 else 1)
            if not mb.intake_station(which):
                b.idle(0.3)
                continue
            mb.wait_handoff()
            if not mb.coral_ready():
                mb.stow()
                continue
            t0 = b.gt
            ok_pose = b.goto(x, z, yaw, tol=0.03, ytol=2.0, timeout=4.0)
            t_arr = b.gt
            tr0 = sum(b.s.get("trough") or [0])        # the two troughs' order can swap between episodes
            pressed = b.tap(L1, confirm=lambda s: s["mech"].get("sp") == SP_ID["L1"], tries=3)
            last_e, settled, g1 = None, 0, b.gt
            while b.gt - g1 < 1.5 and settled < 2:
                b.step(0.0, 0.0, 0.0)
                e = b.mech.get("elev", 0.0)
                settled = settled + 1 if (last_e is not None and abs(e - last_e) < 0.12) else 0
                last_e = e
            t_set = b.gt
            b.step(tap=(PLACE,))
            g2 = b.gt
            while b.gt - g2 < 1.0:
                b.step(0.0, 0.0, 0.0)
            tr1 = sum(b.s.get("trough") or [0])
            held = [c for c in b.s.get("coral", []) if c[3]]
            row = dict(m=m, face=face, r=r, orient=orient, pose_ok=ok_pose, pressed=pressed, elev=round(b.mech.get("elev", 0), 2),
                       scored=tr1 > tr0, tr0=tr0, tr1=tr1, still_held=bool(held), t_drive=round(t_arr - t0, 2),
                       t_l1=round(t_set - t_arr, 2), t=round(b.t, 1))
            rows.append(row)
            print(json.dumps(row), flush=True)
            mb.stow()
            # back off the reef before the next catch
            rx, rz = b.x - f.reef[0], b.z - f.reef[1]
            h = math.hypot(rx, rz)
            b.goto(f.reef[0] + rx / h * 2.2, f.reef[1] + rz / h * 2.2, None, tol=0.2, timeout=1.0)
    except MatchOver:
        pass
for r in radii:
    sub = [x for x in rows if x["r"] == r]
    if sub:
        print("SUMMARY", tag, orient, r, "scored %d/%d" % (sum(1 for x in sub if x["scored"]), len(sub)),
              "t_l1 %.2f" % (sum(x["t_l1"] for x in sub) / len(sub)), flush=True)
json.dump(rows, open(OUT / f"drillt-{tag}.json", "w"))
g.stop()
