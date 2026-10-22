"""Field geometry from the live scene dump (runs/reef/field.json), blue alliance only.

Faces are indexed 0..5 by the angle of their outward normal (face yaw: 0 = +z, 90 = +x); for face yaw a the
outward normal is n = (sin a, cos a) in (x, z). The AutoAlign scoring pose (measured, calib2) sits 1.432 m from
the reef centre along n and 0.17 m to the side; front scoring faces the reef (robot yaw = a + 180), back scoring
faces away (robot yaw = a) with the arm reaching back over the robot.
"""

from __future__ import annotations

from reefbot import FIELD_JSON  # repo-relative paths
import json
import math
from pathlib import Path

FIELD = FIELD_JSON
POSE_R = 1.432          # robot centre distance from reef centre at the AutoAlign scoring pose
POSE_LAT = 0.17         # lateral offset to the branch
LEVEL_BIT = {"L4": 0, "L3": 1, "L2": 2}


class Field:
    def __init__(self, path: Path = FIELD):
        f = json.loads(Path(path).read_text())
        self.raw = f
        self.reef = (f["blueReef"][0], f["blueReef"][2])
        faces = [fc for fc in f["faces"] if fc["p"][0] > 0]
        faces.sort(key=lambda fc: fc["yaw"])
        self.faces = faces                                  # yaw 30, 90, 150, 210, 270, 330
        self.face_yaw = [fc["yaw"] for fc in faces]
        self.low_algae = [bool(fc["lowAlgae"]) for fc in faces]
        poles = f["poles"]["blue"]
        self.poles = [(p["p"][0], p["p"][2]) for p in poles]
        # pole index for (face, side): the pole nearest the AutoAlign lateral target
        self.pole_of = {}
        for fi in range(6):
            for side in ("L", "R"):
                x, z, _ = self.pose(fi, side, back=False)
                a = math.radians(self.face_yaw[fi])
                # project the pose inward to the branch ring (~0.8 m from centre)
                bx, bz = x - (POSE_R - 0.8) * math.sin(a), z - (POSE_R - 0.8) * math.cos(a)
                self.pole_of[(fi, side)] = min(range(len(self.poles)),
                                               key=lambda i: math.hypot(self.poles[i][0] - bx, self.poles[i][1] - bz))
        self.stations = [s for s in f["stations"] if s["alliance"] == 0]
        self.processor = next(p for p in f["processors"] if p["alliance"] == 0)
        self.net = next(n for n in f["nets"] if n["alliance"] == 0)
        self.zones = f["zones"]

    def pose(self, face: int, side: str, back: bool, r: float = POSE_R) -> tuple[float, float, float]:
        """side 'L'/'R' = branch, 'C' = face centre (algae)."""
        a = math.radians(self.face_yaw[face])
        nx, nz = math.sin(a), math.cos(a)
        lx, lz = nz, -nx
        s = POSE_LAT if side == "L" else (-POSE_LAT if side == "R" else 0.0)
        x = self.reef[0] + r * nx + s * lx
        z = self.reef[1] + r * nz + s * lz
        yaw = self.face_yaw[face] if back else (self.face_yaw[face] + 180.0) % 360.0
        return x, z, yaw

    def bit(self, face: int, side: str, level: str) -> int:
        return self.pole_of[(face, side)] * 3 + LEVEL_BIT[level]

    def nearest_face(self, x: float, z: float) -> int:
        ang = math.degrees(math.atan2(x - self.reef[0], z - self.reef[1])) % 360.0
        return min(range(6), key=lambda i: abs((self.face_yaw[i] - ang + 180) % 360 - 180))
