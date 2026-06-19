"""Lidl Plus HTTP API client."""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta

import requests

from lidlplus.auth.oauth import OAuthAuth, parse_oauth_code
from lidlplus.auth.selenium import SeleniumAuth
from lidlplus.exceptions import MissingLogin

log = logging.getLogger(__name__)


class LidlPlusApi:
    """Lidl Plus API connector."""

    _CLIENT_ID = "LidlPlusNativeClient"
    _AUTH_API = "https://accounts.lidl.com"
    _TICKET_API = "https://tickets.lidlplus.com/api/v2"
    _COUPONS_API = "https://coupons.lidlplus.com/app/api"
    _COUPONS_V1_API = "https://coupons.lidlplus.com/app/api/"
    _PROFILE_API = "https://profile.lidlplus.com/profile/api"
    _APP = "com.lidlplus.app"
    _OS = "iOs"
    _APP_VERSION = "16.45.5"
    _TIMEOUT = 30
    _OAUTH_STATE = "lidlplus-browser-auth"
    _REDIRECT_URI = f"{_APP}://callback"
    _SCOPES = "openid profile offline_access lpprofile lpapis"

    def __init__(self, language: str, country: str, refresh_token: str = ""):
        self._login_url = ""
        self._code_verifier = ""
        self._oauth_client = None
        self._refresh_token = refresh_token
        self._expires = None
        self._token = ""
        self._country = country.upper()
        self._language = language.lower()

    @property
    def refresh_token(self) -> str:
        """Lidl Plus API refresh token."""
        return self._refresh_token

    @property
    def token(self) -> str:
        """Current access token."""
        return self._token

    parse_oauth_code = staticmethod(parse_oauth_code)

    def apply_token_response(self, token_response: dict) -> None:
        body = dict(token_response) if not isinstance(token_response, dict) else token_response
        if body.get("error"):
            from lidlplus.exceptions import LoginError

            raise LoginError(f"{body['error']}: {body.get('error_description', '')}".strip())
        self._expires = datetime.utcnow() + timedelta(seconds=int(body["expires_in"]))
        self._token = body["access_token"]
        if "refresh_token" in body:
            self._refresh_token = body["refresh_token"]

    def browser_auth(self, *, open_browser: bool = False, input_func=input):
        """Log in via OAuth PKCE (manual browser callback)."""
        return OAuthAuth(self).browser_auth(
            open_browser=open_browser,
            input_func=input_func,
        )

    def login(self, login: str, password: str, method: str, **kwargs):
        """Automated Selenium login (legacy)."""
        return SeleniumAuth(self).login(login, password, method, **kwargs)

    def _auth(self, payload: dict) -> None:
        default_secret = base64.b64encode(f"{self._CLIENT_ID}:secret".encode()).decode()
        headers = {
            "Authorization": f"Basic {default_secret}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        kwargs = {"headers": headers, "data": payload, "timeout": self._TIMEOUT}
        response = requests.post(f"{self._AUTH_API}/connect/token", **kwargs)
        response.raise_for_status()
        body = response.json()
        self._expires = datetime.utcnow() + timedelta(seconds=body["expires_in"])
        self._token = body["access_token"]
        self._refresh_token = body["refresh_token"]

    def _renew_token(self) -> None:
        payload = {"refresh_token": self._refresh_token, "grant_type": "refresh_token"}
        self._auth(payload)

    def _default_headers(self) -> dict[str, str]:
        if not self._token and self._refresh_token:
            self._renew_token()
        if not self._token:
            raise MissingLogin("You need to login!")
        return {
            "Authorization": f"Bearer {self._token}",
            "App-Version": self._APP_VERSION,
            "Operating-System": self._OS,
            "App": "com.lidl.eci.lidl.plus",
            "Accept-Language": self._language,
        }

    def tickets(self, only_favorite: bool = False) -> list[dict]:
        """List all receipts."""
        url = f"{self._TICKET_API}/{self._country}/tickets"
        kwargs = {"headers": self._default_headers(), "timeout": self._TIMEOUT}
        ticket = requests.get(f"{url}?pageNumber=1&onlyFavorite={only_favorite}", **kwargs).json()
        tickets = ticket["tickets"]
        for page in range(2, int(ticket["totalCount"] / ticket["size"] + 2)):
            tickets += requests.get(f"{url}?pageNumber={page}", **kwargs).json()["tickets"]
        return tickets

    def ticket(self, ticket_id: str) -> dict:
        """Full receipt payload by id."""
        kwargs = {"headers": self._default_headers(), "timeout": self._TIMEOUT}
        url = f"https://tickets.lidlplus.com/api/v3/{self._country}/tickets"
        return requests.get(f"{url}/{ticket_id}", **kwargs).json()

    def coupon_promotions_v1(self) -> dict:
        """Coupon list (API v1)."""
        url = f"{self._COUPONS_V1_API}/v1/promotionslist"
        kwargs = {"headers": {**self._default_headers(), "Country": self._country}, "timeout": self._TIMEOUT}
        return requests.get(url, **kwargs).json()

    def activate_coupon_promotion_v1(self, promotion_id: str) -> str:
        """Activate coupon by id (API v1)."""
        url = f"{self._COUPONS_API}/v1/promotions/{promotion_id}/activation"
        kwargs = {"headers": {**self._default_headers(), "Country": self._country}, "timeout": self._TIMEOUT}
        return requests.post(url, **kwargs).text

    def coupons(self) -> dict:
        """Coupon list (API v2)."""
        url = f"{self._COUPONS_API}/v2/promotionsList"
        headers = {**self._default_headers(), "Country": self._country}
        kwargs = {"headers": headers, "timeout": self._TIMEOUT}
        return requests.get(url, **kwargs).json()

    def activate_coupon(self, coupon_id: str) -> str:
        """Activate coupon by id."""
        url = f"{self._COUPONS_API}/v1/promotions/{coupon_id}/activation"
        kwargs = {"headers": {**self._default_headers(), "Country": self._country}, "timeout": self._TIMEOUT}
        return requests.post(url, **kwargs).text

    def deactivate_coupon(self, coupon_id: str) -> dict:
        """Deactivate coupon by id."""
        url = f"{self._COUPONS_API}/v1/{self._country}/{coupon_id}/activation"
        kwargs = {"headers": self._default_headers(), "timeout": self._TIMEOUT}
        return requests.delete(url, **kwargs).json()

    def loyalty_id(self) -> str:
        """Loyalty card ID."""
        url = f"{self._PROFILE_API}/v1/{self._country}/loyalty"
        kwargs = {"headers": self._default_headers(), "timeout": self._TIMEOUT}
        response = requests.get(url, **kwargs)
        response.raise_for_status()
        return response.text
