#!/usr/bin/env python3
"""One-time migration: add the language preference row to system_settings.

Run: cd backend && .venv/Scripts/python.exe scripts/add_language_setting.py
"""

import asyncio
import asyncpg

DB_URL = "postgresql://postgres@127.0.0.1:5433/pos_dev"


async def main() -> None:
    conn = await asyncpg.connect(DB_URL)
    try:
        existing = await conn.fetchval(
            "SELECT key FROM system_settings WHERE key = 'language'"
        )
        if existing:
            print("Language setting row already exists.")
            return
        await conn.execute(
            """
            INSERT INTO system_settings (key, value, value_type, updated_by)
            VALUES ('language', 'en', 'string', 1)
            """
        )
        print("Language setting row added with default 'en'.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
