"""Full-match REEFSCAPE bot for Madtown 1323 (v1): fixed-priority slot schedule from first principles.

Measured building blocks (runs/reef/calib*): ground pickup ~1.0-1.5 s from a near reef pose, handoff 0.4-0.5 s
(overlaps the drive back), L4 raise 0.9 s; back-side scoring needs no turn after a station pickup. Reef algae block
L3 everywhere and L2 on low-algae faces; L4 and L2-on-high-algae faces are open from the start. Algae near the
barge side go to the net (+4, the barge is ~2.5 m from faces 3-5); algae from the station-side faces are dropped.
"""

from __future__ import annotations

from reefbot import RUNS  # repo-relative paths
import json
import math
import sys
import time
from pathlib import Path

from .bot import Bot, SP_ID, wrap180, MatchOver
from .client import STOW, PLACE, CLIMB
from .gamectl import ReefGame
from .geom import Field
from .nav import route, path_length
from .skills import Skills

OUT = RUNS
R_LEVEL = {"L4": 1.432, "L3": 1.40, "L2": 1.45}
NEAR = (0, 1, 2)
FAR = (3, 4, 5)
CAGE_Z = -2.10


class MatchBot(Skills):
    def __init__(self, bot: Bot, field: Field, log=print):
        super().__init__(bot, field)
        self.log = log
        self.algae_gone = [False] * 6
        self.failed = {}              # slot -> failures
        self.climbed = False

    # ------------------------------------------------------------------ state helpers
    def slot_filled(self, face, side, level) -> bool:
        return self.b.s["reefB"][self.f.bit(face, side, level)] == "1"

    def refresh_algae(self):
        """A face's algae is gone once no algae sits within 0.25 m of its spot on the reef."""
        rc = self.f.reef
        for fi in range(6):
            a = math.radians(self.f.face_yaw[fi])
            sx, sz = rc[0] + 0.68 * math.sin(a), rc[1] + 0.68 * math.cos(a)
            present = any(math.hypot(al[0] - sx, al[1] - sz) < 0.25 and al[2] > 0.6 for al in self.b.s["algae"] if not al[3])
            self.algae_gone[fi] = not present

    def available(self, face, level) -> bool:
        if level == "L4":
            return True
        if level == "L2" and not self.f.low_algae[face]:
            return True
        return self.algae_gone[face]

    def schedule(self):
        """Ordered list of (face, side, level)."""
        sl = []
        for face in (0, 2, 1, 5, 3, 4):
            for side in ("L", "R"):
                sl.append((face, side, "L4"))
        for face in (1, 5, 3):
            for side in ("L", "R"):
                sl.append((face, side, "L2"))
        for face in (0, 2, 1, 4, 5, 3):
            for side in ("L", "R"):
                sl.append((face, side, "L3"))
            if self.f.low_algae[face]:
                for side in ("L", "R"):
                    sl.append((face, side, "L2"))
        return sl

    def next_slot(self):
        self.refresh_algae()
        for face, side, level in self.schedule():
            if self.slot_filled(face, side, level) or self.failed.get((face, side, level), 0) >= 2:
                continue
            if self.available(face, level):
                return (face, side, level)
            return ("ALGAE", face, None)       # the next slot in order needs its face cleared first
        return None

    # ------------------------------------------------------------------ timing
    def climb_deadline(self) -> float:
        """Match-timer value at which the climb must start (travel + 2.4 s climb + margin)."""
        b = self.b
        L = path_length((b.x, b.z), route((b.x, b.z), (1.35, CAGE_Z)))
        return 2.4 + L / 3.2 + 1.2

    # ------------------------------------------------------------------ macro actions
    def coral_cycle(self, face, side, level) -> bool:
        b, f = self.b, self.f
        r = R_LEVEL[level]
        px, pz, _ = f.pose(face, side, True, r=r)
        if not b.has_coral():
            k = self
            if b.mech.get("sp") not in (SP_ID["Stow"], SP_ID["Intake"]):
                k.stow()
            c = self.best_coral(to=(px, pz))
            if c is None:
                st = f.stations[0] if pz > 0 else f.stations[1]
                b.goto(st["trig"]["c"][0] - 1.3, st["trig"]["c"][2] * 0.7, None, tol=0.25, timeout=0.6)
                return False
            if not self.intake_ground(target=c):
                return False
        self.wait_handoff()
        ok = self.place(face, side, level, True, r=r)
        if not ok:
            self.failed[(face, side, level)] = self.failed.get((face, side, level), 0) + 1
        return ok

    def algae_task(self, face) -> bool:
        b = self.b
        if b.has_coral():
            return False
        if b.mech.get("sp") != SP_ID["Stow"]:
            self.stow()
        ok = self.remove_algae(face, back=True, r=1.40)
        if not ok:
            ok = self.remove_algae(face, back=False, r=1.40)
        if not ok:
            self.algae_gone[face] = True       # give up on this face's L3/L2-low slots via failure counts
            for side in ("L", "R"):
                for lvl in ("L3", "L2"):
                    self.failed[(face, side, lvl)] = 9
            return False
        if face in FAR and b.t > self.climb_deadline() + 6:
            self.score_net(z=-2.0)
        else:
            # drop it away from the reef: back off 0.5 m radially, Place (the algae falls in front of the reef)
            x, z, yaw = self.f.pose(face, "C", True, r=1.95)
            b.goto(x, z, None, tol=0.1, timeout=1.2)
            b.step(tap=(PLACE,))
            b.idle(0.2)
            if b.has_algae():
                b.step(tap=(PLACE,)); b.idle(0.2)
            self.stow()
        return True

    def climb(self):
        b = self.b
        self.log(f"climb start t={b.t:.2f}")
        if b.has_algae() or b.has_coral():
            b.step(tap=(PLACE,)); b.idle(0.15)
        if b.mech.get("sp") != SP_ID["Stow"]:
            self.stow()
        b.goto(1.35, CAGE_Z, 90.0, tol=0.08, ytol=4, timeout=4)
        b.tap(CLIMB, confirm=lambda s: s["mech"]["sp"] == SP_ID["Climb"])
        g0 = b.gt
        while not b.s.get("done") and b.gt - g0 < 5:
            if b.mech["sp"] == SP_ID["Climb"]:
                b.step(-1.2, (CAGE_Z - b.z) * 3, 8 * wrap180(90 - b.yaw))
            else:
                b.step()
        self.climbed = True
        self.log(f"climb end t={b.t:.2f} climb={b.s['bd']['climb']} ground={b.s['robot']['ground']}")

    # ------------------------------------------------------------------ match
    def run(self):
        try:
            self._run()
        except MatchOver:
            pass
        return self.b.s

    def _run(self):
        b = self.b
        # auto: preload on face 4 (front, straight ahead from the start line)
        self.place(4, "L", "L4", back=False)
        while not b.s.get("done"):
            if not self.climbed and 0 < b.t < self.climb_deadline() and b.s["gs"] in (1, 2):
                self.climb()
                continue
            if self.climbed:
                b.step(n=50)
                continue
            if b.has_algae():
                if b.x < 3.0 or b.t > self.climb_deadline() + 6:
                    self.score_net(z=-2.0)
                else:
                    b.step(tap=(PLACE,)); b.idle(0.2)
                continue
            nxt = self.next_slot()
            if nxt is None:
                # reef full: trough is not implemented yet; idle near the cage
                b.goto(1.6, CAGE_Z, 90.0, tol=0.2, timeout=1.0)
                continue
            if nxt[0] == "ALGAE":
                self.algae_task(nxt[1])
            else:
                self.coral_cycle(*nxt)
        return b.s


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 47601
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    tag = sys.argv[3] if len(sys.argv) > 3 else "v1"
    g = ReefGame(port=port)
    g.launch()
    f = Field()
    results = []
    for i in range(n):
        b = Bot(g.client, OUT / f"match-{tag}-{i}.jsonl")
        b.reset()
        mb = MatchBot(b, f)
        t0 = time.time()
        s = mb.run()
        res = dict(i=i, score=s["blue"] - s["red"], blue=s["blue"], red=s["red"], bd=s["bd"], rd=s["rd"],
                   reef=s["reefB"].count("1"), wall=round(time.time() - t0, 1))
        results.append(res)
        print(json.dumps(res), flush=True)
        json.dump(dict(res=res, events=mb.events), open(OUT / f"match-{tag}-{i}.json", "w"))
    g.stop()
    sc = [r["score"] for r in results]
    print("scores", sc, "mean", sum(sc) / len(sc))


if __name__ == "__main__":
    main()
