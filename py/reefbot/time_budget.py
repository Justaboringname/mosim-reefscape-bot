"""Where the match time goes, from match event logs (match-*.json): teleop seconds per activity, counts and mean
durations. usage: python -m reefbot.time_budget <glob>..."""
import glob
import json
import sys
from collections import defaultdict

GOT_CORAL = ("station: caught", "intake: acquired", "vertical: grabbed")
GOT_ALGAE = ("algae: acquired", "floor algae: acquired")
NET_END = ("net: scored", "net: released-miss", "net: fail", "net: released")


def budget(ev, t_from=135.0):
    out = defaultdict(float)
    cnt = defaultdict(int)
    last = t_from
    climb_t = None
    for e in ev:
        t, tag = e[1], e[2]
        if t > t_from:
            continue
        if tag.startswith("climb start") or tag == "climb":
            climb_t = t
        if isinstance(e[-1], dict) and e[-1].get("late"):
            cnt[tag] += 1                     # asynchronous shot judgment: a result, not a trip end
            continue
        if tag in GOT_CORAL:
            out["fetch coral"] += last - t; cnt["fetch coral"] += 1; last = t
        elif tag == "place: released":
            out["deliver+place coral"] += last - t; cnt["deliver+place coral"] += 1; last = t
        elif tag in GOT_ALGAE:
            out["fetch algae"] += last - t; cnt["fetch algae"] += 1; last = t
        elif tag in NET_END:
            out["net trip"] += last - t; cnt[tag] += 1; cnt["net trip"] += 1; last = t
    out["other/end"] += last
    return out, cnt


def main():
    files = [p for pat in sys.argv[1:] for p in sorted(glob.glob(pat))]
    tot = defaultdict(float)
    ctot = defaultdict(int)
    scores = []
    for p in files:
        d = json.load(open(p))
        ev = d["events"]
        # climb start is logged via mb.log, not ev: take the last action as the end
        o, c = budget(ev)
        for k, v in o.items():
            tot[k] += v
        for k, v in c.items():
            ctot[k] += v
        scores.append(d["res"]["score"])
    n = len(files)
    print(f"{n} matches, mean score {sum(scores) / max(1, n):.1f}  {scores}")
    for k in ("fetch coral", "deliver+place coral", "fetch algae", "net trip", "other/end"):
        c = ctot.get(k, 0)
        print(f"  {k:22s} {tot[k] / n:6.1f} s/match   n/match {c / n:5.1f}   mean {tot[k] / max(1, c):5.2f} s")
    for k in NET_END:
        print(f"  {k:22s} n/match {ctot.get(k, 0) / n:5.2f}")


if __name__ == "__main__":
    main()
