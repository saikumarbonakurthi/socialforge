"""WhatsApp Cloud API: approved template messages only (outside the 24 hour window nothing else is allowed)."""

import re

import httpx

GRAPH = "https://graph.facebook.com"


class WhatsAppError(Exception):
    pass


def digits(number: str) -> str:
    """The Cloud API wants the number with country code and no plus or spaces."""
    d = re.sub(r"[\s+]", "", number)
    if not re.fullmatch(r"[1-9]\d{7,14}", d):
        raise WhatsAppError("not an international phone number")
    return d


class WhatsAppClient:
    def __init__(
        self, token: str, phone_number_id: str, api_version: str, transport: httpx.BaseTransport | None = None
    ):
        self._url = f"{GRAPH}/{api_version}/{phone_number_id}/messages"
        self._http = httpx.Client(
            transport=transport, timeout=20, headers={"Authorization": f"Bearer {token}"}
        )

    def send_template(self, to: str, template: str, language: str, params: list[str]) -> str:
        """Send one approved template with text body parameters. Returns the WhatsApp message id."""
        body = {
            "messaging_product": "whatsapp",
            "to": digits(to),
            "type": "template",
            "template": {
                "name": template,
                "language": {"code": language},
                "components": [{"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}],
            },
        }
        resp = self._http.post(self._url, json=body)
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("error", {}).get("message", "")
            except ValueError:
                detail = ""
            # Never include the request body, it holds the phone number.
            raise WhatsAppError(f"HTTP {resp.status_code}: {detail[:200]}")
        return resp.json()["messages"][0]["id"]
