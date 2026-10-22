"""One full match at real-time speed in a normal rendered window, for the user to watch. argv: [port] [WxH]"""
import json
import os
import sys
import time

port = int(sys.argv[1]) if len(sys.argv) > 1 else 47601
os.environ["MOSIMRL_RES"] = sys.argv[2] if len(sys.argv) > 2 else "2560x1440"
os.environ["MOSIMRL_FLOAT"] = "0"

from .bot import Bot
from .gamectl import ReefGame
from .geom import Field
from .matchbot import MatchBot, OUT

ZH = {
    "place: released": "放 coral", "place: MISS": "放 coral 失败", "station: caught": "station 接到 coral",
    "station: none": "station 没接到", "station: sweep": "清理 station 接件区", "station: swept": "清理完成",
    "station: fouled, switching": "station 接件区被堵，换另一个站", "algae: acquired": "摘下 algae", "algae: MISS": "摘 algae 失败",
    "net: scored": "algae 进 net", "net: released-miss": "algae 出手没进", "net: fail": "送 net 失败",
    "net: unsettled, moving": "net 前站不稳，换位置", "net: giving up, dropping at the wall": "放弃这个 algae，丢到墙边",
    "vertical: grabbed": "竖着吸到 coral", "vertical: MISS": "竖着吸 coral 失败", "station: all offsets fouled": "station 接件区被堵",
    "intake: acquired": "ground intake 吸到 coral", "intake: miss": "ground intake 没吸到",
    "floor algae: acquired": "吸到地上的 algae", "floor algae: miss": "地上的 algae 没吸到",
    "shed: coral in footprint": "车底有 coral，先甩掉",
    "net: released": "algae 出手", "climb: slipped, retry": "climb 钩子滑脱，重试", "shed: clear": "甩掉了", "shed: still stuck": "还卡在车底",
}

g = ReefGame(port=port, lite=False)
g.launch(wait_s=120)
import subprocess
# optional helper (not shipped): a tiny AppKit tool that brings the demo window to the front
DS = os.environ.get("MOSIMRL_WINDOW_TOOLS", "")
for _ in range(3 if DS and os.path.exists(f"{DS}/activate") else 0):
    subprocess.run([f"{DS}/activate", str(g.pid)], capture_output=True)
    time.sleep(1.0)
time.sleep(4.0)                     # give the viewer a moment before the match starts
b = Bot(g.client, OUT / "demo.jsonl")
b.reset()
b.realtime = True
mb = MatchBot(b, Field())
mb.configure_auto(os.environ.get("DEMO_VARIANT", "g2_hyb4"))
_orig = mb.ev


def ev(tag, **kw):
    _orig(tag, **kw)
    if tag == "plan":
        print(f"[{b.t:6.1f}s] 规划器选择: {kw.get('act')}", flush=True)
    if tag == "intake: miss" and kw.get("abort"):
        print(f"[{b.t:6.1f}s]   (放弃原因: {kw.get('abort')})", flush=True)
    if tag in ZH:
        extra = ""
        if "face" in kw and "level" in kw:
            extra = f" 面{kw['face']}{kw.get('side', '')} {kw['level']}"
        elif tag == "place: released":
            extra = f" (比分 {kw.get('blue')})"
        print(f"[{b.t:6.1f}s] {ZH[tag]}{extra}", flush=True)


mb.ev = ev
mb.log = lambda m: print(f"[{b.t:6.1f}s] {m}", flush=True)
s = mb.run()
res = dict(score=s["blue"] - s["red"], bd=s["bd"], reef=s["reefB"].count("1"))
print("RESULT", json.dumps(res), flush=True)
json.dump(dict(res=res, events=mb.events), open(OUT / "demo.json", "w"))
time.sleep(8)
g.stop()
