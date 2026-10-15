"""The token exchange: one POST to the identity domain, plus JWT payload reading."""

import base64
import binascii
import json
import urllib.error
import urllib.parse
import urllib.request
from logging import getLogger
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from .config import RpstConfig

logger = getLogger(__name__)

GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"
REQUESTED_TOKEN_TYPE = "urn:oci:token-type:oci-rpst"  # nosec B105 - a token type URN, not a credential
ERROR_BODY_CHARS = 200


class ExchangeError(Exception):
    """The exchange failed. Messages never contain the client secret or any token."""


def urlopen(request: urllib.request.Request, timeout: int):
    """The module's single HTTP seam; tests patch this."""
    # The URL is validated as https by RpstConfig.
    return urllib.request.urlopen(request, timeout=timeout)  # nosec B310


def generate_key() -> rsa.RSAPrivateKey:
    """A fresh RSA-2048 key; generated once per process."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def private_pem(key: rsa.RSAPrivateKey) -> bytes:
    """Unencrypted PKCS8 PEM, the form the OCI SDK reads."""
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def public_key_b64(key: rsa.RSAPrivateKey) -> str:
    """Standard base64 of the DER SubjectPublicKeyInfo."""
    der = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(der).decode("ascii")


def jwt_claims(token: str) -> dict[str, Any]:
    """Payload claims of a JWT, decoded WITHOUT verifying the signature.

    Used only for scheduling and logging. Returns {} for anything unparseable.
    """
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    segment = parts[1]
    try:
        payload = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
    except (binascii.Error, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def read_secret_file(path: Path) -> str:
    """File contents with surrounding whitespace removed."""
    return path.read_text(encoding="utf-8").strip()


def redact(text: str, secrets: tuple[str, ...]) -> str:
    """The first ERROR_BODY_CHARS of `text`, with any credential that was echoed back removed."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text[:ERROR_BODY_CHARS]


def exchange(cfg: RpstConfig, key: rsa.RSAPrivateKey) -> tuple[str, str]:
    """Trade the current ServiceAccount token for an RPST.

    Returns (rpst, service_account_token). Raises ExchangeError on any failure.
    Credentials and the SA token are re-read on every call: both rotate underneath us.
    """
    try:
        client_id = read_secret_file(Path(cfg.client_dir) / "client_id")
        client_secret = read_secret_file(Path(cfg.client_dir) / "client_secret")
        sa_token = "".join(read_secret_file(Path(cfg.token_file)).split())
    except OSError as exc:
        raise ExchangeError(f"cannot read an input file: {exc.strerror} ({exc.filename})") from exc

    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    secrets = (client_secret, sa_token, basic)
    form = urllib.parse.urlencode(
        {
            "grant_type": GRANT_TYPE,
            "requested_token_type": REQUESTED_TOKEN_TYPE,
            "subject_token_type": "jwt",  # nosec B105 - a token type name
            "subject_token": sa_token,
            "public_key": public_key_b64(key),
            "res_type": "k8sworkload",
            "rpst_exp": str(cfg.exp_minutes),
        }
    ).encode("ascii")
    request = urllib.request.Request(
        f"{cfg.domain_url}/oauth2/v1/token",
        data=form,
        method="POST",
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )

    try:
        with urlopen(request, cfg.request_timeout) as response:
            status = response.status
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = redact(exc.read().decode("utf-8", "replace"), secrets)
        raise ExchangeError(f"HTTP {exc.code}: {detail}") from exc
    except OSError as exc:
        # URLError and timeouts are OSErrors.
        raise ExchangeError(redact(f"request failed: {exc}", secrets)) from exc

    text = body.decode("utf-8", "replace")
    if status != 200:
        raise ExchangeError(f"HTTP {status}: {redact(text, secrets)}")
    try:
        token = json.loads(text).get("token")
    except (ValueError, AttributeError) as exc:
        raise ExchangeError("response is not a JSON object") from exc
    if not isinstance(token, str) or not token:
        raise ExchangeError("response has no token")
    return token, sa_token
