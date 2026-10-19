"""Every published distribution must retain offline contracts and workflow behavior."""

import json
from pathlib import Path


def test_cloud_package_parity():
    from tools.build_release import validate_cloud_distribution
    from tools.verify_cloud_workflows import source_identity

    path = Path("work/release/cloud-distribution.json")
    assert path.is_file(), "Build and exercise wheel, sdist and Docker cloud distributions first"
    validate_cloud_distribution(json.loads(path.read_text(encoding="utf-8")), *source_identity())
