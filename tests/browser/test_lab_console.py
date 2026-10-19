"""Real browser + local HTTPS API, with an isolated CA-verifying request bridge."""

import asyncio
import json
import os
import re
import ssl
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

from mockazure.config import load_settings


def test_console_login_and_resource_browser(tmp_path):
    config = os.environ.get("MOCKAZURE_BROWSER_CONFIG")
    if not config:
        pytest.skip("Set MOCKAZURE_BROWSER_CONFIG for the disposable local browser qualification")
    settings = load_settings(Path(config))
    origin = str(settings.admin_base_url).rstrip("/")
    password = (settings.data_dir / "browser-test.password").read_text().strip()
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
            ) as http,
        ):
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={"width": 1440, "height": 960})
            requests = []

            def local_request(route):
                # Pin the destination and validate the lab CA.
                # or installing a root certificate into the operating-system trust store.
                request = route.request
                if urlsplit(request.url).netloc != urlsplit(origin).netloc:
                    route.abort()
                    return
                requests.append(request.url)
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

            context.route("**/*", local_request)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(origin + "/_mock/ui/")
            expect(page.get_by_role("heading", name="Enter your lab")).to_be_visible()
            page.get_by_label("Shared lab password").fill(password)
            page.get_by_role("button", name="Sign in", exact=True).click()
            expect(page.get_by_label("Tenant", exact=True)).to_be_visible()
            page.get_by_label("Tenant", exact=True).select_option(
                label=os.environ.get("MOCKAZURE_BROWSER_TENANT", "Developer native lab")
            )
            selected_tenant = page.get_by_label("Tenant", exact=True).input_value()
            page.get_by_label("Resource family").select_option("directory.users")
            expect(page.get_by_role("table")).to_be_visible()
            assert len([url for url in requests if "resources/directory.users?" in url]) <= 2
            page.screenshot(path=str(tmp_path / "console-desktop.png"), full_page=True)
            page.set_viewport_size({"width": 900, "height": 780})
            page.screenshot(path=str(tmp_path / "console-narrow.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.get_by_role("button", name="Profiles", exact=False).click()
            page.get_by_label("Template", exact=True).select_option("developer-lab")
            first_layer = page.locator(".layer-list li code").first.inner_text()
            page.get_by_role("button", name="↓", exact=True).first.click()
            expect(page.locator(".layer-list li code").nth(1)).to_have_text(first_layer)
            page.get_by_role("button", name="↑", exact=True).nth(1).click()
            expect(page.locator(".layer-list li code").first).to_have_text(first_layer)
            page.get_by_text("Profile source, import & export", exact=True).click()
            page.get_by_role("button", name="Save revision", exact=True).click()
            saved = page.get_by_text(re.compile(r"Saved revision .* · ")).inner_text()
            bundle_id = saved.split(" · ")[-1]
            page.get_by_role("button", name="Resources", exact=False).click()
            page.get_by_role("button", name="Profiles", exact=False).click()
            page.get_by_label("Saved bundle", exact=True).select_option(bundle_id)
            page.get_by_role("button", name="Load bundle", exact=True).click()
            expect(page.get_by_text("Loaded revision 1 · " + bundle_id)).to_be_visible()
            page.get_by_label("Template", exact=True).select_option("volume-lab")
            page.get_by_label("Dataset namespace").fill("browser-" + uuid4().hex[:10])
            page.get_by_label("Users", exact=True).fill("60")
            page.get_by_label("Additional parameters JSON").fill(
                json.dumps({"volume-signins.records": 3})
            )
            page.get_by_role("button", name="Preview population", exact=True).click()
            expect(page.get_by_role("heading", name="Review the resolved plan")).to_be_visible(
                timeout=20000
            )
            page.screenshot(path=str(tmp_path / "console-profile.png"), full_page=True)
            page.get_by_role("button", name="Apply this preview", exact=True).click()
            expect(page.get_by_role("heading", name="Population complete")).to_be_visible(
                timeout=90000
            )
            page.get_by_role("button", name="Resources", exact=False).click()
            expect(page.get_by_role("table")).to_be_visible()
            expect(page.get_by_role("button", name="Next page", exact=True)).to_be_enabled()
            assert page.locator("tbody tr").count() == 50
            page.get_by_role("button", name="Next page", exact=True).click()
            expect(page.locator("tbody tr").first).to_be_visible()
            page.get_by_label("Resource family").select_option("directory.groups")
            page.get_by_role("button", name="Add data", exact=True).click()
            edit = page.get_by_role("dialog")
            edit.get_by_label("Resource JSON", exact=True).fill(
                json.dumps({"id": str(uuid4()), "madeUpGraphField": True})
            )
            edit.get_by_role("button", name="Preview change", exact=True).click()
            expect(edit.get_by_role("alert")).to_contain_text(
                "Resource does not match", timeout=10000
            )
            group_id = str(uuid4())
            group = {
                "id": group_id,
                "displayName": "<img src=x onerror=window.executed=true>",
                "mailNickname": "browser-" + uuid4().hex[:8],
                "mailEnabled": False,
                "securityEnabled": True,
                "groupTypes": [],
                "createdDateTime": "2026-10-07T00:00:00Z",
            }
            edit.get_by_label("Resource JSON", exact=True).fill(json.dumps(group))
            edit.get_by_role("button", name="Preview change", exact=True).click()
            expect(edit.get_by_role("button", name="Apply change", exact=True)).to_be_enabled()
            edit.get_by_role("button", name="Apply change", exact=True).click()
            expect(edit).not_to_be_visible()
            assert page.evaluate("window.executed") is None
            page.screenshot(path=str(tmp_path / "console-added-data.png"), full_page=True)
            page.get_by_role("button", name="Sign out", exact=True).click()
            expect(page.get_by_role("heading", name="Enter your lab")).to_be_visible()
            assert not errors
            browser.close()
    finally:
        asyncio.set_event_loop_policy(policy)

    from sqlalchemy import select

    from mockazure.asyncio_support import run
    from mockazure.db.engine import close_database, create_database
    from mockazure.population.identity import resolve_identity
    from mockazure.population.identity_models import LabIdentity
    from mockazure.population.transport import LocalTransport
    from mockazure.population.types import AdapterContext

    async def public_readback():
        database = create_database(settings)
        try:
            async with database.sessions() as session:
                binding = await session.scalar(
                    select(LabIdentity)
                    .where(LabIdentity.tenant_id == UUID(selected_tenant))
                    .order_by(LabIdentity.name)
                    .limit(1)
                )
            ctx = AdapterContext(database, settings, binding.tenant_id)
            response = await LocalTransport(ctx, await resolve_identity(ctx, binding.name)).request(
                "graph", "GET", "/v1.0/groups/" + group_id
            )
            assert response.status_code == 200, response.text
            assert response.json()["displayName"] == group["displayName"]
        finally:
            await close_database(database)

    run(public_readback())
