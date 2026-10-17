"""Actual shipped resources and the qualification guard invalidate every attestation."""

import importlib
from pathlib import Path

import pytest

PATHS = (
    "src/mockazure/providers/arm/data/role-sources/general.html",
    "src/mockazure/providers/graph/schemas/LICENSE.metadata",
    "tests/support/network_guard/sitecustomize.py",
)


@pytest.mark.parametrize(
    "module",
    [
        "release_report",
        "verify_identity",
        "verify_graph",
        "verify_evidence",
        "verify_security",
        "verify_mail",
        "verify_arm",
    ],
)
@pytest.mark.parametrize("relative", PATHS)
def test_current_input_change_invalidates_every_fingerprint(monkeypatch, module, relative):
    target = Path(relative).resolve()
    original = Path.read_bytes
    reader = importlib.import_module("tools." + module).source_fingerprint
    baseline = reader()

    def changed(path):
        value = original(path)
        return value + b"\nchanged qualification input\n" if path.resolve() == target else value

    monkeypatch.setattr(Path, "read_bytes", changed)
    assert reader() != baseline, (module, relative)
