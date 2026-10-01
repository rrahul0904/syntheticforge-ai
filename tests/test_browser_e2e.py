from __future__ import annotations

import os
import socket
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

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
            page.locator("#loginUsername").fill("admin")
            page.locator("#loginPassword").fill("browser-test-password-with-32-characters")
            page.get_by_role("button", name="Sign in").click()
            page.get_by_role("heading", name="Build production-shaped test data without exposing production data.").wait_for()
            assert page.get_by_text("Capability health").is_visible()

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
            page.locator('[data-view="projects"]').click()
            page.locator("#projectList").get_by_text("Browser smoke", exact=True).wait_for()

            page.set_viewport_size({"width": 390, "height": 844})
            page.keyboard.press("Tab")
            assert page.locator("body").is_visible()
            assert not severe_console, f"Browser page errors: {severe_console}"
            page.set_viewport_size({"width": 1280, "height": 900})
            page.get_by_role("button", name="Sign out").click()
            page.get_by_role("heading", name="Sign in to SyntheticForge").wait_for()
            browser.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
