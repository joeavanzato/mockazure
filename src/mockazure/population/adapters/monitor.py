"""Workspace, diagnostic configuration and typed log rows."""

from mockazure.monitor.diagnostic_models import DiagnosticSetting
from mockazure.monitor.log_models import LogRecord
from mockazure.monitor.workspaces import Workspace
from mockazure.population.adapters.browsing import browser


def adapters():
    w, d, r = Workspace, DiagnosticSetting, LogRecord
    return [
        browser(
            "monitor.workspaces",
            "Logs and diagnostics",
            "/subscriptions/{subscription}/resourceGroups/{group}/providers/Microsoft.Operation"
            "alInsights/workspaces/{workspace}",
            w,
            w.customer_id,
            w.scope_key,
            {"id": w.customer_id, "resourceId": w.scope_key},
        ),
        browser(
            "monitor.diagnostics",
            "Logs and diagnostics",
            "/{resource}/providers/Microsoft.Insights/diagnosticSettings/{name}",
            d,
            d.id,
            d.updated_at,
            {"id": d.id, "name": d.name, "properties": d.properties},
            parent=d.resource_key,
            conditions=(d.deleted.is_(False),),
        ),
        browser(
            "monitor.records",
            "Logs and diagnostics",
            "/v1/workspaces/{workspace}/query",
            r,
            r.id,
            r.time_generated,
            {
                "id": r.id,
                "table": r.table_name,
                "sourceId": r.source_id,
                "TimeGenerated": r.time_generated,
            },
            parent=r.workspace_id,
            payload=None,
        ),
    ]
