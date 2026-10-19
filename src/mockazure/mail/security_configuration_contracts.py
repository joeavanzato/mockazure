"""Pinned, offline Exchange cloud parameter allowlist."""

import json
from pathlib import Path

PARAMETERS = json.loads(
    (Path(__file__).parent / "data/security_configuration_parameters.json").read_text(
        encoding="utf-8"
    )
)
