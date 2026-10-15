"""Builders shared by the rpst_sidecar tests."""

import base64
import io
import json
import urllib.error


def b64url(data: bytes) -> str:
    """Unpadded base64url, as JWTs use."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_jwt(**claims) -> str:
    """An unsigned-looking JWT with the given payload claims."""
    header = b64url(json.dumps({"alg": "RS256"}).encode())
    payload = b64url(json.dumps(claims).encode())
    return f"{header}.{payload}.c2ln"


class FakeResponse:
    """What urlopen's context manager yields."""

    def __init__(self, body, status=200):
        self.status = status
        self._body = body if isinstance(body, bytes) else body.encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def ok_response(rpst):
    """A 200 carrying `rpst`."""
    return FakeResponse(json.dumps({"token": rpst}))


def http_error(code, body):
    """An HTTPError whose body can be read."""
    return urllib.error.HTTPError("https://x/oauth2/v1/token", code, "err", {}, io.BytesIO(body.encode()))
