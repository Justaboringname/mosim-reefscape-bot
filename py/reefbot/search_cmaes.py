"""CMA-ES policy search over the scripted bot's control parameters, maximising the full-match score.
Each generation: popsize candidates x REPS matches, played by search_worker processes through the job queue.
Logs every candidate to runs/search/log.jsonl; the running best (by mean over its matches) to best.json.
argv: [popsize] [reps] [generations]"""
from reefbot import REPO  # repo-relative paths
import json
import sys
import time
from pathlib import Path

import cma
import numpy as np

ROOT = REPO / "runs" / "search"
Q, RUN, RES = ROOT / "queue", ROOT / "running", ROOT / "results"
for d in (Q, RUN, RES):
    d.mkdir(parents=True, exist_ok=True)

# name, default (v39), low, high
SPACE = [   # run 3 (10-19 pm): direct placement + g2_hyb; name, default, low, high
    ("direct_kp", 7.0, 4.0, 14.0),
    ("direct_a_brake", 11.0, 7.0, 16.0),
    ("ff_lead", 0.09, 0.03, 0.16),
    ("turn_frac", 0.25, 0.12, 0.7),
    ("kd_yaw", 0.35, 0.0, 0.8),
    ("ground_v_touch", 0.8, 0.3, 1.4),
    ("FF_LINEUP", 0.9, 0.6, 1.4),
    ("drive_amax", 10.94, 7.0, 16.0),
    ("la_gain", 0.18, 0.0, 0.6),
    ("la_base", 0.2, 0.0, 0.9),
    ("ff_kp", 1.277, 0.3, 4.0),
    ("net_final_v", 1.29, 0.5, 2.2),
    ("exit_dist", 0.166, 0.04, 0.5),
    ("exit_angle_low", 71.0, 30.0, 89.0),
    ("climb_margin", 0.96, 0.3, 2.0),
]


def to_params(u):
    return {n: float(lo + min(1.0, max(0.0, x)) * (hi - lo)) for (n, d0, lo, hi), x in zip(SPACE, u)}


def x0_norm():
    return [(d0 - lo) / (hi - lo) for (n, d0, lo, hi) in SPACE]


def requeue_stale(max_age=420):
    """Jobs claimed (mtime = claim time) more than max_age ago without a result go back to the queue (a worker
    died). 10-19 night 1: rename kept the write-time mtime, so late-claimed jobs were requeued while running and
    workers looped on finished jobs (livelock at gen 14)."""
    now = time.time()
    for p in RUN.glob("*.json"):
        jid = p.name.split(".")[0]
        if (RES / f"{jid}.json").exists():
            p.unlink(missing_ok=True)
            continue
        if now - p.stat().st_mtime > max_age:
            try:
                p.rename(Q / f"{jid}.json")
            except OSError:
                pass


PREFIX = "g"


def evaluate(gen, cands, reps):
    ids = {}
    for ci, u in enumerate(cands):
        params = to_params(u)
        for r in range(reps):
            jid = f"{PREFIX}{gen:03d}c{ci:02d}r{r}"
            (Q / f"{jid}.json").write_text(json.dumps(dict(id=jid, params=params)))
            ids[jid] = ci
    scores = {ci: [] for ci in range(len(cands))}
    pending = set(ids)
    while pending:
        for jid in list(pending):
            p = RES / f"{jid}.json"
            if p.exists():
                scores[ids[jid]].append(json.loads(p.read_text())["score"])
                pending.discard(jid)
        if (ROOT / "STOP").exists():
            raise SystemExit("STOP")
        requeue_stale()
        time.sleep(2.0)
    return scores


def main():
    global PREFIX
    pop = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    reps = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    gens = int(sys.argv[3]) if len(sys.argv) > 3 else 60
    PREFIX = sys.argv[4] if len(sys.argv) > 4 else "g"
    x0, sigma = x0_norm(), 0.15
    if len(sys.argv) > 5:                      # warm start from a params JSON (e.g. a previous run's mean.json)
        pj = json.loads(Path(sys.argv[5]).read_text())
        pj = pj.get("params", pj)
        x0 = [(pj.get(n, d0) - lo) / (hi - lo) for (n, d0, lo, hi) in SPACE]
        sigma = 0.10
    es = cma.CMAEvolutionStrategy(x0, sigma, {"popsize": pop, "bounds": [0.0, 1.0], "seed": 1323, "verbose": -9})
    log = open(ROOT / "log.jsonl", "a")
    best = None
    for gen in range(gens):
        cands = es.ask()
        # generation 0 also scores the starting point as a control
        if gen == 0:
            cands[0] = np.array(x0)
        scores = evaluate(gen, cands, reps)
        fit = []
        for ci, u in enumerate(cands):
            m = float(np.mean(scores[ci]))
            fit.append(-m)
            rec = dict(run=PREFIX, gen=gen, cand=ci, mean=round(m, 2), scores=scores[ci], params=to_params(u),
                       t=time.time())
            log.write(json.dumps(rec) + "\n")
            log.flush()
            if best is None or m > best["mean"]:
                best = rec
                (ROOT / "best.json").write_text(json.dumps(best, indent=1))
        es.tell(cands, fit)
        mean_params = to_params(es.mean)
        (ROOT / "mean.json").write_text(json.dumps(dict(gen=gen, params=mean_params), indent=1))
        print(f"gen {gen}: best this gen {-min(fit):.1f}, mean of gen {-np.mean(fit):.1f}, best so far {best['mean']}",
              flush=True)


if __name__ == "__main__":
    main()
