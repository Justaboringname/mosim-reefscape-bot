"""Station drill: near-face coral cycles only (station catch -> back-side L4/L3/L2 on faces 0-2 after removing their
algae by drop), to measure catch rate and riders for the current intake_station. argv: port, matches, tag"""
from reefbot import RUNS  # repo-relative paths
import json, math, os, sys
from pathlib import Path
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot, MatchOver, SP_ID
from reefbot.geom import Field
from reefbot.skills import Skills
from reefbot.client import SPECIAL, PLACE, STOW
OUT = RUNS
port, n, tag = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
FORCE_LATS = [float(x) for x in sys.argv[4].split(",")] if len(sys.argv) > 4 else None   # cycle forced catch offsets
per_lat = {}
g = ReefGame(port=port); g.launch(); f = Field()
def riders(b):
    yaw = math.radians(b.yaw); fx, fz = math.sin(yaw), math.cos(yaw)
    out = 0
    for c in b.s["coral"]:
        if c[3] or not (0.2 < c[2] < 0.65): continue
        dx, dz = c[0] - b.x, c[1] - b.z
        if abs(dx * fx + dz * fz) < 0.5 and abs(dx * fz - dz * fx) < 0.5: out += 1
    return out
tot = dict(attempts=0, caught=0, rider_after=0, time=0.0)
for m in range(n):
    b = Bot(g.client, OUT / f"drill-{tag}-{m}.jsonl"); b.reset(); k = Skills(b, f)
    k.fast_station = os.environ.get("DRILL_FAST") == "1"
    try:
        b.tap(SPECIAL, confirm=lambda s: s["mech"].get("station") == 1)
        k.place(5, "L", "L2", True, r=1.45)                  # preload onto the reef, as in a match
        slots = [(fc, sd, lv) for fc in (0, 1, 2) for lv in ("L4",) for sd in ("L", "R")]
        i = 0
        while b.t > 3:
            fc, sd, lv = slots[i % len(slots)]; i += 1
            which = k.pick_station(0 if f.pose(fc, sd, True)[1] >= 0 else 1)
            if b.has_algae():
                b.step(tap=(PLACE,)); b.idle(0.2)            # drill only: drop a knocked-loose algae it picked up
            if b.mech.get("sp") not in (SP_ID["Stow"], SP_ID["Intake"]):
                b.step(tap=(STOW,))
            t0 = b.gt
            if FORCE_LATS:
                lat_f = FORCE_LATS[i % len(FORCE_LATS)]
                k.catch_lat = lambda w, _l=lat_f: _l
            ok = k.intake_station(which)
            phases = [e for e in k.events if e[2].startswith("station:")]
            if FORCE_LATS:
                pl = per_lat.setdefault(lat_f, [0, 0]); pl[0] += 1; pl[1] += ok
            tot["attempts"] += 1; tot["caught"] += ok; tot["time"] += b.gt - t0
            r = riders(b); tot["rider_after"] += r > 0
            if ok:
                # dump it on the reef if the slot is free, else just place anywhere (back side) and move on
                k.place(fc, sd, lv, True)
    except MatchOver:
        pass
    print(m, json.dumps(tot), "switches", sum(1 for e in k.events if e[2].startswith("station: fouled")), flush=True)
print("FINAL", tag, json.dumps(tot), "catch_rate", round(tot["caught"] / max(1, tot["attempts"]), 3),
      "mean_time", round(tot["time"] / max(1, tot["attempts"]), 3), "per_lat", per_lat)
g.stop()
