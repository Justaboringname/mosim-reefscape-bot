"""Every net release in match logs: robot/ball state at the release frame and where the ball ended up.
usage: python -m reefbot.net_forensics <match json glob>..."""
import glob
import json
import math
import sys


def main():
    for pat in sys.argv[1:]:
        for p in sorted(glob.glob(pat)):
            d = json.load(open(p))
            rows = [r for r in (json.loads(l) for l in open(p.replace(".json", ".jsonl"))) if "robot" in r]
            for e in d["events"]:
                if not e[2].startswith("net:"):
                    continue
                t_end = e[0]
                # release frame: last frame (before the event) where the ball was still held
                cand = [i for i, r in enumerate(rows) if r["gameTime"] <= t_end and any(a[3] for a in r["algae"])]
                if not cand:
                    continue
                i = cand[-1]
                r = rows[i]
                held = [a for a in r["algae"] if a[3]][0]
                hid = r["algae"].index(held)
                ro = r["robot"]
                prev = rows[max(0, i - 6)]["robot"]
                mv = math.hypot(ro["x"] - prev["x"], ro["z"] - prev["z"])
                dy = abs((ro["yaw"] - prev["yaw"] + 180) % 360 - 180)
                traj = []
                for k in (6, 12, 20, 33):
                    if i + k < len(rows) and hid < len(rows[i + k]["algae"]):
                        a = rows[i + k]["algae"][hid]
                        traj.append((round(a[0], 2), round(a[1], 2), round(a[2], 2)))
                print(f"{p.split('/')[-1]:18s} t={e[1]:6.1f} {e[2]:20s} rob=({ro['x']:.2f},{ro['z']:.2f},{ro['yaw']:.0f}) "
                      f"v=({ro['vx']:.2f},{ro['vz']:.2f}) mv0.27s={mv:.3f} dyaw={dy:.1f} elev={r['mech'].get('elev', 0):.1f} "
                      f"ball=({held[0]:.2f},{held[1]:.2f},{held[2]:.3f}) traj={traj}")


if __name__ == "__main__":
    main()
