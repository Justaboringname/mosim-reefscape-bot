"""Dynamic action planner (user, 10-18): from where the robot is NOW, pick the action sequence with the best
points per second, instead of a fixed priority list (the old schedule ran across the field to score coral while a
loose algae lay right next to the robot).

Actions
- ("coral", (face, side, level), source)  source: ("held",) | ("station", which) | ("ground", coral, heading)
- ("algae", face)            remove that face's reef algae (front) and score it in the net
- ("floor_algae", (x, z))    pick a loose algae off the floor and score it in the net (skill still to calibrate)

Time model: leg fits on v35 events (route length L around the reef, residual sd ~0.1 s):
  station catch -> L4 released   0.16 + L / 2.59
  station catch -> L3/L2 released -0.10 + L / 2.33
  released -> next station catch  0.10 + L / 2.36
  algae removal from the face pose ~1.3 s; net trip 3.14 + L / 3.46 (sd 0.76)
Ground pickup / floor algae legs are estimates until measured.

Selection: rank first actions by points/second, then look one action ahead from where each one ends (robot
position, slot filled, algae removed) and keep the first action of the best two-step rate. The climb deadline is
still enforced by MatchBot.
"""

from __future__ import annotations

import math

from .nav import route, path_length

PTS = {"L4": 5.0, "L3": 4.0, "L2": 3.0}
NET_X = 0.57


def dist(a, b) -> float:
    return path_length(a, route(a, b))


class Planner:
    def __init__(self, mb, lookahead: int = 1, floor_algae: bool = False):
        self.mb = mb
        self.lookahead = lookahead
        self.floor_algae = floor_algae

    # ------------------------------------------------------------------ time model
    @staticmethod
    def t_place(L: float, level: str) -> float:
        return 0.16 + L / 2.59 if level == "L4" else max(0.35, -0.10 + L / 2.33)

    @staticmethod
    def t_to_station(L: float) -> float:
        return 0.10 + L / 2.36

    @staticmethod
    def t_ground(L: float) -> float:
        return 0.40 + L / 2.6 + 0.35            # estimate: line-up + feedforward stop + intake

    @staticmethod
    def t_net(L: float) -> float:
        return 3.14 + L / 3.46

    # ------------------------------------------------------------------ state snapshot for lookahead
    def snapshot(self):
        mb = self.mb
        mb.refresh_algae()
        filled = set()
        for face in range(6):
            for side in ("L", "R"):
                for lv in ("L4", "L3", "L2"):
                    if mb.slot_filled(face, side, lv):
                        filled.add((face, side, lv))
        return dict(pos=(mb.b.x, mb.b.z), filled=filled, gone=set(i for i in range(6) if mb.algae_gone[i]),
                    holding=mb.b.has_coral(), holding_algae=mb.b.has_algae())

    # ------------------------------------------------------------------ enumeration
    def slot_ok(self, st, face, side, level) -> bool:
        mb, f = self.mb, self.mb.f
        if (face, side, level) in st["filled"] or mb.failed.get((face, side, level), 0) >= 2:
            return False
        if level == "L4":
            return True
        if level == "L2":
            return (not f.low_algae[face]) or face in st["gone"]
        return face in st["gone"]

    def coral_sources(self, st):
        """(kind, args, t_to_source, source_pos) for the next coral."""
        mb, f = self.mb, self.mb.f
        if st["holding"]:
            return [(("held",), 0.0, st["pos"])]
        out = []
        if mb.ground_mode:
            for _, c, h in mb.ground_choices(frm=st["pos"], to=None)[:4]:
                out.append((("ground", c, h), self.t_ground(dist(st["pos"], (c[0], c[1]))), (c[0], c[1])))
        else:
            for which in (0, 1):
                if mb.station_fouled(which):
                    continue
                x, z, _ = mb.station_pose(which)
                out.append((("station", which), self.t_to_station(dist(st["pos"], (x, z))), (x, z)))
        return out

    def actions(self, st, t_left: float):
        mb, f = self.mb, self.mb.f
        acts = []
        if st["holding_algae"]:
            z = mb.pick_net_z()
            t = self.t_net(dist(st["pos"], (NET_X, z)))
            return [(("net",), 4.0, t, (NET_X, z), None)]
        srcs = self.coral_sources(st)
        for face in range(6):
            for side in ("L", "R"):
                for lv in ("L4", "L3", "L2"):
                    if not self.slot_ok(st, face, side, lv):
                        continue
                    px, pz, _ = f.pose(face, side, True, r=1.43)
                    for src, t_src, spos in srcs:
                        t = t_src + self.t_place(dist(spos, (px, pz)), lv)
                        if t <= t_left:
                            acts.append((("coral", (face, side, lv), src), PTS[lv], t, (px, pz), (face, side, lv)))
        if not st["holding"]:
            for face in range(6):
                if face in st["gone"] or mb.failed.get(("ALGAE", face), 0) >= 2:
                    continue
                fx, fz, _ = f.pose(face, "C", False, r=1.40)
                z = mb.pick_net_z()
                t = 0.4 + dist(st["pos"], (fx, fz)) / 2.6 + 0.9 + self.t_net(dist((fx, fz), (NET_X, z)))
                # option value: the face's L3s (and L2s on a low-algae face) become available
                unlock = sum(1 for s2 in ("L", "R") for lv in (("L3", "L2") if f.low_algae[face] else ("L3",))
                             if (face, s2, lv) not in st["filled"])
                if t <= t_left:
                    acts.append((("algae", face), 4.0 + 0.5 * unlock, t, (NET_X, z), ("ALGAE", face)))
            if self.floor_algae:
                for a in mb.b.s.get("algae", []):
                    if a[3] or a[4] or a[2] > 0.30 or a[0] < 1.2 or math.hypot(a[0] - f.reef[0], a[1] - f.reef[1]) < 1.2:
                        continue
                    z = mb.pick_net_z()
                    t = 0.5 + dist(st["pos"], (a[0], a[1])) / 2.6 + 0.6 + self.t_net(dist((a[0], a[1]), (NET_X, z)))
                    if t <= t_left:
                        acts.append((("floor_algae", (a[0], a[1])), 4.0, t, (NET_X, z), None))
        return acts

    @staticmethod
    def after(st, act):
        kind, v, t, end, key = act
        st2 = dict(pos=end, filled=set(st["filled"]), gone=set(st["gone"]), holding=False, holding_algae=False)
        if kind[0] == "coral":
            st2["filled"].add(kind[1])
        elif kind[0] == "algae":
            st2["gone"].add(kind[1])
        return st2

    # ------------------------------------------------------------------ choice
    def choose(self, t_left: float):
        st = self.snapshot()
        acts = self.actions(st, t_left)
        if not acts:
            return None, []
        acts.sort(key=lambda a: -a[1] / a[2])
        best, best_score, table = None, -1.0, []
        for a in acts[:12]:
            v1, t1 = a[1], a[2]
            score = v1 / t1
            if self.lookahead and t_left - t1 > 1.0:
                st2 = self.after(st, a)
                nxt = self.actions(st2, t_left - t1)
                if nxt:
                    b2 = max(nxt, key=lambda x: x[1] / x[2])
                    score = (v1 + b2[1]) / (t1 + b2[2])
            table.append((round(score, 3), round(v1, 1), round(t1, 2), a[0][0], a[0][1] if len(a[0]) > 1 else None,
                          (a[0][2][0] if a[0][0] == "coral" else None)))
            if score > best_score:
                best, best_score = a, score
        table.sort(key=lambda r: -r[0])
        return best, table
