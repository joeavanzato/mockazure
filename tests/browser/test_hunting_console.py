"""Real browser requests through a CA-validating loopback bridge."""

import asyncio
import os
import ssl
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

from mockazure.config import load_settings


def test_hunting_console_named_identity_types_and_html_escaping(tmp_path):
    config = os.environ.get("MOCKAZURE_BROWSER_CONFIG")
    if not config:
        pytest.skip("Requires the owned native qualification instance")
    settings = load_settings(Path(config))
    origin = str(settings.admin_base_url).rstrip("/")
    tid = os.environ["MOCKAZURE_BROWSER_TENANT_ID"]
    policy = asyncio.get_event_loop_policy()
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    try:
        with (
            sync_playwright() as playwright,
            httpx.Client(
                verify=ssl.create_default_context(cafile=str(settings.ca_file)),
                trust_env=False,
                follow_redirects=False,
                timeout=60,
            ) as http,
        ):
            admin_headers = {"Authorization": "Bearer " + settings.token_file.read_text().strip()}
            clock = http.get(
                origin + f"/_mock/tenants/{tid}/evidence/settings", headers=admin_headers
            ).json()
            timestamp = clock.get("fixed_time") or datetime.now(UTC).isoformat()
            label = '<img src=x onerror="window.huntingXss=true">'
            inserted = http.post(
                origin + f"/_mock/tenants/{tid}/hunting/tables/DeviceInfo/import",
                headers=admin_headers,
                json={
                    "source_ids": ["browser-" + uuid4().hex for _ in range(205)],
                    "rows": [
                        {
                            "Timestamp": timestamp,
                            "DeviceId": "browser-device-" + str(i),
                            "DeviceName": label,
                        }
                        for i in range(205)
                    ],
                },
            )
            assert inserted.status_code == 200
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={"width": 1500, "height": 1000})

            def bridge(route):
                request = route.request
                if urlsplit(request.url).netloc != urlsplit(origin).netloc:
                    route.abort()
                    return
                headers = {
                    k: v
                    for k, v in request.all_headers().items()
                    if k.lower() not in {"host", "content-length", "accept-encoding"}
                }
                response = http.request(
                    request.method,
                    request.url,
                    headers=headers,
                    content=request.post_data_buffer,
                )
                route.fulfill(
                    status=response.status_code,
                    headers=dict(response.headers),
                    body=response.content,
                )

            context.route("**/*", bridge)
            page, errors = context.new_page(), []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(origin + "/_mock/ui/")
            page.get_by_label("Shared lab password").fill(
                (settings.data_dir / "browser-test.password").read_text().strip()
            )
            page.get_by_role("button", name="Sign in", exact=True).click()
            expect(page.get_by_label("Tenant", exact=True)).to_be_visible()
            page.get_by_label("Tenant", exact=True).select_option(tid)
            page.get_by_role("button", name="Hunt evidence", exact=True).click()
            identity = page.get_by_role("combobox", name="Test identity", exact=True)
            expect(identity).to_be_visible()
            allowed = identity.locator("option").evaluate_all(
                "nodes => nodes.map(n => n.value).find(v => v.startsWith('hunting-'))"
            )
            denied = identity.locator("option").evaluate_all(
                "nodes => nodes.map(n => n.value).find(v => v.startsWith('lab-'))"
            )
            assert allowed and denied
            identity.select_option(allowed)
            page.get_by_label("Hunting query", exact=True).fill(
                "DeviceInfo | where DeviceName contains 'onerror' | project DeviceName"
            )
            page.get_by_role("button", name="Run query", exact=True).click()
            results = page.get_by_role("region", name="Query results")
            expect(results).to_contain_text(label)
            assert results.locator("img").count() == 0
            assert page.evaluate("window.huntingXss === undefined")
            expect(page.get_by_text("Evidence reference", exact=False)).to_be_visible()
            page.get_by_text("Result column types", exact=True).click()
            expect(results).to_contain_text("String")
            expect(page.get_by_role("button", name="Download JSON", exact=True)).to_be_enabled()
            page.screenshot(path=str(tmp_path / "hunting-console.png"), full_page=True)
            with page.expect_download() as saved:
                page.get_by_role("button", name="Download JSON", exact=True).click()
            import json

            exported = json.loads(Path(saved.value.path()).read_text())
            assert len(exported["results"]) >= 205 and label in str(exported["results"])
            assert results.locator("table").first.locator("tbody tr").count() == 200
            identity.select_option(denied)
            expect(page.get_by_role("button", name="Download JSON", exact=True)).to_be_disabled()
            page.get_by_role("button", name="Run query", exact=True).click()
            expect(results.get_by_role("alert")).to_contain_text("403")
            assert page.get_by_label("Shared lab password").is_hidden()
            assert not errors
            browser.close()
    finally:
        asyncio.set_event_loop_policy(policy)
