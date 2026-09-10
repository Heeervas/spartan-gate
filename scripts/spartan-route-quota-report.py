#!/usr/bin/env python3
"""Read-only model-isolated Codex quota calibration."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra/spartan-route"))
from spartan_route.quota_report import main, quota_report, timestamp

if __name__ == "__main__":
    main()
