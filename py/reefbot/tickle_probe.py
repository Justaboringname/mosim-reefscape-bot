"""Which phase of a bot instance makes macOS see user activity (display wake)? Prints phase timestamps;
pmset -g assertionslog runs alongside."""
import time, sys
from reefbot.gamectl import ReefGame
from reefbot.client import L4, STOW, INTAKE
def mark(tag):
    print(f"PHASE {time.strftime('%H:%M:%S')} {tag}", flush=True)
g = ReefGame(port=47601)
mark("launch-begin")
g._launch_locked(240)          # start + wait listening, no reset yet
g.connect()
mark("listening-idle-menu"); time.sleep(10)
mark("reset-begin")
s = g.client.reset()
mark("reset-done"); time.sleep(4)
mark("act-no-buttons")
for _ in range(120): g.client.act(0, 0, 0)
mark("act-drive-no-buttons")
for _ in range(120): g.client.act(1.0, 0, 0)
mark("act-taps")
for i in range(60): g.client.act(0, 0, 0, tap=((L4,) if i % 20 == 0 else (STOW,) if i % 20 == 10 else ()))
mark("act-hold-intake")
for _ in range(60): g.client.act(0, 0, 0, hold=(INTAKE,))
mark("done")
g.stop()
