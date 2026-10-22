# MoSim REEFSCAPE bot (team 1323 Madtown)

[中文](README.zh-CN.md)

A scripted bot that plays a full single-robot match of **REEFSCAPE (FRC 2025)** in [MoSimulator](https://store.steampowered.com/app/4398690/), driving the built-in **1323 Madtown** robot. No reinforcement learning and no recorded demos: every skill was designed from the game's mechanics, then tuned with in-game A/B batches. The score is blue minus red with no opponent on the field.

## At a glance

| | |
|---|---|
| Current mean | about **194–195** points (N = 40–80 matches per configuration) |
| Best match | **205** |
| Matches at 200 or more | about **1 in 4** |
| Typical "full" match | 5 auto L4 + full reef (36 coral) + 6 reef algae in the net + deep climb = 193, plus 1–3 floor algae or trough coral |
| Matches per batch | 4 instances × 20 matches in about 35 minutes |

Score progression (means of in-game batches; N in brackets):

| Version | Change | Mean |
|---|---|---|
| v35 | station intake, game AutoAlign placement | 168.8 (10) |
| v41 | parameter search (CMA-ES) on the teleop cycle | 179.5 (40) |
| v44–v46 | own placement controller ("arrive aligned, release on arrival"), ground intake, net fixes | 187.9 (30) |
| v48 | ground intake 2nd coral in auto, end-game floor algae | 188.7 (30) |
| v49 | auto preload on far face 4 from the front (the costliest slot to fill in teleop) | 189.1 (20) |
| v50–v52 | reef-corner avoidance, earlier turn on net trips, clock-aware end game, coral-handoff deadlock escape | 193.7 (20) |
| v56 | deliver-leg heading law and a 0.25 m/s release gate: reef full ~1.5–2 s earlier | 194.2 (80) |
| v57 | optimistic floor-algae gate, pick time capped to protect the climb | 195.2 (40) |
| v58 | end-game L1 trough coral (no measurable gain: 194.1 vs 194.3 off) | 194.1 (40) |

Score SE at N = 20 is about 1 point, so single-batch differences under ~2 points are noise. Per-cycle segment times (`py/reefbot/cycle_report.py`) were used to judge small speed changes instead.

## How it plays

**Auto (15 s):** preload on far reef face 4 from the front (the robot starts facing the reef), then the 2nd coral by ground intake, the three staged coral picked standing with the end effector, and a 6th by ground intake if time allows. Usually 5 L4 in auto.

**Teleop coral cycle (~2.9 s):**
- *Fetch:* ground intake of the cheapest lying coral (the stations keep ground-dropping coral). After a 0.75 m line-up along the coral's axis the intake meets it at about 1.2 m/s (`GROUND_V_TOUCH`; `GROUND_FAST=0` gives the slower stop-at-touch law with a 5 cm push). A coral is skipped when the robot cannot line up on it inside the field or another coral lies in its approach corridor; a coral under the chassis is shed before the next pickup.
- *Deliver:* an A* path around the reef (the reef is a disc plus six corner discs, so the robot hugs the faces without clipping the corners), heading converged during the drive, the level pressed as soon as the coral is in the end effector, and a feedforward stop at the branch pose. Release when within 2.5 cm, 1.5° and 0.25 m/s with the elevator settled. Back placement for L2/L3, front L4 on the far faces.
- *Order:* greedy same-tier slot choice (L4 first), each face's algae removed when its L3s are needed.

**Algae:** removed from the front only (back-removed algae seat deep in the claw and fly backwards), carried to the net with the arm raised early, a diagonal final metre (straight-in approaches stalled on the rim), z error closed before entering the cage zone, release only when the ball sits low and the robot is still.

**End game:** after the reef is full, floor algae (net) when the trip can fit, else an L1 trough coral. The floor-algae pick and the net attempt are capped at the climb reserve; the trough cycle is only started when its time estimate fits. Deep climb with slip detection and retry.

The user's own technique shaped most of this: *arrive with the heading and elevator already right so no alignment time is spent; drive close along the walls and the reef; turn while driving; in auto, take the 2nd coral from the ground and the staged ones standing with the end effector.*

## What did not help

- **Processor:** +6 for blue, but the processor launches the algae into the red net (+4 red), net +2 < +4 for a net shot.
- **Parameter search (CMA-ES)** gave small gains (+1.5 to +2) in two of three runs; the structural changes above mattered far more.
- **Placing the staged coral on far faces** (lost the 5th auto coral), **straight-in net approaches**, **real-clock pacing** of the lockstep bridge (collapses to ~1 fps), **releasing at 0.35 m/s** (1% misses, each a ~3 s re-placement).
- **End-game trough** works (front only, radius ≤ 1.40 m, ~90% in the drill) but did not move the mean.
- Remaining gap to a stable 200: a second floor algae needs ~3 s more than the end game usually has.

## Harness: getting into the game

- `tools/Injector` (Mono.Cecil) inserts one call into the constructor of a locally installed mod DLL; that call loads `harness/MoSimRL`. We used China Modpack's `Alphabots.dll`; another mod needs a different path and type name in `tools/install-hook.sh`. Undo with `tools/restore-hook.sh`.
- `ReefBridge.cs` is a lockstep TCP JSON bridge (default 10 physics steps = 45 ms per decision). State: robot pose and velocity, mechanism setpoint / elevator / coral and algae state, every coral and algae (position, held, scored, axis, instance id), reef bits, trough counts, score breakdowns. Actions: field-oriented velocity, angular rate, button taps and holds (`ReefButtons.cs` injects the robot's own input actions).
- `py/reefbot/gamectl.py` launches instances through the Steam stub. Since MoSim 26.4.2 the stub drops command-line arguments, so instances are configured through environment variables, start as small background windows, and the user's own display settings are restored afterwards.
- **No leaderboard uploads.** In a bot instance the harness sets the game's `wasCheated` flag every frame, so bot matches are never submitted. Normal human sessions are untouched.

## Repository layout

| Path | Contents |
|---|---|
| `py/reefbot/` | `bot.py` (state, drive law, obstacle set), `nav.py` (reef router, grid A*, lookahead), `geom.py` (field poses), `client.py` (bridge client, buttons), `skills.py` (intake, placement, net, trough), `matchbot.py` (match logic, auto routines, end game, climb), `planner.py` (experimental points-per-second planner, off by default), `gamectl.py` (launcher), `demo.py` (one visible match), drills (`drill_*.py`), calibration scripts (`calib*.py`), reports (`batch_report.py`, `cycle_report.py`), parameter search (`search_*.py`) |
| `harness/MoSimRL/` | In-game C#: `Probe.cs` (entry point, no-upload guard), `ReefBridge.cs`, `ReefButtons.cs`, `Compat.cs`, `WindowPin.cs` |
| `tools/` | `Injector/` (Cecil hook), `install-hook.sh`, `restore-hook.sh` |
| `data/field.json` | Field geometry extracted from the game scene (reef faces, stations, cages) |

## Running it

You need macOS, the Steam build of MoSimulator 26.5.x, a locally installed mod to hook, the .NET 9 SDK (or newer), and Python 3.11. Set `MOSIMRL_BUSY_PROCS` (a `pgrep` pattern) if bot launches should wait while some other program runs.

```bash
# while the game is running, copy its Managed folder (the app on disk is only a stub):
#   .../MoSimulator.app/Contents/Resources/Data/Managed  ->  ./managed
dotnet build harness/MoSimRL -c Release          # -> harness/bin/MoSimRL.dll
tools/install-hook.sh                             # quit the game first; undo with tools/restore-hook.sh
mkdir -p run && touch run/ENABLE
cd py && python -m reefbot.matchbot 47601 20 mytag   # 20 matches, logs in <repo>/runs/reef/
python -m reefbot.demo 47601 2560x1440               # one visible real-time match
```

## Status and caveats

- The default configuration is v58 (`AUTO_VARIANT=g2_hyb4`). Most behaviour switches are environment variables (see `os.environ.get` in `bot.py`, `skills.py` and `matchbot.py`); tuned continuous parameters can be overridden with `PARAMS_JSON`. Comments next to most defaults record the A/B that chose them.
- Known bridge quirk: the order of the two troughs in the `trough` state list can swap between episodes (use the sum). The ~90% trough figure was re-counted from the drill logs with the sum.
- For this release the in-game entry point was trimmed to the Reefscape bridge, and the run-directory and Python paths were made repository-relative. The harness compiles, but these release changes were not re-run in the game.

## Disclaimer

- MoSimulator belongs to its developers. This project is not affiliated with them and contains no game code or assets.
- A personal research project under the MIT license.
- The code and documents were written by Claude (Anthropic) under the user's direction.
