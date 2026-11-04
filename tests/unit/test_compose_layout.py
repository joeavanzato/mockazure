"""Auxiliary stacks must retain their repository-relative host state after relocation."""

from pathlib import Path

import pytest
import yaml

from tools.qualification_profile import STAGES, QualificationProfile

ROOT = Path(__file__).resolve().parents[2]


def test_only_default_application_and_test_compose_remain_at_root():
    assert {p.name for p in ROOT.glob("compose*.yaml")} == {"compose.yaml", "compose.test.yaml"}


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("release", [False, True])
def test_qualification_profile_paths_select_existing_application_and_client_stacks(stage, release):
    profile = QualificationProfile(stage, release)
    for path in (profile.compose, profile.clients_compose):
        assert (ROOT / path).is_file()
    for prefix, expected in [
        ("compose.", profile.compose),
        ("compose.clients.", profile.clients_compose),
    ]:
        template = f"deploy/compose/milestones/{prefix}{stage}.yaml"
        assert profile.text(template) == expected


def test_auxiliary_bind_mounts_and_secret_files_stay_in_repository_runtime_locations():
    configs = list((ROOT / "deploy/compose").rglob("*.yaml"))
    assert len(configs) == 27
    for path in configs:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        sources = [secret["file"] for secret in config.get("secrets", {}).values()]
        for service in config["services"].values():
            for volume in service.get("volumes", []):
                if isinstance(volume, str) and volume.startswith("."):
                    sources.append(volume.split(":", 1)[0])
                elif isinstance(volume, dict) and volume.get("type") == "bind":
                    sources.append(volume["source"])
        assert sources, path
        for source in sources:
            resolved = (path.parent / source).resolve()
            relative = resolved.relative_to(ROOT)
            assert relative.parts[0] in {".runtime", "deploy", "integrations", "work"}, (
                path,
                source,
            )
