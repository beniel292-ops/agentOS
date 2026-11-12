"""Clerk-like JWT signing for tests (own module so every importer shares one test key)."""
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://test-app-1.clerk.accounts.dev"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeSigningKey:
    key = KEY.public_key()



def token(*, key=KEY, org="v2", role="admin", sub="user_1", **over) -> str:
    now = int(time.time())
    claims = {"iss": ISSUER, "sub": sub, "iat": now, "nbf": now - 5, "exp": now + 60,
              "azp": "http://localhost:3000"}
    if org == "v2":
        claims |= {"v": 2, "o": {"id": "org_1", "rol": role, "slg": "acme"}}
    elif org == "v1":
        claims |= {"org_id": "org_1", "org_role": f"org:{role}", "org_slug": "acme"}
    claims |= over
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256")


