"""Set the initial Owner password.

The migration seeds an Owner with ``password_hash = '!UNSET'``.
This script replaces the sentinel with a real Argon2id hash.

Usage::

    python scripts/seed_owner_password.py --username owner --password 'Secret!123'

After running, the Owner can log in via ``POST /auth/login``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Ensure the backend package is importable when this script is run
# directly via `python scripts/seed_owner_password.py` (without `pip
# install -e .`).
_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.config import get_settings  # noqa: E402
from app.core.security import PasswordHasher  # noqa: E402
from app.db import UnitOfWork, init_db  # noqa: E402


async def _set_password(username: str, password: str) -> None:
    hasher = PasswordHasher()
    new_hash = hasher.hash(password)
    # init_db first (same event loop).
    settings = get_settings()
    await init_db(settings)
    async with UnitOfWork() as uow:
        row = await uow.first_row(
            "SELECT id, username FROM users WHERE username = :u AND password_hash = '!UNSET'",
            {"u": username},
        )
        if row is None:
            print(f"ERROR: user '{username}' not found or password already set.", file=sys.stderr)
            await uow.rollback()
            return
        await uow.execute(
            "UPDATE users SET password_hash = :h, updated_at = NOW() WHERE id = :id",
            {"h": new_hash, "id": int(row["id"])},
        )
        await uow.commit()
        print(f"Password set for user '{username}' (id={row['id']}).")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Set the initial Owner password.")
    p.add_argument("--username", required=True, help="Target username (default: owner)")
    p.add_argument("--password", required=True, help="New password (min 8 chars)")
    p.set_defaults(username="owner")
    # argparse: make --username have a default without breaking required
    args = p.parse_args(argv)
    if not args.username:
        args.username = "owner"
    if len(args.password) < 8:
        print("ERROR: password must be at least 8 characters.", file=sys.stderr)
        return 2
    asyncio.run(_set_password(args.username, args.password))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
