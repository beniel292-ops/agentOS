"""Create (or replace) a scoped Chief of Staff agent key from the command line.

Usage (prints ONLY the plaintext key, so it can be piped straight into a config file):
    uv run python -m scripts.create_agent_key --tenant <uuid> --name hermes \
        --scopes opportunities:write,opportunities:read,tasks:read,memory:read

An existing active key with the same name is revoked first. The key is stored hashed.
"""
import argparse
import secrets
import sys
import uuid
from datetime import UTC, datetime

from sqlalchemy import update

from app.cos_auth import SCOPES
from app.db import SessionLocal
from app.intake import sha256_hex
from app.models import AgentKey, AuditEvent


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--tenant", required=True, type=uuid.UUID)
    p.add_argument("--name", required=True)
    p.add_argument("--scopes", required=True)
    a = p.parse_args()
    scopes = sorted({s.strip() for s in a.scopes.split(",") if s.strip()})
    unknown = set(scopes) - SCOPES
    if unknown:
        sys.exit(f"unknown scopes: {sorted(unknown)}")
    key = "cos_" + secrets.token_urlsafe(32)
    with SessionLocal() as session:
        session.execute(
            update(AgentKey)
            .where(AgentKey.tenant_id == a.tenant, AgentKey.name == a.name,
                   AgentKey.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        session.add(AgentKey(tenant_id=a.tenant, name=a.name, scopes=scopes,
                             key_hash=sha256_hex(key), key_prefix=key[:12]))
        session.add(AuditEvent(tenant_id=a.tenant, lead_id=None, actor="cli",
                               action="agent_key_created",
                               data={"name": a.name, "scopes": scopes}))
        session.commit()
    print(key)


if __name__ == "__main__":
    main()
