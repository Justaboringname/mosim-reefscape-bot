"""Per-cycle segment report for teleop coral cycles (10-21). The score and reef-full time are too noisy at N20 to
show a 0.07 s/cycle change; the segment means over ~600 deliveries per batch are not (deliver residual sd 0.17 s).
usage: python -m reefbot.cycle_report TAG [TAG ...]   (TAG = match-file prefix, e.g. v54a)
Reports per tag: last-0.6 m time by level, deliver residual vs 0.50 + L/2.8, fetch time, yaw error at 0.6 m,
score mean, reef-full time and no-climb count."""
from reefbot import RUNS as _RUNS  # repo-relative
import glob
import json
import math
import statistics as st
import sys

from .geom import Field
from .nav import route, path_length

RUNS = str(_RUNS)


def wrap(a):
    return (a + 180) % 360 - 180


def report(tag, f):
    last = {"L4": [], "L2/3": []}
    resid, fetch, y06, sc, full, noclimb = [], [], [], [], [], 0
    rel_n, miss_n = 0, 0
    for fn in sorted(glob.glob(f"{RUNS}/match-{tag}-*.json")):
        d = json.load(open(fn))
        ev = d["events"]
        sc.append(d["res"]["score"])
        if d["res"]["bd"]["climb"] < 12:
            noclimb += 1
        states = []
        for line in open(fn[:-5] + ".jsonl"):
            try:
                s = json.loads(line)
            except ValueError:
                continue
            if "t" in s and "robot" in s:
                states.append(s)
        fs = next((s for s in states if s.get("reefB", "").count("1") >= 36), None)
        if fs:
            full.append(fs["t"])
        rel_n += sum(1 for e in ev if e[2] == "place: released")
        miss_n += sum(1 for e in ev if e[2] == "place: MISS")
        acq, last_rel = None, None
        for e in ev:
            if e[1] >= 135:
                continue
            if e[2] in ("algae: acquired", "net: released", "floor algae: acquired"):
                last_rel = None
            if e[2] == "intake: acquired":
                acq = e
                if last_rel is not None and 0 < last_rel - e[1] < 6:
                    fetch.append(last_rel - e[1])
            if e[2] == "place: released":
                last_rel = e[1]
            if e[2] == "align: ready" and acq is not None and isinstance(e[7], dict) and e[7].get("how") == "direct":
                x, z, H = f.pose(e[7]["face"], e[7]["side"], e[7]["back"], r=1.43)
                seg = [s for s in states if e[1] - 0.01 <= s["t"] <= acq[1]]
                acq = None
                if len(seg) < 3:
                    continue
                T = seg[-1]["t"]
                p0 = (seg[0]["robot"]["x"], seg[0]["robot"]["z"])
                L = path_length(p0, route(p0, (x, z)))
                resid.append((seg[0]["t"] - T) - (0.50 + L / 2.8))
                n = next((s for s in seg if math.hypot(s["robot"]["x"] - x, s["robot"]["z"] - z) < 0.6), None)
                if n is not None:
                    last["L4" if e[7]["level"] == "L4" else "L2/3"].append(n["t"] - T)
                    y06.append(abs(wrap(H - n["robot"]["yaw"])))
    if not sc:
        print(f"{tag}: no matches")
        return

    def m(v):
        return f"{st.mean(v):.3f}" if v else "  -  "
    se = st.stdev(sc) / math.sqrt(len(sc)) if len(sc) > 1 else 0
    print(f"{tag:6s} n={len(sc):2d} score {st.mean(sc):6.2f} se {se:.2f} | last0.6 L2/3 {m(last['L2/3'])} (n={len(last['L2/3'])}) "
          f"L4 {m(last['L4'])} | deliver resid {m(resid)} | fetch {m(fetch)} (n={len(fetch)}) | yaw@0.6 {st.mean(y06):.1f} deg | "
          f"reef full {len(full)}/{len(sc)} at {m(full)} s | no-climb {noclimb} | place MISS {miss_n}/{rel_n}")


def main():
    f = Field()
    for tag in sys.argv[1:]:
        report(tag, f)


if __name__ == "__main__":
    main()
