"""Policy-search worker: keeps one MoSim instance and plays one full match per job.
Jobs are JSON files in runs/search/queue ({"id", "params"}); a job is claimed by renaming it into running/,
the result goes to results/<id>.json. Stops when runs/search/STOP exists. argv: port worker_id"""
from reefbot import REPO  # repo-relative paths
import json
import os
import sys
import time
import traceback
from pathlib import Path

from .bot import Bot
from .gamectl import ReefGame
from .geom import Field
from .matchbot import MatchBot

ROOT = REPO / "runs" / "search"
Q, RUN, RES = ROOT / "queue", ROOT / "running", ROOT / "results"
for d in (Q, RUN, RES, ROOT / "tmp"):
    d.mkdir(parents=True, exist_ok=True)


def claim(wid):
    for p in sorted(Q.glob("*.json")):
        dst = RUN / f"{p.stem}.{wid}.json"
        try:
            os.rename(p, dst)
            os.utime(dst, None)          # claim time: the orchestrator requeues by age (rename kept the old mtime)
            return dst
        except OSError:
            continue
    return None


def main():
    port, wid = int(sys.argv[1]), sys.argv[2]
    g = ReefGame(port=port)
    g.launch()
    f = Field()
    while not (ROOT / "STOP").exists():
        job_path = claim(wid)
        if job_path is None:
            time.sleep(1.0)
            continue
        job = json.loads(job_path.read_text())
        if (RES / f"{job['id']}.json").exists():  # already played (a requeued duplicate)
            job_path.unlink(missing_ok=True)
            continue
        t0 = time.time()
        try:
            b = Bot(g.client, ROOT / "tmp" / f"{wid}.jsonl")
            b.reset()
            mb = MatchBot(b, f, log=lambda m: None)
            mb.configure_auto(os.environ.get("AUTO_VARIANT", "near"))
            mb.apply_params(job["params"])
            s = mb.run()
            res = dict(id=job["id"], score=s["blue"] - s["red"], bd=s["bd"], reef=s["reefB"].count("1"),
                       wall=round(time.time() - t0, 1), worker=wid)
            (RES / f"{job['id']}.json").write_text(json.dumps(res))
            job_path.unlink(missing_ok=True)
        except Exception:
            traceback.print_exc()
            # put the job back and restart the game instance
            try:
                os.rename(job_path, Q / f"{job['id']}.json")
            except OSError:
                pass
            try:
                g.stop()
            except Exception:
                pass
            time.sleep(3)
            g = ReefGame(port=port)
            g.launch()
    g.stop()


if __name__ == "__main__":
    main()
