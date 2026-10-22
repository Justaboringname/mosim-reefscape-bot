"""Full-match REEFSCAPE bot for Madtown 1323 (v1): fixed-priority slot schedule from first principles.

Measured building blocks (runs/reef/calib*): ground pickup ~1.0-1.5 s from a near reef pose, handoff 0.4-0.5 s
(overlaps the drive back), L4 raise 0.9 s; back-side scoring needs no turn after a station pickup. Reef algae block
L3 everywhere and L2 on low-algae faces; L4 and L2-on-high-algae faces are open from the start. Algae near the
barge side go to the net (+4, the barge is ~2.5 m from faces 3-5); algae from the station-side faces are dropped.
"""

from __future__ import annotations

from reefbot import RUNS  # repo-relative paths
import json
import os
import math
import sys
import time
from pathlib import Path

from .bot import Bot, SP_ID, wrap180, MatchOver
from .client import STOW, PLACE, CLIMB, SPECIAL
from .gamectl import ReefGame
from .geom import Field
from .nav import route, path_length
from .skills import Skills

OUT = RUNS
R_LEVEL = {"L4": 1.432, "L3": 1.425, "L2": 1.45, "L1": 1.38}   # L3 back needs the coral at radius >= 0.85 (misses at 0.84); L1 = trough, front
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
        self.net_slots = [-0.70, -3.40, -1.40, -2.75, -0.40, -1.75]   # not -2.10: a miss there lands in the climb lane   # spread along the net: stacked algae spill out
        self.nets = 0
        self.net_fails = 0
        self.planner = None
        self.plan_floor_algae = False
        self.shed_fails = 0
        self.rider_ignore_until = -1.0
        self.last_net_z = None
        self.apply_params()

    leg_a = 0.59   # v41 (CMA-ES mean, N40 179.45); v39 0.35
    leg_v = 3.09   # v41 (CMA-ES mean, N40 179.45); v39 3.3
    climb_margin = 0.769   # v45 (search run 3 mean); v41 0.96
    algae_after_margin = 10.8   # v41 (CMA-ES mean, N40 179.45); v39 10.0
    # tunable parameters (policy search): name -> object that holds it
    PARAM_TARGETS = {"net_final_v": "self", "net_x_min": "self", "net_still": "self", "exit_dist": "self",
                     "exit_angle_l4": "self", "exit_angle_low": "self", "leg_a": "self", "leg_v": "self",
                     "climb_margin": "self", "algae_after_margin": "self", "ff_lead": "self",
                     "direct_kp": "self", "direct_a_brake": "self", "ground_v_touch": "self", "FF_LINEUP": "self",
                     "drive_amax": "bot", "la_gain": "bot", "la_base": "bot", "ff_frac": "bot", "ff_kp": "bot",
                     "turn_frac": "bot", "kd_yaw": "bot"}

    def apply_params(self, params=None):
        """Overrides from PARAMS_JSON (a JSON file of {name: value}) or the given dict."""
        if params is None:
            path = os.environ.get("PARAMS_JSON")
            if not path:
                return
            params = json.loads(Path(path).read_text())
        for k, v in params.items():
            tgt = self.PARAM_TARGETS.get(k)
            if tgt == "self":
                setattr(self, k, float(v))
            elif tgt == "bot":
                setattr(self.b, k, float(v))

    # ------------------------------------------------------------------ state helpers
    def slot_filled(self, face, side, level) -> bool:
        # a just-released coral counts as filled until its deferred check resolves (its bit lags ~0.2 s)
        if any(slot == (face, side, level) for (_, _, slot) in self.pending_checks):
            return True
        if (face, side, level) in self.confirmed:
            return True                       # momentary 0s are scorer phantoms, not knocked-off coral
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

    # Value order (v24): the match is time-bound (v23 always ran out with face 4's L4/L3 empty), so the cheapest
    # slots must be the ones left over. L4 (5) first, then per face its algae (4, to the net) followed by its two
    # L3 (4), then L2 (3). The old bottom-up rule came from scorer phantoms: the branch bit drops to 0 while the
    # robot or a held piece is inside the coral's bounds; no L4 ever physically left its branch (1,218 cases).
    L4_ORDER = (1, 0, 2, 5, 3, 4)
    FACE_ORDER = (1, 0, 2, 5, 3, 4)

    def schedule(self):
        """Ordered list of (face, side, level) or ('ALGAE', face, None)."""
        sl = [(face, side, "L4") for face in self.L4_ORDER for side in ("L", "R")]
        for face in self.FACE_ORDER:
            sl += [("ALGAE", face, None), (face, "L", "L3"), (face, "R", "L3")]
        for face in self.FACE_ORDER:
            sl += [(face, "L", "L2"), (face, "R", "L2")]
        return sl

    def safe(self, face, side, level) -> bool:
        """Reachable now: L4 always; L3 once the face's algae is gone; L2 too on low-algae faces."""
        if level == "L4":
            return True
        if level == "L2":
            return (not self.f.low_algae[face]) or self.algae_gone[face]
        return self.algae_gone[face]

    hybrid = False

    def set_station_mode(self, on: bool):
        """RobotSpecial toggles Madtown's stationCoral (station drops into the robot vs onto the floor)."""
        want = 1 if on else 0
        if self.b.mech.get("station") != want:
            self.b.tap(SPECIAL, confirm=lambda s: s["mech"].get("station") == want)

    greedy = os.environ.get("GREEDY", "1") == "1"   # v39: N10 177.3 vs 172.3 without

    def leg_time(self, a, b) -> float:
        return self.leg_a + path_length(a, route(a, b)) / self.leg_v

    def slot_cost(self, face, side, level, holding):
        """Time to bring a coral to this slot from here: via the better station unless one is held. v37: legs to
        the station on the slot's own side ~1.3 s, cross-side ~2.2 s (about 9 side switches per match)."""
        r = R_LEVEL[level]
        px, pz, _ = self.f.pose(face, side, True, r=r)
        here = (self.b.x, self.b.z)
        if holding:
            return self.leg_time(here, (px, pz))
        best = 9e9
        for which in (0, 1):
            x, z, _ = self.station_pose(which, dist=0.8)
            best = min(best, self.leg_time(here, (x, z)) + self.leg_time((x, z), (px, pz)))
        return best

    def next_slot(self, holding_coral=False):
        item = self._next_slot_ordered(holding_coral)
        if item is None and self.trough_end and holding_coral:
            tr = self.trough_option(True)
            if tr is not None:
                return (tr[0], "C", "L1")
        if item is None and (self.floor_tier or self.floor_after_reef) and not holding_coral:
            # reef done (v46: 11/30 matches reach the 193 cap with ~4-9 s to spare): a loose floor algae is +4
            fa = self.floor_algae_option()
            if self.trough_end:
                # v56/v57: ~5 s idle by the cage before the climb when the next floor trip does not fit; a trough
                # coral (+2, drill 90%) fits a ground cycle. Floor algae first when it fits (4 pts)
                floor_ok = fa is not None and self.b.t - (fa[1] + self.floor_est_pad) >= self.climb_deadline()
                if not floor_ok:
                    tr = self.trough_option(False)
                    if tr is not None and self.b.t - tr[1] >= tr[2]:
                        return (tr[0], "C", "L1")
            return ("FLOOR_ALGAE", fa[0], None) if fa else None
        if not self.greedy or item is None or item[0] == "ALGAE":
            return item
        # same priority tier (same level), cheapest from where the robot is now
        level = item[2]
        cands = [(f2, s2, level) for f2 in range(6) for s2 in ("L", "R")
                 if not self.slot_filled(f2, s2, level) and self.failed.get((f2, s2, level), 0) < 2
                 and self.safe(f2, s2, level)]
        best = min(cands, key=lambda it: self.slot_cost(*it, holding_coral)) if cands else item
        if self.floor_tier and level == "L2" and not holding_coral:
            # E2: loose floor algae (staged-coral algae knocked off in auto, net misses) compete with the L2s by
            # points per second: 4 pts / (to the ball + pickup + to the net + shot) vs 3 pts / L2 cycle
            fa = self.floor_algae_option()
            if fa is not None:
                t_l2 = self.slot_cost(*best, holding_coral) if cands else 9e9
                if 4.0 / fa[1] > 3.0 / t_l2:
                    return ("FLOOR_ALGAE", fa[0], None)
        return best if cands else item

    floor_tier = os.environ.get("FLOOR_TIER", "0") == "1"
    trough_end = os.environ.get("TROUGH_END", "1") == "1"

    def trough_option(self, holding: bool):
        """(face, est. seconds until the trough release, climb deadline from that pose) for the cheapest trough face."""
        b, f = self.b, self.f
        here = (b.x, b.z)
        best = None
        for face in range(6):
            if self.failed.get((face, "C", "L1"), 0) >= 2:
                continue
            x, z, _ = f.pose(face, "C", False, r=R_LEVEL["L1"])
            if holding:
                t = self.leg_time(here, (x, z)) + 0.8
            else:
                opts = self.ground_choices(frm=here, to=(x, z))
                if not opts:
                    continue
                c = opts[0][1]
                t = self.leg_time(here, (c[0], c[1])) + 0.3 + self.leg_time((c[0], c[1]), (x, z)) + 0.8
            dl = 2.4 + path_length((x, z), route((x, z), (1.35, CAGE_Z))) / 3.2 + self.climb_margin
            if best is None or t + dl < best[1] + best[2]:
                best = (face, t, dl)
        return best
    floor_after_reef = os.environ.get("FLOOR_AFTER_REEF", "1") == "1"

    def floor_algae_option(self):
        """Cheapest loose floor algae: ((x, z), est. seconds until it is shot into the net)."""
        here = (self.b.x, self.b.z)
        best = None
        for a in self.b.s.get("algae", []):
            # e2: most loose algae end against a wall (x ~8.3-8.56, |z| ~3.72-3.82); facing the wall the intake
            # still reaches them (robot centre ~1.0 m off the wall at the stop)
            if not self.floor_algae_settled(a) or a[0] < 1.3 or abs(a[1]) > 3.85 or a[0] > 8.6:
                continue
            if math.hypot(a[0] - self.f.reef[0], a[1] - self.f.reef[1]) < 1.3 or self.blacklisted(a[0], a[1]):
                continue
            z = self.pick_net_z()
            # pick 1.2 s (demo 10-19 evening: 1.39 s measured; was 0.9) + arm up / crawl / release 0.8
            t = self.leg_time(here, (a[0], a[1])) + 1.2 + self.leg_time((a[0], a[1]), (0.6, z)) + 0.8
            if best is None or t < best[1]:
                best = ((a[0], a[1]), t)
        return best

    def net_trip_feasible(self, z) -> bool:
        """Is there time to carry the held algae to the net at `z` and still climb from there? (10-19 evening demo:
        a floor algae picked with 9.7 s left was carried toward the far wall and dropped because dispose_algae used
        a flat 'deadline + 8 s' rule, while the net trip + climb from the net needed ~7.5 s.)"""
        b = self.b
        if not self.floor_time_check:
            return b.t > self.climb_deadline() + 8
        t_net = self.leg_time((b.x, b.z), (0.6, z)) + 1.6
        return b.t - t_net >= self.net_climb_reserve(z)

    def net_climb_reserve(self, z) -> float:
        """Match time that must be left when the robot is at the net release point to still climb from there."""
        L = path_length((0.6, z), route((0.6, z), (1.35, CAGE_Z)))
        return 2.4 + L / 3.2 + self.climb_margin + 0.4

    def wall_trip_feasible(self) -> bool:
        """Carry a held algae to the alliance wall and still climb from there (v50/v51 review: the old flat
        'deadline + 3 s, x > 2' gate sent robots at the net to the wall with no time left: v51a-15 lost the climb)."""
        b = self.b
        wall = (7.85, 0.9 if b.z >= 0 else -0.9)
        t_wall = self.leg_time((b.x, b.z), wall) + 0.5
        L = path_length(wall, route(wall, (1.35, CAGE_Z)))
        return b.t - t_wall >= 2.4 + L / 3.2 + self.climb_margin + 0.4

    floor_time_check = os.environ.get("FLOOR_TIME_CHECK", "1") == "1"
    # v57 A/B (N40 each, with the pick cap): pad -1.4 195.18 vs +0.5 194.57, 2-pick matches 16 vs 10, no-climb 0 vs 0
    floor_est_pad = float(os.environ.get("FLOOR_EST_PAD", "-1.4"))

    def _next_slot_ordered(self, holding_coral=False):
        self.refresh_algae()
        for item in self.schedule():
            if item[0] == "ALGAE":
                if self.algae_gone[item[1]] or self.failed.get(("ALGAE", item[1]), 0) >= 2:
                    continue
                if not holding_coral:
                    return item
                continue                      # holding coral: place it on the next safe slot instead
            face, side, level = item
            if self.slot_filled(face, side, level) or self.failed.get(item, 0) >= 2:
                continue
            if self.safe(face, side, level):
                return item
        return None

    def est_time(self, item) -> float:
        if item is None:
            return 0.0
        if item[0] == "FLOOR_ALGAE":
            # v52 analysis (96 floor trips): the estimate (fa[1] + 0.5) averaged 7.0 s vs 6.0 s measured, and after the
            # reef was full 53/66 matches stopped with floor algae still on the field, 5.1 s of slack vs 8.3 s needed.
            # A trip that overruns is now capped at the climb reserve (dispose_algae), so the gate can use the mean
            fa = self.floor_algae_option()
            return (fa[1] + self.floor_est_pad) if fa else 6.0
        if item[0] == "ALGAE":
            return 4.5 if item[1] in FAR else 3.5
        if len(item) > 2 and item[2] == "L1":
            # the climb then starts from the reef face: compare against that pose's deadline, not this one's
            tr = self.trough_option(self.b.has_coral())
            if tr is None:
                return 9.0
            return tr[1] + tr[2] - self.climb_deadline()
        if self.greedy and self.precise_est:
            # leg-time model (same as the greedy choice) + ~0.7 s align/release. v39 ended its last cycle with
            # 0.9-5.2 s (mean 3.2 s) to spare after the climb: the flat 3.6/4.6 s estimate called the climb early
            return self.slot_cost(*item, self.b.has_coral()) + 0.7
        return 3.6 if item[0] in NEAR or item[0] == 4 else 4.6

    precise_est = os.environ.get("PRECISE_EST", "0") == "1"

    # ------------------------------------------------------------------ timing
    def climb_deadline(self) -> float:
        """Match-timer value at which the climb must start (travel + 2.4 s climb + margin)."""
        b = self.b
        L = path_length((b.x, b.z), route((b.x, b.z), (1.35, CAGE_Z)))
        return 2.4 + L / 3.2 + self.climb_margin

    # ------------------------------------------------------------------ macro actions
    def coral_cycle(self, face, side, level) -> bool:
        b, f = self.b, self.f
        r = R_LEVEL[level]
        px, pz, _ = f.pose(face, side, True, r=r)
        # hybrid (user 10-19: use both, the ground intake is often faster): teleop in ground mode, station
        # catch only when no lying coral can be taken safely
        ground_now = self.ground_mode and (not self.hybrid or b.s.get("gs", 0) != 0)
        used_station = False
        opts = None
        if not b.has_coral() and ground_now:
            if self.hybrid:
                self.set_station_mode(False)
            if b.mech.get("sp") not in (SP_ID["Stow"], SP_ID["Intake"]):
                b.step(tap=(STOW,))
            if self.coral_in_footprint() and b.gt > self.rider_ignore_until:
                if self.shed_coral():
                    self.shed_fails = 0
                else:
                    # dv4a-4 (133 pts), hyA-8 (66): a coral wedged inside the frame never comes out and the shed loop
                    # ate 50-100 s. After two failed sheds carry it and keep cycling (a few push-penalty points at
                    # worst) and only retry a shed much later
                    self.shed_fails += 1
                    if self.shed_fails < 2:
                        return False          # never take a new coral with a loose one under the robot
                    self.rider_ignore_until = b.gt + 25.0
                    self.ev("shed: giving up, carrying the rider")
            opts = self.ground_choices(frm=(b.x, b.z), to=(px, pz))
        if not b.has_coral() and ground_now and not (self.hybrid and not opts):
            # ground intake (user's choice): with station mode off every station keeps ~3 coral on its floor
            # (CoralStation ground drop every 1.5 s); pick the lying coral that minimises robot->coral->branch
            if not opts:
                # none lying yet: head for the nearer station's drop area and let it drop one
                zs = 2.3 if pz >= 0 else -2.3
                b.goto(6.6, zs, None, tol=0.3, timeout=0.8)
                b.step()
                return False
            _, c, hdg = opts[0]
            pick = self.ground_pick_ff(c, hdg) if self.ground_ff else self.ground_pick(c, hdg, v_contact=self.ground_v_contact)
            if not pick:
                return False
        elif not b.has_coral():
            used_station = True
            if self.hybrid:
                self.set_station_mode(True)
            if b.mech.get("sp") not in (SP_ID["Stow"], SP_ID["Intake"]):
                b.step(tap=(STOW,))          # fire and forget: the arm comes down while driving
            which = self.pick_station(0 if f.stations[0]["p"][2] * pz >= 0 else 1)
            if not self.intake_station(which):
                # back off a little and let the caller retry (drop cadence is 1 s per robot)
                x, z, yaw = self.station_pose(which, dist=1.2)
                b.goto(x, z, yaw, tol=0.15, timeout=0.6)
                b.step()
                return False
        if not self.ground_mode or used_station:
            self.wait_handoff()               # ground mode: the handoff overlaps the drive (place_align presses L4
                                              # once the coral is in the end effector)
        # far faces (3-5, the barge side): L4 from the FRONT, facing the reef. The station lies beyond the reef, so
        # the robot leaves already facing it instead of turning ~180 deg (v25: each cycle spent ~0.5 s turning).
        # Front L3/L2 at AutoAlign's fixed r=1.430 are untested (coral would sit at ~0.96 m): back for those.
        back = not (face in FAR and (level == "L4" or self.front_far_all))
        if not self.ground_mode and self.exit_hints:
            # most likely next stop: the station on this face's side (next coral), unless this face's algae is next
            self.refresh_algae()
            # (_iteration takes this face's algae right after the placement while it is there)
            if self.algae_gone[face] or self.failed.get(("ALGAE", face), 0) >= 2 or b.t <= self.climb_deadline() + self.algae_after_margin:
                st = 0 if f.stations[0]["p"][2] * pz >= 0 else 1
                self.exit_hint = self.station_pose(st, dist=1.2)[:2]
        # when this face's algae is taken right after the placement (_iteration), the radial exit is wasted: the
        # removal pose is 0.18 m away yet the robot drove 0.83 m (t25: 117 legs/20 matches, ~1.2 s/match)
        self.refresh_algae()
        algae_next = not self.algae_gone[face] and \
            self.failed.get(("ALGAE", face), 0) < 2 and b.t > self.climb_deadline() + self.algae_after_margin
        keep = self.exit_after_place
        if algae_next:
            self.exit_after_place = False
        try:
            ok = self.place_any(face, side, level, back)
        finally:
            self.exit_after_place = keep
        if not ok:
            self.failed[(face, side, level)] = self.failed.get((face, side, level), 0) + 1
        return ok

    def algae_task(self, face) -> bool:
        b = self.b
        if b.has_coral():
            return False
        if b.mech.get("sp") != SP_ID["Stow"]:
            self.stow()
        # always from the FRONT: front-removed algae scored in the net 34/42 (v31), back-removed 1/25
        # only from the FRONT: back-removed algae seat deep in the claw and are thrown away from the net (0/50)
        ok = self.remove_algae(face, back=False, r=1.40)
        if not ok:
            ok = self.remove_algae(face, back=False, r=1.45)
        if not ok:
            self.failed[("ALGAE", face)] = self.failed.get(("ALGAE", face), 0) + 1
            if self.failed[("ALGAE", face)] >= 2:
                for side in ("L", "R"):
                    for lvl in ("L3",) + (("L2",) if self.f.low_algae[face] else ()):
                        self.failed[(face, side, lvl)] = 9
            self.stow()
            return False
        self.dispose_algae(face)
        return True

    def dispose_algae(self, face):
        b = self.b
        # every algae goes to the net when there is time (+4, and a dropped algae rolls into traffic: v2b's
        # dropped algae cost 8 points of push penalties)
        z = self.pick_net_z()
        if self.net_fails and self.last_net_z is not None and abs(z - self.last_net_z) < 0.3:
            # never retry the same pinned approach (e2-7, v43b-1): next candidate instead
            alt = [c for c in self.NET_CANDIDATES if abs(c - self.last_net_z) >= 0.6]
            z = alt[0] if alt else z
        # review (10-20, verified on v50/v51 logs): score_net is not clock-aware and its pinned / back-out branches
        # run up to its 7.5 s timeout, so a late trip accepted by net_trip_feasible ended with 1-2 s left and no
        # climb (v51a-11, v51c-16: -12 each). Cap the attempt at the climb reserve; b.gt and b.t advance together
        cap = 7.5
        if self.floor_time_check:
            cap = min(7.5, b.t - self.net_climb_reserve(z))
        if self.net_trip_feasible(z) and self.net_fails < 2 and cap >= 1.0:
            self.last_net_z = z
            ok = self.score_net(z=z, timeout=cap)
            self.nets += 1
            if ok or not b.has_algae():
                self.net_fails = 0
                return
            # still holding it after a full attempt: one more try, then give up on this algae (v21-4 looped 60 s)
            self.net_fails += 1
            if self.net_fails < 2:
                return
            self.ev("net: giving up, dropping at the wall")
        if self.floor_time_check and not self.wall_trip_feasible():
            # no time for the 6 m trip to the wall before the climb: drop it here and go. Not in the cage lane
            # (x < 1.7 within 0.6 m of CAGE_Z): sidestep in z first
            if b.x < 1.7 and abs(b.z - CAGE_Z) < 0.6:
                b.goto(b.x, CAGE_Z + (0.75 if b.z >= CAGE_Z else -0.75), None, tol=0.15, timeout=0.6)
            self.ev("algae: dropping here before the climb", x=round(b.x, 2), z=round(b.z, 2))
            b.step(tap=(PLACE,))
            b.idle(0.15)
            if b.has_algae():
                b.step(tap=(PLACE,)); b.idle(0.15)
            self.net_fails = 0
            self.stow()
            return
        # station-side faces: carry it to the alliance wall between the staged coral, facing +x, and drop it there
        zs = 0.9 if b.z >= 0 else -0.9
        b.goto(7.85, zs, 90.0, tol=0.15, ytol=8, timeout=2.5)
        b.step(tap=(PLACE,))
        b.idle(0.15)
        if b.has_algae():
            b.step(tap=(PLACE,)); b.idle(0.15)
        self.net_fails = 0
        self.stow()

    # net release points, best historical first (v21-v31: -0.4 9/10, -2.75 and -1.75 ~7/10, -3.4 2/12)
    # v42b eval: z=-0.40 scored 72% vs 93% elsewhere and was re-chosen after its own misses -> last
    NET_CANDIDATES = (-2.45, -1.45, -2.75, -1.75, -0.75, -3.05, -0.40)

    def pick_net_z(self) -> float:
        """Release where no algae already sits in or on the net (user: a second algae dropped where an earlier one
        lies bounces off and flies). Not -2.10: a miss there lands in the climb lane."""
        held_or_net = [a for a in self.b.s.get("algae", []) if not a[3] and a[0] < 1.3 and a[2] > 1.5]
        best, best_clear = None, -1.0
        for z in self.NET_CANDIDATES:
            clear = min((abs(a[1] - z) for a in held_or_net), default=9.0)
            if clear > 0.50:
                return z
            if clear > best_clear:
                best, best_clear = z, clear
        return best

    def climb(self):
        b = self.b
        self.log(f"climb start t={b.t:.2f}")
        # c-runs (10-19): a coral picked up by the ground intake at the last moment sat in the intake; the PLACE tap
        # left the robot in Place, where the Climb button does nothing -> no climb (4 of 30 matches). Only an algae
        # is dropped; a coral is simply carried (Stow hands it to the end effector)
        if b.has_algae():
            b.step(tap=(PLACE,)); b.idle(0.15)
        if b.mech.get("sp") != SP_ID["Stow"]:
            self.stow()
            if b.mech.get("sp") != SP_ID["Stow"]:
                b.tap(STOW, confirm=lambda s: s["mech"]["sp"] == SP_ID["Stow"], tries=6)
        # t25/dv4 climb failures (5/40): (a) hook latched (cage=1) but AutoClimbTriggered never fired -> sat in
        # Climb until t=0; (b) latched, lifted, then dropped (cage 0, ground 1) with no retry; (c) cage lost while
        # still in Climb and the -0.8 m/s reverse carried the robot 3.6 m under the barge (x -2.3) before the retry.
        # Climb button cycles Stow -> Climb -> Climbed -> Stow (ReefscapeRobotBase); a press in Climb forces Climbed.
        for attempt in range(3):
            if attempt == 0:
                b.goto(1.35, CAGE_Z, 90.0, tol=0.08, ytol=4, timeout=4)
            if not b.tap(CLIMB, confirm=lambda s: s["mech"]["sp"] == SP_ID["Climb"]):
                b.tap(STOW, confirm=lambda s: s["mech"]["sp"] == SP_ID["Stow"], tries=6)
                b.tap(CLIMB, confirm=lambda s: s["mech"]["sp"] == SP_ID["Climb"])
            g0 = b.gt
            lifted, cage_since, slip = False, None, None
            while not b.s.get("done") and b.gt - g0 < 5:
                sp, cage, ground = b.mech["sp"], b.mech.get("cage"), b.s["robot"].get("ground")
                if ground == 0:
                    lifted = True
                if sp == SP_ID["Climb"]:
                    if not cage:
                        if cage_since is not None:
                            slip = "lost cage in Climb"
                            break
                        if b.x > 0.30:
                            b.step(-0.8, (CAGE_Z - b.z) * 3, 8 * wrap180(90 - b.yaw))
                        else:
                            slip = "past the cage without a latch"
                            break
                    else:
                        cage_since = cage_since or b.gt
                        if b.gt - cage_since > 0.4:
                            b.tap(CLIMB, confirm=lambda s: s["mech"]["sp"] == SP_ID["Climbed"])
                        else:
                            b.step(0.0, 0.0, 0.0)
                elif sp == SP_ID["Climbed"]:
                    if (lifted and ground == 1 and not cage) or (not lifted and not cage and b.gt - (cage_since or g0) > 0.4):
                        slip = "dropped" if lifted else "Climbed without a cage"
                        break
                    b.step(0.0, 0.0, 0.0)
                else:
                    b.step()
            if slip is None or b.s.get("done") or attempt == 2:
                break
            self.ev("climb: slipped, retry", why=slip, x=round(b.x, 2), t=round(b.t, 2))
            for _ in range(3):                                   # back to Stow through the button cycle
                if b.mech["sp"] == SP_ID["Stow"]:
                    break
                cur = b.mech["sp"]
                b.tap(CLIMB, confirm=lambda s, cur=cur: s["mech"]["sp"] != cur)
            # v46: two slips cost the climb because each retry backed off to x 1.2 (2.5 s); 0.35 m is enough to
            # re-latch (the hook catches at x ~0.47)
            b.goto(min(b.x + 0.35, 1.0), CAGE_Z, 90.0, tol=0.08, ytol=4, timeout=1.5)
        self.climbed = True
        self.log(f"climb end t={b.t:.2f} climb={b.s['bd']['climb']} ground={b.s['robot']['ground']}")

    # ------------------------------------------------------------------ match
    def run(self):
        try:
            self._run()
        except MatchOver:
            pass
        self.resolve_nets(wait=0.0)           # shots still in the air at the buzzer: judge on the final state
        return self.b.s

    def _run(self):
        b = self.b
        # station mode on (RobotSpecial toggles Madtown's stationCoral): coral come from the station into the wrist
        if not self.AUTO_GROUND:
            b.tap(SPECIAL, confirm=lambda s: s["mech"].get("station") == 1)
        self.fast_leave = True
        self.fast_station = True          # one-phase station entry: drill-fast1 130/130 caught, 0 riders, 1.73 s
        self.auto_routine()
        if self.AUTO_GROUND and not self.ground_mode:
            b.wait_until(lambda s: s.get("gs", 0) != 0, timeout=15)
            b.tap(SPECIAL, confirm=lambda s: s["mech"].get("station") == 1)
        while not b.s.get("done"):
            n0 = b.nsteps
            self._iteration()
            if b.nsteps == n0:
                # a macro returned without advancing the match (e.g. already at its waiting point): never spin
                self.ev("spin-guard", sp=b.sp, x=round(b.x, 2), z=round(b.z, 2))
                b.step()

    # staged ("lollipop") coral: (x, z, approach yaw facing it, face/side its L4 goes to, back placement?)
    STAGED = ((7.555, 0.0, 90.0, 1, "R", True), (7.555, 1.826, 30.0, 0, "L", True), (7.555, -1.829, 150.0, 2, "R", True))
    PRELOAD = (1, "L", True)
    AUTO_STATION = ()                     # extra station cycles in auto: (face, side, back)
    AUTO_GROUND = False                   # station mode off in auto: the stations ground-drop coral every 1.5 s
    ground_mode = False                   # whole match: coral from the floor with the ground intake
    ground_v_contact = 2.8
    exit_hints = os.environ.get("EXIT_HINTS", "1") == "1"
    # far faces: L3/L2 from the front too (no ~180 deg turn on the way from the station); untested before v38
    front_far_all = os.environ.get("FRONT_FAR", "0") == "1"

    def configure_auto(self, variant: str):
        """Auto route variants (calib21 compares them by L4 count / timestamps before t=135)."""
        if variant in ("front", "front5"):
            # front L4 after a standing pickup: the arm only swings from front-low to front-high instead of over
            # the top to the back (~1.4 s grab-to-settled for back)
            self.STAGED = ((7.555, 0.0, 90.0, 1, "R", False), (7.555, 1.826, 30.0, 0, "L", False),
                           (7.555, -1.829, 150.0, 2, "R", False))
        if variant == "front5":
            # preload front at face 5: the robot starts facing the reef, so L4 can rise from t=150
            self.PRELOAD = (5, "L", False)
            self.STAGED = ((7.555, 1.826, 30.0, 0, "L", False), (7.555, 0.0, 90.0, 1, "R", False),
                           (7.555, -1.829, 150.0, 2, "R", False))
        self.AUTO_STATION = ((2, "L", True), (0, "R", True))
        if variant == "near":
            # preload on the nearest branch: 5R front, 2.7 m from the start pose, which already faces the reef,
            # so L4 rises from t=150; the standing coral keep the faster back placement
            self.PRELOAD = (5, "R", False)
            self.STAGED = ((7.555, 1.826, 30.0, 0, "L", True), (7.555, 0.0, 90.0, 1, "R", True),
                           (7.555, -1.829, 150.0, 2, "R", True))
            self.AUTO_STATION = ((2, "L", True), (1, "L", True))
        if variant == "near_ground":
            self.configure_auto("near")
            self.AUTO_GROUND = True
            self.ground_mode = True
        if variant == "g2f_hyb":
            # g2_hyb with the preload placed FRONT at 0R: the robot starts facing the reef, so L4 can be pressed at
            # t=150 (the back placement waits ~1.0 s for the facing latch, then 0.86 s of elevator: preload 1.85 s)
            self.configure_auto("g2_hyb")
            self.PRELOAD = (0, "R", False)
        if variant == "g2_hyb4":
            # preload on the true back face (4L front, 3.0 m from the start): face 4 is the most expensive teleop
            # slot (6.3 m from either station, 2.4 s deliver leg vs 1.5-1.8 s for faces 0-2 in v48)
            self.configure_auto("g2_hyb")
            self.PRELOAD = (4, "L", False)
        if variant in ("g2_hyb5", "g2_hyb5far"):
            # user (10-19): the preload goes on the face nearest the start (5R front, 2.7 m, already facing the reef)
            # instead of 0R: in teleop a 5R slot costs ~1.3 m more per leg from station 0 than 0R, so auto should
            # take it. "far": the staged N/S coral also go to far-from-station slots 5L/3R (C stays 1R)
            self.configure_auto("g2_hyb")
            self.PRELOAD = (5, "R", False)
            if variant == "g2_hyb5far":
                self.STAGED = ((7.555, 1.826, 30.0, 5, "L", True), (7.555, 0.0, 90.0, 1, "R", True),
                               (7.555, -1.829, 150.0, 3, "R", True))
        if variant in ("g2", "g2_hyb"):
            # user's auto (10-19): 2nd coral by ground intake (the station's start-of-match floor drop), the three
            # standing staged coral with the end effector, then a 6th by ground intake. Station mode OFF from t=150
            # so both stations keep ground-dropping (CoralStation.TryGroundDrop every 1.5 s up to 3 in the trigger)
            self.configure_auto("near")
            self.PRELOAD = (0, "R", True)
            self.AUTO_GROUND = True
            self.AUTO_GROUND_FIRST = ((0, "L", True),)
            self.STAGED = ((7.555, 1.826, 30.0, 1, "L", True), (7.555, 0.0, 90.0, 1, "R", True),
                           (7.555, -1.829, 150.0, 2, "R", True))
            self.AUTO_STATION = ()
            self.AUTO_GROUND_LAST = ((2, "L", True),)
            if variant == "g2_hyb":
                self.ground_mode = True
                self.hybrid = True
                if os.environ.get("GROUND_FAST", "1") == "1":
                    self.ground_v_touch = float(os.environ.get("GROUND_V_TOUCH", "1.2"))
                    self.FF_LINEUP = float(os.environ.get("FF_LINEUP", "0.75"))
        if variant in ("near0", "near0f", "near0_hyb"):
            # preload on face 0 (on the way to the first staged coral) instead of 5R: v41 placed the 5th auto coral
            # at/after t=135.0 in 29/40 matches (start -> 5R -> staged N took 4.2 s)
            self.configure_auto("near")
            self.PRELOAD = (0, "R", variant != "near0f")
            if variant == "near0_hyb":
                self.ground_mode = True
                self.hybrid = True
                if os.environ.get("GROUND_FAST", "1") == "1":
                    self.ground_v_touch = float(os.environ.get("GROUND_V_TOUCH", "1.2"))
                    self.FF_LINEUP = float(os.environ.get("FF_LINEUP", "0.75"))
        if variant == "near_hyb":
            # auto exactly as "near" (station mode); teleop ground intake with station fallback
            self.configure_auto("near")
            self.ground_mode = True
            self.hybrid = True
            if os.environ.get("GROUND_FAST", "0") == "1":
                self.ground_v_touch = 0.8
                self.FF_LINEUP = 0.9
        if variant in ("near_plan", "ground_plan"):
            self.configure_auto("near" if variant == "near_plan" else "near_ground")
            self.plan_floor_algae = os.environ.get("PLAN_FLOOR_ALGAE", "0") == "1"
            self.use_planner = True
        if variant == "observe_ground":
            self.AUTO_GROUND = True
            self.AUTO_STATION = ()

    def auto_routine(self):
        """Preload, then the three standing staged coral with the end effector (Stack), each to an L4 on the face
        behind it, then station coral while auto lasts, all with the game's AutoAlign."""
        b = self.b
        pf, ps, pb = self.PRELOAD
        self.place_any(pf, ps, "L4", pb)
        for (face, side, back) in self.AUTO_GROUND_FIRST:
            if b.s.get("gs", 0) != 0:
                break
            self._auto_ground(face, side)
        for (lx, lz, yaw, face, side, back) in self.STAGED:
            if b.s.get("gs", 0) != 0:
                break
            standing = any(math.hypot(c[0] - lx, c[1] - lz) < 0.15 and c[2] > 0.12 and not c[3]
                           for c in b.s.get("coral", []))
            if not standing or self.slot_filled(face, side, "L4"):
                continue
            if self.pick_vertical(lx, lz, yaw, tap_l4=back):
                self.place_any(face, side, "L4", back)
            else:
                self.stow()
        for (face, side, back) in self.AUTO_STATION:
            if b.s.get("gs", 0) != 0 or b.t < 138.5 or self.slot_filled(face, side, "L4"):
                break
            self.coral_cycle(face, side, "L4")
        for (face, side, back) in self.AUTO_GROUND_LAST:
            # attempt whenever auto is still running: a cycle that does not finish by t=135 simply carries the
            # coral into teleop (the release waits out the pause), nothing is lost
            if b.s.get("gs", 0) != 0 or self.slot_filled(face, side, "L4"):
                break
            self._auto_ground(face, side)

    AUTO_GROUND_FIRST = ()
    AUTO_GROUND_LAST = ()

    def _auto_ground(self, face, side, tries=2):
        """One ground-intake coral cycle inside auto (ground_mode forced on, no station fallback)."""
        gm, hy = self.ground_mode, self.hybrid
        self.ground_mode, self.hybrid = True, False
        self.allow_second = True
        try:
            for _ in range(tries):
                if self.b.s.get("gs", 0) != 0 or self.slot_filled(face, side, "L4"):
                    break
                if self.coral_cycle(face, side, "L4"):
                    break
        finally:
            self.ground_mode, self.hybrid = gm, hy
            self.allow_second = False

    def _iteration(self):
        b = self.b
        self.resolve_checks(self.failed)
        # the planner also runs in the rest of auto (after the staged coral): the demo's 5th auto coral went to
        # the value-order slot 1L (2.2 m) instead of the nearest L4 and was placed 0.8 s after auto ended
        if self.use_planner and b.s.get("gs", 0) in (0, 1, 2) and not b.has_algae():
            return self._planned_iteration()
        if True:
            nxt = self.next_slot(holding_coral=b.has_coral())
            if not self.climbed and b.s["gs"] in (1, 2) and b.t - self.est_time(nxt) < self.climb_deadline():
                self.climb()
                return
            if self.climbed:
                b.step(n=50)
                return
            if b.has_algae():
                self.dispose_algae(self.f.nearest_face(b.x, b.z))
                return
            if b.has_coral() and getattr(self, "coral_stuck", 0) >= 2:
                # a coral that neither reaches the end effector nor drops (two failed unjams, incl. the Algae-mode
                # drop): no level press, no algae intake can work with it held. Wait by the cage for the climb
                # instead of looping jam -> drop (v51a-12 looped 90 s)
                b.goto(1.6, CAGE_Z, 90.0, tol=0.2, timeout=1.0)
                b.step()
                return
            if nxt is None:
                # reef done (face 4 is L4-only): wait by the cage for the climb. Always step, never spin.
                b.goto(1.6, CAGE_Z, 90.0, tol=0.2, timeout=1.0)
                b.step()
                return
            if nxt[0] == "ALGAE":
                self.algae_task(nxt[1])
            elif nxt[0] == "FLOOR_ALGAE":
                # the net attempt is capped at the climb reserve (dispose_algae); cap the pick too, so an
                # optimistic gate (FLOOR_EST_PAD < 0) can never eat the climb: what is left after the pick must
                # still cover the carry to the net (+0.8 s release) and the reserve there
                pick_to = 4.0
                if self.floor_time_check:
                    z = self.pick_net_z()
                    pick_to = min(4.0, b.t - self.net_climb_reserve(z) - self.leg_time(nxt[1], (0.6, z)) - 0.8)
                if pick_to < 0.6:
                    b.goto(1.6, CAGE_Z, 90.0, tol=0.2, timeout=0.5)
                    b.step()
                    return
                if self.pick_floor_algae(*nxt[1], timeout=pick_to):
                    self.dispose_algae(self.f.nearest_face(b.x, b.z))
                else:
                    self.bad_spots.append((nxt[1][0], nxt[1][1], b.gt))
                    self.bad_spots.append((nxt[1][0], nxt[1][1], b.gt))
                self.ensure_mode(False)
            else:
                ok = self.coral_cycle(*nxt)
                # empty-handed right at this face: take its algae now (it will be needed for the L3s) instead
                # of a dedicated trip back later
                face = nxt[0]
                self.refresh_algae()
                if ok and not b.has_coral() and not self.algae_gone[face] \
                        and self.failed.get(("ALGAE", face), 0) < 2 and b.t > self.climb_deadline() + self.algae_after_margin:
                    self.algae_task(face)


    # ------------------------------------------------------------------ planner-driven teleop
    use_planner = False

    def _planned_iteration(self):
        """One planner step: climb when due, else the action with the best two-step points/second from here."""
        b = self.b
        if self.climbed:
            b.step(n=50)
            return
        t_left = b.t - self.climb_deadline()
        if not self.climbed and b.s["gs"] in (1, 2) and t_left < 1.5:
            self.climb()
            return
        if self.planner is None:
            from .planner import Planner
            self.planner = Planner(self, floor_algae=self.plan_floor_algae)
        act, table = self.planner.choose(t_left)
        if act is None:
            b.goto(1.6, CAGE_Z, 90.0, tol=0.2, timeout=1.0)
            b.step()
            return
        kind = act[0]
        self.ev("plan", act=[kind[0], str(kind[1]) if len(kind) > 1 else None], rate=table[0][0] if table else None)
        if kind[0] == "coral":
            face, side, level = kind[1]
            src = kind[2]
            if src[0] == "ground":
                if b.mech.get("sp") not in (SP_ID["Stow"], SP_ID["Intake"]):
                    b.step(tap=(STOW,))
                if self.coral_in_footprint() and not self.shed_coral():
                    return
                if not (self.ground_pick_ff(src[1], src[2]) if self.ground_ff else self.ground_pick(src[1], src[2])):
                    return
            elif src[0] == "station":
                if b.mech.get("sp") not in (SP_ID["Stow"], SP_ID["Intake"]):
                    b.step(tap=(STOW,))
                if not self.intake_station(src[1]):
                    return
                self.wait_handoff()
            back = not (face in FAR and level == "L4")
            if not self.place_any(face, side, level, back):
                self.failed[(face, side, level)] = self.failed.get((face, side, level), 0) + 1
        elif kind[0] == "algae":
            self.algae_task(kind[1])
        elif kind[0] == "floor_algae":
            self.ensure_mode(False) if b.has_coral() else None
            if self.pick_floor_algae(*kind[1]):
                self.dispose_algae(self.f.nearest_face(b.x, b.z))
            self.ensure_mode(False)
        elif kind[0] == "net":
            self.dispose_algae(self.f.nearest_face(b.x, b.z))


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
        mb.configure_auto(os.environ.get("AUTO_VARIANT", "g2_hyb4"))
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
