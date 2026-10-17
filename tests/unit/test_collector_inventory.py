import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(not shutil.which("pwsh"), reason="PowerShell AST parser not installed")
def test_inventory_preserves_unmapped_named_commands_and_dynamic_locations(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "collector.ps1").write_text(
        "Get-SweepRule -Mailbox test\nGet-RemoteDomain\nGet-TransportConfig\n"
        "Get-FutureEvidenceProvider\n& $dynamicCommand\n",
        encoding="utf-8",
    )
    output = tmp_path / "inventory.json"
    subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-File",
            str(ROOT / "integrations/powershell/Get-CollectorInventory.ps1"),
            "-SourceRoot",
            str(source),
            "-OutputPath",
            str(output),
        ],
        check=True,
    )
    file = json.loads(output.read_text(encoding="utf-8-sig"))["files"][0]
    assert [c["command"] for c in file["calls"]] == [
        "Get-SweepRule",
        "Get-RemoteDomain",
        "Get-TransportConfig",
        "Get-FutureEvidenceProvider",
    ]
    assert file["dynamic_command_lines"] == [5]
