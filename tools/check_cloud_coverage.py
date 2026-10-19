"""Check explicit cloud coverage declarations against contracts and behavior evidence."""

import argparse
import ast
import hashlib
import json
import re
from pathlib import Path

from starlette.routing import Match

from mockazure.cloud.contracts import _catalog, verify_references

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ("exchange", "arm-providers", "storage", "keyvault", "graph-workflows", "queries")
START = "<!-- cloud-operation-index:start -->"
END = "<!-- cloud-operation-index:end -->"


def validate_entry(entry, *, implemented, test_exists, qualified_operations):
    status = entry.get("status")
    if status not in {"planned", "unsupported", "implemented", "qualified"}:
        raise ValueError("Unknown coverage status")
    if status in {"planned", "unsupported"}:
        if entry.get("qualification"):
            raise ValueError("An unavailable operation cannot have qualification evidence")
        return
    if not implemented or not entry.get("handler"):
        raise ValueError("Supported operation has no declared implementation")
    if not entry.get("tests") or not all(test_exists(p) for p in entry["tests"]):
        raise ValueError("Supported operation lacks behavior test evidence")
    if status == "qualified" and (
        not entry.get("qualification") or entry["operationId"] not in qualified_operations
    ):
        raise ValueError("Qualified operation lacks source-matched native behavior evidence")


def test_exists(value):
    name, _, symbol = value.partition("::")
    path = (ROOT / name).resolve()
    if not path.is_relative_to(ROOT / "tests") or not path.is_file():
        return False
    return not symbol or any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
    )


def contract_hash(row):
    return hashlib.sha256(
        json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def implemented(row, entry, app):
    handler = (ROOT / entry["handler"]).resolve()
    if not handler.is_relative_to(ROOT / "src/mockazure") or not handler.is_file():
        return False
    if entry["service"] == "exchange":
        from mockazure.providers.exchange.bootstrap import PARAMETERS

        if row["command"] in PARAMETERS:
            return True
        compiled = {
            "Get-EXORecipient": "/adminapi/beta/tenant/Recipient",
            "Get-EXORecipientPermission": "/adminapi/beta/tenant/Recipient('key')",
            "Get-EXOMailboxPermission": "/adminapi/beta/tenant/Mailbox('key')/MailboxPermission",
        }
        path = compiled.get(row["command"])
        return path is not None and route_exists(app, "GET", path)
    if entry["service"] in {"storage", "keyvault"} or row["operationId"].startswith(
        "loganalytics."
    ):
        # Dedicated origins dispatch through middleware rather than FastAPI routes.
        # This only detects a removed dispatcher; tests remain mandatory evidence.
        return any(
            isinstance(n, ast.AsyncFunctionDef) and n.name == "handle"
            for n in ast.parse(handler.read_text(encoding="utf-8")).body
        )
    path = row["path"].split("?", 1)[0]
    if path == "{uploadUrl}":
        return route_exists(app, row["method"], "/_upload/mail/local-capability")
    path = path.replace(
        "{resourceUri}",
        "subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/local/providers/Microsoft.Storage/storageAccounts/local/blobServices/default",
    )
    path = re.sub(r"\{[^}]+\}", "00000000-0000-0000-0000-000000000001", path)
    if entry["service"] == "graph-workflows":
        path = "/v1.0" + path
    if path.lower().startswith("/subscriptions/"):
        path = re.sub(
            r"(?i)(?<=/)(subscriptions|tenants|resourcegroups|resources|providers)(?=/|$)",
            lambda m: m.group(0).lower(),
            path,
        )
        path = re.sub(
            r"(?i)/providers/microsoft\.insights/(diagnosticsettingscategories|diagnosticsettings)(?=/|$)",
            lambda m: m.group(0).lower(),
            path,
        )
        path = re.sub(
            r"(?i)/providers/microsoft\.storage/(storageaccounts|checknameavailability)(?=/|$)",
            lambda m: m.group(0).lower(),
            path,
        )
    return route_exists(app, row["method"], path)


def route_exists(app, method, path):
    scope = {"type": "http", "method": method, "path": path, "root_path": "", "app": app}
    return any(route.matches(scope)[0] == Match.FULL for route in app.routes)


def render(entries):
    lines = [
        START,
        "## Cloud workflow operation index",
        "",
        "This checked index expands the service-level tables above. **Implemented** means a "
        "bounded local profile with declared behavior tests. Workflow mappings identify "
        "representative client checks; they do not claim every operation/property is "
        "independently exercised by every SDK. `tools.check_cloud_coverage --check` rejects "
        "missing tests/dispatchers, stale contracts, and unsupported qualification claims. "
        "Passing this structural check is not a substitute for executing tests or clients.",
        "",
        "| Service / operation | Microsoft URI or command | API version | Support | "
        "Behavior evidence / client workflow | Limits |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for entry in entries:

        def clean(value):
            return str(value).replace("|", "&#124;").replace("\n", " ")

        tests = "<br>".join(f"[test]({p.partition('::')[0]})" for p in entry["tests"])
        workflows = (
            ", ".join(entry.get("workflowMapping", [])) or "No direct native workflow mapping"
        )
        lines.append(
            "| "
            + " | ".join(
                map(
                    clean,
                    (
                        f"{entry['service']} / `{entry['operationId']}`",
                        f"`{entry['uri']}`",
                        entry["version"],
                        entry["status"].capitalize(),
                        tests + "<br>" + workflows,
                        " ".join(entry["limitations"]),
                    ),
                )
            )
            + " |"
        )
    return "\n".join([*lines, "", END])


def check(report_path=None):
    from mockazure.admin.app import create_app
    from mockazure.config import Settings

    entries = json.loads(
        (ROOT / "integrations/cloud-coverage-index.json").read_text(encoding="utf-8")
    )["operations"]
    catalogs = {
        (service, r["operationId"]): r
        for service in SERVICES
        for r in _catalog(service)["operations"]
    }
    keys = [(e["service"], e["operationId"]) for e in entries]
    if len(keys) != len(set(keys)) or set(keys) != set(catalogs):
        raise ValueError("Coverage index does not match the complete operation catalog")
    qualified = {service: set() for service in SERVICES}
    if report_path:
        from tools.verify_cloud_workflows import ECOSYSTEMS, cases, source_identity, validate_report

        report = json.loads(Path(report_path).read_text(encoding="utf-8"))
        validate_report(report, ECOSYSTEMS)
        if (
            report.get("passed") is not True
            or (report["sourceCommit"], report["sourceFingerprint"]) != source_identity()
        ):
            raise ValueError("Qualification report is not current source")
        for phase in report.get("phases", [report]):
            for definition in cases()[phase["phase"]]["workflows"]:
                qualified[definition["service"]].update(definition["operationIds"])
    app = create_app(
        Settings(
            data_dir=ROOT / "work/coverage-check",
            database_url="postgresql+psycopg://127.0.0.1/mockazure",
        )
    )
    for entry in entries:
        row = catalogs[entry["service"], entry["operationId"]]
        if contract_hash(row) != entry["contractSha256"]:
            raise ValueError(
                "Coverage declaration needs review after a contract change: " + entry["operationId"]
            )
        validate_entry(
            entry,
            implemented=implemented(row, entry, app),
            test_exists=test_exists,
            qualified_operations=qualified[entry["service"]],
        )
    document = (ROOT / "COVERAGE.md").read_text(encoding="utf-8")
    if (
        document.count(START) != 1
        or document.count(END) != 1
        or document.split(START)[1].split(END)[0] != render(entries).split(START)[1].split(END)[0]
    ):
        raise ValueError("COVERAGE operation table differs from the explicit reviewed index")
    for service in SERVICES:
        verify_references(service)
    return {"operations": len(entries), "services": len(SERVICES), "passed": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    print(json.dumps(check(args.report)))


if __name__ == "__main__":
    main()
