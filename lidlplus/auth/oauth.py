"""OAuth PKCE browser login via oic."""

from __future__ import annotations

import base64
import logging
import re
import webbrowser
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

import requests

from lidlplus.exceptions import LoginError, WebBrowserException

if TYPE_CHECKING:
    from lidlplus.api import LidlPlusApi

try:
    from oic.oic import Client as OicClient
    from oic.utils.authn.client import CLIENT_AUTHN_METHOD
except ImportError:
    OicClient = None
    CLIENT_AUTHN_METHOD = None

log = logging.getLogger(__name__)


def parse_oauth_code(value: str) -> str:
    """Extract an OAuth authorization code from a callback URL or raw code."""
    value = value.strip().strip("'\"")
    if not value:
        raise ValueError("Authorization callback is empty.")
    if "://" in value or "?" in value or value.startswith("code="):
        query = value.split("?", 1)[-1] if "?" in value else value
        params = parse_qs(query.lstrip("?"))
        if codes := params.get("code"):
            return codes[0]
    if match := re.search(r"(?:^|[?&])code=([0-9A-Fa-f]+)", value):
        return match.group(1)
    if re.fullmatch(r"[0-9A-Fa-f]+", value):
        return value
    raise ValueError(f"Could not find authorization code in: {value!r}")


class OAuthAuth:
    """Manual OAuth PKCE login for Lidl Plus."""

    def __init__(self, api: LidlPlusApi):
        self._api = api

    def _require_oic(self) -> None:
        if OicClient is None:
            raise ImportError("OAuth login requires oic (installed by default with lidl-plus).")

    def _create_oauth_client(self):
        self._require_oic()
        client = OicClient(client_authn_method=CLIENT_AUTHN_METHOD, client_id=self._api._CLIENT_ID)
        client.provider_config(self._api._AUTH_API)
        client.client_secret = "secret"
        self._api._oauth_client = client
        return client

    def _register_oauth_client(self) -> str:
        if self._api._login_url:
            return self._api._login_url
        client = self._create_oauth_client()
        code_challenge, self._api._code_verifier = client.add_code_challenge()
        args = {
            "client_id": client.client_id,
            "response_type": "code",
            "scope": self._api._SCOPES,
            "redirect_uri": self._api._REDIRECT_URI,
            **code_challenge,
        }
        auth_req = client.construct_AuthorizationRequest(
            request_args=args,
            state=self._api._OAUTH_STATE,
        )
        self._api._login_url = auth_req.request(client.authorization_endpoint)
        return self._api._login_url

    @property
    def register_link(self) -> str:
        args = {
            "Country": self._api._country,
            "language": f"{self._api._language}-{self._api._country}",
        }
        params = "&".join(f"{key}={value}" for key, value in args.items())
        return f"{self._register_oauth_client()}&{params}"

    def exchange_authorization_code(self, code: str) -> dict:
        if not self._api._code_verifier:
            raise LoginError("Missing PKCE verifier. Start browser-auth from the beginning.")
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._api._REDIRECT_URI,
            "code_verifier": self._api._code_verifier,
            "client_id": self._api._CLIENT_ID,
        }
        default_secret = base64.b64encode(f"{self._api._CLIENT_ID}:secret".encode()).decode()
        headers = {
            "Authorization": f"Basic {default_secret}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        response = requests.post(
            f"{self._api._AUTH_API}/connect/token",
            headers=headers,
            data=payload,
            timeout=self._api._TIMEOUT,
        )
        try:
            body = response.json()
        except ValueError as error:
            raise LoginError(f"Token exchange failed: {response.text}") from error
        if not response.ok:
            raise LoginError(
                f"{body.get('error', response.status_code)}: "
                f"{body.get('error_description', '')}".strip(": ")
            )
        self._api.apply_token_response(body)
        return body

    def browser_auth(self, *, open_browser: bool = False, input_func=input):
        """
        Log in via the system browser and exchange the OAuth callback for tokens.

        Uses oic for PKCE/OAuth2. Does not open a browser by default so DevTools
        can be opened before navigating to the login URL.
        """
        auth_url = self.register_link
        print("\n=== Lidl Plus browser login ===\n")
        print("1. Open your browser and DevTools (F12) → Network tab — keep it open.")
        print("2. Open the login URL below and sign in (complete 2FA if prompted).")
        print("3. After login the browser tries to open com.lidlplus.app:// — it cannot,")
        print("   so the address bar will NOT show the callback URL (request shows as canceled).")
        print("4. In Network, find the canceled callback?code=... request.")
        print("   Copy Request URL from Headers, or paste just the code value below.\n")
        print("Login URL:\n")
        print(auth_url)
        print()
        if open_browser:
            print("Opening your default browser...\n")
            if not webbrowser.open(auth_url, new=2):
                print("Could not open a browser automatically — open the URL above manually.\n")
        while True:
            callback = input_func("Paste callback URL or authorization code: ").strip()
            if not callback:
                print("Cancelled.")
                raise KeyboardInterrupt
            try:
                code = parse_oauth_code(callback)
            except ValueError as error:
                print(f"Could not read authorization code: {error}\n")
                continue
            try:
                self.exchange_authorization_code(code)
            except LoginError as error:
                print(f"Token exchange failed: {error}\n")
                continue
            except Exception as error:  # pylint: disable=broad-except
                print(f"Token exchange failed: {error}\n")
                continue
            print("\nLogin successful.\n")
            return self._api
