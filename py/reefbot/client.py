"""Newline-delimited JSON client for harness/MoSimRL/ReefBridge.cs."""

from __future__ import annotations

import json
import socket

# Button order must match ReefButtons.cs
INTAKE, PLACE, L1, L2, L3, L4, CLIMB, STOW, SPECIAL, MODE, IMODE, ALIGN_L, ALIGN_R = range(13)
NB = 13


class BridgeError(RuntimeError):
    pass


class ReefClient:
    def __init__(self, port: int, host: str = "127.0.0.1", timeout: float = 180.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.settimeout(timeout)
        self._buf = b""
        self.pending_state: dict | None = None     # first state of a match already started by the launcher

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def call(self, msg: dict) -> dict:
        self.sock.sendall((json.dumps(msg) + "\n").encode())
        while b"\n" not in self._buf:
            chunk = self.sock.recv(1 << 20)
            if not chunk:
                raise BridgeError("bridge closed the connection")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        reply = json.loads(line)
        if not reply.get("ok", False):
            raise BridgeError(reply.get("error", "unknown bridge error"))
        return reply

    def hello(self) -> dict:
        return self.call({"cmd": "hello"})

    def config(self, steps_per_frame: int | None = None, decision_steps: int | None = None,
               time_scale: float | None = None) -> dict:
        m: dict = {"cmd": "config"}
        if steps_per_frame is not None:
            m["stepsPerFrame"] = steps_per_frame
        if decision_steps is not None:
            m["decisionSteps"] = decision_steps
        if time_scale is not None:
            m["timeScale"] = time_scale
        return self.call(m)

    def reset(self) -> dict:
        # a reset right after another one (launcher warm-up) blew the reef algae off the field: reuse that match
        if self.pending_state is not None:
            st, self.pending_state = self.pending_state, None
            return st
        return self.call({"cmd": "reset"})

    def field(self) -> dict:
        return self.call({"cmd": "field"})

    def act(self, vx: float = 0.0, vz: float = 0.0, w: float = 0.0, hold=(), tap=(), n: int = -1, free: bool = False) -> dict:
        h = [0] * NB
        for b in hold:
            h[b] = 1
        return self.call({"cmd": "act", "v": [float(vx), float(vz)], "w": float(w), "hold": h,
                          "tap": [int(b) for b in tap], "n": int(n), "free": 1 if free else 0})
