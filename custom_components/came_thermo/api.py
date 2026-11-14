"""Async client for the (unofficial) CAME Connect thermostat cloud API.

Reverse engineered from the CAME Connect web app
(https://www.cameconnect.net / https://app.cameconnect.net).

Auth flow
---------
The web app authenticates in two steps, both against https://app.cameconnect.net/api:

1. ``POST /oauth/auth-code`` with the OAuth client credentials as HTTP Basic auth
   plus the account credentials in the body. It answers ``{"code": ..., "state": ...}``.
2. ``POST /oauth/token`` with that code and the PKCE ``code_verifier`` matching the
   ``code_challenge`` used in step 1. It answers an access token valid for ~2 hours.

The access token is accepted by both the main API and the thermostat API
(``https://thermo.cameconnect.net``).

Thermostat API
--------------
* ``GET  /v2/devices``                -> groups with their thermostat devices
* ``GET  /devices/{keycode}``         -> full thermostat state
* ``POST /control/{keycode}/{keycode}`` with a JSON patch -> change state
* ``wss://thermo.cameconnect.net:8443/WebSocket/{keycode}/{keycode}``
  subprotocols ``['bearer', <access_token>]`` -> live pushes such as ``{"temp_loc": 20.8}``
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets
import time
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

API_BASE = "https://app.cameconnect.net/api"
THERMO_BASE = "https://thermo.cameconnect.net"
THERMO_WS = "wss://thermo.cameconnect.net:8443/WebSocket"

# The OAuth client of the official web app. CAME publishes no developer portal, so
# the identity of their own app has to be supplied by each user (see README). There
# is deliberately no hard-coded default here.
DEFAULT_CLIENT_ID = ""
DEFAULT_CLIENT_SECRET = ""

# The OAuth client of the web app is registered for exactly this redirect URI and
# performs a real PKCE exchange, so a code obtained with a given verifier must be
# redeemed with that same verifier.
REDIRECT_URI = "https://app.cameconnect.net/role"


class CameAuthError(Exception):
    """Raised when the account credentials are rejected."""


class CameApiError(Exception):
    """Raised on unexpected API responses."""


class CameThermoAuth:
    """Handles OAuth login and token refresh."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        client_id: str = DEFAULT_CLIENT_ID,
        client_secret: str = DEFAULT_CLIENT_SECRET,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._client_id = client_id
        self._client_secret = client_secret
        self._access_token: str | None = None
        self._expires_at: float = 0
        self._lock = asyncio.Lock()

    def _require_client(self) -> None:
        """The OAuth client has to come from the user; fail with a clear message."""
        if not self._client_id or not self._client_secret:
            raise CameAuthError(
                "no OAuth client configured: set the CAME Connect client_id and "
                "client_secret in the integration options (see README)"
            )

    @property
    def access_token(self) -> str | None:
        if self._access_token and time.time() < self._expires_at - 120:
            return self._access_token
        return None

    async def async_get_token(self, force: bool = False) -> str:
        """Return a valid access token, logging in again when needed."""
        async with self._lock:
            if not force and self.access_token:
                return self.access_token
            await self._async_login()
            assert self._access_token is not None
            return self._access_token

    async def _async_login(self) -> None:
        self._require_client()
        # PKCE pair: a fresh verifier per login, challenge = S256(verifier).
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("utf-8")).digest())
            .decode()
            .rstrip("=")
        )

        auth_code_url = API_BASE + "/oauth/auth-code"
        params = {
            "client_id": self._client_id,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "state": secrets.token_urlsafe(24),
            "nonce": secrets.token_urlsafe(24),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        data = {
            "grant_type": "authorization_code",
            "username": self._username,
            "password": self._password,
            "client_id": self._client_id,
        }
        async with self._session.post(
            auth_code_url,
            params=params,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
            auth=aiohttp.BasicAuth(self._client_id, self._client_secret),
        ) as resp:
            body = await _json_or_text(resp)
            if resp.status >= 400 or not isinstance(body, dict) or "code" not in body:
                raise CameAuthError(f"login failed ({resp.status}): {body}")

        async with self._session.post(
            API_BASE + "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": body["code"],
                "redirect_uri": REDIRECT_URI,
                "client_id": self._client_id,
                "code_verifier": verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
            auth=aiohttp.BasicAuth(self._client_id, self._client_secret),
        ) as resp:
            token = await _json_or_text(resp)
            if resp.status >= 400 or not isinstance(token, dict) or "access_token" not in token:
                raise CameAuthError(f"token exchange failed ({resp.status}): {token}")

        self._access_token = token["access_token"]
        self._expires_at = time.time() + float(token.get("expires_in", 3600))
        _LOGGER.debug("CAME Connect login ok, token valid for %ss", token.get("expires_in"))


async def _json_or_text(resp: aiohttp.ClientResponse) -> Any:
    text = await resp.text()
    try:
        return await resp.json(content_type=None)
    except Exception:  # noqa: BLE001 - the API sometimes answers HTML error pages
        return text[:300]


class CameThermoApi:
    """Small typed wrapper over the thermostat endpoints."""

    def __init__(self, session: aiohttp.ClientSession, auth: CameThermoAuth) -> None:
        self._session = session
        self._auth = auth

    async def _request(
        self,
        method: str,
        path: str,
        *,
        base: str = THERMO_BASE,
        json: dict[str, Any] | None = None,
    ) -> Any:
        for attempt in range(2):
            token = await self._auth.async_get_token(force=attempt > 0)
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            async with self._session.request(
                method, base + path, headers=headers, json=json
            ) as resp:
                if resp.status == 401 and attempt == 0:
                    continue
                body = await _json_or_text(resp)
                if resp.status >= 400:
                    raise CameApiError(f"{method} {path} -> {resp.status}: {body}")
                return body
        raise CameApiError(f"{method} {path} -> unauthorized")

    async def async_get_devices(self) -> list[dict[str, Any]]:
        """Return every thermostat device reachable by the account."""
        groups = await self._request("GET", "/v2/devices")
        if isinstance(groups, dict):  # older shape: {"content": {"ownedGroups": [...]}}
            content = groups.get("content") or groups
            groups = list(content.get("ownedGroups") or []) + list(
                content.get("sharedGroups") or []
            )
        devices: dict[str, dict[str, Any]] = {}
        for group in groups or []:
            for item in group.get("items") or []:
                keycode = item.get("keycode") or item.get("devcode")
                if keycode and keycode not in devices:
                    devices[keycode] = {
                        "keycode": keycode,
                        "name": (item.get("cameconnect") or {}).get("Name")
                        or item.get("Description")
                        or keycode,
                        "model": (item.get("cameconnect") or {}).get("ProductTypeName")
                        or item.get("ProductTypeName"),
                        "device_id": (item.get("cameconnect") or {}).get("Id")
                        or item.get("deviceid"),
                        "group": group.get("name"),
                        "raw": item,
                    }
        return list(devices.values())

    async def async_get_device(self, keycode: str) -> dict[str, Any]:
        """Return the full state of one thermostat."""
        data = await self._request("GET", f"/devices/{keycode}")
        if not isinstance(data, dict):
            raise CameApiError(f"unexpected device payload for {keycode}: {data!r}")
        return data

    async def async_send_command(self, keycode: str, payload: dict[str, Any]) -> Any:
        """Push a partial state patch to the thermostat."""
        body = {"keycode": keycode, **payload}
        return await self._request("POST", f"/control/{keycode}/{keycode}", json=body)


def thermostat_ws_url(keycode: str) -> str:
    return f"{THERMO_WS}/{keycode}/{keycode}"
