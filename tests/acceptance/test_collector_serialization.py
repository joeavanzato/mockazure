"""Execute the actual pinned collector serializers on local synthetic values."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HAWK = (
    "collectors/hawk/hawk-a44536f2b4225dd8ea2e463db88d5db40cfac8c7/"
    "Hawk/internal/functions/Out-MultipleFileType.ps1"
)
MES = (
    "collectors/Microsoft-Extractor-Suite/"
    "Microsoft-Extractor-Suite-47565f7842784cd3667ff630e3dccb49fa3408ed/"
    "Scripts/Get-MessageTraceLog.ps1"
)


def source(relative):
    profile = os.environ.get("MOCKAZURE_SERIALIZER_TEST_PROFILE", "serialization-v1")
    if profile == "original":
        return ROOT / "work/vendor" / relative
    from tools.collector_profiles import prepare_profile

    return prepare_profile(ROOT, profile) / relative


def powershell(tmp_path, script):
    exe = shutil.which("pwsh")
    assert exe, "Install the pinned local PowerShell runtime before qualification"
    path = tmp_path / "probe.ps1"
    path.write_text("$ErrorActionPreference='Stop'\n" + script, encoding="utf-8")
    result = subprocess.run(
        [exe, "-NoLogo", "-NoProfile", "-File", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_hawk_append_is_one_document_and_preserves_nested_records(tmp_path):
    path = source(HAWK).as_posix().replace("'", "''")
    folder = tmp_path.as_posix().replace("'", "''")
    result = powershell(
        tmp_path,
        f"""
. '{path}'
function Out-LogFile {{ param($string,[switch]$Action,[switch]$NoDisplay,[switch]$silentnotice) }}
$global:Hawk=@{{filepath='{folder}'}}
foreach($i in 1..3){{
  [pscustomobject]@{{id="$i"; nested=@{{addresses=@("a$i@example.test","b$i@example.test")}}}} |
    Out-MultipleFileType -FilePrefix Rules -User 'user@example.test' -json -Append
}}
$p=Join-Path '{folder}' 'user@example.test/Rules_user.json'
$values=Get-Content $p -Raw | ConvertFrom-Json -ErrorAction Stop
@($values) | ConvertTo-Json -Depth 10 -Compress
""",
    )
    assert [r["id"] for r in result] == ["1", "2", "3"]
    assert result[1]["nested"]["addresses"] == ["a2@example.test", "b2@example.test"]


@pytest.mark.parametrize("culture", ["en-US", "nl-NL"])
def test_mes_csv_preserves_fractional_timestamp_without_mutating_pagination(tmp_path, culture):
    path = source(MES).as_posix().replace("'", "''")
    output = (tmp_path / "trace.csv").as_posix().replace("'", "''")
    result = powershell(
        tmp_path,
        f"""
[cultureinfo]::CurrentCulture=[cultureinfo]::GetCultureInfo('{culture}')
. '{path}'
function Write-LogFile {{ param($Message,$Level) }}
$script:Encoding='utf8'
$received=[DateTime]::Parse('2026-10-07T00:00:00.1234567Z',
    [cultureinfo]::InvariantCulture,[Globalization.DateTimeStyles]::RoundtripKind)
$script:row=[pscustomobject]@{{MessageTraceId='trace'
    RecipientAddress='recipient@example.test'; Received=$received; Status='Delivered'}}
function Get-MessageTraceV2 {{
    param($StartDate,$EndDate,$ResultSize,$StartingRecipientAddress)
    ,$script:row
}}
$params=@{{startDate=[DateTime]'2026-10-06'; endDate=[DateTime]'2026-10-08'
    OutputFile='{output}'}}
Retrieve-MessageTrace @params
$r=Import-Csv '{output}'
@{{received=$r.Received; still_datetime=($script:row.Received -is [DateTime])
    unchanged=($script:row.Received.Ticks -eq $received.Ticks)
    id=$r.MessageTraceId}} | ConvertTo-Json -Compress
""",
    )
    assert result["received"] == "2026-10-07T00:00:00.1234567Z"
    assert result["still_datetime"] and result["unchanged"] and result["id"] == "trace"
