"""Drive an installed Chromium through its standard WebDriver HTTP interface."""

import json
import os
import shutil
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from conftest import free_port, publish


@pytest.fixture
def browser(tmp_path):
    driver = shutil.which("chromedriver")
    chromium = os.environ.get("FWC_CHROME") or next(
        (
            p
            for name in ("chromium", "google-chrome", "google-chrome-stable", "chrome")
            if (p := shutil.which(name))
        ),
        None,
    )
    assert driver and chromium, "Install matching Chromium and chromedriver on PATH"
    endpoint = f"http://127.0.0.1:{free_port()}"

    def command(method, path, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        req = Request(
            endpoint + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(req, timeout=12) as reply:
                return json.load(reply)["value"]
        except HTTPError as error:
            pytest.fail(
                f"WebDriver {method} {path}: {error.read().decode(errors='replace')}"
            )

    with subprocess.Popen(
        [driver, "--port=" + endpoint.rsplit(":", 1)[1]], stdout=subprocess.DEVNULL
    ) as process:
        try:
            deadline = time.monotonic() + 5
            while True:
                try:
                    command("GET", "/status")
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.03)
            session = command(
                "POST",
                "/session",
                {
                    "capabilities": {
                        "alwaysMatch": {
                            "browserName": "chrome",
                            "goog:chromeOptions": {
                                "binary": chromium,
                                "args": [
                                    "--headless=new",
                                    "--disable-dev-shm-usage",
                                    "--disable-background-networking",
                                    "--no-first-run",
                                    "--user-data-dir=" + str(tmp_path / "profile"),
                                ],
                            },
                        }
                    }
                },
            )
            prefix = "/session/" + session["sessionId"]

            def run(script):
                return command(
                    "POST", prefix + "/execute/sync", {"script": script, "args": []}
                )

            def wait(expression):
                deadline = time.monotonic() + 6
                while not run("return Boolean(" + expression + ")"):
                    if time.monotonic() >= deadline:
                        pytest.fail("Browser timed out: " + expression)
                    time.sleep(0.035)

            try:
                yield (
                    lambda url: command("POST", prefix + "/url", {"url": url}),
                    run,
                    wait,
                )
            finally:
                command("DELETE", prefix)
        finally:
            process.terminate()
            process.wait(5)


def test_pages_and_controls(device, browser):
    port, state, _ = device
    visit, run, wait = browser
    for i in range(26):
        data = {
            "network": f"02:00:00:00:00:{i:02x}",
            "ssid": f"Field {i}",
            "channel": 1,
            "time_ns": 123,
            "owned": True,
        }
        publish(state, "network.seen", data, f"sample{i}")
    visit(f"http://127.0.0.1:{port}")
    wait("document.querySelector('#live').textContent === 'Live'")
    wait("document.querySelector('#services').textContent.includes('web')")
    publish(state, "peer.seen", {"peer": "other-fw", "name": "Field companion"}, "peer")
    wait("document.querySelector('#activity').textContent.includes('Field companion')")
    run("document.querySelector('nav a[href=\"/records\"]').click()")
    wait("document.querySelectorAll('#networks tr').length === 25")
    run("document.querySelector('#next').click()")
    wait("document.querySelector('#page').textContent === 'Page 2'")
    assert run("return document.querySelectorAll('#networks tr').length") == 1
    run("document.querySelector('#previous').click()")
    wait("document.querySelector('#page').textContent === 'Page 1'")
    run("document.querySelector('nav a[href=\"/settings\"]').click()")
    wait("!document.querySelector('#editor button').disabled")
    run(
        "document.querySelector('[name=dwell_ms]').value='1500'; document.querySelector('#editor button').click()"
    )
    wait("document.querySelector('#notice').textContent.includes('saved')")
    run("location.reload()")
    wait("document.querySelector('[name=dwell_ms]').value === '1500'")
    run("document.querySelector('footer a').click()")
    wait("document.body.textContent.includes('events committed since startup')")
    assert run("return document.scripts.length") == 0


def test_permission_toggle(device, browser):
    import shutil
    from pathlib import Path

    from fwc.capture.contract import plugin_config

    port, state, _ = device
    name = "fwc-plugin-hop"
    shutil.copytree(
        Path(__file__).resolve().parents[2] / "plugins/examples" / name,
        state / "plugins" / name,
    )
    visit, run, wait = browser
    visit(f"http://127.0.0.1:{port}/settings")
    field = "document.querySelector('[data-permission=\"radio.tx\"]')"
    wait(field)
    assert not run("return " + field + ".checked")
    run(field + ".click()")
    wait(field + ".checked && !" + field + ".disabled")
    assert plugin_config(state, name)[0] == {"radio.tx"}
    run("location.reload()")
    wait(field + " && " + field + ".checked")
    run(field + ".click()")
    wait("!" + field + ".checked && !" + field + ".disabled")
    assert plugin_config(state, name)[0] == set()
