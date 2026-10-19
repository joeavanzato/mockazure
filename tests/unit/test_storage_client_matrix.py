"""Storage qualification rejects missing ecosystems, phases and byte evidence."""

import copy
import hashlib

import pytest

from tools import verify_cloud_workflows as workflow


def test_storage_phase_matrix_is_explicit():
    assert "C" in workflow.cases(), "Storage matrix is missing"
    definition = workflow.cases()["C"]
    assert definition["notApplicable"] == {}
    assert all(set(row["ecosystems"]) == workflow.ECOSYSTEMS for row in definition["workflows"])


def test_storage_client_byte_evidence_is_required():
    from tools import verify_storage_binary as binary

    assert hasattr(binary, "validate_client_result"), "Native byte gate is missing"
    good = {
        "passed": True,
        "objects": {
            s: {
                "bytes": 3145745,
                "sha256": hashlib.sha256((bytes(range(256)) * 12289)[:3145745]).hexdigest(),
            }
            for s in ("blob", "file")
        },
    }
    binary.validate_client_result("python", good, 3145745)
    bad = copy.deepcopy(good)
    bad["objects"]["file"]["bytes"] = 0
    with pytest.raises(ValueError):
        binary.validate_client_result("python", bad, 3145745)
    with pytest.raises(ValueError):
        binary.validate_client_result("typescript", {"passed": True, "created": 2}, 3145745)
