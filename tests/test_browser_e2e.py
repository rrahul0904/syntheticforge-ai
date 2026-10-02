from __future__ import annotations

import os
import hashlib
import socket
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from app.security import hash_password


def test_production_browser_login_generation_project_agent_and_logout(tmp_path: Path):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert),
        "-days", "1", "-subj", "/CN=127.0.0.1",
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    db = tmp_path / "source.sqlite"
    import sqlite3
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        con.execute("INSERT INTO customers VALUES (1,'source row')")

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env.update({
        "SYNTHETICFORGE_ENV": "production",
        "SYNTHETICFORGE_HOME": str(tmp_path / "data"),
        "SYNTHETICFORGE_ADMIN_USERNAME": "admin",
        "SYNTHETICFORGE_ADMIN_PASSWORD_HASH": hash_password("browser-test-password-with-32-characters"),
        "SYNTHETICFORGE_API_TOKEN_SHA256": "0" * 64,
        "SYNTHETICFORGE_CORS_ORIGINS": "https://127.0.0.1",
        "SYNTHETICFORGE_APPLICATION_SHA": "b" * 40,
        "SYNTHETICFORGE_REPLICAS": "1",
        "SYNTHETICFORGE_SESSION_TTL_SECONDS": "28800",
        "WEB_CONCURRENCY": "1",
        "PYTHONPATH": str(Path.cwd()),
    })
    command = [
        sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port),
        "--ssl-certfile", str(cert), "--ssl-keyfile", str(key), "--workers", "1",
    ]
    process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"https://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                if process.poll() is not None:
                    raise AssertionError("Uvicorn exited before browser smoke started")
                time.sleep(0.1)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(ignore_https_errors=True, accept_downloads=True, viewport={"width": 1280, "height": 900})
            page = context.new_page()
            page.set_default_timeout(8_000)
            severe_console = []
            page.on("pageerror", lambda error: severe_console.append(str(error)))
            page.goto(base, wait_until="domcontentloaded")
            login_dialog = page.get_by_role("dialog", name="Sign in to SyntheticForge")
            expect(login_dialog).to_be_visible()
            expect(login_dialog).to_have_attribute("open", "")
            username = login_dialog.get_by_label("Username")
            password = login_dialog.get_by_label("Password")
            expect(username).to_be_focused()
            page.keyboard.press("Tab")
            expect(password).to_be_focused()
            page.keyboard.press("Tab")
            expect(page.get_by_role("button", name="Sign in")).to_be_focused()
            username.fill("admin")
            password.fill("browser-test-password-with-32-characters")
            page.get_by_role("button", name="Sign in").click()
            page.get_by_role("heading", name="Build production-shaped test data without exposing production data.").wait_for()
            expect(page.locator("#loginPassword")).to_have_value("")
            assert page.get_by_text("Capability health").is_visible()

            # Same-origin writes must fail closed without the session CSRF token.
            csrf = page.evaluate("fetch('/api/auth/session').then(r=>r.json()).then(s=>s.csrf_token)")
            assert csrf and csrf not in page.locator("body").inner_text()
            assert csrf not in page.content()
            csrf_denial = page.evaluate("fetch('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'CSRF denied'})}).then(r=>r.status)")
            assert csrf_denial == 403

            page.locator('[data-view="generate"]').click()
            page.locator("#sysSchemaInput").fill("CREATE TABLE public.people (id INTEGER PRIMARY KEY, name VARCHAR(80) NOT NULL);")
            page.locator("#sysRowCount").fill("5")
            page.locator("#parseSystemBtn").click()
            page.get_by_text("Parsed 1 tables and 0 relationships.").wait_for()
            page.locator("#generateSystemBtn").click()
            page.get_by_text("Generation complete", exact=False).wait_for(timeout=20_000)
            assert page.locator("#validationBadge").inner_text().startswith("VALIDATED")
            with page.expect_download() as download:
                page.locator("#exportSystemBtn").click()
            artifact = tmp_path / "dataset.zip"
            download.value.save_as(artifact)
            assert artifact.stat().st_size > 100

            page.locator('[data-view="connectors"]').click()
            page.locator("#connectorType").select_option("sqlite")
            page.locator("#connectorDatabase").fill(str(db))
            page.locator("#connectorSchema").fill("main")
            with page.expect_response(lambda response: response.url.endswith("/api/connectors/test")) as connector_response:
                page.locator("#testConnectorBtn").click()
            response = connector_response.value
            assert response.status == 200, response.text()
            page.locator("#connectorResult").wait_for()
            assert "Connection successful" in page.locator("#connectorResult").inner_text()
            page.locator("#inspectConnectorBtn").click()
            page.get_by_text("Introspected 1 tables and 0 relationships.").wait_for()

            page.locator('[data-view="projects"]').click()
            page.locator("#newProjectBtn").click()
            page.locator("#projectName").fill("Browser smoke")
            page.locator("#saveProjectBtn").click()
            page.locator("#projectList").get_by_text("Browser smoke", exact=True).wait_for()

            # Exercise import formats through the actual schema workspace.
            imports = [
                ("json", '{"type":"object","properties":{"id":{"type":"integer"},"label":{"type":"string"}},"required":["id"]}'),
                ("openapi", '{"openapi":"3.0.0","info":{"title":"Smoke","version":"1"},"paths":{"/people":{"get":{"responses":{"200":{"description":"ok","content":{"application/json":{"schema":{"type":"object","properties":{"id":{"type":"integer"}}}}}}}}}}}'),
                ("avro", '{"type":"record","name":"Person","fields":[{"name":"id","type":"long"},{"name":"name","type":"string"}]}'),
                ("csv", 'id,name\n1,Ada\n2,Lin\n'),
            ]
            page.locator('[data-view="generate"]').click()
            for input_format, content in imports:
                page.locator("#sysInputFormat").select_option(input_format)
                page.locator("#sysSchemaInput").fill(content)
                with page.expect_response(lambda response: response.url.endswith("/api/parse-schema")) as parsed_response:
                    page.locator("#parseSystemBtn").click()
                parsed = parsed_response.value
                assert parsed.status == 200, f"{input_format} import failed: {parsed.text()}"
                assert parsed.json()["tables"], f"{input_format} import returned no tables"

            page.locator('[data-view="agents"]').click()
            page.locator("#agentGoal").fill("Create a small customers QA dataset with realistic names and unique identifiers.")
            page.locator("#agentRows").fill("5")
            page.locator("#startAgentBtn").click()
            page.get_by_text("COMPLETED", exact=True).wait_for(timeout=45_000)
            with page.expect_download() as agent_download:
                page.locator("#downloadAgentBtn").click()
            agent_artifact = tmp_path / "agent.zip"
            agent_download.value.save_as(agent_artifact)
            assert agent_artifact.stat().st_size > 100

            # Provider secrets are accepted into a password field, never echoed in status or page markup.
            page.locator('[data-view="settings"]').click()
            expect(page.locator("#providerStatus")).to_contain_text("No external AI configured")
            page.locator("#providerType").select_option("openai-compatible")
            page.locator("#providerModel").fill("browser-smoke-model")
            page.locator("#providerBaseUrl").fill("https://provider.invalid/v1")
            provider_secret = "browser-secret-never-render-this-7f6c8d2a"
            page.locator("#providerApiKey").fill(provider_secret)
            page.locator("#saveProviderBtn").click()
            expect(page.locator("#providerStatus")).to_contain_text("API key present: yes")
            expect(page.locator("#providerApiKey")).to_have_value("")
            assert provider_secret not in page.locator("body").inner_text()
            assert provider_secret not in page.content()
            page.locator("#clearProviderBtn").click()
            expect(page.locator("#providerStatus")).to_contain_text("No external AI configured")

            # Expire a second real session in the isolated test database and verify the auth boundary immediately.
            expiry_context = browser.new_context(ignore_https_errors=True)
            expiry_page = expiry_context.new_page()
            expiry_page.goto(base, wait_until="domcontentloaded")
            expiry_page.locator("#loginUsername").fill("admin")
            expiry_page.locator("#loginPassword").fill("browser-test-password-with-32-characters")
            with expiry_page.expect_response(lambda response: response.url.endswith("/api/auth/login")) as expiry_login:
                expiry_page.get_by_role("button", name="Sign in").click()
            assert expiry_login.value.status == 200, expiry_login.value.text()
            expect(expiry_page.get_by_role("dialog", name="Sign in to SyntheticForge")).to_be_hidden()
            session_cookie = next(cookie["value"] for cookie in expiry_context.cookies(base) if cookie["name"] == "sf_session")
            session_digest = hashlib.sha256(session_cookie.encode()).hexdigest()
            with sqlite3.connect(tmp_path / "data" / "state.db") as state_db:
                changed = state_db.execute("UPDATE auth_sessions SET expires_at=? WHERE token_hash=?", ("2000-01-01T00:00:00+00:00", session_digest)).rowcount
            assert changed == 1
            expired = expiry_page.evaluate("Promise.all([fetch('/api/auth/session').then(r=>r.json()),fetch('/api/projects').then(r=>r.status)])")
            assert expired[0]["authenticated"] is False
            assert expired[0]["role"] is None
            assert expired[1] == 401
            expiry_context.close()

            browser_state = context.storage_state()
            page.close()
            context.close()
            process.terminate()
            process.wait(timeout=10)
            process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            ssl_context = ssl._create_unverified_context()
            for _ in range(100):
                try:
                    with urllib.request.urlopen(base + "/api/health", context=ssl_context, timeout=1) as response:
                        assert response.status == 200
                    break
                except OSError:
                    if process.poll() is not None:
                        raise AssertionError("Uvicorn failed to restart against the existing /data volume")
                    time.sleep(0.1)
            context = browser.new_context(ignore_https_errors=True, accept_downloads=True, viewport={"width": 1280, "height": 900}, storage_state=browser_state)
            page = context.new_page()
            page.set_default_timeout(8_000)
            page.goto(base, wait_until="domcontentloaded")
            restored_session = page.evaluate("fetch('/api/auth/session').then(r=>r.json())")
            assert restored_session["authenticated"] is True
            assert restored_session["role"] == "admin"
            expect(page.get_by_role("dialog", name="Sign in to SyntheticForge")).to_be_hidden()
            expect(page.get_by_role("button", name="Sign out")).to_be_visible()
            page.locator('[data-view="projects"]').click()
            page.locator("#projectList").get_by_text("Browser smoke", exact=True).wait_for()

            # Check document-level horizontal overflow on the main workspace screens at desktop and phone widths.
            for view in ("dashboard", "generate", "connectors", "projects", "jobs", "agents"):
                page.locator(f"[data-view='{view}']").click()
                page.locator(f"#{view}View.active").wait_for()
                sizes = page.evaluate("({client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth})")
                assert sizes["scroll"] <= sizes["client"], f"Horizontal page overflow on {view} at desktop: {sizes}"
            page.set_viewport_size({"width": 390, "height": 844})
            for view in ("dashboard", "generate", "connectors", "projects", "jobs", "agents"):
                page.locator(f"[data-view='{view}']").click()
                page.locator(f"#{view}View.active").wait_for()
                sizes = page.evaluate("({client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth})")
                assert sizes["scroll"] <= sizes["client"], f"Horizontal page overflow on {view} at 390px: {sizes}"
            page.keyboard.press("Tab")
            assert page.locator("body").is_visible()
            assert not severe_console, f"Browser page errors: {severe_console}"
            page.set_viewport_size({"width": 1280, "height": 900})
            page.get_by_role("button", name="Sign out").click()
            expect(page.get_by_role("dialog", name="Sign in to SyntheticForge")).to_be_visible()
            revoked = page.evaluate("Promise.all([fetch('/api/auth/session').then(r=>r.json()),fetch('/api/projects').then(r=>r.status)])")
            assert revoked[0]["authenticated"] is False
            assert revoked[1] == 401
            browser.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_production_operator_dashboard_keeps_safe_workspace_available(tmp_path: Path):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert),
        "-days", "1", "-subj", "/CN=127.0.0.1",
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    admin_password = "browser-admin-password-with-at-least-32-characters"
    operator_password = "browser-operator-password-with-at-least-32-characters"
    env = os.environ.copy()
    env.update({
        "SYNTHETICFORGE_ENV": "production",
        "SYNTHETICFORGE_HOME": str(tmp_path / "data"),
        "SYNTHETICFORGE_ADMIN_USERNAME": "admin",
        "SYNTHETICFORGE_ADMIN_PASSWORD_HASH": hash_password(admin_password),
        "SYNTHETICFORGE_OPERATOR_USERNAME": "operator",
        "SYNTHETICFORGE_OPERATOR_PASSWORD_HASH": hash_password(operator_password),
        "SYNTHETICFORGE_API_TOKEN_SHA256": "0" * 64,
        "SYNTHETICFORGE_CORS_ORIGINS": "https://127.0.0.1",
        "SYNTHETICFORGE_APPLICATION_SHA": "c" * 40,
        "SYNTHETICFORGE_REPLICAS": "1",
        "WEB_CONCURRENCY": "1",
        "PYTHONPATH": str(Path.cwd()),
    })
    command = [
        sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port),
        "--ssl-certfile", str(cert), "--ssl-keyfile", str(key), "--workers", "1",
    ]
    process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"https://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                if process.poll() is not None:
                    raise AssertionError("Uvicorn exited before operator browser smoke started")
                time.sleep(0.1)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(ignore_https_errors=True, viewport={"width": 1280, "height": 900})
            page = context.new_page()
            page.set_default_timeout(8_000)
            page.goto(base, wait_until="domcontentloaded")

            # Seed ordinary workspace state as an administrator, and verify diagnostics still work for admins.
            page.locator("#loginUsername").fill("admin")
            page.locator("#loginPassword").fill(admin_password)
            page.get_by_role("button", name="Sign in").click()
            page.get_by_role("heading", name="Build production-shaped test data without exposing production data.").wait_for()
            expect(page.locator("#parquetStatus")).not_to_have_text("Checking")
            assert page.locator("#parquetStatus").text_content().strip() in {"Operational", "Optional driver"}
            admin_csrf = page.evaluate("fetch('/api/auth/session').then(r=>r.json()).then(s=>s.csrf_token)")
            seeded = page.evaluate("""async csrf => {
              const response = await fetch('/api/projects', {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf}, body:JSON.stringify({name:'Operator-visible project',description:'safe project summary'})});
              return {status:response.status,body:await response.json()};
            }""", admin_csrf)
            assert seeded["status"] == 200, seeded
            assert page.evaluate("fetch('/api/diagnostics').then(r=>r.status)") == 200
            page.get_by_role("button", name="Sign out").click()
            page.get_by_role("heading", name="Sign in to SyntheticForge").wait_for()

            # An operator sees the workspace and may read projects/jobs and generate records.
            page.locator("#loginUsername").fill("operator")
            page.locator("#loginPassword").fill(operator_password)
            page.get_by_role("button", name="Sign in").click()
            page.get_by_role("heading", name="Build production-shaped test data without exposing production data.").wait_for()
            page.locator("#recentProjects").get_by_text("Operator-visible project", exact=True).wait_for()
            assert page.locator("#dashProjects").inner_text() == "1"
            assert page.locator("[data-view='diagnostics']").is_hidden()
            operator_routes = page.evaluate("""async () => {
              const results = {};
              for (const path of ['/api/projects', '/api/jobs', '/api/dashboard-summary']) {
                const response = await fetch(path);
                results[path] = {status:response.status,body:await response.json()};
              }
              return results;
            }""")
            assert all(item["status"] == 200 for item in operator_routes.values()), operator_routes

            # Generation and its safe summary work without waiting on privileged diagnostics.
            page.locator("[data-view='generate']").click()
            page.locator("#sysSchemaInput").fill("CREATE TABLE public.operator_rows (id INTEGER PRIMARY KEY, label VARCHAR(40));")
            page.locator("#sysRowCount").fill("3")
            page.locator("#parseSystemBtn").click()
            page.get_by_text("Parsed 1 tables and 0 relationships.").wait_for()
            page.locator("#generateSystemBtn").click()
            page.get_by_text("Generation complete", exact=False).wait_for(timeout=20_000)
            page.locator("[data-view='dashboard']").click()
            expect(page.locator("#dashRows")).not_to_have_text("0")
            assert page.locator("#parquetStatus").text_content().strip() == "Admin only"

            # Admin-only endpoints fail with 403 for an authenticated operator on the actual server.
            csrf = page.evaluate("fetch('/api/auth/session').then(r=>r.json()).then(s=>s.csrf_token)")
            protected = page.evaluate("""async csrf => {
              const results = {};
              const requests = [
                ['GET','/api/diagnostics'], ['GET','/api/audit'], ['GET','/api/connector-receipts'],
                ['GET','/api/provider-status'], ['POST','/api/provider-settings'], ['DELETE','/api/provider-settings'],
                ['POST','/api/connectors/test'], ['POST','/api/connectors/introspect'], ['POST','/api/write-approvals'],
                ['POST','/api/agent-runs/missing/approve-load']
              ];
              for (const [method,path] of requests) {
                const response = await fetch(path, {method, headers:{'Content-Type':'application/json','X-CSRF-Token':csrf}, body:method==='GET'||method==='DELETE'?undefined:'{}'});
                results[`${method} ${path}`] = response.status;
              }
              results['GET /api/connectors'] = (await fetch('/api/connectors')).status;
              const summary = await fetch('/api/dashboard-summary');
              results['GET /api/dashboard-summary after generation'] = {status:summary.status,body:await summary.json()};
              return results;
            }""", csrf)
            assert all(status == 403 for route, status in protected.items() if route not in {"GET /api/connectors", "GET /api/dashboard-summary after generation"}), protected
            assert protected["GET /api/connectors"] == 200, protected
            assert protected["GET /api/dashboard-summary after generation"]["status"] == 200, protected
            assert protected["GET /api/dashboard-summary after generation"]["body"]["generated_rows"] >= 3, protected
            assert page.locator("#dashProjects").inner_text() == "1"
            assert page.locator("#dashRows").text_content().strip() != "0"
            page.locator("#recentProjects").get_by_text("Operator-visible project", exact=True).wait_for()
            browser.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
