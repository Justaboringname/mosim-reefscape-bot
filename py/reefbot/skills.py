"""Mid-level skills for Madtown: ground coral intake, branch placement (front/back), timing instrumentation."""

from __future__ import annotations

from reefbot import FIELD_JSON  # repo-relative paths
import math

from .bot import Bot, SP_ID, wrap180, OMEGA_SIGN as OMEGA_SIGN_SK
from .client import INTAKE, PLACE, L1, L2, L3, L4, STOW, ALIGN_L, ALIGN_R, MODE
from .geom import Field, POSE_R as POSE_R_DEFAULT

LEVEL_BTN = {"L4": L4, "L3": L3, "L2": L2}
LEVEL_SP = {"L4": SP_ID["L4"], "L3": SP_ID["L3"], "L2": SP_ID["L2"]}
R_FALLBACK = {"L4": 1.432, "L3": 1.425, "L2": 1.45}     # own-controller standoffs (L3 back needs >= 1.425)
# The Barge setpoint holds reef algae ~0.1 m BEHIND the robot centre (x 0.66 when facing the net at yaw 270, i.e.
# outside the net box x <= 0.5: v28 released 6/12 onto the net rim). Backing in at yaw 90 puts it at x ~0.47.
NET_YAW = 270.0


class Skills:
    def __init__(self, bot: Bot, field: Field):
        self.b, self.f = bot, field
        self.events: list[tuple] = []
        self.bad_spots: list[tuple] = []        # (x, z, game_time) of failed pickups
        self._lineup: dict = {}                # ff line-up run per (coral x, z, heading), set by ground_choices
        self.fast_leave = False
        self.fast_station = False            # one-phase station entry (drill-validated before use in matches)
        self.exit_after_place = True         # straight radial exit after a placement (no dragging the arm)
        self.exit_hint = None                # where the robot goes after the next placement (exit direction)
        import os as _os
        self.align_loose = _os.environ.get("ALIGN_LOOSE", "0") == "1"   # E1: looser AutoAlign release
        self.exit_wide = _os.environ.get("EXIT_WIDE", "0") == "1"       # E3: 75 deg exit after L2/L3
        self.ground_strict = False           # skip wall-side / clustered floor coral (dclean: too strict, wandered)
        self.ground_angle = 30.0             # calib23: 30 deg 37/40 acquired, 0 under the bumper
        self.ground_ff = True                # user's feedforward pickup: arrive at touch + 5 cm with ~0 speed
        self.ff_lead = 0.07                  # command latency + drive lag compensation (s); v45 search mean (was 0.09)
        self.pending_checks: list[tuple] = []
        self.pending_nets: list[tuple] = []        # (algae index, release game time, z) judged by resolve_nets
        self.confirmed: set = set()          # slots whose coral was seen scored: a later 0 bit is a phantom

    def blacklisted(self, x, z) -> bool:
        now = self.b.gt
        n = sum(1 for (bx, bz, t) in self.bad_spots if now - t < 15.0 and math.hypot(bx - x, bz - z) < 0.3)
        return n >= 2

    def ev(self, tag: str, **kw):
        b = self.b
        self.events.append((round(b.gt, 3), round(b.t, 2), tag, round(b.x, 3), round(b.z, 3), round(b.yaw, 1), b.sp, kw))

    # ------------------------------------------------------------------ helpers
    def facing_dot(self) -> float:
        b = self.b
        fx, fz = math.sin(math.radians(b.yaw)), math.cos(math.radians(b.yaw))
        rx, rz = self.f.reef[0] - b.x, self.f.reef[1] - b.z
        d = math.hypot(rx, rz)
        return (fx * rx + fz * rz) / max(d, 1e-6)

    def stow(self):
        b = self.b
        if b.mech.get("sp") != SP_ID["Stow"]:
            b.tap(STOW, confirm=lambda s: s["mech"]["sp"] == SP_ID["Stow"])

    # ------------------------------------------------------------------ coral intake
    def loose_coral(self, near=None, max_y=0.25):
        b = self.b
        px, pz = near if near else (b.x, b.z)
        cs = [c for c in b.s["coral"] if not c[3] and not c[4] and c[2] < max_y and c[0] > 2.0]
        return min(cs, key=lambda c: math.hypot(c[0] - px, c[1] - pz)) if cs else None

    def approach_ok(self, c, hdg_deg, others):
        """A heading is usable if no other coral would enter the intake box together with c (side by side within the
        box width, or just beyond it), and the robot centre can reach 0.55 m behind... through c without the bumper
        hitting a wall before the pickup."""
        ux, uz = math.sin(math.radians(hdg_deg)), math.cos(math.radians(hdg_deg))
        for o in others:
            dx, dz = o[0] - c[0], o[1] - c[1]
            along = dx * ux + dz * uz
            lat = -dx * uz + dz * ux
            # only a neighbour at about the same depth (side by side in the intake box) jams it; one further along
            # the heading enters after the target is already pulled in (~0.05 s), one before it is simply taken first
            if -0.12 < along < 0.22 and abs(lat) < 0.45:
                return False
        # robot centre at pickup: 0.52 m behind the coral along the heading; bumper must stay inside the field
        rx, rz = c[0] - 0.52 * ux, c[1] - 0.52 * uz
        if abs(rz) > 4.02 - 0.47 or rx > 8.77 - 0.47:
            return False
        # stay clear of the reef (radius ~1.0 m plus bumper)
        if math.hypot(rx - self.f.reef[0], rz - self.f.reef[1]) < 1.45:
            return False
        # and the bumper must not hit a wall before the intake reaches the coral
        if abs(c[1] + 0.05 * uz) > 4.02 - 0.12 or c[0] + 0.05 * ux > 8.77 - 0.12:
            return False
        return True

    def coral_options(self, frm=None, to=None):
        """(cost, coral, heading) for every lying coral and a usable approach heading."""
        b = self.b
        fx, fz = frm if frm else (b.x, b.z)
        lying = [c for c in b.s["coral"] if not c[3] and not c[4] and c[2] < 0.1 and c[0] > 2.5
                 and math.hypot(c[0] - self.f.reef[0], c[1] - self.f.reef[1]) > 1.5
                 and not self.blacklisted(c[0], c[1])]
        allc = [c for c in b.s["coral"] if not c[3]]
        opts = []
        for c in lying:
            others = [o for o in allc if o is not c]
            direct = math.degrees(math.atan2(c[0] - fx, c[1] - fz))
            axis = math.degrees(math.atan2(c[5], c[6])) if len(c) > 6 else None
            best = None
            for off in (0, 25, -25, 50, -50, 80, -80):
                h = direct + off
                # a coral lying across the heading (~90 deg) and centred jams the intake (11 of 11 slow failures were
                # across); prefer headings within 55 deg of its long axis, never beyond 75
                rel_pen = 0.0
                if axis is not None:
                    rel = abs((axis - h) % 180.0)
                    rel = min(rel, 180.0 - rel)
                    if rel > 75:
                        continue
                    rel_pen = 0.4 if rel > 55 else 0.0
                if self.approach_ok(c, h, others):
                    d1 = math.hypot(c[0] - fx, c[1] - fz)
                    d2 = math.hypot(c[0] - to[0], c[1] - to[1]) if to else 0.0
                    cost = d1 + d2 + 0.006 * abs(off) + rel_pen
                    if best is None or cost < best[0]:
                        best = (cost, c, h)
            if best:
                opts.append(best)
        opts.sort(key=lambda o: o[0])
        return opts

    def best_coral(self, frm=None, to=None):
        o = self.coral_options(frm, to)
        return (o[0][1], o[0][2]) if o else None

    def coral_ready(self) -> bool:
        """Coral in the end effector and settled: EE-stow (3), or ground-EE (4, the Stack path) at target."""
        m = self.b.mech.get("coral", {})
        return bool(m.get("has")) and (m.get("st") == 3 or (m.get("st") == 4 and m.get("at") == 1))

    def intake_ground(self, speed=4.4, v_contact=2.4, a_dec=8.0, timeout=4.0, target=None, retries=1, method="intake") -> bool:
        """Ground pickup with the front intake. The intake needs ~0.4 s to swing down (box 0.44-0.59 m ahead, 9 cm
        up) and the pull only locks if the coral stays within its planar box; above ~2 m/s at contact the coral slides
        under the chassis and every grab breaks. So: full speed in, slow to `v_contact` for the last ~0.3 m, and if
        the coral ends up under the bumper, back off and retry instead of pushing it (push penalties)."""
        b = self.b
        g0 = b.gt
        tgt = target or self.best_coral()
        if tgt is None:
            self.ev("intake: no coral")
            return False
        c, hdg = tgt if isinstance(tgt, tuple) else (tgt, None)
        cx, cz = c[0], c[1]
        tries = 0
        contact_v = None
        under_since = None
        while b.gt - g0 < timeout and not b.has_coral():
            c2 = self.loose_coral(near=(cx, cz))
            if c2 and math.hypot(c2[0] - cx, c2[1] - cz) < 0.35:
                cx, cz = c2[0], c2[1]
            ax, az = cx - b.x, cz - b.z
            d = max(math.hypot(ax, az), 1e-3)
            yaw = math.radians(b.yaw)
            fwd = ax * math.sin(yaw) + az * math.cos(yaw)
            lat = ax * math.cos(yaw) - az * math.sin(yaw)
            tyaw = math.degrees(math.atan2(ax, az))
            if hdg is not None and d > 0.75:
                # line up on the chosen approach line first: aim at a point 0.75 m before the coral on that line
                ux, uz = math.sin(math.radians(hdg)), math.cos(math.radians(hdg))
                lx, lz = cx - ux * 0.75, cz - uz * 0.75
                if math.hypot(lx - b.x, lz - b.z) > 0.25:
                    tyaw = hdg
            tap = ()
            if method == "stack" and b.mech.get("sp") != SP_ID["Stack"] and not b.has_coral():
                # Stack = end-effector ground pickup (no handoff); L1 from Stow while empty
                if b.mech.get("sp") == SP_ID["Stow"] and INTAKE not in b.hold:
                    tap = (L1,)
                elif b.mech.get("sp") not in (SP_ID["Stow"], SP_ID["Stack"]) and INTAKE not in b.hold:
                    tap = (STOW,)
            if d < max(1.1, 0.45 * math.hypot(b.s["robot"]["vx"], b.s["robot"]["vz"]) + 0.7):
                if method != "stack" or b.mech.get("sp") == SP_ID["Stack"]:
                    b.hold = {INTAKE}
            # coral under the bumper line and not grabbed: back off and retry
            if fwd < 0.47 and abs(lat) < 0.35:
                under_since = under_since or b.gt
                if b.gt - under_since > 0.12:
                    tries += 1
                    self.bad_spots.append((cx, cz, b.gt))
                    self.ev("intake: under bumper", fwd=round(fwd, 3), tries=tries)
                    b.hold = set()
                    bx, bz = b.x - math.sin(yaw) * 0.45, b.z - math.cos(yaw) * 0.45
                    b.goto(bx, bz, b.yaw, tol=0.06, timeout=0.8)
                    under_since = None
                    if tries > retries:
                        break
                    continue
            else:
                under_since = None
            v = min(speed, math.sqrt(v_contact ** 2 + 2 * a_dec * max(0.0, d - 0.62)))
            if contact_v is None and d < 0.66:
                contact_v = math.hypot(b.s["robot"]["vx"], b.s["robot"]["vz"])
            # aim through the coral; the aim point is clamped inside the field walls
            if hdg is not None and d > 0.75:
                ux, uz = math.sin(math.radians(hdg)), math.cos(math.radians(hdg))
                lx, lz = cx - ux * 0.75, cz - uz * 0.75
                if math.hypot(lx - b.x, lz - b.z) > 0.25:
                    px, pz = lx, lz
                else:
                    px, pz = cx + ux * 0.6, cz + uz * 0.6
            else:
                px, pz = cx + ax / d * 0.6, cz + az / d * 0.6
            px = min(8.3, max(-8.3, px))
            pz = min(3.55, max(-3.55, pz))
            if hdg is not None and math.hypot(px - b.x, pz - b.z) > 0.3 and d > 0.75:
                v = speed      # still lining up: no contact slow-down yet
            vx, vz, w, _ = b.drive_cmd(px, pz, tyaw if d > 0.4 else None, vmax=v, ignore=(cx, cz))
            b.step(vx, vz, w, tap=tap)
        got = b.has_coral()
        if not got:
            self.bad_spots.append((cx, cz, b.gt))
        self.ev("intake: acquired" if got else "intake: miss", dt=round(b.gt - g0, 3), method=method,
                contact_v=round(contact_v, 2) if contact_v is not None else None)
        b.hold = set()
        return got

    _ST_WALLS = None

    def pose_in_field(self, x, z, yaw_deg, half=0.46, margin=0.04) -> bool:
        """All four bumper corners of the robot at (x, z, yaw) inside the field: outer walls (|x| < 8.77,
        |z| < 4.02) and the four diagonal coral-station walls (line through the station's p, normal = its drop
        direction; the catch pose touches it at 0.75 m from the drop point). ff1 drill: 5 of 15 misses were
        line-up points behind a wall, the robot pinned against it for the whole timeout."""
        if Skills._ST_WALLS is None:
            import json as _json
            from pathlib import Path as _P
            F = _json.loads((FIELD_JSON).read_text())
            walls = []
            for st in F["stations"]:
                fx, fz = st["spawnFwd"][0], st["spawnFwd"][2]
                hn = math.hypot(fx, fz)
                walls.append((st["p"][0], st["p"][2], fx / hn, fz / hn))
            Skills._ST_WALLS = walls
        y = math.radians(yaw_deg)
        ux, uz = math.sin(y), math.cos(y)
        for a in (-half, half):
            for c in (-half, half):
                cx, cz = x + a * ux + c * uz, z + a * uz - c * ux
                if abs(cx) > 8.77 - margin or abs(cz) > 4.02 - margin:
                    return False
                for px, pz, nx, nz in Skills._ST_WALLS:
                    if (cx - px) * nx + (cz - pz) * nz < margin:
                        return False
        return True

    def ground_choices(self, frm, to):
        """(cost, coral, heading) for lying coral, approached ALONG their long axis (either direction): coral
        that entered across the intake slid under the bumper (g1: every under-bumper event had the coral at 55-90 deg
        to the heading). The line-up point 0.85 m before the coral must be inside the field and off the reef."""
        b = self.b
        fx, fz = frm
        allc = [c for c in b.s["coral"] if not c[3]]
        # drill d45: of 21 failed pickups, 11 were coral within ~0.35 m of a wall/corner and 11 had another coral
        # within 0.45 m; both were rare among the 78 successes
        lying = [c for c in b.s["coral"] if not c[3] and not c[4] and c[2] < 0.1 and c[0] > 2.5
                 and math.hypot(c[0] - self.f.reef[0], c[1] - self.f.reef[1]) > 1.5
                 and (not self.ground_strict or (c[0] < 8.42 and abs(c[1]) < 3.67
                      and not any(o is not c and math.hypot(o[0] - c[0], o[1] - c[1]) < 0.40 and o[2] < 0.3 for o in allc)))
                 and not self.blacklisted(c[0], c[1])]
        out = []
        for c in lying:
            axis = math.degrees(math.atan2(c[5], c[6]))
            others = [o for o in allc if o is not c]
            best = None
            # g1 data: contact angle (coral axis vs heading) 30-60 deg -> 32/34 acquired; 0-15 -> 3/6; 75-90 -> 11/29
            hs = ((axis + self.ground_angle, axis - self.ground_angle, axis + 180.0 - self.ground_angle,
                   axis - 180.0 + self.ground_angle) if not self.ground_ff else
                  tuple(axis + d for d in range(0, 360, 15)))
            for h in hs:
                ux, uz = math.sin(math.radians(h)), math.cos(math.radians(h))
                rel = abs(wrap180(h - axis)) % 180.0
                rel = min(rel, 180.0 - rel)
                if self.ground_ff:
                    # ff1 drill: every acquisition had the coral at 56-83 deg to the heading; 0-45 deg never took
                    if rel < 45:
                        continue
                    # longest line-up run that keeps the whole robot inside the field (ff2: a fixed 1.35 m left
                    # nothing to pick near the station corner)
                    L = next((L_ for L_ in (self.FF_LINEUP, 1.1, 0.9, 0.75)
                              if all(self.pose_in_field(c[0] - ux * s_, c[1] - uz * s_, h, margin=0.03)
                                     for s_ in (0.62, min(0.75, L_), L_))), None)
                    if L is None:
                        continue
                    lx, lz = c[0] - ux * L, c[1] - uz * L
                    if lx < -0.2 or math.hypot(lx - self.f.reef[0], lz - self.f.reef[1]) < 1.45:
                        continue
                else:
                    lx, lz = c[0] - ux * 0.85, c[1] - uz * 0.85
                    if abs(lz) > 3.45 or lx > 8.25 or lx < -0.2 or math.hypot(lx - self.f.reef[0], lz - self.f.reef[1]) < 1.45:
                        continue
                # ff stops at touch + 5 cm, so only what overlaps the robot at the stop or on the way in matters
                # (checked below); approach_ok assumes the fast pickup that overruns the coral (demo: piles of 3
                # coral 0.15-0.37 m apart at both stations were all rejected -> 62 s without a pickable coral)
                if not self.ground_ff and not self.approach_ok(c, h, others):
                    continue
                # the swept corridor of the straight-in phase must hold no other coral: the robot takes the target
                # and presses the neighbours under the chassis (41 of 68 stuck coral entered while intaking)
                blocked = False
                for o in others:
                    if o[3] or o[2] > 0.3:
                        continue
                    dx, dz = o[0] - c[0], o[1] - c[1]
                    al = dx * ux + dz * uz
                    la = -dx * uz + dz * ux
                    if self.ground_ff:
                        # the bumper box sweeps from the line-up point to the stop (front 0.46 m ahead of the robot
                        # centre, which stops ~0.6 m short of the target): a neighbour overlapping that strip is
                        # pushed along / pressed under the frame. Its extents follow its own orientation.
                        rn = abs(wrap180(math.degrees(math.atan2(o[5], o[6])) - h)) % 180.0
                        rn = min(rn, 180.0 - rn)
                        ah = 0.15 * abs(math.cos(math.radians(rn))) + 0.057 * abs(math.sin(math.radians(rn)))
                        lh = 0.15 * abs(math.sin(math.radians(rn))) + 0.057 * abs(math.cos(math.radians(rn)))
                        if al + ah > -(L + 0.46) and al - ah < -0.15 and abs(la) - lh < 0.49:
                            blocked = True
                            break
                        # intake box at the stop (its front reaches ~5 cm past the target's near edge, +-0.23 m wide):
                        # a neighbour in it would be taken together with the target (double intake -> jam)
                        if -0.15 <= al - ah < 0.04 and abs(la) - lh < 0.28:
                            blocked = True
                            break
                    elif -1.0 < al < 0.25 and abs(la) < 0.35:
                        blocked = True
                        break
                if blocked:
                    continue
                turn = abs(wrap180(h - b.yaw)) / 180.0
                if self.ground_ff:
                    ang_pen = 0.0 if rel >= 60 else 0.4
                else:
                    # calib23 (fast two-phase pickup): 30 deg 37/40, 0-15 and 75-90 worse
                    ang_pen = 0.0 if 20 <= rel <= 60 else (0.3 if rel < 75 else 0.8)
                # approach from outside a pile: prefer lines whose start is far from the other floor coral
                crowd = sum(1 for o in others if not o[3] and o[2] < 0.3 and math.hypot(o[0] - lx, o[1] - lz) < 0.8)
                # dog-leg: a line-up point reached sideways (bearing robot->line-up vs the approach heading) forces
                # an L-shaped corner the robot cannot take at speed (eval 10-19: +0.14-0.23 s on 40% of picks)
                bl = math.hypot(lx - fx, lz - fz)
                dog = 0.0
                if bl > 0.3:
                    dog = self.dogleg_w * (1.0 - ((lx - fx) * ux + (lz - fz) * uz) / bl)
                cost = (bl + (math.hypot(c[0] - to[0], c[1] - to[1]) if to else 0.0)
                        + 0.6 * turn + 0.8 * crowd + ang_pen + dog)
                if best is None or cost < best[0]:
                    best = (cost, c, h % 360.0)
                    if self.ground_ff:
                        self._lineup[(round(c[0], 2), round(c[1], 2), round(h % 360.0))] = L
            if best:
                out.append(best)
        out.sort(key=lambda o: o[0])
        return out

    def coral_in_footprint(self):
        """Loose coral inside the robot's footprint box: the game counts every coral in that box as 'controlled' and
        penalises pushing one while holding another (ReefscapeRobotBase.checkForPushPenalty). (fwd, lat) list."""
        b = self.b
        yaw = math.radians(b.yaw)
        fx, fz = math.sin(yaw), math.cos(yaw)
        out = []
        for c in b.s["coral"]:
            if c[3] or c[4] or c[2] > 0.3:
                continue
            dx, dz = c[0] - b.x, c[1] - b.z
            fw, la = dx * fx + dz * fz, dx * fz - dz * fx
            if abs(fw) < 0.50 and abs(la) < 0.50:
                out.append((fw, la))
        return out

    def shed_coral(self, max_tries=2) -> bool:
        if self.b.has_coral():
            return True
        """Get a stuck coral out of the footprint. Under the chassis (fwd < 0.3): drive forward ~0.9 m and let the
        robot pass over it (drills: those episodes ended with the coral leaving behind the robot while it drove
        forward); at the intake lip (fwd >= 0.3): back away 0.5 m. Intake up while doing it."""
        b = self.b
        for _ in range(max_tries):
            inside = self.coral_in_footprint()
            if not inside:
                return True
            fw = min(inside, key=lambda t: abs(t[0]))[0]
            yaw = math.radians(b.yaw)
            ux, uz = math.sin(yaw), math.cos(yaw)
            sgn = 1.0 if fw < 0.3 else -1.0
            dist = 0.9 if sgn > 0 else 0.5
            x0, z0 = b.x, b.z
            g0 = b.gt
            b.hold = set()
            # stay inside the field: if forward runs into a wall, go the other way
            tx, tz = b.x + sgn * ux * dist, b.z + sgn * uz * dist
            if abs(tz) > 3.6 or tx > 8.35 or tx < -0.3:
                sgn = -sgn
            self.ev("shed: coral in footprint", fwd=round(fw, 2), dir="forward" if sgn > 0 else "back")
            while b.gt - g0 < 0.8 and math.hypot(b.x - x0, b.z - z0) < dist:
                b.step(sgn * ux * 3.0, sgn * uz * 3.0, 0.0)
            b.step()
        ok = not self.coral_in_footprint()
        self.ev("shed: " + ("clear" if ok else "still stuck"))
        return ok

    @staticmethod
    def track_piece(lst, idx, last_xz, max_jump=0.35):
        """Index of the same piece in a fresh list: by the bridge's stable id (field 8, bridges from 10-19 on) when
        the entry at idx carries one, else the nearest unheld piece to the last known position within max_jump
        (the game's object list is not index-stable: a station drop inserts mid-list). None if lost."""
        if idx is None or not lst:
            return None
        want_id = None
        if idx < len(lst) and len(lst[idx]) > 8:
            want_id = lst[idx][8]
        if want_id is not None:
            # the id at idx may already belong to another piece after an insertion: search by id
            for i, c in enumerate(lst):
                if len(c) > 8 and c[8] == want_id and not c[3]:
                    return i if last_xz is None or math.hypot(c[0] - last_xz[0], c[1] - last_xz[1]) < 1.5 else None
        best, bd = None, max_jump
        for i, c in enumerate(lst):
            if c[3] or c[4]:
                continue
            dd = math.hypot(c[0] - last_xz[0], c[1] - last_xz[1]) if last_xz else 9.0
            if dd < bd:
                best, bd = i, dd
        return best

    def second_at_intake(self, idx) -> bool:
        """A lying coral other than the target (index idx) that would enter the intake together with it: at the
        mouth (0.25-0.80 m ahead, |lat| < 0.40) and NOT behind the target (hyB: 38 of 76 aborts were pile coral
        lying beyond the target, which the touch+push stop never reaches)."""
        b = self.b
        yaw = math.radians(b.yaw)
        fx, fz = math.sin(yaw), math.cos(yaw)
        t = b.s["coral"][idx] if idx is not None and idx < len(b.s["coral"]) else None
        t_fw = ((t[0] - b.x) * fx + (t[1] - b.z) * fz) if t is not None else 9.0
        for i, c in enumerate(b.s["coral"]):
            if i == idx or c[3] or c[4] or c[2] > 0.3:
                continue
            if t is not None and math.hypot(c[0] - t[0], c[1] - t[1]) < 0.05:
                continue                                          # the target itself (list re-indexed)
            dx, dz = c[0] - b.x, c[1] - b.z
            fw, la = dx * fx + dz * fz, dx * fz - dz * fx
            # lateral half-extent of that coral from its own axis (0.15 along, 0.057 across) vs the intake box
            # half-width 0.23 (g2 auto: station piles ~0.3 m apart aborted every 6th pick as 'second')
            rn = abs(wrap180(math.degrees(math.atan2(c[5], c[6])) - b.yaw)) % 180.0
            rn = min(rn, 180.0 - rn)
            lh = 0.15 * abs(math.sin(math.radians(rn))) + 0.057 * abs(math.cos(math.radians(rn)))
            if 0.25 < fw < 0.80 and abs(la) - lh < 0.23 and fw < t_fw + 0.08:
                return True
        return False

    FF_LINEUP = 1.35            # line-up point before the coral (centre to robot centre), checked in-field
    dogleg_w = float(__import__('os').environ.get('DOGLEG_W', '0.5'))   # cost weight of a sideways line-up approach
    allow_second = False        # take the pick even with a neighbour at the intake (auto: 6/20 g2 matches lost the 2nd coral to 'second')
    ground_v_touch = 0.0        # speed at intake touch (0 = user's stop-at-touch; GROUND_FAST: 0.8 + short line-up)
    ground_wait_slide = __import__('os').environ.get('GROUND_WAIT_SLIDE', '1') == '1'   # v42b: ground misses 145 -> 91 / 30 matches

    def ground_pick_ff(self, c, h, timeout=3.5, a_brake=12.0, push=0.05) -> bool:
        """User's technique (10-18): never ram the coral. Plan the speed so the robot arrives with ~zero speed
        exactly where the intake box touches the coral plus a ~5 cm push, braking ~0.2 s before (a_brake ~12 m/s^2
        from ~3 m/s). The 45 ms command latency is compensated by planning from the predicted position. The intake
        box spans 0.44-0.59 m ahead of the robot centre (calib5); the coral's half extent along the heading adds
        0.15|cos(rel)| + 0.057|sin(rel)|. Intake is lowered >= 0.4 s before contact. If the coral is not taken
        within 0.4 s at the stop, push another 5 cm once, then back straight out."""
        b = self.b
        g0 = b.gt
        cx, cz = c[0], c[1]
        ux, uz = math.sin(math.radians(h)), math.cos(math.radians(h))
        lxu, lzu = uz, -ux
        idx = next((i for i, cc in enumerate(b.s["coral"]) if abs(cc[0] - cx) < 1e-6 and abs(cc[1] - cz) < 1e-6), None)
        pid = b.s["coral"][idx][8] if idx is not None and len(b.s["coral"][idx]) > 8 else None
        phase = "line"
        stop_t = None
        extra = 0.0
        got = False
        s_min, c_prev, abort, rel = 9.0, None, None, 0.0
        slide_seen = -9.0
        c0 = (cx, cz)
        L = self._lineup.get((round(cx, 2), round(cz, 2), round(h % 360.0)), self.FF_LINEUP)
        while b.gt - g0 < timeout and not b.has_coral():
            cl = b.s["coral"]
            if pid is not None:
                idx = next((i for i, cc in enumerate(cl) if len(cc) > 8 and cc[8] == pid), None)
            elif idx is not None:
                idx = self.track_piece(cl, idx, (cx, cz))
            c2 = cl[idx] if idx is not None and idx < len(cl) else None
            rel = 0.0
            if c2 is not None and not c2[3]:
                cx, cz = c2[0], c2[1]
                rel = abs(wrap180(math.degrees(math.atan2(c2[5], c2[6])) - h)) % 180.0
                rel = min(rel, 180.0 - rel)
            s_al = (cx - b.x) * ux + (cz - b.z) * uz
            lat = (b.x - cx) * lxu + (b.z - cz) * lzu
            yerr = abs(wrap180(h - b.yaw))
            v_now = b.s["robot"]["vx"] * ux + b.s["robot"]["vz"] * uz          # speed along the line
            half = 0.15 * abs(math.cos(math.radians(rel))) + 0.057 * abs(math.sin(math.radians(rel)))
            s_stop = 0.589 + half - push - extra                               # touch the intake box + push
            b.hold = {INTAKE} if s_al < s_stop + 1.4 else set()
            if phase == "line":
                if abs(lat) < 0.08 and yerr < 6 and abs(b.s["robot"]["wy"]) < 90 and s_al < L + 0.5 and \
                        b.gt - slide_seen > 0.15:
                    phase = "in"
                else:
                    # the target is an obstacle on the way to the line-up point (ff2: 7 of 19 misses were the
                    # target bumped while driving around it to a line-up point on its far side)
                    vx, vz, w, _ = b.drive_cmd(cx - ux * L, cz - uz * L, h)
            if phase == "in":
                # never chase (ff1 ep1: the target was knocked away, the in-phase speed law sent the robot into the
                # station pile at 4.4 m/s, a coral flipped over the bumper into the frame and rode there for
                # 30 s of push penalties): abort if the target moved away or sideways, is sliding, or a second
                # coral is at the intake (two at once jam: one is taken, the other is pressed into the frame)
                s_min = min(s_min, s_al)
                c_v = 0.0
                if c_prev is not None and b.gt > c_prev[2]:
                    c_v = math.hypot(cx - c_prev[0], cz - c_prev[1]) / (b.gt - c_prev[2])
                second = (not self.allow_second) and self.second_at_intake(idx)
                if s_al > s_min + 0.08 or abs(lat) > 0.15 or (c_v > 0.4 and s_al > s_stop + 0.05) or second:
                    abort = ("moved" if s_al > s_min + 0.08 else "lateral" if abs(lat) > 0.15 else
                             "sliding" if c_v > 0.4 else "second")
                    break
                s_pred = s_al - max(v_now, 0.0) * self.ff_lead                 # where we will be when it applies
                rem = s_pred - s_stop
                vt = self.ground_v_touch
                v_ax = math.sqrt(vt * vt + 2 * a_brake * rem) if rem > 0 else 0.0
                v_ax = min(3.0, v_ax if rem > 0.02 else (max(0.25, vt) if rem > -0.005 - 0.1 * vt else 0.0))
                v_lat = -3.0 * lat
                vx, vz = ux * v_ax + lxu * v_lat, uz * v_ax + lzu * v_lat
                w = OMEGA_SIGN_SK * max(-600.0, min(600.0, 8.0 * wrap180(h - b.yaw)))
                if rem <= 0.005 and abs(v_now) < 0.15:
                    stop_t = stop_t or b.gt
                    if b.gt - stop_t > 0.4:
                        if extra == 0.0:
                            extra = 0.05                                       # one more 5 cm push
                            stop_t = None
                        else:
                            break
            elif math.hypot(cx - c0[0], cz - c0[1]) > ((0.80 if self.ground_wait_slide else 0.40) if self.ground_v_touch > 0
                                                       else 0.15):
                abort = "moved before line-up"
                break
            elif self.ground_v_touch > 0 and c_prev is not None and b.gt > c_prev[2] and \
                    math.hypot(cx - c_prev[0], cz - c_prev[1]) / (b.gt - c_prev[2]) > 0.3:
                if not self.ground_wait_slide:
                    abort = "sliding before line-up"     # follow a nudged coral, never a sliding one
                    break
                # c-runs: 68 of 148 misses aborted here (pile coral knocked by a station drop roll for a while):
                # keep following it to the line-up, the in-phase is entered only once it is still
                slide_seen = b.gt
            c_prev = (cx, cz, b.gt)
            b.step(vx, vz, w)
        got = b.has_coral()
        b.hold = set()
        if not got:
            self.bad_spots.append((cx, cz, b.gt))
            g1 = b.gt
            while b.gt - g1 < 0.3:
                b.step(-ux * 1.5, -uz * 1.5, 0.0)
        self.ev("intake: acquired" if got else "intake: miss", dt=round(b.gt - g0, 3), method="ff", phase=phase,
                extra=extra, abort=abort, rel=round(rel, 1), s_min=round(s_min, 3))
        if not got and not b.has_coral() and self.coral_in_footprint():
            self.shed_coral()
        return got

    def ground_pick(self, c, h, timeout=3.5, v_contact=2.8) -> bool:
        """Two-phase ground pickup along the coral's long axis: line up 0.85 m before it (heading locked, no
        rotation left), then drive straight in with the intake already down (it needs ~0.4 s), braking to
        ~v_contact at the intake box (0.44-0.59 m ahead). Abort (back off) instead of pushing a coral that has
        turned across or is under the bumper."""
        b = self.b
        g0 = b.gt
        cx, cz = c[0], c[1]
        ux, uz = math.sin(math.radians(h)), math.cos(math.radians(h))
        lxu, lzu = uz, -ux
        phase = "line"
        got = False
        under_since = None
        # follow THIS coral by its index (stable frame to frame): station floor coral lie in clusters ~0.3 m
        # apart and nearest-neighbour tracking jumped to a neighbour with a different axis (g2: false aborts)
        idx = next((i for i, cc in enumerate(b.s["coral"]) if abs(cc[0] - cx) < 1e-6 and abs(cc[1] - cz) < 1e-6), None)
        while b.gt - g0 < timeout and not b.has_coral():
            cl = b.s["coral"]
            c2 = cl[idx] if idx is not None and idx < len(cl) else None
            if c2 is not None and not c2[3]:
                cx, cz = c2[0], c2[1]
                rel = abs(wrap180(math.degrees(math.atan2(c2[5], c2[6])) - h))
                rel = min(rel, 180.0 - rel)
            else:
                rel = 0.0
            s_al = (cx - b.x) * ux + (cz - b.z) * uz            # distance to the coral along the line
            lat = (b.x - cx) * lxu + (b.z - cz) * lzu
            yerr = abs(wrap180(h - b.yaw))
            b.hold = {INTAKE} if s_al < 1.6 else set()
            if phase == "line":
                if abs(lat) < 0.10 and yerr < 8 and abs(b.s["robot"]["wy"]) < 90 and 0.6 < s_al < 1.4:
                    phase = "in"
                else:
                    vx, vz, w, _ = b.drive_cmd(cx - ux * 0.85, cz - uz * 0.85, h, ignore=(cx, cz))
            if phase == "in":
                # g1 data: every under-bumper happened below 1.7 m/s; acquisitions had median contact 2.44 m/s
                rem = max(0.0, s_al - 0.55)
                v_ax = min(3.2, math.sqrt(v_contact ** 2 + 2 * 6.0 * rem))
                v_lat = -3.0 * lat
                vx, vz = ux * v_ax + lxu * v_lat, uz * v_ax + lzu * v_lat
                w = OMEGA_SIGN_SK * max(-600.0, min(600.0, 8.0 * wrap180(h - b.yaw)))
                if s_al < 0.47 and abs(lat) < 0.30:
                    under_since = under_since or b.gt
                    if b.gt - under_since > 0.15:
                        self.ev("intake: under bumper", fwd=round(s_al, 3))
                        break
            b.step(vx, vz, w)
        got = b.has_coral()
        if not got:
            self.bad_spots.append((cx, cz, b.gt))
            # back straight out so the coral is not dragged along (push penalties)
            b.hold = set()
            g1 = b.gt
            while b.gt - g1 < 0.35:
                b.step(-ux * 2.0, -uz * 2.0, 0.0)
        b.hold = set()
        self.ev("intake: acquired" if got else "intake: miss", dt=round(b.gt - g0, 3), method="ground2", phase=phase)
        if not got and not b.has_coral() and self.coral_in_footprint():
            self.shed_coral()                 # only after a failed pickup: a just-taken coral is still in handoff
        return got

    # ------------------------------------------------------------------ floor algae (algae ground intake)
    ALGAE_BOX_FRONT = 0.661     # robot-frame forward edge of the algae ground-intake box (calib24 dump)

    def ensure_mode(self, algae: bool) -> bool:
        """RobotModeToggle switches Coral/Algae mode (mech.mode 0/1). A held algae forces Algae mode."""
        b = self.b
        want = 1 if algae else 0
        if b.mech.get("mode") == want:
            return True
        return b.tap(MODE, confirm=lambda s: s["mech"].get("mode") == want)

    def floor_algae_settled(self, a, prev=None) -> bool:
        """A loose algae resting on the floor (centre ~0.21 m = its radius), not on a staged coral, not rolling."""
        if a[3] or a[4] or a[2] > 0.30:
            return False
        return prev is None or math.hypot(a[0] - prev[0], a[1] - prev[1]) < 0.02

    def pick_floor_algae(self, ax, az, timeout=4.0, a_brake=12.0, push=0.05) -> bool:
        """Algae ground intake (Madtown.cs:486-495: Algae mode + Intake, no coral held). Same feedforward approach
        as coral: face the ball, arrive with ~zero speed where the intake box touches it plus a 5 cm push, then hold
        still with Intake held: the intake pulls a nearby ball in by itself (calib24b: balls followed the robot for
        3 m at up to 3 m/s while Intake was held), so never back away from a ball that moves toward the robot.
        calib24 dump: the box (Intake held, Algae mode) spans 0.315-0.661 m ahead of the robot centre at
        0.27-0.54 m height; a floor ball (r 0.206, centre 0.21) has a 0.196 m horizontal radius at the box bottom.
        The ball is followed by its index, picked before the mode switch (first try lost an exact-position match
        because the ball was still bouncing). The arm must start from Stow (calib24b: from Stack it never took)."""
        b = self.b
        if b.has_coral() or b.has_algae():
            return b.has_algae()
        g0 = b.gt
        al = b.s["algae"]
        idx = min(range(len(al)), key=lambda i: (al[i][0] - ax) ** 2 + (al[i][1] - az) ** 2) if al else None
        if idx is not None and math.hypot(al[idx][0] - ax, al[idx][1] - az) > 0.4:
            idx = None
        if b.mech.get("sp") not in (SP_ID["Stow"], SP_ID["Intake"]):
            b.tap(STOW, confirm=lambda s: s["mech"].get("sp") == SP_ID["Stow"])
        ok = self.ensure_mode(True)
        if not ok:
            b.idle(0.3)
            ok = self.ensure_mode(True)
        if not ok:
            self.ev("floor algae: mode switch failed")
            return False
        stop_t, extra, d_min = None, 0.0, 9.0
        while b.gt - g0 < timeout and not b.has_algae():
            al = b.s["algae"]
            if idx is not None and idx < len(al):
                if al[idx][3] or al[idx][4]:
                    break                                         # someone else has it / scored
                ax, az = al[idx][0], al[idx][1]
            dx, dz = ax - b.x, az - b.z
            d_min = min(d_min, math.hypot(dx, dz))
            # a ball against a wall is approached square to that wall (a slanted approach puts a bumper corner
            # into the wall before the intake reaches the ball)
            if ax > 8.25:
                ux, uz = 1.0, 0.0
            elif abs(az) > 3.55:
                ux, uz = 0.0, (1.0 if az > 0 else -1.0)
            else:
                dn = max(math.hypot(dx, dz), 1e-6)
                ux, uz = dx / dn, dz / dn
            h = math.degrees(math.atan2(ux, uz))
            d = dx * ux + dz * uz                                 # distance along the approach line
            lat = dx * uz - dz * ux
            s_stop = self.ALGAE_BOX_FRONT + 0.196 - push - extra
            b.hold = {INTAKE} if d < s_stop + 1.4 else set()
            if (d > s_stop + 1.2 or abs(lat) > 0.25) and stop_t is None:
                vx, vz, w, _ = b.drive_cmd(ax - ux * (s_stop + 0.8), az - uz * (s_stop + 0.8), h, ignore=(ax, az))
            else:
                v_now = b.s["robot"]["vx"] * ux + b.s["robot"]["vz"] * uz
                rem = d - max(v_now, 0.0) * self.ff_lead - s_stop
                v_ax = math.sqrt(2 * a_brake * rem) if rem > 0.02 else (0.25 if rem > -0.01 else 0.0)
                v_ax = min(3.0, v_ax)
                v_lat = 3.0 * lat
                vx, vz = ux * v_ax + uz * v_lat, uz * v_ax - ux * v_lat
                w = OMEGA_SIGN_SK * max(-600.0, min(600.0, 8.0 * wrap180(h - b.yaw)))
                if rem <= 0.01 and abs(v_now) < 0.15:
                    stop_t = stop_t or b.gt
                    # wait while the intake pulls the ball in; one extra 5 cm push if it does not come
                    if b.gt - stop_t > 1.0:
                        if extra == 0.0:
                            extra, stop_t = 0.05, None
                        else:
                            break
            b.step(vx, vz, w)
        got = b.has_algae()
        b.hold = set()
        self.ev("floor algae: " + ("acquired" if got else "miss"), dt=round(b.gt - g0, 2),
                d=round(math.hypot(ax - b.x, az - b.z), 3), d_min=round(d_min, 3), extra=extra)
        return got

    def station_pose(self, which: int, dist: float = 0.64, lat: float = 0.0):
        """Catch pose for station `which` (index into Field.stations): `dist` m from its drop spawn point along the
        drop direction and `lat` m along the station (the station slides its drop point to the robot's lateral
        position, clamped to +-0.8 m: CoralStation.AlignCoralSpawnPoint), facing the station. calib18: 36/36 at lat 0."""
        sx, sz, fx, fz = self.station_frame(which)
        lx, lz = -fz, fx
        return (sx + fx * dist + lx * lat, sz + fz * dist + lz * lat,
                math.degrees(math.atan2(-fx, -fz)) % 360.0)

    def station_frame(self, which: int):
        st = self.f.stations[which]
        fx, fz = st["spawnFwd"][0], st["spawnFwd"][2]
        hn = math.hypot(fx, fz)
        return st["spawn"][0], st["spawn"][2], fx / hn, fz / hn

    CATCH_LATS = (0.0, 0.15, -0.15, 0.3, -0.3, 0.45, -0.45)

    def _lat_feasible(self, which: int, lat: float) -> bool:
        """Robot (bumper half-size ~0.46 m) at the wall-contact pose (0.75 m along) must stay inside the field walls."""
        x, z, yaw = self.station_pose(which, dist=0.75, lat=lat)
        sx, sz, fx, fz = self.station_frame(which)
        lx, lz = -fz, fx
        for a in (-0.46, 0.46):
            for b in (-0.46, 0.46):
                cx, cz = x + a * fx + b * lx, z + a * fz + b * lz
                if cx > 8.77 - 0.03 or abs(cz) > 4.02 - 0.03:
                    return False
        return True

    def catch_lat(self, which: int):
        """Lateral catch offset whose corridor (wall to pre-pose) holds no floor coral, nearest the station centre;
        None if every feasible offset is fouled. A floor coral in the corridor is bumped by the creep, the robot
        stops short and the drop lands on it ('rider'), after which every catch there fails: all collapses
        (v17-v20, drills) started that way; missed drops land ~0.37 m along, right in the corridor."""
        sx, sz, fx, fz = self.station_frame(which)
        floor = []
        b = self.b
        for c in b.s["coral"]:
            # lying or leaning coral (not the ones riding on our own robot, not mid-air drops)
            if c[3] or c[4] or c[2] > 0.8 or math.hypot(c[0] - b.x, c[1] - b.z) < 0.55:
                continue
            a = (c[0] - sx) * fx + (c[1] - sz) * fz
            lat = -(c[0] - sx) * fz + (c[1] - sz) * fx
            # the catch corridor and the approach lane in front of it: a coral in the lane gets pushed into the
            # corridor on the way in (v22-0: the station's own start-of-match drop)
            if -0.3 < a < 2.6:
                floor.append((a, lat, 0.0))
        for al in b.s.get("algae", []):
            # staged-coral algae knocked off in auto roll to the alliance wall, often right in front of a station
            if al[3] or al[4] or al[2] > 0.6:
                continue
            a = (al[0] - sx) * fx + (al[1] - sz) * fz
            lat = -(al[0] - sx) * fz + (al[1] - sz) * fx
            if -0.3 < a < 2.6:
                floor.append((a, lat, 0.12))
        for lc in self.CATCH_LATS:
            if not self._lat_feasible(which, lc):
                continue
            # inside the corridor (along < 1.3) the creep would bump it: need lateral clearance; further out only
            # the pre-pose (1.04 m along) must stay clear of it, or the planner would push it in (v22-0)
            if all((abs(l - lc) > 0.62 + extra) if a < 1.3 else (math.hypot(a - 1.04, l - lc) > 0.65 + extra)
                   for a, l, extra in floor):
                return lc
        return None

    def station_fouled(self, which: int) -> bool:
        return self.catch_lat(which) is None

    def pick_station(self, pref: int) -> int:
        """The preferred station unless every catch offset there is fouled and the other one has a clear one."""
        if self.station_fouled(pref) and not self.station_fouled(1 - pref):
            self.ev("station: fouled, switching", pref=pref)
            return 1 - pref
        return pref

    def corridor_coral(self, which: int, along_max=1.3):
        """(along, lat) of lying/leaning coral in front of the station (not riding on our robot)."""
        sx, sz, fx, fz = self.station_frame(which)
        b = self.b
        out = []
        for c in b.s["coral"]:
            if c[3] or c[4] or c[2] > 0.8 or math.hypot(c[0] - b.x, c[1] - b.z) < 0.55:
                continue
            a = (c[0] - sx) * fx + (c[1] - sz) * fz
            lat = -(c[0] - sx) * fz + (c[1] - sz) * fx
            if -0.3 < a < along_max and abs(lat) < 1.0:
                out.append((a, lat))
        for al in b.s.get("algae", []):
            if al[3] or al[4] or al[2] > 0.6:
                continue
            a = (al[0] - sx) * fx + (al[1] - sz) * fz
            lat = -(al[0] - sx) * fz + (al[1] - sz) * fx
            if -0.3 < a < along_max and abs(lat) < 1.0:
                out.append((a, lat))
        return out

    def sweep_station(self, which: int, timeout=4.0) -> bool:
        """Clear the catch corridor: with Intake released (no drops), put the robot against the station face on one
        side of the coral and slide sideways along the face, pushing them out past the far end of the robot's catch
        range. Used when no lateral catch offset has a clear corridor (a single coral in the middle blocks all of
        them: the robot can only shift ~0.5 m between the walls)."""
        b = self.b
        g0 = b.gt
        cc = self.corridor_coral(which)
        if not cc:
            return True
        b.hold = set()
        mean_lat = sum(l for _, l in cc) / len(cc)
        sgn = 1.0 if mean_lat >= 0 else -1.0
        start_lat = (min(l for _, l in cc) - 0.78) if sgn > 0 else (max(l for _, l in cc) + 0.78)
        start_lat = max(-0.55, min(0.5, start_lat))
        end_lat = 0.5 * sgn if sgn > 0 else -0.55
        x0, z0, yaw = self.station_pose(which, dist=1.15, lat=start_lat)
        x1, z1, _ = self.station_pose(which, dist=0.78, lat=start_lat)
        x2, z2, _ = self.station_pose(which, dist=0.78, lat=end_lat)
        self.ev("station: sweep", which=which, n=len(cc), sgn=sgn, start=round(start_lat, 2), end=end_lat)
        b.goto(x0, z0, yaw, tol=0.1, ytol=6, timeout=2.5)
        b.goto(x1, z1, yaw, tol=0.08, ytol=6, timeout=1.0, avoid=False)
        g1 = b.gt
        while b.gt - g1 < 1.6:
            vx, vz, w, d = b.drive_cmd(x2, z2, yaw, vmax=1.5, avoid=False)
            if d < 0.06:
                break
            b.step(vx, vz, w)
        x3, z3, _ = self.station_pose(which, dist=1.15, lat=end_lat)
        b.goto(x3, z3, yaw, tol=0.12, timeout=0.8, avoid=False)
        left = self.corridor_coral(which)
        self.ev("station: swept", which=which, left=len(left), dt=round(b.gt - g0, 2))
        return self.catch_lat(which) is not None

    def intake_station(self, which: int, timeout=3.5) -> bool:
        """Station mode (RobotSpecial on): the station drops a coral (>= 1 s apart) once the robot is within 0.811 m
        of its drop point along the drop direction with Intake held; the wrist needs ~0.35 s after the press. Two
        phases (v17, 0 misses on clean corridors): drive to a pre-pose 0.40 m outside the catch pose with Intake held
        from 2 m, stop there, then creep in at 1.4 m/s; the drop then lands as the robot reaches the wall. The
        approach caps the closing speed along the drop axis so the robot can never coast into the drop range (a
        drop triggered at speed falls in front of the robot, onto the floor of the corridor)."""
        b = self.b
        g0 = b.gt
        lat = self.catch_lat(which)
        if lat is None:
            self.ev("station: all offsets fouled", which=which)
            self.sweep_station(which)
            lat = self.catch_lat(which)
            if lat is None:
                lat = 0.0
        x, z, yaw = self.station_pose(which, lat=lat)
        px, pz, _ = self.station_pose(which, dist=0.64 + 0.40, lat=lat)
        sx, sz, fx, fz = self.station_frame(which)
        RANGE = 0.811
        armed_since = None
        phase = "pre"
        last_gt = b.gt
        auto_end = False
        # never give up during the final creep: the drop may be in the air (drill-lat2: a 3.5 s timeout released
        # Intake at the instant the coral dropped)
        while (b.gt - g0 < timeout or (phase in ("in", "fast") and b.gt - g0 < timeout + 1.5)) and not b.has_coral():
            if b.s.get("rs", 0) != 0 or auto_end:
                g0 += b.gt - last_gt          # disabled (auto->teleop pause) or waiting it out: doesn't count
            last_gt = b.gt
            along = (b.x - sx) * fx + (b.z - sz) * fz
            # the elevator sags during the pause and needs ~0.3 s to come back to the station height (~0)
            if INTAKE in b.hold and b.s.get("rs", 0) == 0 and b.mech.get("sp") == SP_ID["Intake"] and b.mech.get("elev", 0) > -0.12:
                armed_since = armed_since or b.gt
            else:
                armed_since = None
            armed = armed_since is not None and b.gt - armed_since >= 0.35
            v_now = math.hypot(b.s["robot"]["vx"], b.s["robot"]["vz"])
            dp = math.hypot(px - b.x, pz - b.z)
            # a drop in the last moments of auto lands on a robot frozen by the auto->teleop pause: wait at the
            # pre-pose until teleop (drill-cap4: the t=135.00 drop was missed and fouled the corridor)
            auto_end = b.s.get("gs") == 0 and b.t < 135.9
            if phase == "pre" and armed and dp < 0.12 and v_now < 0.6 and abs(wrap180(yaw - b.yaw)) < 5 and not auto_end:
                phase = "in"
            lat_now = -(b.x - sx) * fz + (b.z - sz) * fx
            if phase == "pre" and self.fast_station and not auto_end:
                # one-phase entry: once on the drop axis (lateral and heading aligned, wrist armed early) drive
                # straight in, braking so the robot crosses the drop range (0.811) at ~0.9 m/s like the clean
                # two-phase catches, instead of stopping 0.4 m out first
                lat_err = lat_now - lat
                yerr_now = abs(wrap180(yaw - b.yaw))
                if along < 2.6 and abs(lat_err) < 0.10 and yerr_now < 6 and armed_since is not None \
                        and (along > 1.25 or (armed and along > RANGE + 0.02)):
                    phase = "fast"
            if phase == "fast":
                lat_err = lat_now - lat
                yerr_now = abs(wrap180(yaw - b.yaw))
                if along > 1.25 and (abs(lat_err) > 0.09 or yerr_now > 6):
                    phase = "pre"                 # lost alignment before committing: fall back to the stop
                elif along <= 1.25 and not armed:
                    phase = "pre"
            if phase == "fast":
                v_ax = min(4.4, math.sqrt(0.9 ** 2 + 2 * 7.0 * max(0.0, along - 0.85)))
                v_lat = -3.0 * (lat_now - lat)
                lx, lz = -fz, fx
                vx = -fx * v_ax + lx * v_lat
                vz = -fz * v_ax + lz * v_lat
                w = OMEGA_SIGN_SK * max(-600.0, min(600.0, 8.0 * wrap180(yaw - b.yaw)))
            elif phase == "pre":
                vx, vz, w, d = b.drive_cmd(px, pz, yaw)
                # closing-speed cap: stop 0.10 m outside the range at 8 m/s^2 (the robot brakes at ~13-16), one
                # 45 ms decision late
                room = max(0.0, along - (RANGE + 0.10))
                cap = max(0.0, math.sqrt(2 * 8.0 * room) - 8.0 * 0.045)
                vc = -(vx * fx + vz * fz)
                if vc > cap:
                    vx += (vc - cap) * fx
                    vz += (vc - cap) * fz
            else:
                # straight creep along the drop direction (like calib18's 36/36 catches)
                vx, vz, w, d = b.drive_cmd(x, z, yaw, vmax=1.4, avoid=False)
            want = math.hypot(x - b.x, z - b.z) < (3.0 if self.fast_station else 2.0) and not b.has_algae()
            if phase in ("pre",) and (along < RANGE + 0.05 or (auto_end and b.t < 135.3)):
                want = False                  # inside the range uncontrolled (e.g. after the auto pause): back out first
            b.hold = {INTAKE} if want else set()
            b.step(vx, vz, w)
        got = b.has_coral()
        b.hold = set()
        self.ev("station: caught" if got else "station: none", which=which, lat=lat, dt=round(b.gt - g0, 3),
                phase=phase)
        return got

    def wait_handoff(self, timeout=1.5) -> bool:
        b = self.b
        ok = b.wait_until(lambda s: s["mech"]["coral"]["has"] == 1 and (s["mech"]["coral"]["st"] == 3 or (s["mech"]["coral"]["st"] == 4 and s["mech"]["coral"]["at"] == 1)), timeout)
        self.ev("handoff", ok=ok)
        return ok

    # ------------------------------------------------------------------ placement
    def remove_algae(self, face: int, back: bool, r: float = 1.40, timeout=4.0) -> bool:
        """Empty-handed: L3 (high algae) / L2 (low algae) + hold Intake at the face centre."""
        b, f = self.b, self.f
        g0 = b.gt
        x, z, yaw = f.pose(face, "C", back, r=r)
        low = f.low_algae[face]
        btn, spid = (L2, SP_ID["LowAlgae"]) if low else (L3, SP_ID["HighAlgae"])
        tapped = False
        while b.gt - g0 < timeout and not b.has_algae():
            vx, vz, w, d = b.drive_cmd(x, z, yaw)
            tap = ()
            if not tapped and d < 1.6:
                fd = self.facing_dot()
                if (fd > 0.35 and not back) or (fd < -0.35 and back):
                    tap = (btn,); tapped = True
            elif tapped and b.mech.get("sp") != spid and not b.has_algae():
                tapped = False
            if tapped and b.mech.get("sp") == spid:
                b.hold = {INTAKE}
            b.step(vx, vz, w, tap=tap)
        got = b.has_algae()
        b.hold = set()
        self.ev("algae: acquired" if got else "algae: MISS", face=face, back=back, r=r, low=low, dt=round(b.gt - g0, 3))
        return got

    def held_algae_local(self):
        b = self.b
        held = [a for a in b.s.get("algae", []) if a[3]]
        if not held:
            return None
        a = held[0]
        yaw = math.radians(b.yaw)
        dx, dz = a[0] - b.x, a[1] - b.z
        return dx * math.sin(yaw) + dz * math.cos(yaw), a[2]

    def held_algae_rd(self):
        """Release-impulse direction of the held algae in the robot frame (fwd, lat, up), from the bridge's world
        vector (the piece's own -y: GamePieceController.ReleaseV1). None with an old bridge."""
        b = self.b
        held = [a for a in b.s["algae"] if a[3]]
        if not held or len(held[0]) < 8:
            return None
        dx, dz, dy = held[0][5], held[0][6], held[0][7]
        yaw = math.radians(b.yaw)
        fx, fz = math.sin(yaw), math.cos(yaw)
        return (round(dx * fx + dz * fz, 3), round(dx * fz - dz * fx, 3), round(dy, 3))

    net_reseat = False          # v36: re-seating never fixed the ball's orientation and cost 2 give-ups
    net_press_at_stop = False   # calib25 policy P2: raise the arm only once stopped at the net
    net_final_v = 1.286   # v45 (search run 3 mean); v41 1.29

    net_brake = 0.0             # gentle braking profile (m/s^2); v42c 6.0: misses 15% vs 7%, slower -> off
    net_press_dist = float(__import__('os').environ.get('NET_PRESS_DIST', '1.85'))  # Barge press once this far from the reef centre (eval: elev >= 34 before the brake -> 0/208 flips; was 2.1)
    net_diag = __import__('os').environ.get('NET_DIAG', '1') == '1'                 # diagonal final metre to the net
    net_x_min = 0.559   # v41 (CMA-ES mean, N40 179.45); v39 0.57
    net_still = float(__import__('os').environ.get('NET_STILL', '0.223'))   # v41 (CMA-ES mean, N40 179.45); v39 0.25
    # v49c: the robot reached x 1.5 with a 33 deg heading error (102/120 trips) and x 1.0 still 19 deg off, then
    # hunted inside the cage zone (x<1.0 -> release 1.85 s vs 1.34 s when within 10 deg; 17 pinned releases, 4
    # back-outs). The trip rotates ~84 deg/s against a 91 deg mean error in 1.1-1.3 s: ask for the turn earlier and
    # damp less on the way (user: "一边走一边旋转，到位时方向已对"), and accept a 25 deg error at the stop (the shot
    # depends on how the ball sits in the claw, not on the heading)
    net_fast_turn = __import__('os').environ.get('NET_FAST_TURN', '1') == '1'
    net_turn_frac = float(__import__('os').environ.get('NET_TURN_FRAC', '0.22'))
    net_kd = float(__import__('os').environ.get('NET_KD', '0.35'))
    net_at_yaw = float(__import__('os').environ.get('NET_AT_YAW', '25'))
    exit_dist = 0.04   # v45 (search run 3 mean); v41 0.166
    exit_angle_l4 = 50.0   # v41 (CMA-ES mean, N40 179.45); v39 50.0
    exit_angle_low = 57.4   # v45 (search run 3 mean); v41 71.0

    def score_net(self, z=-2.0, timeout=7.5, x_min=None) -> bool:
        """Algae to the blue net (Barge = L4 with algae). Forensics over 309 logged releases (10-18):
        - the release is a weak one-shot impulse (Madtown.cs:561-564), independent of FacingReef; whether the ball
          clears the near rim (tip point x~0.515) depends only on how it sits in the claw at release;
        - it scores when it sits low/forward (held y < 2.368, elevator < 45.35 and not rising: 179/183) and the
          robot has been still (no rotation/translation for 0.45 s: 109/114); it rolls back off the rim when the
          approach jammed it high against the barge bar (robot still correcting, or the arm still rising during a
          hard stop, nose-up pitch).
        So: arm up early (pressed as soon as the robot is clear of the reef), slow final 0.8 m, then send NOTHING at
        the stop and release only when the ball is settled low; judge success 1.5 s later by where the ball is."""
        b = self.b
        x_min = self.net_x_min if x_min is None else x_min
        g0 = b.gt
        tapped = False
        released = False
        hist = []                          # (gt, x, z, yaw) at the stop
        stop_since = None
        tried_reset = False
        algae_id = next((i for i, a in enumerate(b.s["algae"]) if a[3]), None)
        rd_at_release = None
        reseats = 0
        via_done = False
        pinned_since = None
        inside_since = None
        while b.gt - g0 < timeout and b.has_algae():
            tap = ()
            rc = self.f.reef
            if not tapped and math.hypot(b.x - rc[0], b.z - rc[1]) > self.net_press_dist and \
                    (not self.net_press_at_stop or (b.x < x_min + 0.08 and math.hypot(b.s["robot"]["vx"], b.s["robot"]["vz"]) < 0.1)):
                tap = (L4,); tapped = True            # arm up early: ~0.7 s to reach the Barge height
            elif tapped and b.mech.get("sp") not in (SP_ID["Barge"], SP_ID["Place"]):
                tapped = False
            elev = b.mech.get("elev", 0.0)
            up = b.mech.get("sp") == SP_ID["Barge"] and elev > 40
            auto_end = b.s.get("gs") == 0 and b.t < 136.6
            yerr = abs(wrap180(NET_YAW - b.yaw))
            spd = math.hypot(b.s["robot"]["vx"], b.s["robot"]["vz"])
            # pinned short of the target (eval e2-7, v43b-1: wedged under the barge at x 0.34 / z 0.6 m off, speed 0
            # with a non-zero command for the whole 7.5 s timeout, twice, then a wall drop: ~19 s + 4 pts). Not `at`
            # (z or yaw off) but not moving for 0.5 s inside x < 1.0: release here if the arm is up and the robot is
            # inside the net box, else back out once and re-approach
            if b.x < 1.0 and stop_since is None:
                inside_since = inside_since or b.gt
            else:
                inside_since = None
            if b.x < 1.0 and stop_since is None and ((spd < 0.05 and abs(b.s["robot"]["wy"]) < 5) or
                                                     (inside_since is not None and b.gt - inside_since > 2.5)):
                pinned_since = pinned_since or b.gt
                if b.gt - pinned_since > 0.5 or (inside_since is not None and b.gt - inside_since > 2.5):
                    if up and b.x < x_min + 0.15 and not auto_end:
                        self.ev("net: pinned, releasing", x=round(b.x, 2), dz=round(b.z - z, 2))
                        rd_at_release = self.held_algae_rd()
                        b.step(tap=(PLACE,))
                        released = True
                        break
                    if not tried_reset:
                        tried_reset = True
                        self.ev("net: pinned, backing out", x=round(b.x, 2), dz=round(b.z - z, 2))
                        g2 = b.gt
                        while b.gt - g2 < 0.8 and b.x < 1.3:
                            b.step(2.5, 0.0, 0.0)
                        pinned_since = None
                        inside_since = None
                        continue
            else:
                pinned_since = None
            if stop_since is None:
                # arrived, or pressed against the barge/cage short of x_min (n1: stuck at x 0.63 with the boundary at
                # 0.63, the stillness clock reset every time it jittered across)
                at = up and yerr < self.net_at_yaw and abs(b.z - z) < 0.35 and (b.x < x_min + 0.06 or (b.x < 0.72 and spd < 0.08))
            else:
                at = up and yerr < self.net_at_yaw + 5 and b.x < x_min + 0.15     # hysteresis once stopped
            if at:
                stop_since = stop_since or b.gt
                hist.append((b.gt, b.x, b.z, b.yaw, elev))
                win = [h for h in hist if b.gt - h[0] <= self.net_still]
                still = (b.gt - stop_since >= self.net_still and
                         max(abs(wrap180(h[3] - b.yaw)) for h in win) < 2.0 and
                         max(math.hypot(h[1] - b.x, h[2] - b.z) for h in win) < 0.02)
                held = [a for a in b.s["algae"] if a[3]]
                hy = held[0][2] if held else 9.0
                rising = len(hist) >= 4 and (elev - hist[-4][4]) / max(b.gt - hist[-4][0], 1e-3) > 0.3
                settled = hy < 2.368 and elev < 45.35 and not rising
                if still and settled and not auto_end:
                    rd_at_release = self.held_algae_rd()
                    # the release impulse is the ball's own -y (GamePieceController.ReleaseV1): v35t, its forward
                    # component >= +0.10 scored 50/52, <= +0.04 scored 0/7. The ball keeps turning in the claw after
                    # a stop; re-seat it once (arm down and up again) when it points back/up
                    if self.net_reseat and rd_at_release is not None and rd_at_release[0] < 0.07 and reseats < 1:
                        reseats += 1
                        self.ev("net: reseat", rd=rd_at_release)
                        b.tap(STOW, confirm=lambda s: s["mech"]["sp"] == SP_ID["Stow"])
                        g2 = b.gt
                        while b.gt - g2 < 0.8 and b.mech.get("elev", 0.0) > 5.0:
                            b.step(0.0, 0.0, 0.0)
                        b.step(0.0, 0.0, 0.0, tap=(L4,))
                        tapped = True
                        stop_since = None
                        hist = []
                        continue
                    b.step(tap=(PLACE,))
                    released = True
                    break
                if b.gt - stop_since > 1.0:
                    if not tried_reset:
                        # never got low and settled: back off and come in again slowly
                        tried_reset = True
                        b.goto(1.15, z, NET_YAW, tol=0.12, ytol=6, timeout=1.0)
                        stop_since = None
                        hist = []
                        continue
                    if not auto_end:
                        rd_at_release = self.held_algae_rd()
                        b.step(tap=(PLACE,))
                        released = True
                        break
                b.step(0.0, 0.0, 0.0)              # at the stop: no corrections at all
                continue
            stop_since = None
            hist = []
            # slow final approach (a hard stop pitches the robot nose-up and knocks the ball back in the claw)
            # smooth braking into the net: v_cap = sqrt(net_final_v^2 + 2 a (x - x_min)) (demo 10-19: 4.2 m/s at
            # x 1.4 then the 1.29 m/s cap = a ~12 m/s^2 stop with the arm up turned the ball in the claw, overshot to
            # x 0.41 and the shot flew back)
            if self.net_brake:
                v_cap = min(4.4, math.sqrt(self.net_final_v ** 2 + 2.0 * self.net_brake * max(0.0, b.x - x_min)))
            else:
                v_cap = 4.4 if b.x > 1.4 else self.net_final_v
            # eval 10-19 (658 releases): a straight-in final metre (velocity within ~20 deg of -x) stalls the ball
            # on the rim 12-16% of the time vs 1% for diagonal arrivals (same release state); the first shot of a
            # match (face 3 / face 1 -> z -0.4) is straight-in and missed 17/60. Route through a via point so the
            # last metre is driven at >= 30 deg to the barge
            tx, tz = x_min, z
            if self.net_diag and b.x > 1.9 and abs(b.z - z) < 0.6 and not via_done:
                s_ = 1.0 if z < -1.9 else -1.0
                tx, tz = 1.7, z + 0.7 * s_
            elif via_done is False:
                via_done = True
            # the cage columns hang at x ~0 (z -1.05 / -2.10 / -3.15), 3 cm from the bumper edge at the release x:
            # never enter x < 0.9 with the z error open (v43b-1: diagonal approach to z -3.05 jammed a bumper corner
            # on the -2.10 cage at z -2.45 for 18 s). Close z first at x 0.85 (bumper edge 0.39 from the columns)
            if b.x < 0.95 and abs(b.z - z) > 0.22:
                tx, tz = 0.85, z
            # v43c: 6 'net: fail' in 10 matches: inside x < 1.2 the P yaw law (8/deg, up to -290 deg/s) oscillated
            # +-30 deg around NET_YAW at low speed and the swung bumper corner kept hitting the -2.10 cage column,
            # bouncing the robot north of a z -3.05 target for the whole timeout. Gentle yaw inside the cage zone.
            if b.x < 1.2:
                vx, vz, w, _ = b.drive_cmd(tx, tz, NET_YAW, vmax=v_cap, kyaw=3.5, wmax=150.0)
            elif self.net_fast_turn:
                tf, kd = b.turn_frac, b.kd_yaw
                b.turn_frac, b.kd_yaw = self.net_turn_frac, self.net_kd
                try:
                    vx, vz, w, _ = b.drive_cmd(tx, tz, NET_YAW, vmax=v_cap)
                finally:
                    b.turn_frac, b.kd_yaw = tf, kd
            else:
                vx, vz, w, _ = b.drive_cmd(tx, tz, NET_YAW, vmax=v_cap)
            if self.net_press_at_stop and not tapped and b.x < x_min + 0.08:
                vx, vz, w = 0.0, 0.0, 0.0
            b.step(vx, vz, w, tap=tap)
        ok = False
        if released:
            # stay still only until the ball has left the claw (the wrist flick after Place throws it; v35 stood
            # here ~1.5 s to judge the shot, ~7 s per match), then lower the arm and go: the shot is judged later
            # by resolve_nets from where the ball lies (bd.net also counts balls that roll back off the rim)
            g1 = b.gt
            a0 = b.s["algae"][algae_id] if algae_id is not None and algae_id < len(b.s["algae"]) else None
            while b.gt - g1 < 0.5:
                b.step(0.0, 0.0, 0.0)
                if a0 is not None and algae_id < len(b.s["algae"]) and b.gt - g1 >= 0.2:
                    a = b.s["algae"][algae_id]
                    if a0[0] - a[0] > 0.12:
                        break
            if b.mech.get("sp") != SP_ID["Stow"]:
                b.step(tap=(STOW,))
            if algae_id is not None:
                self.pending_nets.append((algae_id, b.gt, z))
            ok = True
        else:
            if b.mech.get("sp") != SP_ID["Stow"]:
                b.tap(STOW, confirm=lambda s: s["mech"]["sp"] == SP_ID["Stow"])
        self.ev("net: released" if released else "net: fail", dt=round(b.gt - g0, 3), net=b.s["bd"]["net"], z=z,
                rd=rd_at_release)
        return ok

    def resolve_nets(self, wait=1.5):
        """Judge released net shots once the ball has landed: in the net box (x < 0.45, z within the blue net)."""
        b = self.b
        keep = []
        for (aid, t_rel, z) in self.pending_nets:
            if b.gt - t_rel < wait:
                keep.append((aid, t_rel, z))
                continue
            ok = False
            if aid < len(b.s["algae"]):
                a = b.s["algae"][aid]
                ok = a[0] < 0.45 and -3.82 < a[1] < -0.15
            self.ev("net: scored" if ok else "net: released-miss", late=True, z=z, net=b.s["bd"]["net"])
        self.pending_nets = keep

    def place(self, face: int, side: str, level: str, back: bool, timeout=6.0, hold_after=0.25, r: float = None) -> bool:
        b, f = self.b, self.f
        g0 = b.gt
        x, z, yaw = f.pose(face, side, back, r=r or POSE_R_DEFAULT)
        bit = f.bit(face, side, level)
        btn, spid = LEVEL_BTN[level], LEVEL_SP[level]
        tapped = b.mech.get("sp") == spid          # pressing the level again while there would stow the arm
        settled = 0
        last_e = None
        arrived_at = None
        last_c = None
        c_still = 0
        while b.gt - g0 < timeout:
            vx, vz, w, d = b.drive_cmd(x, z, yaw)
            yerr = abs(wrap180(yaw - b.yaw))
            tap = ()
            if not tapped and self.coral_ready() and d < 3.4:
                fd = self.facing_dot()
                if (fd > 0.35 and not back) or (fd < -0.35 and back):
                    tap = (btn,)
                    tapped = True
            elif tapped and b.mech.get("sp") not in (spid, SP_ID["Place"]) and b.has_coral():
                tapped = False      # the press was lost or toggled back: retry next decision
            e = b.mech.get("elev", 0.0)
            settled = settled + 1 if (last_e is not None and abs(e - last_e) < 0.12 and b.mech.get("sp") == spid) else 0
            last_e = e
            if d < 0.025 and yerr < 1.5 and arrived_at is None:
                arrived_at = b.gt
            hc = self.held_coral_rel()
            c_still = c_still + 1 if (hc and last_c and abs(hc[0] - last_c[0]) < 0.01 and abs(hc[2] - last_c[2]) < 0.01) else 0
            last_c = hc
            if arrived_at is not None and settled >= 2 and c_still >= 2 and d < 0.03 and yerr < 2.0:
                break
            b.step(vx, vz, w, tap=tap)
        self.ev("place: ready", face=face, side=side, level=level, back=back, r=r, dt=round(b.gt - g0, 3),
                elev=round(b.mech.get("elev", 0), 2), arrived=arrived_at is not None)
        self.wait_pause()
        b.step(tap=(PLACE,))
        if self.fast_leave:
            # leave as soon as the coral is out of the end effector; whether it scored is checked later
            g1 = b.gt
            while b.gt - g1 < 0.3 and b.has_coral():
                b.step()
            self.pending_checks.append((bit, b.gt, (face, side, level)))
            self.ev("place: released", bit=bit, dt=round(b.gt - g0, 3), blue=b.s["blue"])
            return True
        g1 = b.gt
        while b.gt - g1 < 0.6 and b.s["reefB"][bit] != "1":
            b.step()
        ok = b.s["reefB"][bit] == "1"
        self.ev("place: scored" if ok else "place: MISS", bit=bit, dt=round(b.gt - g0, 3), blue=b.s["blue"])
        return ok

    def held_coral_rel(self):
        """(forward, lateral, height) of the held coral relative to the robot centre, or None."""
        b = self.b
        held = [c for c in b.s.get("coral", []) if c[3]]
        if not held:
            return None
        c = held[0]
        yaw = math.radians(b.yaw)
        dx, dz = c[0] - b.x, c[1] - b.z
        return dx * math.sin(yaw) + dz * math.cos(yaw), dx * math.cos(yaw) - dz * math.sin(yaw), c[2]

    def exit_reef(self, dist=None, timeout=0.45, max_angle=50.0):
        """Right after a reef placement the arm still reaches over the branch: turning or sliding sideways drags it
        through the reef and the robot crawls at <0.7 m/s until the elevator is down (~0.7 s; calib22: no such limit
        in open field). Back straight out along the face normal with no rotation first, arm going down."""
        b, rc = self.b, self.f.reef
        dist = self.exit_dist if dist is None else dist
        x0, z0 = b.x, b.z
        nx, nz = b.x - rc[0], b.z - rc[1]
        h = math.hypot(nx, nz)
        nx, nz = nx / h, nz / h
        # v35t: the pure radial exit reached 2.3 m/s, then the turn onto the route to the station braked the robot
        # back to 1.4 m/s (~0.4 s lost per cycle). With a hint of where it goes next, exit along a blend of the
        # radial direction and the route's first leg, keeping >= cos(50 deg) of it radial (arm clears the branch)
        ux, uz = nx, nz
        hint, self.exit_hint = self.exit_hint, None
        if hint is not None:
            from .nav import route
            path = route((b.x, b.z), hint)
            wx, wz = path[0] if path else hint
            if math.hypot(wx - b.x, wz - b.z) < 0.3 and len(path) > 1:
                wx, wz = path[1]
            tx, tz = wx - b.x, wz - b.z
            tn = math.hypot(tx, tz)
            if tn > 0.3:
                tx, tz = tx / tn, tz / tn
                c = tx * nx + tz * nz
                if c >= math.cos(math.radians(max_angle)):
                    ux, uz = tx, tz
                else:
                    # rotate the radial direction 50 deg toward the route direction
                    sgn = 1.0 if (nx * tz - nz * tx) > 0 else -1.0
                    a = math.radians(max_angle) * sgn
                    ux, uz = nx * math.cos(a) - nz * math.sin(a), nx * math.sin(a) + nz * math.cos(a)
        g0 = b.gt
        first = True
        while b.gt - g0 < timeout and math.hypot(b.x - x0, b.z - z0) < dist:
            b.step(ux * 4.4, uz * 4.4, 0.0, tap=(STOW,) if first and b.mech.get("sp") in (SP_ID["Place"],) else ())
            first = False

    # ------------------------------------------------------------------ built-in AutoAlign placement
    align_back_ok = True          # cleared if the game's AutoAlign turns out to refuse backward alignment

    def unjam_coral(self, btn=None) -> bool:
        """A held coral that never reaches the end effector (vr3b-9: 40 s of placement timeouts with the coral
        sitting in the intake, st 2 / at 1, Madtown's floorCoral flag set: the Stow handoff branch requires
        !floorCoral and the flag is only cleared by a setpoint other than Stow/Stack, which this bot presses only
        once the coral is ready -> deadlock). Tap a level button (any) to clear the flag, then Stow re-runs the
        handoff; if it still is not ready, drop the coral with PLACE and report False."""
        b = self.b
        if not b.has_coral() or self.coral_ready():
            return True
        self.ev("coral: jammed, unjam", st=b.mech.get("coral", {}).get("st"), sp=b.mech.get("sp"),
                floor=b.mech.get("floor"), hand=b.mech.get("hand"))
        rc = self.f.reef
        if math.hypot(b.x - rc[0], b.z - rc[1]) < 1.9:
            nx, nz = b.x - rc[0], b.z - rc[1]
            h = math.hypot(nx, nz)
            g1 = b.gt
            while b.gt - g1 < 0.5 and math.hypot(b.x - rc[0], b.z - rc[1]) < 2.0:
                b.step(nx / h * 2.0, nz / h * 2.0, 0.0)          # free the arm from the reef first
        if self.coral_ready():
            return True
        b.step(tap=(btn or L2,))
        if b.wait_until(lambda s: not s["mech"]["coral"]["has"] or s["mech"]["coral"]["st"] == 3, 1.0):
            return self.coral_ready() or not b.has_coral()
        b.tap(STOW, confirm=lambda s: s["mech"]["sp"] == SP_ID["Stow"], tries=4)
        b.hold = {INTAKE}
        b.idle(0.4)
        b.hold = set()
        if b.wait_until(lambda s: not s["mech"]["coral"]["has"] or s["mech"]["coral"]["st"] == 3, 1.0):
            return self.coral_ready() or not b.has_coral()
        b.step(tap=(PLACE,))
        b.idle(0.3)
        if b.has_coral() and self.b.mech.get("hand"):
            # v51a-12 (80 pts, 1 of 896 logged matches): the handoff never reached its joint targets (handoffing=1,
            # st 2, Stow) and Madtown.cs:370-375 turns every Place press back into Stow while handoffing, so this
            # "drop" never dropped and the robot looped jam -> drop for 90 s. handoffing is cleared in Algae mode
            # while the coral controller is at target (Madtown.cs:357-359); Place then releases the coral from the
            # intake (532-581). Toggle to Algae, Place, toggle back.
            self.ev("coral: handoff stuck, algae-mode drop", st=b.mech.get("coral", {}).get("st"))
            self.ensure_mode(True)
            b.wait_until(lambda s: not s["mech"].get("hand"), 0.5)
            b.step(tap=(PLACE,))
            b.wait_until(lambda s: not s["mech"]["coral"]["has"], 0.6)
            self.ensure_mode(False)
            self.stow()
        # still holding after every drop attempt: count it; the match loop stops coral work after two
        self.coral_stuck = (getattr(self, "coral_stuck", 0) + 1) if b.has_coral() else 0
        self.ev("coral: dropped the jammed coral", has=b.has_coral(), stuck=self.coral_stuck)
        return False

    def wait_pause(self, limit=3.5):
        """The auto->teleop pause (rs == 1, ~3 s, match timer frozen at 135.00): button taps are dropped by the game,
        a PLACE tapped here was logged as released while the coral stayed in the end effector (v42b e1-8)."""
        b = self.b
        g = b.gt
        while b.s.get("rs") == 1 and b.gt - g < limit:
            b.step(0.0, 0.0, 0.0)
        return b.gt - g

    trough_r = float(__import__("os").environ.get("TROUGH_R", "1.38"))

    def place_trough(self, face: int, timeout: float = 3.0) -> bool:
        """L1 into the trough (drill_trough 10-21: FRONT only, face centre; r 1.30-1.40 40/45 in, 1.50 2/23, 1.60 0/22, back 0/91 (sum of both troughs)).
        L1 is pressed once the coral is in the end effector and the robot is within 1.2 m (ReefscapeRobotBase:259-281:
        with a coral held L1 -> L1 setpoint, l1Setpoint has no front/back variant); Place releases with (0, -2.5, 0).
        The trough counts any coral inside its boxes (2 pts teleop), so no slot bit to check."""
        b, f = self.b, self.f
        g0 = b.gt
        x, z, H = f.pose(face, "C", False, r=self.trough_r)
        tapped = b.mech.get("sp") == SP_ID["L1"]
        last_e, settled = None, 0
        c0 = b.s["bd"]["coral"]
        ok = False
        while b.gt - g0 < timeout and b.has_coral():
            if b.s.get("rs") == 1:
                g0 += self.wait_pause()
                continue
            d = math.hypot(x - b.x, z - b.z)
            yerr = abs(wrap180(H - b.yaw))
            tap = ()
            if not tapped and self.coral_ready() and d < 1.2:
                tap = (L1,)
                tapped = True
            elif tapped and b.mech.get("sp") not in (SP_ID["L1"], SP_ID["Place"]):
                tapped = False
            e = b.mech.get("elev", 0.0)
            settled = settled + 1 if (last_e is not None and abs(e - last_e) < 0.12 and b.mech.get("sp") == SP_ID["L1"]) else 0
            last_e = e
            v = math.hypot(b.s["robot"]["vx"], b.s["robot"]["vz"])
            if d < 0.04 and yerr < 2.5 and v < 0.2 and settled >= 2:
                ok = True
                break
            if not tapped and b.gt - g0 > 1.5 and b.has_coral() and not self.coral_ready():
                if not self.unjam_coral(L2):
                    return False
            vx, vz, w, _ = b.drive_cmd(x, z, H)
            b.step(vx, vz, w, tap=tap)
        if not ok:
            self.ev("trough: not converged", face=face, d=round(math.hypot(x - b.x, z - b.z), 3), dt=round(b.gt - g0, 2))
            self.stow()
            return False
        self.wait_pause()
        b.step(tap=(PLACE,))
        g1 = b.gt
        while b.gt - g1 < 0.4 and b.has_coral():
            b.step()
        self.ev("trough: released", face=face, dt=round(b.gt - g0, 3), coral_pts=b.s["bd"]["coral"] - c0)
        self.stow()
        if self.exit_after_place:
            self.exit_reef(max_angle=self.exit_angle_low)
        return True

    place_mode = __import__("os").environ.get("PLACE_MODE", "direct")  # "direct" (own FF stop, v43) | "align" (game AutoAlign)
    direct_kp = float(__import__("os").environ.get("DIRECT_KP", "11.0"))  # final proportional gain of place_direct

    def place_any(self, face: int, side: str, level: str, back: bool) -> bool:
        if level == "L1":
            return self.place_trough(face)
        if self.place_mode == "direct":
            return self.place_direct(face, side, level, back)
        return self.place_align(face, side, level, back)

    direct_a_brake = 15.0       # v46 placement drill: (kp 11, a 15, lead 0.07) last 0.6 m 0.405 s vs 0.54 at (8, 12); 0 misses/18

    # v53 logs (1,600 deliveries): L2/L3 entering the last 0.6 m within 10 deg of the final heading took 0.46 s to
    # ready, 10-25 deg off 0.60-0.67 s (about 3/4 of them). Converge the heading earlier on the deliver leg only
    # (same override-and-restore pattern as score_net; ground pickups and net trips keep the global law).
    # PLACE_TURN_FRAC / PLACE_KD / PLACE_FF_FLOOR unset -> unchanged behaviour
    # v54 A/B (N20 each, ~470 L2/L3 deliveries per arm): yaw error at 0.6 m 18.5 -> 4.6 deg, last 0.6 m 0.577 ->
    # 0.553 s (L4 0.812 -> 0.744), deliver residual -0.024 -> -0.062 s with (0.25, 0.35, 0.2): ~1.2 s per match
    place_turn_frac = __import__("os").environ.get("PLACE_TURN_FRAC", "0.25")
    place_kd = __import__("os").environ.get("PLACE_KD", "0.35")
    place_ff_floor = __import__("os").environ.get("PLACE_FF_FLOOR", "0.2")
    # after reaching 2.5 cm the robot still needs ~0.14 s to drop below 0.15 m/s (v54, L2/L3: speed is the last
    # release condition in 458/947); PLACE_V_OK relaxes that gate (misses are tracked by cycle_report).
    # v55 (N20 each): 0.15 resid -0.047 s, MISS 3/717 | 0.25 resid -0.106, MISS 0/718 | 0.35 resid -0.127, MISS 8/725
    # (each miss costs a ~3 s re-placement) -> 0.25
    place_v_ok = float(__import__("os").environ.get("PLACE_V_OK", "0.25"))

    def place_direct(self, face: int, side: str, level: str, back: bool, timeout=4.0, a_brake=None, final=1.6) -> bool:
        b = self.b
        saved = (b.turn_frac, b.kd_yaw, b.ff_floor)
        try:
            if self.place_turn_frac is not None:
                b.turn_frac = float(self.place_turn_frac)
            if self.place_kd is not None:
                b.kd_yaw = float(self.place_kd)
            if self.place_ff_floor is not None:
                b.ff_floor = float(self.place_ff_floor)
            return self._place_direct(face, side, level, back, timeout=timeout, a_brake=a_brake, final=final)
        finally:
            b.turn_frac, b.kd_yaw, b.ff_floor = saved

    def _place_direct(self, face: int, side: str, level: str, back: bool, timeout=4.0, a_brake=None, final=1.6) -> bool:
        """User's technique (10-19): arrive with heading and elevator already right and release on arrival, no
        align phase. The game's AutoAlign (place_align) creeps the last 0.5 m with a 0.2 s time constant: engage ->
        ready 0.59-0.72 s per placement in v42b for a leg a 15 m/s^2 stop covers in ~0.25 s. Here the final
        straight is driven with the ground_pick_ff feedforward stop (latency lead ff_lead), heading feedforward
        finishing at arrival, level pressed as soon as the coral is in the end effector and the facing latch is
        right; release when d < 3 cm, yaw < 2 deg, elevator settled and the held coral still. Falls back to
        place_align if not converged within `timeout`."""
        b, f = self.b, self.f
        a_brake = self.direct_a_brake if a_brake is None else a_brake
        g0 = b.gt
        x, z, H = f.pose(face, side, back, r=1.430)      # AutoAlign's pose: identical held-coral offsets in the logs
        rc = f.reef
        btn, spid = LEVEL_BTN[level], LEVEL_SP[level]
        bit = f.bit(face, side, level)
        tapped = b.mech.get("sp") == spid
        settled, last_e, last_c, c_still = 0, None, None, 0
        b.hold = set()
        ok_pose = False
        # g2d-0: 5 misses, all L3 back released at d 16-28 mm with 1 settled step: the lower levels need the
        # AutoAlign-grade stop (align misses rise steeply with d: 0.3% < 5 mm, 4.5% at 25 mm)
        # dv3a: L4 released at d<3 cm / v<0.3 / one still step missed 2 of 33; use the AutoAlign-grade stop for
        # every level (the time cost is the proportional tail, ~0.1 s)
        d_ok, y_ok, v_ok, n_ok = 0.025, 1.5, self.place_v_ok, 2
        taps = 0
        while b.gt - g0 < timeout:
            if b.s.get("rs") == 1:
                g0 += self.wait_pause()          # auto->teleop pause: drive and buttons are dead, do not count it
                continue
            d = math.hypot(x - b.x, z - b.z)
            yerr = wrap180(H - b.yaw)
            tap = ()
            if not tapped and self.coral_ready() and d < 3.4:
                fd = self.facing_dot()
                if (fd > 0.35 and not back) or (fd < -0.35 and back):
                    tap = (btn,)
                    tapped = True
                    taps += 1
            elif tapped and b.mech.get("sp") not in (spid, SP_ID["Place"]) and b.has_coral():
                tapped = False
                if taps >= 4:
                    # v45b-5 (10 pts): a coral left in the ground-EE state (st 4) after a pause-eaten L4 press was
                    # never accepted by any level press for the rest of the match
                    self.ev("place: level press rejected, dropping", st=b.mech.get("coral", {}).get("st"))
                    self.stow()
                    b.step(tap=(PLACE,))
                    b.idle(0.3)
                    return False
            e = b.mech.get("elev", 0.0)
            settled = settled + 1 if (last_e is not None and abs(e - last_e) < 0.12 and b.mech.get("sp") == spid) else 0
            last_e = e
            hc = self.held_coral_rel()
            c_still = c_still + 1 if (hc and last_c and abs(hc[0] - last_c[0]) < 0.01 and abs(hc[2] - last_c[2]) < 0.01) else 0
            last_c = hc
            vxn, vzn = b.s["robot"]["vx"], b.s["robot"]["vz"]
            v_now = math.hypot(vxn, vzn)
            if d < d_ok and abs(yerr) < y_ok and v_now < v_ok and settled >= n_ok and c_still >= n_ok and tapped:
                ok_pose = True
                break
            if not tapped and b.gt - g0 > 1.5 and b.has_coral() and not self.coral_ready():
                if not self.unjam_coral(btn):
                    return False
                g0 = b.gt
            if d < 0.85 and b.has_coral() and not self.coral_ready():
                # vr3b-9: arriving at the branch pose before the intake->end-effector handoff finished left the arm
                # blocked by the reef (handoffing stuck, st 2) for 40 s. Hold 0.9 m out until the coral is ready.
                rx, rz = b.x - rc[0], b.z - rc[1]
                rn = math.hypot(rx, rz)
                tx_, tz_ = rc[0] + rx / rn * (1.430 + 0.9), rc[1] + rz / rn * (1.430 + 0.9)
                vx, vz, w, _ = b.drive_cmd(tx_, tz_, H, vmax=1.5)
                b.step(vx, vz, w, tap=tap)
                continue
            vx, vz, w, _ = b.drive_cmd(x, z, H)
            if d < final and d > 1e-6:
                ux, uz = (x - b.x) / d, (z - b.z) / d
                cn = math.hypot(vx, vz)
                straight = cn < 1e-6 or (vx * ux + vz * uz) / cn > 0.94
                if straight:
                    v_al = vxn * ux + vzn * uz
                    rem = d - max(v_al, 0.0) * self.ff_lead
                    # dir1: a 0.25 m/s creep floor made the robot circle the target at d 2.6-3.6 cm / 0.3-0.57 m/s
                    # for the full 4 s in 20 of 165 placements; the last centimetres need a proportional law
                    # with no floor (AutoAlign's own is v = d / 0.2)
                    # dv2a: a 4/s proportional law over the whole final 1.4 m was SLOWER than AutoAlign (kp 5). Brake
                    # on the sqrt law and hand over to a proportional law only where the discrete step forces it:
                    # v <= rem / (lead + step) ~ 7 rem, i.e. inside ~0.45 m
                    v_ff = math.sqrt(2 * a_brake * rem) if rem > 0 else 0.0
                    v_cmd = min(4.4, v_ff, self.direct_kp * max(rem, 0.0))
                    vx, vz = ux * v_cmd, uz * v_cmd
                    # heading: drive_cmd's PD law (rate feedback) is kept as is
            b.step(vx, vz, w, tap=tap)
        if not ok_pose:
            self.ev("direct: not converged, align fallback", d=round(math.hypot(x - b.x, z - b.z), 3),
                    yerr=round(abs(wrap180(H - b.yaw)), 1), dt=round(b.gt - g0, 2))
            return self.place_align(face, side, level, back)
        hc = self.held_coral_rel()
        self.ev("align: ready", face=face, side=side, level=level, back=back, dt=round(b.gt - g0, 3), engaged=None,
                d=round(math.hypot(x - b.x, z - b.z), 3), coral_fwd=round(hc[0], 3) if hc else None, how="direct")
        self.wait_pause()
        b.step(tap=(PLACE,))
        g1 = b.gt
        while b.gt - g1 < 0.3 and b.has_coral():
            b.step()
        self.pending_checks.append((bit, b.gt, (face, side, level)))
        self.ev("place: released", bit=bit, dt=round(b.gt - g0, 3), blue=b.s["blue"], how="direct")
        if self.exit_after_place:
            self.exit_reef(max_angle=(75.0 if self.exit_wide else self.exit_angle_low) if level != "L4" else self.exit_angle_l4)
        return True

    def place_align(self, face: int, side: str, level: str, back: bool, timeout=6.0) -> bool:
        """Place with the game's own ReefscapeAutoAlign (user's request). Our planner brings the robot to within
        0.6 m of the target with the right heading; then ALIGN_L/R is HELD with free=1 (the bridge stops
        overriding the drive) and the game drives to the branch pose (r 1.430, lateral 0.17). The branch is
        latched on the press edge from the 2 nearest face nodes, so the press must happen inside the face's
        angular sector (L: phi in [-23, 36] deg, R: [-36, 23]) or it latches the neighbour face. Falls back to
        place() if the latch looks wrong or backward alignment is refused."""
        b, f = self.b, self.f
        if back and not self.align_back_ok:
            return self.place(face, side, level, back, r=R_FALLBACK[level])
        g0 = b.gt
        x, z, H = f.pose(face, side, back, r=1.430)
        btn, spid = LEVEL_BTN[level], LEVEL_SP[level]
        abtn = ALIGN_L if side == "L" else ALIGN_R
        bit = f.bit(face, side, level)
        rc = f.reef
        tapped = b.mech.get("sp") == spid
        engaged = None
        settled = 0
        last_e = None
        bad_steps = 0
        last_c = None
        c_still = 0
        while b.gt - g0 < timeout:
            if b.s.get("rs") == 1:
                g0 += self.wait_pause()
                continue
            d = math.hypot(x - b.x, z - b.z)
            yerr = abs(wrap180(H - b.yaw))
            tap = ()
            if not tapped and self.coral_ready() and d < 3.4:
                fd = self.facing_dot()
                if (fd > 0.35 and not back) or (fd < -0.35 and back):
                    tap = (btn,)
                    tapped = True
            elif tapped and b.mech.get("sp") not in (spid, SP_ID["Place"]) and b.has_coral():
                tapped = False
            if not tapped and b.gt - g0 > 1.5 and b.has_coral() and not self.coral_ready() and engaged is None:
                if not self.unjam_coral(btn):
                    return False
                g0 = b.gt
            if engaged is None:
                phi = wrap180(math.degrees(math.atan2(b.x - rc[0], b.z - rc[1])) - f.face_yaw[face])
                in_sector = (-17.0 <= phi <= 28.0) if side == "L" else (-28.0 <= phi <= 17.0)
                if d <= 0.6 and in_sector and yerr <= 45 and abtn not in b.hold and (self.coral_ready() or not b.has_coral()):
                    engaged = b.gt
                    b.hold = {abtn}
                    self.ev("align: engage", face=face, side=side, level=level, d=round(d, 2), phi=round(phi, 1))
                else:
                    vx, vz, w, _ = b.drive_cmd(x, z, H)
                    b.step(vx, vz, w, tap=tap)
                    continue
            b.step(tap=tap, free=True)
            d = math.hypot(x - b.x, z - b.z)
            vx_, vz_ = b.s["robot"]["vx"], b.s["robot"]["vz"]
            v = math.hypot(vx_, vz_)
            dt_e = b.gt - engaged
            # wrong or missing latch: the bridge reports AutoAlign's latched target (x, z); it must be our branch
            al = b.s.get("al")
            if al is not None and dt_e > 0.08:
                tgt_ok = al.get("has") == 1 and math.hypot(al["tgt"][0] - x, al["tgt"][1] - z) < 0.06
                bad_steps = 0 if tgt_ok else bad_steps + 1
                if bad_steps >= 2:
                    self.ev("align: wrong latch, fallback", has=al.get("has"), tgt=al.get("tgt"), d=round(d, 2))
                    b.hold = set()
                    b.step()
                    return self.place(face, side, level, back, r=R_FALLBACK[level])
            if back and dt_e > 0.4 and abs(wrap180(H - b.yaw)) > 60:
                self.align_back_ok = False
                self.ev("align: backward alignment refused, fallback")
                b.hold = set()
                b.step()
                return self.place(face, side, level, back, r=R_FALLBACK[level])
            e = b.mech.get("elev", 0.0)
            settled = settled + 1 if (last_e is not None and abs(e - last_e) < 0.12 and b.mech.get("sp") == spid) else 0
            last_e = e
            # the held coral itself must have stopped swinging (a coral picked up standing travels from the front-low
            # end effector to the back-high L4 pose and still swings +-0.2 m after the elevator has settled)
            hc = self.held_coral_rel()
            c_still = c_still + 1 if (hc and last_c and abs(hc[0] - last_c[0]) < 0.01 and abs(hc[2] - last_c[2]) < 0.01) else 0
            last_c = hc
            if self.align_loose:
                if d < 0.04 and abs(wrap180(H - b.yaw)) < 2.5 and v < 0.25 and settled >= 1 and c_still >= 2:
                    break
            elif d < 0.025 and abs(wrap180(H - b.yaw)) < 1.5 and v < 0.15 and settled >= 2 and c_still >= 2:
                break
        hc = self.held_coral_rel()
        self.ev("align: ready", face=face, side=side, level=level, back=back, dt=round(b.gt - g0, 3),
                engaged=round(b.gt - engaged, 3) if engaged else None, d=round(math.hypot(x - b.x, z - b.z), 3),
                coral_fwd=round(hc[0], 3) if hc else None)
        self.wait_pause()
        b.step(tap=(PLACE,), free=True)
        g1 = b.gt
        while b.gt - g1 < 0.3 and b.has_coral():
            b.step(free=True)
        b.hold = set()
        self.pending_checks.append((bit, b.gt, (face, side, level)))
        self.ev("place: released", bit=bit, dt=round(b.gt - g0, 3), blue=b.s["blue"], how="align")
        if self.exit_after_place:
            self.exit_reef(max_angle=(75.0 if self.exit_wide else self.exit_angle_low) if level != "L4" else self.exit_angle_l4)
        return True

    # ------------------------------------------------------------------ vertical (staged) coral with the end effector
    def pick_vertical(self, lx: float, lz: float, yaw: float, timeout=4.0, tap_l4: bool = True) -> bool:
        """Stack setpoint in Coral mode (tap L1 from Stow, empty robot, Intake released) lowers the end effector to
        a box 0.60 m ahead of the robot centre at 0.18 m height; holding Intake pulls a standing coral in. Enter
        Stack >= 1 m away (the arm swing passes the algae ball's height), then drive front-first onto the line
        and in without stopping, braking to ~0.6 m/s at the grab point (coral centre 0.60 m ahead). On the grab,
        release Intake and press L4 in the same step: the press is buffered through one Stow tick, so the arm
        swings up to L4 while the robot drives back to the reef."""
        b = self.b
        g0 = b.gt
        ux, uz = math.sin(math.radians(yaw)), math.cos(math.radians(yaw))
        lxu, lzu = uz, -ux                                     # lateral axis
        gx, gz = lx - ux * 0.60, lz - uz * 0.60
        # (10-19: a 1.6 m line-up with the in-phase gate from 2.3 m was slower, 1.35 -> 1.49 s to the grab; back to 1.05)
        sx, sz = lx - ux * 1.05, lz - uz * 1.05
        b.hold = set()
        if b.mech.get("sp") not in (SP_ID["Stow"], SP_ID["Stack"]):
            b.step(tap=(STOW,))
        phase = "line"
        got = False
        while b.gt - g0 < timeout:
            if b.s.get("rs") == 1:
                g0 += self.wait_pause()
                continue
            sp = b.mech.get("sp")
            tap = ()
            if sp == SP_ID["Stow"] and not b.has_coral() and not b.has_algae() and INTAKE not in b.hold \
                    and math.hypot(lx - b.x, lz - b.z) > 1.0:
                tap = (L1,)
            elif sp not in (SP_ID["Stow"], SP_ID["Stack"]) and not b.has_coral():
                tap = (STOW,)
            s_along = (lx - b.x) * ux + (lz - b.z) * uz          # distance to the coral along the approach line
            lat = (b.x - lx) * lxu + (b.z - lz) * lzu
            yerr = abs(wrap180(yaw - b.yaw))
            if phase == "line" and sp == SP_ID["Stack"] and abs(lat) < 0.08 and yerr < 4 \
                    and abs(b.s["robot"]["wy"]) < 50 and 0.75 < s_along < 1.8:
                phase = "in"                  # aligned AND no longer turning (a-ff: entered while still turning, 12 deg
                                              # off at 1.6 m/s, bumped the coral over)
            # the standing coral got knocked over or pushed away: give up at once instead of nudging it for 4 s.
            # Only before the grab zone: while the end effector pulls it in, it leaves its spot before `has` is set
            # (demo3: a real grab was flagged as knocked over and the next staged coral was skipped)
            if s_along > 0.85 and not any(math.hypot(c[0] - lx, c[1] - lz) < 0.15 and c[2] > 0.12 and not c[3]
                                          for c in b.s.get("coral", [])) and not b.has_coral():
                self.ev("vertical: knocked over", lx=lx, lz=lz)
                break
            if phase == "line":
                vx, vz, w, d = b.drive_cmd(sx, sz, yaw)
            else:
                b.hold = {INTAKE}
                rem = max(0.0, s_along - 0.60)
                v_ax = min(2.5, math.sqrt(0.6 ** 2 + 2 * 5.0 * rem))
                v_lat = -3.0 * lat
                vx, vz = ux * v_ax + lxu * v_lat, uz * v_ax + lzu * v_lat
                w = OMEGA_SIGN_SK * max(-600.0, min(600.0, 8.0 * wrap180(yaw - b.yaw)))
                if s_along < 0.50:
                    vx, vz = -ux * 0.3, -uz * 0.3              # overshoot guard: never let the bumper hit it
            b.step(vx, vz, w, tap=tap)
            if b.has_coral():
                got = True
                break
        b.hold = set()
        if got and tap_l4:
            self.wait_pause()                                  # a press during the auto->teleop pause is dropped
            b.step(tap=(L4,))                                  # buffered: swings straight up to L4 (back side)
        else:
            b.step()
        self.ev("vertical: " + ("grabbed" if got else "MISS"), lx=lx, lz=lz, dt=round(b.gt - g0, 2),
                st=b.mech.get("coral", {}).get("st"), phase=phase)
        return got

    def resolve_checks(self, failed: dict):
        """Placements released with fast_leave: 0.8 s later the branch bit must be set, else count a failure."""
        b = self.b
        self.resolve_nets()
        keep = []
        for (bit, t, slot) in self.pending_checks:
            if b.s["reefB"][bit] == "1" and b.gt - t > 0.3:
                self.confirmed.add(slot)
                self.ev("place: scored", bit=bit)
            elif b.gt - t < 2.0:
                # the scorer clears a branch bit while the robot or a held piece is inside the coral's bounds
                # ("phantom" zeros, 59 L4 cases in v1-v23, 0 physical losses): give it time
                keep.append((bit, t, slot))
            else:
                failed[slot] = failed.get(slot, 0) + 1
                self.ev("place: MISS", bit=bit, slot=list(slot))
        self.pending_checks = keep
