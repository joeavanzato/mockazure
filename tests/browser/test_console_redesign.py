"""Connected UI checks against an explicitly configured disposable HTTPS instance."""

import asyncio
import os
import ssl
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

from mockazure.config import load_settings


@contextmanager
def console_browser():
    config = os.environ.get("MOCKAZURE_BROWSER_CONFIG")
    if not config:
        pytest.skip("Set MOCKAZURE_BROWSER_CONFIG to the task-owned disposable instance")
    settings = load_settings(Path(config))
    origin = str(settings.admin_base_url).rstrip("/")
    policy = asyncio.get_event_loop_policy()
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    try:
        with (
            sync_playwright() as p,
            httpx.Client(
                verify=ssl.create_default_context(cafile=str(settings.ca_file)),
                trust_env=False,
                follow_redirects=False,
                timeout=60,
            ) as http,
        ):
            admin = {"Authorization": "Bearer " + settings.token_file.read_text().strip()}
            tenants = http.get(origin + "/_mock/tenants?limit=100", headers=admin).json()["items"]
            tid = os.environ.get("MOCKAZURE_BROWSER_TENANT_ID", tenants[0]["id"])
            browser = p.chromium.launch()
            context = browser.new_context(
                viewport={"width": 1440, "height": 1000}, color_scheme="light"
            )

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
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(origin + "/_mock/ui/")
            page.get_by_label("Shared lab password").fill(
                (settings.data_dir / "browser-test.password").read_text().strip()
            )
            page.get_by_role("button", name="Sign in", exact=True).click()
            page.get_by_label("Tenant", exact=True).select_option(tid)
            yield page, context, http, origin, tid, admin, errors
            browser.close()
    finally:
        asyncio.set_event_loop_policy(policy)


def test_all_screens_themes_and_narrow_layout(tmp_path):
    with console_browser() as (page, context, http, origin, tid, admin, errors):
        screens = [
            ("Explore resources", "Explore your cloud."),
            ("API workbench", "Test the interaction."),
            ("Compose lab", "Build a useful starting point."),
            ("Population runs", "Follow every step."),
            ("Hunt evidence", "Follow the evidence."),
            ("Inspector", "Look under the surface."),
            ("Console admin", "Your console session."),
        ]
        for label, title in screens:
            page.get_by_role("button", name=label, exact=True).click()
            expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
            expect(page.locator("#notice.error")).to_have_count(0)
            slug = label.lower().replace(" ", "-")
            for theme, width in [
                ("light", 1440),
                ("dark", 390),
                ("light", 320),
                ("light", 900),
                ("dark", 375),
            ]:
                page.emulate_media(color_scheme=theme)
                page.set_viewport_size({"width": width, "height": 1000})
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
                    label,
                    width,
                )
                expect(
                    page.get_by_role("button", name="Sign out", exact=True).first
                ).to_be_visible()
                page.screenshot(path=str(tmp_path / f"{slug}-{theme}-{width}.png"), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 1000})
        assert not errors, errors


def test_explorer_detail_routes_and_composer_typing(tmp_path):
    with console_browser() as (page, context, http, origin, tid, admin, errors):
        expect(page.get_by_role("button", name="Inspect", exact=True).first).to_be_visible()
        opener = page.get_by_role("button", name="Inspect", exact=True).first
        opener.click()
        page.get_by_role("button", name="Close", exact=True).click()
        expect(opener).to_be_focused()
        opener.click()
        dialog = page.get_by_role("dialog")
        expect(dialog).to_be_visible()
        assert "record=" in page.url
        page.get_by_role("button", name="Raw JSON", exact=True).click()
        expect(dialog.locator("pre").last).to_be_visible()
        page.screenshot(path=str(tmp_path / "record-drawer.png"), full_page=True)
        page.reload()
        expect(page.get_by_role("dialog")).to_be_visible()
        page.get_by_role("button", name="Close", exact=True).click()
        expect(page.get_by_role("dialog")).not_to_be_visible()
        page.get_by_role("button", name="Compose lab", exact=True).click()
        page.get_by_role("button", name="Continue to configure", exact=True).click()
        value = "review-typing-123"
        page.get_by_label("Dataset namespace", exact=True).fill(value)
        page.get_by_label("Dataset namespace", exact=True).press("Tab")
        expect(page.get_by_label("Dataset namespace", exact=True)).to_have_value(value)
        page.get_by_role("button", name="1. Choose layers", exact=True).click()
        page.get_by_role("button", name="2. Configure", exact=True).click()
        expect(page.get_by_label("Dataset namespace", exact=True)).to_have_value(value)
        assert not errors, errors


def admin_fixture(http, origin, tid, admin, family, payload, operation="import"):
    base = origin + f"/_mock/tenants/{tid}/resources/{family}"
    prepared = http.post(
        base + "/preview",
        headers=admin,
        json={
            "operation": operation,
            "logical_key": str(__import__("uuid").uuid4()),
            "payload": payload,
        },
    )
    assert prepared.status_code == 200, prepared.text
    value = prepared.json()
    result = http.post(
        base + "/apply",
        headers=admin,
        json={
            "mutation": value["mutation"],
            "receipt": value["receipt"],
            "allow_replace": True,
        },
    )
    assert result.status_code == 200, result.text
    return result.json()


def open_workbench(page, tid, family="directory.users", mode="service"):
    page.evaluate(
        "(hash)=>{location.hash=hash}", f"#/workbench?tenant={tid}&family={family}&mode={mode}"
    )
    expect(page.get_by_role("heading", name="Test the interaction.")).to_be_visible()
    expect(page.get_by_label("Resource family", exact=True)).to_have_value(family)
    page.get_by_text("Advanced request JSON", exact=True).click()


def test_workbench_authorization_review_replacement_and_upload(tmp_path):
    import json
    from uuid import uuid4

    with console_browser() as (page, context, http, origin, tid, admin, errors):
        names = {role: "ui-" + role + "-" + uuid4().hex[:8] for role in ("reader", "writer")}
        for role, name in names.items():
            admin_fixture(
                http,
                origin,
                tid,
                admin,
                "identity.clients",
                {
                    "id": name,
                    "display_name": name,
                    "permissions": [
                        {
                            "resource": "graph",
                            "kind": "application",
                            "permissions": ["User.Read.All"]
                            + (["User.ReadWrite.All"] if role == "writer" else []),
                        }
                    ],
                },
                "provision",
            )
        domain = next(
            t["domain"]
            for t in http.get(origin + "/_mock/tenants?limit=100", headers=admin).json()["items"]
            if t["id"] == tid
        )
        user_id = str(uuid4())
        admin_fixture(
            http,
            origin,
            tid,
            admin,
            "directory.users",
            {
                "id": user_id,
                "displayName": "Before browser update",
                "mailNickname": "ui" + uuid4().hex[:8],
                "userPrincipalName": "ui" + uuid4().hex[:8] + "@" + domain,
                "createdDateTime": "2026-10-07T00:00:00Z",
            },
        )
        open_workbench(page, tid)
        identity = page.get_by_label("Test identity", exact=True)
        identity.select_option(names["reader"])
        page.get_by_label("Operation", exact=True).select_option("read-item")
        editor = page.get_by_label("Resource JSON", exact=True)
        editor.fill(json.dumps({"variables": {"id": user_id}}))
        page.get_by_role("button", name="Run request", exact=True).click()
        expect(page.get_by_text("HTTP 200", exact=False)).to_be_visible()
        page.get_by_label("Operation", exact=True).select_option("update")
        payload = {"variables": {"id": user_id}, "body": {"displayName": "After browser update"}}
        editor.fill(json.dumps(payload))
        page.get_by_role("button", name="Preview change", exact=True).click()
        apply = page.get_by_role("button", name="Apply change", exact=True)
        expect(apply).to_be_enabled()
        page.get_by_role("button", name="Apply change", exact=True).click()
        expect(page.locator("#notice")).to_contain_text("403")
        expect(apply).to_be_disabled()
        identity.select_option(names["writer"])
        page.get_by_role("button", name="Preview change", exact=True).click()
        expect(apply).to_be_enabled()
        editor.fill(json.dumps({**payload, "body": {"displayName": "Changed after preview"}}))
        expect(apply).to_be_disabled()
        editor.fill(json.dumps(payload))
        page.get_by_role("button", name="Preview change", exact=True).click()
        expect(apply).to_be_enabled()
        apply.click()
        expect(page.get_by_role("heading", name="Change applied", exact=True)).to_be_visible()
        read = http.post(
            origin + f"/_mock/console/tenants/{tid}/resources/directory.users/read",
            headers=admin,
            json={
                "operation": "read-item",
                "identity_ref": names["reader"],
                "payload": {"variables": {"id": user_id}},
            },
        )
        assert (
            read.status_code == 200
            and read.json()["body"]["displayName"] == payload["body"]["displayName"]
        )
        page.get_by_role("button", name="Hunt evidence", exact=True).click()
        expect(page.get_by_role("heading", name="Follow the evidence.")).to_be_visible()
        page.get_by_label("Test identity", exact=True).select_option(names["reader"])
        open_workbench(page, tid)
        page.get_by_label("Test identity", exact=True).select_option(names["writer"])
        page.get_by_label("Resource JSON", exact=True).fill(json.dumps(payload))
        page.get_by_role("button", name="Preview change", exact=True).click()
        expect(apply).to_be_enabled()
        apply.click()
        page.get_by_role("button", name="View evidence", exact=True).click()
        expect(page.get_by_label("Test identity", exact=True)).to_have_value(names["reader"])
        expect(page.get_by_role("button", name="Download JSON", exact=True)).to_be_disabled()
        page.get_by_role("button", name="Run query", exact=True).click()
        expect(
            page.get_by_role("region", name="Query results").get_by_role("alert")
        ).to_contain_text("403")
        open_workbench(page, tid)
        page.get_by_role("button", name="Lab administration", exact=True).click()
        expect(page.get_by_role("heading", name="Change applied", exact=True)).to_have_count(0)
        expect(identity).to_be_disabled()
        alert_id = "ui-" + uuid4().hex
        admin_fixture(
            http,
            origin,
            tid,
            admin,
            "security.alerts_v2",
            {"id": alert_id, "title": "Keep", "description": "Remove"},
        )
        open_workbench(page, tid, "security.alerts_v2", "fixture")
        page.get_by_label("Resource JSON", exact=True).fill(
            json.dumps({"id": alert_id, "title": "Keep"})
        )
        page.get_by_role("button", name="Preview change", exact=True).click()
        consent = page.get_by_label("Allow this replacement", exact=True)
        expect(consent).to_be_visible()
        expect(apply).to_be_disabled()
        consent.check()
        expect(apply).to_be_enabled()
        consent.uncheck()
        expect(apply).to_be_disabled()
        consent.check()
        apply.click()
        expect(page.get_by_role("heading", name="Change applied", exact=True)).to_be_visible()
        record = http.get(
            origin + f"/_mock/tenants/{tid}/resources/security.alerts_v2",
            headers=admin,
            params={"identifier": alert_id},
        ).json()["items"][0]
        assert "description" not in record["properties"]
        page.get_by_role("button", name="Upload content", exact=True).click()
        dialog = page.get_by_role("dialog")
        file = dialog.get_by_label("File", exact=True)
        file.set_input_files(
            {
                "name": "too-large.bin",
                "mimeType": "application/octet-stream",
                "buffer": b"x" * (16777216 + 1),
            }
        )
        expect(dialog.get_by_role("button", name="Upload", exact=True)).to_be_disabled()
        file.set_input_files(
            {
                "name": "boundary.bin",
                "mimeType": "application/octet-stream",
                "buffer": b"x" * 16777216,
            }
        )
        expect(dialog.get_by_role("button", name="Upload", exact=True)).to_be_enabled()
        file.set_input_files(
            {"name": "proof.txt", "mimeType": "text/plain", "buffer": b"real UI upload"}
        )
        dialog.get_by_role("button", name="Upload", exact=True).click()
        expect(dialog.get_by_text("Content ready:", exact=False)).to_be_visible()
        content_id = dialog.locator("code").inner_text()
        downloaded = http.get(
            origin + f"/_mock/tenants/{tid}/content/objects/{content_id}/$value", headers=admin
        )
        assert downloaded.status_code == 200 and downloaded.content == b"real UI upload"
        dialog.get_by_role("button", name="Use content ID", exact=True).click()
        expect(dialog).not_to_be_visible()
        assert (
            json.loads(page.get_by_label("Resource JSON", exact=True).input_value())["content_id"]
            == content_id
        )
        expect(apply).to_be_disabled()
        assert not errors, errors


def test_composer_revision_roundtrip_new_tenant_and_downloads(tmp_path):
    import json
    from uuid import uuid4

    with console_browser() as (page, context, http, origin, tid, admin, errors):
        page.get_by_role("button", name="Compose lab", exact=True).click()
        page.get_by_label("Template", exact=True).select_option("volume-lab")
        page.get_by_role("button", name="2. Configure", exact=True).click()
        namespace = "ui-saved-" + uuid4().hex[:8]
        page.get_by_label("Create a new tenant", exact=True).check()
        for label, value in [
            ("Tenant slug", namespace),
            ("Tenant domain", namespace + ".test"),
            ("Display name", "Saved UI lab"),
            ("Dataset namespace", namespace),
            ("Users", "2"),
            ("Days of activity", "3"),
            ("Seed", "7"),
        ]:
            page.get_by_label(label, exact=True).fill(value)
        page.get_by_label("Additional parameters JSON", exact=True).fill(
            '{"volume-signins.records": 3}'
        )
        route = page.url
        page.locator(".skip").focus()
        page.locator(".skip").press("Enter")
        assert page.url == route
        expect(page.get_by_label("Dataset namespace", exact=True)).to_have_value(namespace)
        page.get_by_text("Profile source, import & export", exact=True).click()
        page.get_by_role("button", name="Save revision", exact=True).click()
        expect(page.get_by_text("Saved revision 1", exact=False)).to_be_visible()
        bundle_id = page.get_by_label("Saved bundle", exact=True).input_value()
        with page.expect_download() as download:
            page.get_by_role("button", name="Export bundle", exact=True).click()
        exported = json.loads(Path(download.value.path()).read_text())
        assert (
            exported["documents"]["volume-lab"]["consoleDefaults"]["datasetNamespace"] == namespace
        )
        page.get_by_label("Additional parameters JSON", exact=True).fill("{}")
        with page.expect_download() as download:
            page.get_by_role("button", name="Export bundle", exact=True).click()
        removed = json.loads(Path(download.value.path()).read_text())
        assert "volume-signins.records" not in removed["documents"]["volume-lab"]["parameters"]
        page.reload()
        page.get_by_label("Saved bundle", exact=True).select_option(bundle_id)
        page.get_by_role("button", name="Load bundle", exact=True).click()
        expect(page.get_by_text("Loaded revision 1", exact=False)).to_be_visible()
        page.get_by_label("Template", exact=True).select_option("volume-lab")
        page.get_by_role("button", name="2. Configure", exact=True).click()
        expect(page.get_by_label("Dataset namespace", exact=True)).to_have_value(namespace)
        expect(page.get_by_label("Create a new tenant", exact=True)).to_be_checked()
        expect(page.get_by_label("Seed", exact=True)).to_have_value("7")
        page.get_by_role("button", name="3. Review", exact=True).click()
        page.get_by_role("button", name="Preview population", exact=True).click()
        expect(page.get_by_role("heading", name="Review the resolved plan")).to_be_visible(
            timeout=30000
        )
        page.get_by_role("button", name="Apply this preview", exact=True).click()
        expect(page.get_by_role("heading", name="Population complete")).to_be_visible(timeout=90000)
        new_tid = page.get_by_label("Tenant", exact=True).input_value()
        assert new_tid != tid
        page.get_by_text("Immutable run configuration", exact=True).click()
        page.get_by_role("button", name="Load configuration", exact=True).click()
        expect(page.locator("details pre")).to_be_visible()
        for label in ["Download manifest", "Download ground truth"]:
            with page.expect_download() as download:
                page.get_by_role("button", name=label, exact=True).click()
            content = Path(download.value.path()).read_text()
            assert content and namespace in content
        run_url = page.url
        page.reload()
        expect(page.get_by_role("heading", name="Population complete")).to_be_visible()
        assert page.url == run_url
        assert not errors, errors


def test_late_preview_and_tenant_response_are_discarded():
    import json
    from uuid import uuid4

    with console_browser() as (page, context, http, origin, tid, admin, errors):
        open_workbench(page, tid, "security.alerts_v2", "fixture")
        editor = page.get_by_label("Resource JSON", exact=True)
        editor.fill(json.dumps({"id": "delay-" + uuid4().hex, "title": "Before"}))
        page.evaluate("""() => {
            const original=window.fetch;
            window.fetch=async (...args)=>{const response=await original(...args);
                if(String(args[0]).endsWith('/preview')) await new Promise(resolve=>{
                    window.releasePreview=resolve;document.body.dataset.previewReady='true';});
                return response;};
        }""")
        page.get_by_role("button", name="Preview change", exact=True).click()
        expect(page.locator("body")).to_have_attribute("data-preview-ready", "true")
        editor.fill(json.dumps({"id": "delay-" + uuid4().hex, "title": "After"}))
        page.evaluate("window.releasePreview()")
        expect(page.get_by_role("button", name="Preview change", exact=True)).to_be_enabled()
        expect(page.get_by_role("button", name="Apply change", exact=True)).to_be_disabled()
        expect(page.get_by_role("heading", name="Review this change")).to_have_count(0)
        other = next(
            t["id"]
            for t in http.get(origin + "/_mock/tenants?limit=100", headers=admin).json()["items"]
            if t["id"] != tid
        )
        page.evaluate(
            """tenant => {
            const original=window.fetch;
            window.fetch=async (...args)=>{const response=await original(...args);
                if(String(args[0]).includes('/tenants/'+tenant+'/resources/directory.users?'))
                    await new Promise(resolve=>{window.releaseTenant=resolve;
                        document.body.dataset.tenantReady='true';});
                return response;};
        }""",
            tid,
        )
        page.evaluate(
            "hash=>{location.hash=hash}", f"#/resources?tenant={tid}&family=directory.users"
        )
        expect(page.locator("body")).to_have_attribute("data-tenant-ready", "true")
        page.get_by_label("Tenant", exact=True).select_option(other)
        expect(page.get_by_role("heading", name="Explore your cloud.")).to_be_visible()
        page.evaluate("window.releaseTenant()")
        expect(page.get_by_label("Tenant", exact=True)).to_have_value(other)
        assert tid not in page.locator("#workspace").inner_text()
        assert not errors, errors


def test_profile_yaml_import_and_inherited_layer_integrity():
    import json

    import yaml

    with console_browser() as (page, context, http, origin, tid, admin, errors):
        page.get_by_role("button", name="Compose lab", exact=True).click()
        page.get_by_text("Profile source, import & export", exact=True).click()
        editor = page.get_by_label("Bundle JSON", exact=True)
        source = json.loads(editor.input_value())
        original = list(source["documents"]["developer-lab"]["dataProfiles"])
        source["documents"]["ui-child"] = {
            "schemaVersion": 1,
            "kind": "template",
            "name": "ui-child",
            "extends": "developer-lab",
        }
        editor.fill(json.dumps(source))
        page.get_by_role("button", name="Load editor changes", exact=True).click()
        page.get_by_label("Template", exact=True).select_option("ui-child")
        page.get_by_role("button", name="Move " + original[0] + " down", exact=True).click()
        changed = json.loads(editor.input_value())
        assert changed["documents"]["developer-lab"]["dataProfiles"] == original
        assert changed["documents"]["ui-child"]["dataProfiles"][1] == original[0]
        files = [
            {
                "name": name + ".yaml",
                "mimeType": "application/yaml",
                "buffer": yaml.safe_dump(document).encode(),
            }
            for name, document in changed["documents"].items()
        ]
        page.get_by_label("Profile files", exact=True).set_input_files(files)
        page.get_by_role("button", name="Import files", exact=True).click()
        expect(page.get_by_text("Imported revision 1", exact=True)).to_be_visible()
        page.get_by_label("Template", exact=True).select_option("ui-child")
        expect(page.locator(".layer-list li code").nth(1)).to_have_text(original[0])
        editor.fill('{"documents":')
        page.get_by_role("button", name="Load editor changes", exact=True).click()
        expect(editor).to_have_value('{"documents":')
        expect(editor).to_have_attribute("aria-invalid", "true")
        assert not errors, errors
