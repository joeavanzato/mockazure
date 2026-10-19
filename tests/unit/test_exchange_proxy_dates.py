"""Execute the production proxy date conversion using native PowerShell."""

import json
import re
import shutil
import subprocess

import pytest

from mockazure.providers.exchange.bootstrap import fixed_proxy_source


def test_search_proxy_unspecified_dates_are_utc():
    executable = shutil.which("pwsh")
    if executable is None:
        pytest.skip("Native PowerShell is separately required by the client qualification gate")
    endpoint = "https://localhost/adminapi/v2.0/00000000-0000-0000-0000-000000000001/InvokeCommand"
    source = fixed_proxy_source(("Search-UnifiedAuditLog",), endpoint)
    block = re.search(r"if \(\$value -is \[datetime\]\).*?(?=\n\s*\$parameters)", source, re.S)
    assert block is not None
    script = (
        "$value=[datetime]::new(2026,10,18,12,0,0,[DateTimeKind]::Unspecified);"
        + block.group()
        + ";@{value=$value;offset=[TimeZoneInfo]::Local.GetUtcOffset("
        "[datetime]::new(2026,10,18,12,0,0)).TotalHours} | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        [executable, "-NoLogo", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    observed = json.loads(result.stdout)
    if observed["offset"] == 0:
        pytest.skip("Non-UTC fixture is exercised by the required Docker qualification")
    assert observed["value"].startswith("2026-10-18T12:00:00")
