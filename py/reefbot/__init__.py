"""REEFSCAPE (FRC 2025) bot for MoSim: Madtown 1323, planned from first principles (no demos)."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]          # repository root (this file is py/reefbot/__init__.py)
RUNS = REPO / "runs" / "reef"                       # match logs, drills, batch results (git-ignored)
FIELD_JSON = REPO / "data" / "field.json"           # field geometry extracted from the game scene
RUNS.mkdir(parents=True, exist_ok=True)
