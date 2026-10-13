"""Builders shared by the jwks_publisher tests."""

import json


def make_key(kid="kid-a", **overrides):
    """A well-formed public RSA signing key."""
    key = {"kty": "RSA", "alg": "RS256", "use": "sig", "kid": kid, "n": "bW9kdWx1cw", "e": "AQAB"}
    key.update(overrides)
    return key


def jwks_text(*keys):
    """Serialise a key set the way the API server does (compact)."""
    return json.dumps({"keys": list(keys)}, separators=(",", ":"))
