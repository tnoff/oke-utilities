"""Read the cluster's live key set and check it is safe to publish."""

import json
import ssl
import urllib.request
from pathlib import Path

# JWK members that carry private key material (RFC 7518 sections 6.2.2, 6.3.2
# and 6.4). The hosted file is world readable, so any key that has one is
# rejected outright rather than stripped.
PRIVATE_MEMBERS = frozenset({"d", "p", "q", "dp", "dq", "qi", "oth", "k"})


class JwksError(Exception):
    """The key set is unreadable or not safe to publish."""


def fetch_live_jwks(url: str, service_account_dir: str, timeout: int) -> bytes:
    """GET the key set from the API server with this pod's ServiceAccount."""
    if not url.startswith("https://"):
        raise JwksError(f"refusing a non-https JWKS url: {url}")
    sa_dir = Path(service_account_dir)
    token = (sa_dir / "token").read_text(encoding="utf-8").strip()
    context = ssl.create_default_context(cafile=str(sa_dir / "ca.crt"))
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    # The scheme is checked above, so a file: or ftp: url cannot get here.
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:  # nosec B310
        return response.read()


def canonicalize(body: bytes | str) -> tuple[str, list[str]]:
    """Validate a key set and return (canonical text, sorted key ids).

    Canonical form sorts keys by `kid` and drops insignificant whitespace, so two
    copies of the same set compare equal whatever order the API server used.
    """
    try:
        document = json.loads(body)
    except ValueError as exc:
        raise JwksError(f"key set is not valid JSON: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("keys"), list) or not document["keys"]:
        raise JwksError("key set has no keys")

    seen = set()
    for key in document["keys"]:
        if not isinstance(key, dict):
            raise JwksError("a key is not an object")
        leaked = PRIVATE_MEMBERS & key.keys()
        if leaked:
            raise JwksError(f"key carries private members {sorted(leaked)}; refusing to publish it")
        if key.get("kty") != "RSA":
            raise JwksError(f"unsupported key type {key.get('kty')!r}")
        for member in ("kid", "n", "e"):
            if not isinstance(key.get(member), str) or not key[member]:
                raise JwksError(f"key is missing {member}")
        if key.get("alg", "RS256") != "RS256" or key.get("use", "sig") != "sig":
            raise JwksError(f"key {key['kid']} is not an RS256 signing key")
        if key["kid"] in seen:
            raise JwksError(f"duplicate kid {key['kid']}")
        seen.add(key["kid"])

    keys = sorted(document["keys"], key=lambda item: item["kid"])
    return json.dumps({"keys": keys}, sort_keys=True, separators=(",", ":")), sorted(seen)
