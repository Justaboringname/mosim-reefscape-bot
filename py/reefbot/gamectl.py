"""Launch / stop REEFSCAPE bot instances of MoSim 26.5.x.

Since 26.4.2 the Steam app is a stub (MacOS/MoSim) that unpacks the real player and runs it as a child process
WITHOUT the command-line args, so -batchmode/-nographics/-logFile can't be passed. The environment does reach the
child, so the bot instance is configured through env vars that only this launcher sets:
  MOSIMRL_REEF=1 (ReefBridge + no-upload guard), MOSIMRL_PORT, MOSIMRL_OWNER (orphan guard), MOSIMRL_LITE=1
  (cameras and canvases disabled: no rendering work), MOSIMRL_TEAM.
A Steam-launched human session never has these, so it is untouched. The bridge writes run/reef/inst-<port>.json
with the real player's pid; only those pids (and their stub parents) are ever killed.
"""

from __future__ import annotations

from reefbot import REPO  # repo-relative paths
import fcntl
import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path

from .client import ReefClient

SETTINGS = Path.home() / "Library/Application Support/com.mosimulator.mosimulator"
GUARDED = ("MatchSettings.json",)
# AppSettings.json display keys come from a persistent baseline, not a launch-time snapshot: with several bot instances
# launched 25 s apart, a later launch snapshotted the earlier bot's windowed 320x200 and "restored" it (10-19 night)
APP_BASELINE = REPO / "run" / "user_appsettings.json"
APP_KEYS = ("WindowMode", "ResolutionWidth", "ResolutionHeight")


def _app_display() -> dict:
    f = SETTINGS / "AppSettings.json"
    try:
        d = json.loads(f.read_text())
        return {k: d[k] for k in APP_KEYS if k in d}
    except Exception:
        return {}


def _app_baseline() -> dict:
    """The user's AppSettings display values; refreshed only from values the bot never writes (WindowMode != 2)."""
    cur = _app_display()
    if cur and cur.get("WindowMode") != 2:
        APP_BASELINE.parent.mkdir(parents=True, exist_ok=True)
        APP_BASELINE.write_text(json.dumps(cur, indent=1))
        return cur
    try:
        return json.loads(APP_BASELINE.read_text())
    except Exception:
        return {}


class SettingsGuard:
    """The game writes MatchSettings.json itself (PlayerSettings.SaveSpawnpoint -> MatchManager.SaveSettings) when
    a spawn point is missing, which would persist the bot's in-memory robot selection into the user's file.
    Keep a copy at launch and put it back if a bot session changed it."""

    def __init__(self):
        self.saved = {n: (SETTINGS / n).read_bytes() for n in GUARDED if (SETTINGS / n).exists()}
        self.screen = _screen_prefs_baseline()
        self.app = _app_baseline()

    def restore_if_changed(self) -> list[str]:
        changed = []
        for n, b in self.saved.items():
            f = SETTINGS / n
            if not f.exists() or f.read_bytes() != b:
                f.write_bytes(b)
                changed.append(n)
        if self.app and _app_display() != self.app:
            f = SETTINGS / "AppSettings.json"
            try:
                txt = f.read_text()
                d = json.loads(txt)
                d.update(self.app)
                f.write_text(json.dumps(d, indent=4))
                changed.append("AppSettings.json")
            except Exception:
                pass
        cur = _read_screen_prefs()
        if self.screen and cur != self.screen:
            for k, v in self.screen.items():
                subprocess.run(["defaults", "write", PREFS_DOMAIN, k, "-int", str(v)], capture_output=True)
            changed.append("screen prefs")
        if changed:
            print(f"[reef] restored user settings changed by the bot session: {changed}", flush=True)
        return changed


# Unity saves the player's window size/mode to the macOS prefs when it quits; a Lite bot instance (320x200 windowed,
# moved to a corner) would leave the user's own MoSim opening as a tiny window. Keep the user's values in a baseline
# file (only updated from values that are not the bot's) and write them back after every bot session.
PREFS_DOMAIN = "com.mosimulator.mosimulator"
SCREEN_KEYS = ("Screenmanager Fullscreen mode", "Screenmanager Resolution Width", "Screenmanager Resolution Height",
               "Screenmanager Resolution Use Native", "Screenmanager Window Position X", "Screenmanager Window Position Y")
SCREEN_BASELINE = REPO / "run" / "user_screen_prefs.json"


def _read_screen_prefs() -> dict:
    out = {}
    for k in SCREEN_KEYS:
        r = subprocess.run(["defaults", "read", PREFS_DOMAIN, k], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip().lstrip("-").isdigit():
            out[k] = int(r.stdout.strip())
    return out


def _screen_prefs_baseline() -> dict:
    cur = _read_screen_prefs()
    bot_like = cur.get("Screenmanager Resolution Width") == 320 and cur.get("Screenmanager Resolution Height") == 200
    if cur and not bot_like:
        SCREEN_BASELINE.write_text(json.dumps(cur, indent=1))
        return cur
    try:
        return json.loads(SCREEN_BASELINE.read_text())
    except (OSError, ValueError):
        return {}

ROOT = REPO
RUN = ROOT / "run"
(RUN / "reef").mkdir(parents=True, exist_ok=True)
APP = Path.home() / "Library/Application Support/Steam/steamapps/common/MoSimulator/MoSimulator.app"


def _listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _ppid(pid: int) -> int | None:
    out = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return int(out) if out.isdigit() else None


class ReefGame:
    def __init__(self, port: int = 47600, team: int = 1323, lite: bool = True, steps_per_frame: int = 2,
                 decision_steps: int = 10, call_timeout: float = 120.0):
        self.port, self.team, self.lite = port, team, lite
        self.steps_per_frame, self.decision_steps = steps_per_frame, decision_steps
        self.call_timeout = call_timeout
        self.pid: int | None = None
        self.client: ReefClient | None = None
        self.inst = RUN / "reef" / f"inst-{port}.json"
        self.guard: SettingsGuard | None = None

    def launch(self, wait_s: float = 75.0, tries: int = 3) -> None:
        """Start an instance; a start that hangs (seen at the protected robot-pack step) is killed and retried."""
        busy = self.user_busy()
        if busy:
            raise RuntimeError(f"not launching: {busy}")
        for i in range(tries):
            try:
                self._launch_once(wait_s)
                return
            except RuntimeError as e:
                print(f"[reef:{self.port}] launch attempt {i + 1} failed: {e}", flush=True)
                self._kill_unlisted()
                time.sleep(3)
        raise RuntimeError(f"port {self.port}: no working instance after {tries} attempts")

    def _kill_unlisted(self) -> None:
        """Kill only what this launcher started: player processes whose environment carries our MOSIMRL_PORT, and
        their stub parents. Never touches a MoSim the user launched from Steam."""
        out = subprocess.run(["pgrep", "-f", ".internet/MoSimulator.app/Contents/MacOS/MoSim"], capture_output=True, text=True).stdout.split()
        for p in out:
            pid = int(p)
            env = subprocess.run(["ps", "eww", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
            if f"MOSIMRL_PORT={self.port}" not in env or "MOSIMRL_REEF=1" not in env:
                continue
            stub = _ppid(pid)
            for q in (pid, stub):
                if q and q > 1:
                    try:
                        os.kill(q, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    @staticmethod
    def user_busy() -> str | None:
        """A non-bot MoSim (the user's own session) running: don't start (a new stub launch re-unpacks the game and
        would hang their session). MOSIMRL_BUSY_PROCS (a pgrep -il pattern, empty by default) adds other programs,
        e.g. a game that heavy batches would interrupt."""
        for p in subprocess.run(["pgrep", "-f", ".internet/MoSimulator.app/Contents/MacOS/MoSim"], capture_output=True, text=True).stdout.split():
            env = subprocess.run(["ps", "eww", "-o", "command=", "-p", p], capture_output=True, text=True).stdout
            if "MOSIMRL_REEF=1" not in env:
                return f"user MoSim pid {p}"
        busy = os.environ.get("MOSIMRL_BUSY_PROCS", "")
        if busy and subprocess.run(["pgrep", "-il", busy], capture_output=True, text=True).stdout.strip():
            return f"busy: {busy} running"
        return None

    def _launch_once(self, wait_s: float) -> None:
        if _listening(self.port):
            raise RuntimeError(f"port {self.port} already in use")
        self.inst.unlink(missing_ok=True)
        # one launch at a time: two stubs unpacking into the same .cache dir at once left one instance hung
        lockf = open(RUN / "reef" / "launch.lock", "a+")
        fcntl.flock(lockf, fcntl.LOCK_EX)
        try:
            self._launch_locked(wait_s)
            # keep the lock through robot-pack loading: two instances loading the protected packs at once left one
            # of them hung (hello never answered). The first reset loads the match scene.
            self.connect()
            self.client.pending_state = self.client.call({"cmd": "reset"})
        finally:
            fcntl.flock(lockf, fcntl.LOCK_UN)
            lockf.close()

    def _launch_locked(self, wait_s: float) -> None:
        if self.guard is None:
            self.guard = SettingsGuard()
        # The player reads its window mode/size at startup: with an exclusive-fullscreen user setting every bot launch
        # first grabbed the whole screen and switched Spaces. Start bot instances as a small background window
        # (MOSIMRL_BOT_WINDOW_XY, default top-left); the guard restores the user's values on stop.
        # demo (lite=False): a visible window of MOSIMRL_RES at the top-left
        demo_w, demo_h = (int(x) for x in os.environ.get("MOSIMRL_RES", "2560x1440").split("x"))
        bx, by = (int(x) for x in os.environ.get("MOSIMRL_BOT_WINDOW_XY", "40,40").split(","))
        ww, wh, wx, wy = (320, 200, bx, by) if self.lite else (demo_w, demo_h, 120, 160)
        for k, v in (("Screenmanager Fullscreen mode", 3), ("Screenmanager Resolution Width", ww),
                     ("Screenmanager Resolution Height", wh), ("Screenmanager Resolution Use Native", 0),
                     ("Screenmanager Window Position X", wx), ("Screenmanager Window Position Y", wy)):
            subprocess.run(["defaults", "write", PREFS_DOMAIN, k, "-int", str(v)], capture_output=True)
        # ...and the game's own display setting: SettingsService applies AppSettings.WindowMode/Resolution at startup
        # (0 = exclusive fullscreen), so the loading screen still went fullscreen. Windowed
        # 320x200 for the bot; SettingsGuard snapshotted the file above and puts the user's bytes back on stop.
        app = SETTINGS / "AppSettings.json"
        try:
            a = json.loads(app.read_text())
            a.update(WindowMode=2, ResolutionWidth=ww, ResolutionHeight=wh)
            app.write_text(json.dumps(a, indent=4))
        except (OSError, ValueError):
            pass
        env = dict(os.environ, MOSIMRL_REEF="1", MOSIMRL_PORT=str(self.port), MOSIMRL_OWNER=str(os.getpid()),
                   MOSIMRL_LITE="1" if self.lite else "0", MOSIMRL_TEAM=str(self.team), MOSIMRL_RUN_DIR=str(RUN))
        # bot runs stay in the background; a demo comes to the front so the user can watch it
        subprocess.run(["open", "-n"] + (["-g"] if self.lite else []) + [str(APP)], env=env, check=True)
        t0 = time.time()
        while time.time() - t0 < wait_s:
            if self.inst.exists() and _listening(self.port):
                self.pid = json.loads(self.inst.read_text())["pid"]
                break
            time.sleep(1.0)
        else:
            raise RuntimeError(f"port {self.port}: reef bridge never started listening")

    def connect(self) -> None:
        self.client = ReefClient(self.port, timeout=self.call_timeout)
        self.client.hello()
        info = self.client.config(steps_per_frame=self.steps_per_frame, decision_steps=self.decision_steps)
        print(f"[reef:{self.port}] connected pid={self.pid} {info}", flush=True)

    def attach_or_launch(self) -> None:
        if _listening(self.port) and self.inst.exists():
            self.pid = json.loads(self.inst.read_text())["pid"]
            self.connect()
        else:
            self.launch()

    def stop(self) -> None:
        if self.client:
            self.client.close()
            self.client = None
        if self.pid and _alive(self.pid):
            stub = _ppid(self.pid)
            for p in (self.pid, stub):
                if p and p > 1:
                    try:
                        os.kill(p, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            for _ in range(40):
                if not _alive(self.pid):
                    break
                time.sleep(0.25)
            else:
                os.kill(self.pid, signal.SIGKILL)
        self.pid = None
        if self.guard is not None:
            time.sleep(1.0)
            self.guard.restore_if_changed()
