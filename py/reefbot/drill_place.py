"""Placement drill for the direct controller: station mode on, repeat station catch -> place_direct on the next free
L4 (then L2 on faces without a low algae) until the reef is full or the match ends. Reports per placement the
final-approach time (first frame with d < 0.6 m -> release) and misses. Settings from env: DIRECT_KP, DIRECT_A,
FF_LEAD. argv: port, episodes, tag"""
from reefbot import RUNS  # repo-relative paths
import json, math, os, sys
from pathlib import Path
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot, MatchOver, SP_ID
from reefbot.client import SPECIAL, STOW
from reefbot.geom import Field
from reefbot.matchbot import MatchBot
OUT = RUNS
port, n, tag = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
g = ReefGame(port=port); g.launch(); f = Field()
res = []
for m in range(n):
    b = Bot(g.client, OUT / f"drillp-{tag}-{m}.jsonl"); b.reset()
    mb = MatchBot(b, f, log=lambda s: None); mb.configure_auto("near"); mb.fast_leave = True; mb.fast_station = True
    mb.place_mode = "direct"
    mb.direct_kp = float(os.environ.get("DIRECT_KP", mb.direct_kp)); mb.direct_a_brake = float(os.environ.get("DIRECT_A", mb.direct_a_brake))
    mb.ff_lead = float(os.environ.get("FF_LEAD", mb.ff_lead))
    slots = [(fc, sd, "L4") for fc in (1, 0, 2, 5, 3, 4) for sd in ("L", "R")] + \
            [(fc, sd, "L2") for fc in (1, 0, 2, 5, 3, 4) for sd in ("L", "R") if not f.low_algae[fc]]
    try:
        b.tap(SPECIAL, confirm=lambda s: s["mech"].get("station") == 1)
        mb.place_any(5, "R", "L4", False)
        b.wait_until(lambda s: s.get("gs", 0) != 0, timeout=20)
        i = 0
        while b.t > 4 and i < len(slots):
            fc, sd, lv = slots[i]; i += 1
            mb.resolve_checks(mb.failed)
            if b.mech.get("sp") not in (SP_ID["Stow"], SP_ID["Intake"]):
                b.step(tap=(STOW,))
            px, pz, _ = f.pose(fc, sd, True)
            which = mb.pick_station(0 if f.stations[0]["p"][2] * pz >= 0 else 1)
            if not mb.intake_station(which):
                i -= 1; b.idle(0.3); continue
            mb.wait_handoff()
            mb.coral_cycle(fc, sd, lv)
        mb.resolve_checks(mb.failed); b.idle(1.0); mb.resolve_checks(mb.failed)
    except MatchOver:
        pass
    ev = mb.events
    rel = [e for e in ev if e[2] == "place: released" and e[1] < 134]
    miss = [e for e in ev if e[2] == "place: MISS"]
    fb = [e for e in ev if e[2] == "direct: not converged, align fallback"]
    rdy = [e[-1]["dt"] for e in ev if e[2] == "align: ready" and e[1] < 134]
    r = dict(m=m, placed=len(rel), miss=len(miss), fallback=len(fb), ready_dt_median=round(sorted(rdy)[len(rdy) // 2], 2) if rdy else None,
             kp=mb.direct_kp, a=mb.direct_a_brake, lead=mb.ff_lead)
    res.append(r); print(json.dumps(r), flush=True)
    json.dump(dict(res=r, events=ev), open(OUT / f"drillp-{tag}-{m}.json", "w"))
print("FINAL", tag, json.dumps(dict(placed=sum(r["placed"] for r in res), miss=sum(r["miss"] for r in res), fallback=sum(r["fallback"] for r in res))))
g.stop()
