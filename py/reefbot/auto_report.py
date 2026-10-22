"""Auto-phase timeline of match logs: L4 placements before t=135 and their timestamps. argv: glob..."""
import glob, json, sys
for pat in sys.argv[1:]:
    for p in sorted(glob.glob(pat)):
        if not p.endswith(".json"):
            continue
        d = json.load(open(p))
        R = [r for r in (json.loads(l) for l in open(p.replace(".json", ".jsonl"))) if "robot" in r]
        auto = [r for r in R if r["gs"] == 0]
        rel = [e[1] for e in d["events"] if e[2] == "place: released" and e[1] >= 134.9]
        grabs = [(e[1], e[2][10:]) for e in d["events"] if e[2].startswith("vertical:")]
        print(f"{p.split('/')[-1]:22s} score {d['res']['score']:4d} auto_pts {auto[-1]['blue']:3d} auto_L4 {len(rel)} "
              f"releases {[round(t, 2) for t in rel]} vertical {[g[1] for g in grabs]}")
