"""Run pinned collector functions and verify their original on-disk output."""

import csv
import json
import ssl
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.qualification_profile import profile_for  # noqa: E402

PROFILE = profile_for("m3c")
RUNTIME = ROOT / PROFILE.text(".runtime/m3c")
FUNCTIONS = {
    "hawk_credentials": ("hawk", "Get-HawkTenantAppAndSPNCredentialDetail"),
    "mes_policies": ("mes", "Get-ConditionalAccessPolicies"),
    "mes_grants": ("mes", "Get-OAuthPermissionsGraph"),
    "mes_methods": ("mes", "Get-MFA"),
    "mes_roles": ("mes", "Get-AllRoleActivity"),
}


def rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def verify_denied_rows(label, mode, records, username):
    if not records:
        return
    assert label == "mes_methods" and mode == "delegated"
    flags = [
        "email",
        "fido2",
        "app",
        "password",
        "phone",
        "softwareoath",
        "hellobusiness",
        "temporaryAccessPass",
        "certificateBasedAuthConfiguration",
        "qrcodepin",
    ]
    assert records == [{"user": username, "MFAstatus": "Disabled", **dict.fromkeys(flags, "False")}]


def verify_denied_outputs(label, mode, files, username):
    records = [row for path in files if path.suffix.lower() == ".csv" for row in rows(path)]
    verify_denied_rows(label, mode, records, username)
    for path in files:
        if path.suffix.lower() != ".json":
            continue
        content = path.read_text(encoding="utf-8-sig").strip()
        if not content:
            continue
        value = json.loads(content)
        assert value is None or value == [], f"Denied collector emitted JSON records: {path.name}"


def qualify_collectors():
    from verify_security import sha

    state = RUNTIME / "clients/powershell-state"
    config = json.loads((state / "client.json").read_text())
    token = json.loads((state / "application-token.json").read_text())["access_token"]
    with httpx.Client(
        base_url=PROFILE.text("https://localhost:32443"),
        trust_env=False,
        verify=ssl.create_default_context(cafile=str(RUNTIME / "instance/ca.pem")),
        headers={"Authorization": "Bearer " + token},
        timeout=60,
    ) as client:

        def get(path):
            response = client.get("/v1.0/" + path)
            response.raise_for_status()
            return response.json()

        apps = get("applications")["value"]
        principals = get("servicePrincipals")["value"]
        expected_credentials = {
            collection: {
                (obj["id"], cred["keyId"])
                for obj in values
                for field in ("keyCredentials", "passwordCredentials")
                for cred in obj.get(field, [])
            }
            for collection, values in (("Application", apps), ("SPN", principals))
        }
        expected_policy = get("identity/conditionalAccess/policies/" + config["policy_id"])
        expected_user = get(
            "users/"
            + config["user_id"]
            + "?$select=id,userPrincipalName,displayName,signInActivity"
        )
    stamp = str(time.time_ns())
    cases = []
    for mode in ("application", "delegated"):
        for label, (collector, function) in FUNCTIONS.items():
            for denied in (False, True):
                name = f"{stamp}-{mode}-{label}" + ("-denied" if denied else "")
                case = {
                    "mode": mode,
                    "collector": collector,
                    "function": function,
                    "denial": "permission" if denied else None,
                    "start": "2026-10-01",
                    "end": "2026-10-31",
                    "output_dir": "/state/collectors/" + name,
                    "report": "/state/" + name + ".json",
                }
                path = state / (name + "-case.json")
                path.write_text(json.dumps(case))
                cases.append((label, mode, denied, name, path))
    batch = state / ("collectors-" + stamp + ".ps1")
    batch.write_text(
        "$ErrorActionPreference='Stop'\n"
        + "\n".join(
            "try { & /project/integrations/powershell/Test-SecurityCollectors.ps1 "
            f"-CasePath /state/{path.name} }} catch {{ Write-Host 'Case recorded: {name}' }}"
            for _, _, _, name, path in cases
        )
        + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            PROFILE.text("compose.clients.m3c.yaml"),
            "run",
            "--rm",
            "powershell",
            "/state/" + batch.name,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
    )
    (ROOT / "work/security-collectors.log").write_text(
        result.stdout + result.stderr, encoding="utf-8"
    )
    assert result.returncode == 0, "Collector batch failed; inspect local log"
    output = {
        name: {
            "passed": False,
            "records_verified": 0,
            "source_hashes": {},
            "output_hashes": {},
            "cases": [],
        }
        for name in FUNCTIONS
    }
    for label, mode, denied, name, _ in cases:
        report = json.loads((state / (name + ".json")).read_text(encoding="utf-8-sig"))
        folder = state / "collectors" / name
        files = list(folder.rglob("*.csv")) + list(folder.rglob("*.json"))
        records = [row for path in files if path.suffix == ".csv" for row in rows(path)]
        if denied:
            # Some original collectors log-and-continue; zero sensitive output is the gate.
            verify_denied_outputs(label, mode, files, expected_user["userPrincipalName"])
            logs = "\n".join(p.read_text(encoding="utf-8-sig") for p in folder.rglob("*.log"))
            assert report["error"] or any(
                word in logs for word in ("403", "denied", "Denied", "Forbidden")
            )
        else:
            assert report["error"] is None, f"{label}/{mode}: {report['error']}"
            assert records, f"{label}/{mode}: empty output"
            if label == "hawk_credentials":
                for collection, expected in expected_credentials.items():
                    assert expected
                    csv_rows = [
                        row
                        for p in files
                        if p.suffix == ".csv" and p.name.startswith(collection + "CertsAndSecrets")
                        for row in rows(p)
                    ]
                    assert {(r["AppObjectID"], r["KeyID"]) for r in csv_rows} == expected
                    jp = next(p for p in files if p.name == collection + "CertsAndSecrets.json")
                    data = json.loads(jp.read_text(encoding="utf-8-sig"))
                    if isinstance(data, dict):
                        data = [data]
                    assert {(r["AppObjectID"], r["KeyID"]) for r in data} == expected
                    assert all(r["CredType"] in {"PasswordSecret", "X509Certificate"} for r in data)
            elif label == "mes_policies":
                row = next(r for r in records if r["Id"] == expected_policy["id"])
                assert row["State"] == "disabled" and row["BuiltInControls"] == "mfa"
                assert row["DisplayName"] == expected_policy["displayName"]
            elif label == "mes_grants":
                # Bind both credential modes to actual configured principals and scopes.
                encoded = json.dumps(records)
                assert config["client_id"] in encoded and config["public_client_id"] in encoded
                assert "Application" in encoded and "Delegated" in encoded
                assert "User.Read.All" in encoded and "Organization.Read.All" in encoded
            elif label == "mes_methods":
                methods = [r for r in records if "MFAstatus" in r]
                registrations = [r for r in records if "methodsRegistered" in r]
                assert len(methods) == len(registrations) == 1
                method, registration = methods[0], registrations[0]
                assert method["user"] == expected_user["userPrincipalName"]
                assert method["MFAstatus"] == "Enabled"
                assert all(
                    method[field] == "True" for field in ("email", "app", "password", "phone")
                )
                assert registration["id"] == expected_user["id"]
                assert registration["isMfaRegistered"] == "True"
                assert registration["isMfaCapable"] == "False"
                assert set(registration["methodsRegistered"].split(", ")) == {
                    "email",
                    "mobilePhone",
                    "microsoftAuthenticatorPush",
                }
            else:
                encoded = json.dumps(records)
                assert (
                    expected_user["userPrincipalName"] in encoded
                    and "Global Administrator" in encoded
                )
                assert expected_user["id"] in encoded
                row = next(r for r in records if r["UserId"] == expected_user["id"])
                assert row["Role"] == "Global Administrator"
                assert row["UserName"] == expected_user["userPrincipalName"]
                assert row["DisplayName"] == expected_user["displayName"]
                assert row["AccountEnabled"] == "True"
                assert row["LastInteractiveSignIn"]
            output[label]["records_verified"] += len(records)
        item = output[label]
        item["cases"].append({"mode": mode, "denied": denied, "passed": True})
        for path, digest in report["source_hashes"].items():
            relative = path.removeprefix("/vendor/")
            assert digest == sha(ROOT / "work/vendor" / relative)
            item["source_hashes"][relative] = digest
        for path in files:
            text = path.read_text(encoding="utf-8-sig")
            for secret_file in ("secret.json", "negative-secret.json"):
                secret = json.loads((state / secret_file).read_text())["secret"]
                assert secret not in text
            item["output_hashes"][path.relative_to(RUNTIME).as_posix()] = sha(path)
    for item in output.values():
        item["passed"] = True
    return output


if __name__ == "__main__":
    report = qualify_collectors()
    (ROOT / "work/security-collector-results.json").write_text(json.dumps(report, indent=2))
    print("Original security collector output gates passed")
