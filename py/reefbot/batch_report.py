"""Compare match batches: score stats, placement mode/timing/misses, auto timeline. usage: python -m reefbot.batch_report tagA tagB ..."""
from reefbot import RUNS  # repo-relative paths
import glob
import json
import statistics as st
import sys
from collections import Counter, defaultdict


def report(tag):
    files = sorted(glob.glob(f"{RUNS}/match-{tag}-*.json"))
    sc, c, rdy, fifth, auto_n, sixth = [], Counter(), defaultdict(list), [], [], 0
    for p in files:
        d = json.load(open(p))
        ev = d["events"]
        sc.append(d["res"]["score"])
        c["pen"] += d["res"]["bd"]["pen"]
        c["climb0"] += d["res"]["bd"]["climb"] == 0
        rel = [e for e in ev if e[2] == "place: released"]
        in_auto = [e for e in rel if e[1] > 135.0]
        auto_n.append(len(in_auto))
        if len(rel) >= 5:
            fifth.append(rel[4][1])
        if len(in_auto) >= 6:
            sixth += 1
        for e in ev:
            t = e[2]
            if t in ("place: MISS", "direct: not converged, align fallback", "intake: miss", "intake: acquired",
                     "station: caught", "net: scored", "net: released-miss", "vertical: MISS", "climb: slipped, retry"):
                c[t] += 1
            if t == "place: MISS":
                c["MISS " + str(e[-1].get("slot", ["?", "?", "?"])[2])] += 1
            if t == "place: released":
                c["how=" + str(e[-1].get("how"))] += 1
            if t == "align: ready" and e[1] < 134:
                rdy[(e[-1].get("how", "align"), e[-1]["level"])].append(e[-1]["dt"])
    n = len(sc)
    if not n:
        print(tag, "no matches")
        return
    print(f"== {tag}: n={n} mean {sum(sc)/n:.1f} sd {st.pstdev(sc):.1f} min {min(sc)} max {max(sc)}  auto coral mean {sum(auto_n)/n:.2f} "
          f"(5 in auto {sum(1 for a in auto_n if a >= 5)}/{n}, 6: {sixth}) 5th release median t={st.median(fifth) if fifth else None}")
    print("   ", dict(c))
    for k in sorted(rdy):
        v = rdy[k]
        print(f"    ready dt {k}: n={len(v)} median {st.median(v):.2f} p90 {sorted(v)[int(0.9*len(v))]:.2f}")


if __name__ == "__main__":
    for t in sys.argv[1:]:
        report(t)
