"""Low-level driving helpers on top of ReefClient: state tracking, pose control, taps with confirmation.

Frames: Unity world, metres. x = along the field (blue alliance wall at +8.77), z = across. yaw = eulerAngles.y in
degrees, 0 = facing +z, 90 = facing +x. The drive command is a world-frame velocity (vx, vz) in m/s plus the
DriveController omega in deg/s (its sign relative to yaw is calibrated: OMEGA_SIGN).
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

from .client import INTAKE, PLACE, L1, L2, L3, L4, CLIMB, STOW, SPECIAL, ALIGN_L, ALIGN_R, ReefClient
from .nav import route, lookahead_point, path_length, plan

DT_STEP = 0.0045
OMEGA_SIGN = 1.0          # +1: positive omega increases yaw (calibrated in calib1)

SP = {0: "Stow", 1: "Intake", 2: "Place", 3: "L1", 4: "Stack", 5: "L2", 6: "LowAlgae", 7: "L3", 8: "HighAlgae",
      9: "L4", 10: "Processor", 11: "Barge", 12: "RobotSpecial", 13: "Climb", 14: "Climbed"}
SP_ID = {v: k for k, v in SP.items()}


class MatchOver(Exception):
    pass


def wrap180(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


class Bot:
    def __init__(self, client: ReefClient, log_path: Path | None = None, decision_steps: int = 10):
        self.c = client
        self.s: dict = {}
        self.n = decision_steps
        self.log = open(log_path, "w") if log_path else None
        self.hold: set[int] = set()
        self.field: dict | None = None
        self.nsteps = 0
        self.realtime = False
        self._rt0 = None

    # ------------------------------------------------------------------ state
    def reset(self) -> dict:
        self.s = self.c.reset()
        self.hold = set()
        self._log()
        return self.s

    def _log(self):
        if self.log:
            self.log.write(json.dumps(self.s) + "\n")

    @property
    def t(self) -> float:            # match timer, counts down 150 -> 0
        return self.s["t"]

    @property
    def gt(self) -> float:           # game time (Time.time), keeps running through the auto->teleop pause
        return self.s["gameTime"]

    @property
    def x(self) -> float:
        return self.s["robot"]["x"]

    @property
    def z(self) -> float:
        return self.s["robot"]["z"]

    @property
    def yaw(self) -> float:
        return self.s["robot"]["yaw"]

    @property
    def mech(self) -> dict:
        return self.s.get("mech", {})

    @property
    def sp(self) -> str:
        return SP.get(self.mech.get("sp", -1), "?")

    def has_coral(self) -> bool:
        return bool(self.mech.get("coral", {}).get("has"))

    def has_algae(self) -> bool:
        return bool(self.mech.get("algae", {}).get("has"))

    def score(self) -> float:
        return self.s["blue"] - self.s["red"]

    # ------------------------------------------------------------------ actuation
    def step(self, vx=0.0, vz=0.0, w=0.0, tap=(), n=None, free=False) -> dict:
        if self.s.get("done"):
            raise MatchOver()
        # while the robot is disabled (the 3 s auto->teleop pause) hold nothing: a held Intake at a station still
        # makes it drop a coral, which lands on the frozen robot and rides on it for the rest of the match
        hold = tuple(self.hold) if self.s.get("rs", 0) == 0 else ()
        self.s = self.c.act(vx, vz, w, hold=hold, tap=tap if self.s.get("rs", 0) == 0 else (), n=n or self.n, free=free)
        self.s["cmd"] = [round(vx, 3), round(vz, 3), round(w, 1), int(free)]     # the command that led to this state
        self.nsteps += 1
        if self.realtime:
            # demo pacing: keep game time in step with wall time (the lockstep game otherwise runs as fast as it can)
            now = time.monotonic()
            if self._rt0 is None or self.gt < self._rt0[1]:
                self._rt0 = (now, self.gt)
            lag = (self.gt - self._rt0[1]) - (now - self._rt0[0])
            if lag > 0:
                time.sleep(lag)
        self._log()
        return self.s

    def idle(self, seconds: float, free=False):
        k = max(1, round(seconds / (self.n * DT_STEP)))
        for _ in range(k):
            self.step(free=free)

    def tap(self, b: int, confirm=None, tries: int = 3, settle: int = 2) -> bool:
        """Tap button b; `confirm(state)` checks it landed (else retry)."""
        for _ in range(tries):
            self.step(tap=(b,))
            for _ in range(settle):
                if confirm is None or confirm(self.s):
                    return True
                self.step()
            if confirm is None or confirm(self.s):
                return True
        return False

    # ------------------------------------------------------------------ pose control
    def loose_pieces(self, ignore=None):
        """Coral/algae lying on the floor (not held, not scored, not on the reef): push-penalty hazards."""
        out = []
        for c in self.s.get("coral", []):
            if not c[3] and not c[4] and c[2] < 0.25:
                out.append((c[0], c[1]))
        for a in self.s.get("algae", []):
            if not a[3] and a[2] < 0.5:
                out.append((a[0], a[1]))
        if ignore is not None:
            out = [p for p in out if math.hypot(p[0] - ignore[0], p[1] - ignore[1]) > 0.3]
        return out

    LOLLIPOPS = ((7.555, 0.0), (7.555, 1.83), (7.555, -1.83))
    STATIONS = ((8.09, 3.60), (8.10, -3.61))          # blue coral-station drop points (field.json spawn)

    def obstacles(self, ignore=None, goal=None):
        """Circles for the planner: reef (hard), staged coral (hard), floor algae (hard), floor coral (soft)."""
        obs = [(4.2989, 0.0, 1.28, True)]
        # v50a vs v50d (N20 each): the discs cut reef hits 5.5 -> 1.4 per match and lifted the mean 189.2 -> 192.2, but
        # the fixed auto routes got longer: the 5th auto coral slipped past t=135 in 8/20 matches (was 1/20), 15.0 s
        # vs 13.7 s. REEF_CORNERS_AUTO=0 keeps the discs for teleop only (gs 0 = auto)
        if self.reef_corners and (self.reef_corners_auto or self.s.get("gs", 1) != 0):
            # v49c: 112 reef impacts in 20 matches (5.6/match, 1.5 m/s lost each), 110 of them while driving AROUND the
            # reef at 1.47 m from its centre, clustered at the hexagon's corners (angle mod 60 = 30): the 1.28 m disc
            # is the face apothem (0.83) + half the robot width, but a reef corner sits at 0.96 and the robot's own
            # corner sweeps 0.65 from its centre. Six hard discs on the corners keep the centre >= 0.72 from them
            # (0.65 + the ~0.06 m lookahead corner cut); the faces stay huggable at 1.28.
            for k in range(6):
                th = math.radians(30.0 + 60.0 * k)
                obs.append((4.2989 + 0.96 * math.cos(th), 0.96 * math.sin(th), self.reef_corner_r, True))
        for (lx, lz) in self.LOLLIPOPS:
            if any(math.hypot(c[0] - lx, c[1] - lz) < 0.15 and c[2] > 0.12 for c in self.s.get("coral", []) if not c[3]):
                obs.append((lx, lz, 0.78, True))
        for a in self.s.get("algae", []):
            # every loose algae (not only y < 0.5: a staged-coral algae knocked off in auto bounces at y 0.4-0.9 and
            # the robot pushed it into the next standing coral, a-ff2-0), except the ones still on the reef
            if not a[3] and not a[4] and a[2] < 1.2 and a[0] > -0.6 and \
                    math.hypot(a[0] - 4.2989, a[1]) > 1.05:
                obs.append((a[0], a[1], 0.72, True))
        for c in self.s.get("coral", []):
            if not c[3] and not c[4] and c[2] < 0.12 and c[0] > -0.6:
                # every loose floor coral is hard: driven over, it ends up under the chassis and every later move
                # while holding a coral is a push penalty (ground drills: 27 of 68 stuck coral came from travel)
                obs.append((c[0], c[1], 0.6, True))
        if ignore is not None:
            obs = [o for o in obs if math.hypot(o[0] - ignore[0], o[1] - ignore[1]) > 0.3]
        if goal is not None:
            # a goal inside a hard circle (other than the reef) would make the plan impossible: soften it
            obs = [(cx, cz, r, hard and (r > 1.0 or math.hypot(cx - goal[0], cz - goal[1]) > r)) for (cx, cz, r, hard) in obs]
        return obs

    ff_floor = 0.3      # minimum time (s) the heading feedforward spreads a turn over (drive_cmd, L > 0.8)
    reef_corners = __import__('os').environ.get('REEF_CORNERS', '1') == '1'
    reef_corner_r = float(__import__('os').environ.get('REEF_CORNER_R', '0.72'))
    reef_corners_auto = __import__('os').environ.get('REEF_CORNERS_AUTO', '0') == '1'   # v51: teleop-only ties always-on (192.8 vs 193.2 excl. a jam match) with better auto timing (5th coral past t=135 4/40 vs 9/40)
    drive_amax = 9.777   # v45 (search run 3 mean, N20 189.5); v41 10.94
    la_gain = 0.151   # v45 (search run 3 mean, N20 189.5); v41 0.18
    la_base = 0.335   # v45 (search run 3 mean, N20 189.5); v41 0.2
    ff_frac = 0.717   # v41 (CMA-ES mean, N40 179.45); v39 0.8
    ff_kp = 0.3   # v45 (search run 3 mean, N20 189.5); v41 1.277
    turn_frac = float(__import__('os').environ.get('TURN_FRAC', '0.396'))  # t25: yaw error at 0.6 m 19 -> 12 deg, score equal; was ff_frac 0.717
    kd_yaw = float(__import__('os').environ.get('KD_YAW', '0.658'))        # yaw-rate feedback (deg/s per deg/s)

    def drive_cmd(self, tx, tz, tyaw=None, vmax=4.4, amax=None, kp=5.0, kyaw=8.0, wmax=600.0, avoid=True,
                  ignore=None, dodge=True):
        """World velocity + omega toward (tx, tz) along an A* path around the reef, staged coral and floor pieces;
        speed limited by the remaining path length."""
        amax = self.drive_amax if amax is None else amax
        d = math.hypot(tx - self.x, tz - self.z)
        if avoid and d > 0.35:
            key = (round(tx, 2), round(tz, 2))
            if getattr(self, "_plan_key", None) != key or self.nsteps - getattr(self, "_plan_step", -99) >= 4:
                self._plan = plan((self.x, self.z), (tx, tz), self.obstacles(ignore=ignore, goal=(tx, tz)))
                self._plan_key, self._plan_step = key, self.nsteps
            path = self._plan
        else:
            path = [(tx, tz)]
        if len(path) > 1:
            # drop waypoints already passed: the plan is up to 4 steps old, and at 4 m/s a waypoint left 0.7 m behind
            # inflates the remaining length by twice that and delays braking (v19 hit the station wall at 2.4 m/s)
            while len(path) > 1 and math.hypot(path[1][0] - self.x, path[1][1] - self.z) <= \
                    math.hypot(path[1][0] - path[0][0], path[1][1] - path[0][1]):
                path = path[1:]
            if avoid and d > 0.35:
                self._plan = path
        if len(path) > 1:
            L = path_length((self.x, self.z), path)
            v_now = math.hypot(self.s["robot"]["vx"], self.s["robot"]["vz"])
            ax, az = lookahead_point((self.x, self.z), path, la=max(0.5, self.la_gain * v_now + self.la_base))
        else:
            L, ax, az = d, tx, tz
        dx, dz = ax - self.x, az - self.z
        dd = math.hypot(dx, dz)
        v = min(vmax, math.sqrt(2 * amax * max(L - 0.01, 0.0)), kp * L)
        ux, uz = (dx / dd, dz / dd) if dd > 1e-6 else (0.0, 0.0)
        vx, vz = ux * v, uz * v
        w = 0.0
        if tyaw is not None:
            e = wrap180(tyaw - self.yaw)
            # measured yaw-rate response (dv3a): wy follows the command with ~0.15 s lag at ~0.55 gain, so a pure
            # P law overshoots (yaw error 5 deg at 0.6 m grew to 9 deg at 0.3 m) and the heading was still
            # 8-28 deg off when the final approach started: rate feedback (kd) and an earlier turn (user 10-19:
            # arrive with the heading already right)
            wy = OMEGA_SIGN * self.s["robot"].get("wy", 0.0)
            if L > 0.8:
                # feedforward heading: spread the turn over the first `turn_frac` of the time to arrival instead of
                # spinning at full rate (a swerve spinning at 350 deg/s tops out at ~2.4 m/s translation, calib22)
                t_arr = L / max(0.75 * vmax, 0.5) + 0.25
                w_ff = e / max(self.turn_frac * t_arr, self.ff_floor)
                w = OMEGA_SIGN * max(-wmax, min(wmax, w_ff + self.ff_kp * e - self.kd_yaw * wy))
            else:
                w = OMEGA_SIGN * max(-wmax, min(wmax, kyaw * e - self.kd_yaw * wy))
        return vx, vz, w, d

    def goto(self, tx, tz, tyaw=None, tol=0.03, ytol=2.0, timeout=6.0, vmax=4.4, tap=(), **kw) -> bool:
        t0 = self.gt
        first = True
        while True:
            vx, vz, w, d = self.drive_cmd(tx, tz, tyaw, vmax=vmax, **kw)
            yerr = abs(wrap180(tyaw - self.yaw)) if tyaw is not None else 0.0
            if d < tol and yerr < ytol:
                return True
            if self.gt - t0 > timeout or self.s.get("done"):
                return False
            self.step(vx, vz, w, tap=tap if first else ())
            first = False

    def wait_until(self, pred, timeout=3.0, free=False) -> bool:
        t0 = self.gt
        while not pred(self.s):
            if self.gt - t0 > timeout or self.s.get("done"):
                return False
            self.step(free=free)
        return True
