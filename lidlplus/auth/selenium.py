"""Legacy Selenium-based automated login."""

from __future__ import annotations

import html
import logging
import re
from typing import TYPE_CHECKING, Callable

from lidlplus.exceptions import LegalTermsException, LoginError, WebBrowserException

if TYPE_CHECKING:
    from lidlplus.api import LidlPlusApi

try:
    from getuseragent import UserAgent
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions
    from selenium.webdriver.support.ui import WebDriverWait
    from seleniumwire import webdriver
    from seleniumwire.utils import decode
    from webdriver_manager.chrome import ChromeDriverManager
    from webdriver_manager.core.os_manager import ChromeType
    from webdriver_manager.firefox import GeckoDriverManager
except ImportError:
    UserAgent = None
    Service = None
    By = None
    expected_conditions = None
    WebDriverWait = None
    webdriver = None
    decode = None
    ChromeDriverManager = None
    GeckoDriverManager = None
    ChromeType = None

log = logging.getLogger(__name__)


class SeleniumAuth:
    """Automated browser login (legacy; breaks when Lidl changes their UI)."""

    def __init__(self, api: LidlPlusApi):
        self._api = api

    def _require_selenium(self) -> None:
        if webdriver is None:
            raise ImportError(
                "Selenium login requires optional dependencies: uv sync --extra selenium"
            )

    def _init_chrome(self, headless: bool = True):
        self._require_selenium()
        user_agent = UserAgent(self._api._OS.lower()).Random()
        logging.getLogger("WDM").setLevel(logging.NOTSET)
        options = webdriver.ChromeOptions()
        if headless:
            options.add_argument("headless")
        options.add_experimental_option("mobileEmulation", {"userAgent": user_agent})
        for chrome_type in [ChromeType.GOOGLE, ChromeType.MSEDGE, ChromeType.CHROMIUM]:
            try:
                service = Service(ChromeDriverManager(chrome_type=chrome_type).install())
                return webdriver.Chrome(service=service, options=options)
            except AttributeError:
                continue
        raise WebBrowserException("Unable to find a suitable Chrome driver")

    def _init_firefox(self, headless: bool = True):
        self._require_selenium()
        user_agent = UserAgent(self._api._OS.lower()).Random()
        logging.getLogger("WDM").setLevel(logging.NOTSET)
        options = webdriver.FirefoxOptions()
        profile = webdriver.FirefoxProfile()
        profile.set_preference("general.useragent.override", user_agent)
        return webdriver.Firefox(options=options)

    def _get_browser(self, headless: bool = True):
        try:
            return self._init_chrome(headless=headless)
        except Exception as exc1:  # pylint: disable=broad-except
            try:
                return self._init_firefox(headless=headless)
            except Exception as exc2:  # pylint: disable=broad-except
                raise WebBrowserException from exc1 and exc2

    @staticmethod
    def _accept_legal_terms(browser, wait, accept: bool = True):
        wait.until(expected_conditions.visibility_of_element_located((By.ID, "checkbox_Accepted"))).click()
        if not accept:
            title = browser.find_element(By.TAG_NAME, "h2").text
            raise LegalTermsException(title)
        browser.find_element(By.TAG_NAME, "button").click()

    def _parse_code(self, browser, wait, accept_legal_terms: bool = True) -> str:
        for request in reversed(browser.requests):
            if f"{self._api._AUTH_API}/connect" not in request.url:
                continue
            location = request.response.headers.get("Location", "")
            if "legalTerms" in location:
                self._accept_legal_terms(browser, wait, accept=accept_legal_terms)
                return self._parse_code(browser, wait, False)
            if code := re.findall("code=([0-9A-F]+)", location):
                return code[0]
        return ""

    @staticmethod
    def _click(browser, button, request: str = ""):
        del browser.requests
        browser.backend.storage.clear_requests()
        browser.find_element(*button).click()
        SeleniumAuth._check_input_error(browser)
        if request and browser.wait_for_request(request, 10):
            SeleniumAuth._check_input_error(browser)

    @staticmethod
    def _check_input_error(browser):
        if errors := browser.find_elements(By.CLASS_NAME, "input-error-message"):
            for error in errors:
                if error.text:
                    raise LoginError(error.text)

    def _check_login_error(self, browser):
        response = browser.wait_for_request(f"{self._api._AUTH_API}/Account/Login.*", 10).response
        body = html.unescape(
            decode(response.body, response.headers.get("Content-Encoding", "identity")).decode()
        )
        if error := re.findall('app-errors="\\{[^:]*?:.(.*?).}', body):
            raise LoginError(error[0])

    def _check_2fa_auth(
        self,
        browser,
        wait,
        verify_mode: str = "phone",
        verify_token_func: Callable[[], str] | None = None,
    ):
        if verify_mode not in ["phone", "email"]:
            raise ValueError(f'Unknown 2fa-mode "{verify_mode}" - Only "phone" or "email" supported')
        response = browser.wait_for_request(f"{self._api._AUTH_API}/Account/Login.*", 10).response
        if "/connect/authorize/callback" not in response.headers.get("Location"):
            element = wait.until(expected_conditions.visibility_of_element_located((By.CLASS_NAME, verify_mode)))
            element.find_element(By.TAG_NAME, "button").click()
            verify_code = verify_token_func()  # type: ignore[misc]
            browser.find_element(By.NAME, "VerificationCode").send_keys(verify_code)
            self._click(browser, (By.CLASS_NAME, "role_next"))

    @staticmethod
    def _normalize_phone(phone: str, country: str) -> str:
        digits = phone.strip().replace(" ", "")
        if digits.startswith("+"):
            digits = digits[1:]
        if country == "DE" and digits.startswith("49"):
            digits = digits[2:]
        return digits

    @staticmethod
    def _dismiss_cookie_consent(browser):
        for element in browser.find_elements(By.ID, "cookie-consent-accept"):
            if element.is_displayed():
                element.click()
                return

    def login(
        self,
        login: str,
        password: str,
        method: str,
        *,
        verify_mode: str = "phone",
        verify_token_func: Callable[[], str] | None = None,
        headless: bool = True,
        accept_legal_terms: bool = True,
    ):
        """Simulate app auth via Selenium."""
        from lidlplus.auth.oauth import OAuthAuth

        browser = self._get_browser(headless=headless)
        oauth = OAuthAuth(self._api)
        browser.get(oauth.register_link)
        wait = WebDriverWait(browser, 15)
        self._dismiss_cookie_consent(browser)
        if method == "p":
            login = self._normalize_phone(login, self._api._country)
            wait.until(
                expected_conditions.element_to_be_clickable(
                    (By.CSS_SELECTOR, '[data-testid="switch-method-button"]')
                )
            ).click()
            wait.until(expected_conditions.element_to_be_clickable((By.NAME, "input-phone"))).send_keys(login)
        else:
            wait.until(expected_conditions.element_to_be_clickable((By.NAME, "input-email"))).send_keys(login)
        self._click(browser, (By.CSS_SELECTOR, '[data-testid="login-or-register-submit-button"]'))
        wait.until(expected_conditions.element_to_be_clickable((By.NAME, "Password"))).send_keys(password)
        self._click(browser, (By.CSS_SELECTOR, '[data-testid="button-primary"]'))

        self._check_login_error(browser)
        self._check_2fa_auth(browser, wait, verify_mode, verify_token_func)
        browser.wait_for_request(f"{self._api._AUTH_API}/connect.*")
        code = self._parse_code(browser, wait, accept_legal_terms=accept_legal_terms)
        oauth.exchange_authorization_code(code)
        browser.close()
        return self._api
