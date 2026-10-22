"""Routing around the blue reef and along the field walls, plus a lookahead path follower.

The reef (hexagon, apothem 0.83 m, corner radius ~0.96 m) plus the robot's half-diagonal (~0.65 m) keeps the robot
centre outside a circle of radius KEEP around the reef centre; scoring poses sit at 1.40-1.43 m, i.e. *on* the
face flats, which is fine because the final approach is radial. A straight segment that would cross the KEEP circle
is replaced by: tangent-ish entry onto an arc of radius ARC, the arc the short way round, then exit toward the goal.
"""

from __future__ import annotations

import math

KEEP = 1.30
ARC = 1.62
FIELD_X = 8.77 - 0.48
FIELD_Z = 4.02 - 0.48


def _seg_circle_hit(ax, az, bx, bz, cx, cz, r) -> bool:
    dx, dz = bx - ax, bz - az
    L2 = dx * dx + dz * dz
    if L2 < 1e-9:
        return math.hypot(ax - cx, az - cz) < r
    t = max(0.0, min(1.0, ((cx - ax) * dx + (cz - az) * dz) / L2))
    px, pz = ax + t * dx, az + t * dz
    return math.hypot(px - cx, pz - cz) < r


def route(a, b, reef=(4.2989, 0.0), keep=KEEP, arc=ARC, step_deg=25.0):
    """Waypoints from a to b (excluding a, including b) that avoid the reef disc."""
    ax, az = a
    bx, bz = b
    cx, cz = reef
    if not _seg_circle_hit(ax, az, bx, bz, cx, cz, keep):
        return [(bx, bz)]
    th_a = math.atan2(ax - cx, az - cz)
    th_b = math.atan2(bx - cx, bz - cz)
    d = (th_b - th_a + math.pi) % (2 * math.pi) - math.pi        # short way round
    n = max(1, int(abs(math.degrees(d)) / step_deg))
    pts = []
    for i in range(1, n + 1):
        th = th_a + d * i / (n + 1)
        x, z = cx + arc * math.sin(th), cz + arc * math.cos(th)
        pts.append((max(-FIELD_X, min(FIELD_X, x)), max(-FIELD_Z, min(FIELD_Z, z))))
    # drop leading arc points the robot can already skip (direct line clear)
    while pts and not _seg_circle_hit(ax, az, pts[min(1, len(pts) - 1)][0], pts[min(1, len(pts) - 1)][1], cx, cz, keep) and len(pts) > 1:
        pts.pop(0)
    # and trailing ones from which the goal is directly reachable
    while len(pts) > 1 and not _seg_circle_hit(pts[-2][0], pts[-2][1], bx, bz, cx, cz, keep):
        pts.pop()
    pts.append((bx, bz))
    return pts


def lookahead_point(pos, path, la=0.7):
    """Point `la` metres ahead along the polyline starting at the robot position."""
    px, pz = pos
    pts = [(px, pz)] + list(path)
    remaining = la
    for i in range(len(pts) - 1):
        x0, z0 = pts[i]
        x1, z1 = pts[i + 1]
        seg = math.hypot(x1 - x0, z1 - z0)
        if seg >= remaining:
            t = remaining / max(seg, 1e-9)
            return x0 + t * (x1 - x0), z0 + t * (z1 - z0)
        remaining -= seg
    return pts[-1]


def path_length(pos, path) -> float:
    pts = [pos] + list(path)
    return sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(len(pts) - 1))


# ---------------------------------------------------------------------------------------------------------------
# Grid A* among circular obstacles (reef, staged "lollipop" coral, floor algae, floor coral), blue half of the field.

import heapq

RES = 0.1
X0, X1 = -0.4, 8.30          # robot-centre limits (walls at -?, 8.77; bumper half-diagonal ~0.47 along x)
Z0, Z1 = -3.55, 3.55
NX = int(round((X1 - X0) / RES)) + 1
NZ = int(round((Z1 - Z0) / RES)) + 1


def _cell(x, z):
    i = int(round((min(max(x, X0), X1) - X0) / RES))
    j = int(round((min(max(z, Z0), Z1) - Z0) / RES))
    return i, j


def _pt(i, j):
    return X0 + i * RES, Z0 + j * RES


def _clear(a, b, obstacles, hard_only=True):
    for (cx, cz, r, hard) in obstacles:
        if hard_only and not hard:
            continue
        if _seg_circle_hit(a[0], a[1], b[0], b[1], cx, cz, r):
            return False
    return True


def plan(start, goal, obstacles, soft_cost=6.0):
    """obstacles: (cx, cz, radius, hard). Hard ones are impassable (unless start/goal lie inside them); soft ones
    cost `soft_cost` per metre crossed. Returns a smoothed waypoint list (excluding start, including goal)."""
    if _clear(start, goal, obstacles, hard_only=False):
        return [goal]
    blocked = {}
    for (cx, cz, r, hard) in obstacles:
        i0, j0 = _cell(cx - r, cz - r)
        i1, j1 = _cell(cx + r, cz + r)
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                x, z = _pt(i, j)
                if (x - cx) ** 2 + (z - cz) ** 2 < r * r:
                    prev = blocked.get((i, j), 0.0)
                    blocked[(i, j)] = max(prev, 1e9 if hard else soft_cost)
    s, g = _cell(*start), _cell(*goal)
    blocked.pop(s, None)
    # goal inside an obstacle (e.g. a scoring pose next to a floor piece): allow it
    if blocked.get(g, 0) >= 1e9:
        blocked[g] = soft_cost
    openq = [(0.0, 0.0, s)]
    came = {s: None}
    gcost = {s: 0.0}
    nbr = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0), (1, 1, 1.414), (1, -1, 1.414), (-1, 1, 1.414), (-1, -1, 1.414)]
    found = False
    while openq:
        _, gc, cur = heapq.heappop(openq)
        if cur == g:
            found = True
            break
        if gc > gcost.get(cur, 1e18):
            continue
        for di, dj, w in nbr:
            n = (cur[0] + di, cur[1] + dj)
            if not (0 <= n[0] < NX and 0 <= n[1] < NZ):
                continue
            pen = blocked.get(n, 0.0)
            if pen >= 1e9:
                continue
            ng = gc + w * RES * (1.0 + pen)
            if ng < gcost.get(n, 1e18):
                gcost[n] = ng
                came[n] = cur
                h = math.hypot(n[0] - g[0], n[1] - g[1]) * RES
                heapq.heappush(openq, (ng + h, ng, n))
    if not found:
        return route(start, goal)          # fall back to the reef-only router
    cells = []
    c = g
    while c is not None:
        cells.append(_pt(*c))
        c = came[c]
    cells.reverse()
    cells[0] = start
    cells[-1] = goal
    # line-of-sight smoothing against all obstacles (soft ones too, so the smoothed path keeps its detours)
    out = []
    i = 0
    while i < len(cells) - 1:
        j = len(cells) - 1
        while j > i + 1 and not _clear(cells[i], cells[j], obstacles, hard_only=False):
            j -= 1
        out.append(cells[j])
        i = j
    return out
