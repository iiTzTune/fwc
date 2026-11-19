"""Exercise the companion pages in an installed Chromium browser."""

import os
import shutil
from contextlib import contextmanager, suppress
from pathlib import Path

import pytest
from conftest import publish, running_device
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.client_config import ClientConfig
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from urllib3.exceptions import ReadTimeoutError

PAGE_SECONDS = 60
ELEMENT_SECONDS = 45
SESSION_SECONDS = 120


class Page:
    def __init__(self, driver):
        self.driver = driver
        self.wait = WebDriverWait(driver, ELEMENT_SECONDS)
        self.target = "about:blank"

    def open(self, url):
        self.target = url
        self.driver.get(url)
        self.loaded(url)

    def loaded(self, url):
        self.target = url
        self.wait.until(EC.url_to_be(url), f"navigation to {url}")
        self.wait.until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )

    def clickable(self, selector):
        return self.wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, selector)), selector
        )

    def visible(self, selector):
        return self.wait.until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, selector)), selector
        )

    def text(self, selector, value):
        self.wait.until(
            EC.text_to_be_present_in_element((By.CSS_SELECTOR, selector), value),
            f"{selector} contains {value!r}",
        )

    def link(self, selector):
        link = self.clickable(selector)
        self.target = link.get_attribute("href")
        link.click()
        self.loaded(self.target)

    def reload(self):
        self.driver.refresh()
        self.loaded(self.target)


@contextmanager
def browser_session(profile):
    binary = os.environ.get("FWC_CHROME") or next(
        (
            path
            for name in ("chromium", "google-chrome", "google-chrome-stable", "chrome")
            if (path := shutil.which(name))
        ),
        None,
    )
    executable = shutil.which("chromedriver")
    assert binary and executable, "Install matching Chromium and chromedriver on PATH"
    options = webdriver.ChromeOptions()
    options.binary_location = binary
    for arg in (
        "--headless=new",
        "--disable-dev-shm-usage",
        "--disable-background-networking",
        "--no-first-run",
        f"--user-data-dir={profile}",
    ):
        options.add_argument(arg)
    service = Service(executable_path=executable)
    page = None
    config = ClientConfig(
        remote_server_addr=service.service_url,
        timeout=SESSION_SECONDS,
        init_args_for_pool_manager={"init_args_for_pool_manager": {"retries": False}},
    )
    try:
        service.start()
        driver = webdriver.Remote(
            command_executor=service.service_url, options=options, client_config=config
        )
        page = Page(driver)
        driver.set_page_load_timeout(PAGE_SECONDS)
        yield page
    except (TimeoutException, ReadTimeoutError, TimeoutError) as exc:
        title, url = "<session unavailable>", "<no page>"
        if page:
            config.timeout = 3
            title, url = "<unavailable>", page.target
            with suppress(WebDriverException, OSError, ReadTimeoutError):
                title = driver.title
            with suppress(WebDriverException, OSError, ReadTimeoutError):
                url = driver.current_url
        raise TimeoutError(
            f"Browser timeout; title={title!r} URL={url!r}: {exc}"
        ) from exc
    finally:
        if page:
            config.timeout = 5
            with suppress(WebDriverException, OSError, ReadTimeoutError):
                driver.quit()
        service.stop()


@pytest.fixture
def browser(tmp_path):
    with browser_session(tmp_path / "profile") as page:
        yield page


def exercise_pages(device, page):
    port, state, _ = device
    base = f"http://127.0.0.1:{port}"
    for i in range(26):
        publish(
            state,
            "network.seen",
            {
                "network": f"02:00:00:00:00:{i:02x}",
                "ssid": f"Field {i}",
                "channel": 1,
                "time_ns": 123,
                "owned": True,
            },
            f"sample{i}",
        )
    page.open(base + "/")
    page.text("#live", "Live")
    page.text("#services", "web")
    publish(state, "peer.seen", {"peer": "other-fw", "name": "Field companion"}, "peer")
    page.text("#activity", "Field companion")
    page.link('nav a[href="/records"]')
    page.wait.until(
        lambda d: len(d.find_elements(By.CSS_SELECTOR, "#networks tr")) == 25
    )
    page.clickable("#next").click()
    page.text("#page", "Page 2")
    assert len(page.driver.find_elements(By.CSS_SELECTOR, "#networks tr")) == 1
    page.clickable("#previous").click()
    page.text("#page", "Page 1")
    page.link('nav a[href="/settings"]')
    field = page.clickable("[name=dwell_ms]")
    field.clear()
    field.send_keys("1500")
    page.clickable("#editor button").click()
    page.text("#notice", "saved")
    page.reload()
    page.wait.until(
        EC.text_to_be_present_in_element_value((By.NAME, "dwell_ms"), "1500")
    )
    page.link("footer a")
    page.text("body", "events committed since startup")
    assert not page.driver.find_elements(By.TAG_NAME, "script")


def test_pages_and_controls(tmp_path, core_binary, capsys):
    for attempt in range(2):
        work = tmp_path / f"attempt-{attempt}"
        work.mkdir()
        try:
            with (
                running_device(work, core_binary) as device,
                browser_session(work / "profile") as page,
            ):
                exercise_pages(device, page)
            return
        except TimeoutError as exc:
            if attempt:
                raise
            with capsys.disabled():
                print(f"test_pages_and_controls retry 1/1: {exc}", flush=True)


def test_permission_toggle(device, browser):
    from fwc.capture.contract import plugin_config

    port, state, _ = device
    name = "fwc-plugin-hop"
    shutil.copytree(
        Path(__file__).resolve().parents[2] / "plugins/examples" / name,
        state / "plugins" / name,
    )
    browser.open(f"http://127.0.0.1:{port}/settings")
    field = '[data-permission="radio.tx"]'
    assert not browser.clickable(field).is_selected()
    browser.clickable(field).click()
    browser.wait.until(lambda d: plugin_config(state, name)[0] == {"radio.tx"})
    browser.reload()
    assert browser.clickable(field).is_selected()
    browser.clickable(field).click()
    browser.wait.until(lambda d: plugin_config(state, name)[0] == set())
