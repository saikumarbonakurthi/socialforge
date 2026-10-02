import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

APP_ID = "00000000-aaaa-bbbb-cccc-000000000001"
SERVICE_URL = "https://smba.trafficmanager.net/in/"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC = KEY.public_key()


def resolver(token):
    return PUBLIC


def make_token(
    *, aud=APP_ID, iss="https://api.botframework.com", serviceurl=SERVICE_URL, exp_in=600, key=KEY
):
    claims = {"aud": aud, "iss": iss, "exp": int(time.time()) + exp_in, "serviceurl": serviceurl}
    return jwt.encode(claims, key, algorithm="RS256")


def activity(text="", *, who="demo-ravi", kind="message", **extra):
    base = {
        "type": kind,
        "id": "act-1",
        "serviceUrl": SERVICE_URL,
        "conversation": {"id": f"conv-{who}"},
        "from": {"id": f"29:{who}", "aadObjectId": who},
        "text": text,
    }
    base.update(extra)
    return base
