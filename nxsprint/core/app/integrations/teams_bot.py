"""Azure Bot Service plumbing: verify inbound activities, send replies and proactive messages."""

import time
from collections.abc import Callable

import httpx
import jwt

BOT_ISSUER = "https://api.botframework.com"
BOT_JWKS_URL = "https://login.botframework.com/v1/.well-known/keys"
TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
TOKEN_SCOPE = "https://api.botframework.com/.default"


class BotAuthError(Exception):
    pass


KeyResolver = Callable[[str], object]  # token -> signing key


def default_key_resolver() -> KeyResolver:
    client = jwt.PyJWKClient(BOT_JWKS_URL, cache_keys=True)
    return lambda token: client.get_signing_key_from_jwt(token).key


def _norm(url: str) -> str:
    return url.rstrip("/").lower()


def verify_activity(auth_header: str | None, app_id: str, service_url: str, resolve_key: KeyResolver) -> dict:
    """Check the Bearer token Microsoft puts on every inbound activity.

    Signature, issuer, audience (our app id) and expiry are checked. The token's serviceurl claim must also
    equal the activity's serviceUrl, otherwise a valid token could be replayed to point our replies, and the
    bot token we attach to them, at an attacker's host.
    """
    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise BotAuthError("missing bearer token")
    token = auth_header.split(" ", 1)[1].strip()
    try:
        claims = jwt.decode(
            token,
            resolve_key(token),
            algorithms=["RS256"],
            audience=app_id,
            issuer=BOT_ISSUER,
            options={"require": ["exp", "iss", "aud"]},
        )
    except Exception as exc:  # any decode or key lookup problem is a rejection
        raise BotAuthError(f"invalid token: {type(exc).__name__}") from exc
    claim_url = claims.get("serviceurl")
    if not claim_url or _norm(claim_url) != _norm(service_url):
        raise BotAuthError("serviceUrl does not match the token")
    return claims


class BotClient:
    """Sends messages through the Bot Framework connector with a client credentials token."""

    def __init__(
        self, app_id: str, password: str, tenant_id: str, transport: httpx.BaseTransport | None = None
    ):
        self._app_id, self._password, self._tenant = app_id, password, tenant_id
        self._http = httpx.Client(transport=transport, timeout=20)
        self._token: str | None = None
        self._expires = 0.0

    def _bearer(self) -> str:
        if self._token is None or time.time() > self._expires - 60:
            resp = self._http.post(
                TOKEN_URL.format(tenant=self._tenant),
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._app_id,
                    "client_secret": self._password,
                    "scope": TOKEN_SCOPE,
                },
            )
            resp.raise_for_status()
            body = resp.json()
            self._token, self._expires = (
                body["access_token"],
                time.time() + float(body.get("expires_in", 3600)),
            )
        return self._token

    def send_text(
        self, service_url: str, conversation_id: str, text: str, reply_to_id: str | None = None
    ) -> None:
        if not service_url.startswith("https://"):
            raise ValueError("refusing to send to a non https serviceUrl")
        activity = {"type": "message", "text": text, "textFormat": "plain"}
        if reply_to_id:
            activity["replyToId"] = reply_to_id
        resp = self._http.post(
            f"{service_url.rstrip('/')}/v3/conversations/{conversation_id}/activities",
            json=activity,
            headers={"Authorization": f"Bearer {self._bearer()}"},
        )
        resp.raise_for_status()
