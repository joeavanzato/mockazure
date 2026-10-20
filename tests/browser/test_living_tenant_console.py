"""Native HTTPS browser acceptance for activity controls and related records."""

import asyncio
import json
import os
import re
import ssl
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

from mockazure.config import load_settings


def test_finite_activity_and_related_navigation(tmp_path):
    config = os.environ.get("MOCKAZURE_BROWSER_CONFIG")
    if not config:
        pytest.skip("Requires the isolated native browser qualification instance")
    settings = load_settings(Path(config))
    origin = str(settings.admin_base_url).rstrip("/")
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
                timeout=120,
            ) as http,
        ):
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={"width": 1440, "height": 1000})

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
                    request.method, request.url, headers=headers, content=request.post_data_buffer
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
            tenant = page.get_by_label("Tenant", exact=True)
            expect(tenant).to_be_visible()
            tenant.select_option(os.environ["MOCKAZURE_BROWSER_TENANT_ID"])
            page.get_by_role("button", name="Profiles", exact=False).click()
            page.get_by_label("Template", exact=True).select_option("living-department")
            expect(page.get_by_label("Users", exact=True)).to_have_value("250")
            expect(page.get_by_label("Days of activity")).to_have_value("14")
            assert page.locator(".layer-list li").count() == 7
            page.get_by_text("Profile source, import & export", exact=True).click()
            editor = page.get_by_label("Bundle JSON", exact=True)
            source = json.loads(editor.input_value())
            source["documents"]["developer-lab"]["dataProfiles"] = ["exchange-mail-and-audits"]
            layer = source["documents"]["exchange-mail-and-audits"]
            layer["steps"] = {
                k: v
                for k, v in layer["steps"].items()
                if k in {"mail-settings", "mailbox", "mail-folders", "calendar"}
            }
            editor.fill(json.dumps(source))
            page.get_by_role("button", name="Load editor changes", exact=True).click()
            page.get_by_label("Template", exact=True).select_option("developer-lab")
            page.get_by_label("Users", exact=True).fill("2")
            page.get_by_label("Dataset namespace").fill("browser-activity-" + uuid4().hex[:8])
            page.get_by_label("Run a finite activity batch").check()
            page.get_by_label("Activity recipe step").fill("calendar")
            page.get_by_label("Action count").fill("2")
            page.get_by_label("Pacing (milliseconds)").fill("60000")
            page.get_by_role("button", name="Preview population", exact=True).click()
            expect(page.get_by_role("heading", name="Review the resolved plan")).to_be_visible(
                timeout=30000
            )
            page.get_by_role("button", name="Apply this preview", exact=True).click()
            expect(page.locator(".activity-progress")).to_contain_text("1 / 2", timeout=90000)
            page.get_by_role("button", name="Cancel run", exact=True).click()
            expect(page.locator(".badge.cancelled")).to_be_visible()
            expect(page.locator(".activity-progress")).to_contain_text("1 / 2")
            page.get_by_role("button", name="Resume and recheck", exact=True).click()
            expect(page.get_by_role("heading", name="Population complete")).to_be_visible(
                timeout=90000
            )
            expect(page.locator(".activity-progress")).to_contain_text("2 / 2")
            expect(page.locator(".activity-progress")).to_contain_text("Data reference")
            page.screenshot(path=str(tmp_path / "finite-activity.png"), full_page=True)
            page.get_by_role("button", name="Resources", exact=False).click()
            page.get_by_label("Resource family").select_option("sharepoint.drive")
            page.get_by_role("button", name="Inspect", exact=True).first.click()
            modal = page.get_by_role("dialog")
            expect(modal.get_by_role("heading", name="Related resources")).to_be_visible()
            modal.get_by_role("button", name="Inspect", exact=True).first.click()
            expect(modal.locator("pre")).to_contain_text(re.compile(r'"id":'))
            modal.get_by_role("button", name="Close", exact=True).click()
            page.get_by_label("Resource family").select_option("mail.events")
            page.get_by_role("button", name="Add data", exact=True).click()
            modal.get_by_label("Operation", exact=True).select_option("create")
            expect(modal.get_by_text(re.compile("Public API action using"))).to_be_visible()
            modal.get_by_label("Operation", exact=True).select_option("import")
            expect(
                modal.get_by_text(re.compile("Administrative fixture operation"))
            ).to_be_visible()
            assert not errors
            browser.close()
    finally:
        asyncio.set_event_loop_policy(policy)
