import json

import httpx
import pytest

from app.integrations.teams_bot import BotAuthError, BotClient, verify_activity
from tests.bot_helpers import APP_ID, OTHER_KEY, SERVICE_URL, make_token, resolver


def check(token, service_url=SERVICE_URL, app_id=APP_ID):
    return verify_activity(f"Bearer {token}" if token else None, app_id, service_url, resolver)


def test_valid_token_passes():
    assert check(make_token())["aud"] == APP_ID


@pytest.mark.parametrize(
    "token",
    [
        make_token(aud="someone-else"),
        make_token(iss="https://evil.example"),
        make_token(exp_in=-10),
        make_token(key=OTHER_KEY),  # signed by a key we do not trust
        make_token(serviceurl="https://attacker.example/"),
        "not.a.jwt",
        None,
    ],
    ids=["audience", "issuer", "expired", "signature", "serviceurl", "garbage", "missing"],
)
def test_bad_tokens_are_rejected(token):
    with pytest.raises(BotAuthError):
        check(token)


def test_serviceurl_in_activity_must_match_token_even_if_token_is_valid():
    with pytest.raises(BotAuthError, match="serviceUrl"):
        check(make_token(), service_url="https://attacker.example/")


def test_serviceurl_comparison_ignores_trailing_slash_and_case():
    assert check(make_token(), service_url=SERVICE_URL.rstrip("/").upper())


def test_non_bearer_header_rejected():
    with pytest.raises(BotAuthError):
        verify_activity("Basic abc", APP_ID, SERVICE_URL, resolver)


def test_client_gets_token_once_and_posts_to_the_connector():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        if "login.microsoftonline.com/TENANT/" in str(request.url):
            return httpx.Response(200, json={"access_token": "TOK", "expires_in": 3600})
        return httpx.Response(200, json={"id": "x"})

    bot = BotClient("app", "pw", "TENANT", transport=httpx.MockTransport(handler))
    bot.send_text(SERVICE_URL, "conv1", "hello", reply_to_id="a1")
    bot.send_text(SERVICE_URL, "conv1", "again")
    token_calls = [c for c in calls if "microsoftonline" in str(c.url)]
    sends = [c for c in calls if "microsoftonline" not in str(c.url)]
    assert len(token_calls) == 1 and len(sends) == 2  # token is cached
    assert (
        b"client_credentials" in token_calls[0].content and b"api.botframework.com" in token_calls[0].content
    )
    assert str(sends[0].url) == f"{SERVICE_URL}v3/conversations/conv1/activities"
    assert sends[0].headers["authorization"] == "Bearer TOK"
    body = json.loads(sends[0].content)
    assert body == {"type": "message", "text": "hello", "textFormat": "plain", "replyToId": "a1"}


def test_client_refuses_plain_http_service_url():
    bot = BotClient("app", "pw", "T", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    with pytest.raises(ValueError, match="https"):
        bot.send_text("http://insecure.example/", "c", "x")


def test_client_surfaces_connector_errors():
    def handler(request):
        if "microsoftonline" in str(request.url):
            return httpx.Response(200, json={"access_token": "T", "expires_in": 3600})
        return httpx.Response(429)

    bot = BotClient("app", "pw", "T", transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        bot.send_text(SERVICE_URL, "c", "x")
