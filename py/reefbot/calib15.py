"""Calibration 15: algae removal sweep (all six faces, one orientation, standoff r) + net drop geometry.
argv: port, orient ('front'|'back'), r"""
from reefbot import RUNS  # repo-relative paths
import json, math, sys
from pathlib import Path
from reefbot.gamectl import ReefGame
from reefbot.bot import Bot, SP_ID
from reefbot.geom import Field
from reefbot.skills import Skills
from reefbot.client import L4, PLACE, STOW, INTAKE
OUT = RUNS
PORT = int(sys.argv[1])
CONFIGS = [(o, float(r)) for o, r in (c.split(":") for c in sys.argv[2:])]
g = ReefGame(port=PORT); g.launch()
b = Bot(g.client, OUT / f"calib15-{PORT}.jsonl")
f = Field(); k = Skills(b, f)
allres = {}
for ORIENT, R in CONFIGS:
  BACK = ORIENT == "back"
  b.reset()
  res = []
  def local(p):
      yaw = math.radians(b.yaw); fx, fz = math.sin(yaw), math.cos(yaw); rx, rz = fz, -fx
      dx, dz = p[0] - b.x, p[1] - b.z
      return [round(dx * fx + dz * fz, 3), round(dx * rx + dz * rz, 3), round(p[2], 3)]
  # get rid of the preload without touching any face: drop it on the floor near the start
  b.step(tap=(PLACE,)); b.idle(0.4)
  order = [4, 3, 5, 0, 1, 2]
  for i, face in enumerate(order):
      if b.t < 10: break
      k.stow()
      alg_before = [a[:3] for a in b.s["algae"] if math.hypot(a[0] - f.reef[0], a[1] - f.reef[1]) < 0.9]
      ok = k.remove_algae(face, BACK, r=R, timeout=3.0)
      e = k.events[-1]
      held = [a for a in b.s["algae"] if a[3]]
      res.append(dict(face=face, low=f.low_algae[face], ok=ok, dt=e[7]["dt"], sp=b.sp, held_local=local(held[0]) if held else None))
      print(res[-1], flush=True)
      if not ok:
          b.hold = set(); k.stow(); continue
      if i % 2 == 0:
          # net attempt: face -x near the blue net (z -2), Barge setpoint, creep until blocked, drop
          b.goto(1.6, -2.0, 270, tol=0.06, ytol=3, timeout=4)
          b.tap(L4, confirm=lambda s: s["mech"]["sp"] == SP_ID["Barge"])
          b.idle(1.0)
          held = [a for a in b.s["algae"] if a[3]]
          bl = local(held[0]) if held else None
          g0 = b.gt; xs = []
          while b.gt - g0 < 2.5:
              b.step(-1.0, 0, 0); xs.append(b.x)
              if len(xs) > 6 and abs(xs[-1] - xs[-6]) < 0.005: break
          x_block = round(b.x, 3)
          n0 = b.s["bd"]["net"]
          b.step(tap=(PLACE,)); b.idle(1.2)
          res.append(dict(net_try=face, barge_local=bl, x_block=x_block, net=b.s["bd"]["net"] - n0,
                          algae_after=[[round(v, 2) for v in a[:3]] for a in b.s["algae"] if abs(a[0]) < 1.5 and a[1] < 0.5]))
          print(res[-1], flush=True)
      else:
          b.step(tap=(PLACE,)); b.idle(0.3)
      k.stow()

  print("final", ORIENT, R, b.s["blue"], b.s["red"], b.s["bd"], flush=True)
  allres[f"{ORIENT}:{R}"] = res
  while not b.s.get("done"):
      b.step(n=200)
json.dump(allres, open(OUT / "calib15-sweep.json", "w"))
g.stop()
