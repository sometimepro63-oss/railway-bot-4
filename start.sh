set -e

echo "Checking env variables..."
if [ -z "$DATABASE_URL" ]; then
  echo "DATABASE_URL is missing"
else
  echo "DATABASE_URL is present"
fi

if [ -z "$DATABASE_PUBLIC_URL" ]; then
  echo "DATABASE_PUBLIC_URL is missing"
else
  echo "DATABASE_PUBLIC_URL is present"
fi

env | grep -E "DATABASE|POSTGRES|PG" | sed -E 's/=.*/=*** /' || true

echo "Waiting for database..."
python - <<'PY'
import asyncio
import os
import sys

import asyncpg

def pick_db_url() -> str:
    for key in ("DATABASE_URL", "DATABASE_PUBLIC_URL", "POSTGRES_URL", "POSTGRES_PUBLIC_URL"):
        val = os.getenv(key)
        if val and val.strip():
            return val.strip()
    return ""

def to_asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")

async def main() -> None:
    url = pick_db_url()
    if not url:
        print("Database URL is missing (DATABASE_URL / DATABASE_PUBLIC_URL / POSTGRES_URL / POSTGRES_PUBLIC_URL).", file=sys.stderr)
        sys.exit(2)

    dsn = to_asyncpg_dsn(url)
    deadline_s = int(os.getenv("DB_WAIT_SECONDS", "90"))
    interval_s = float(os.getenv("DB_WAIT_INTERVAL_SECONDS", "2"))

    last_err: Exception | None = None
    for _ in range(max(1, int(deadline_s / interval_s))):
        try:
            conn = await asyncpg.connect(dsn, timeout=5)
            await conn.execute("SELECT 1;")
            await conn.close()
            print("Database is reachable.")
            return
        except Exception as e:
            last_err = e
            await asyncio.sleep(interval_s)

    print(f"Database is not reachable after {deadline_s}s: {last_err}", file=sys.stderr)
    sys.exit(1)

asyncio.run(main())
PY

alembic upgrade head
exec uvicorn app.main_api:app --host 0.0.0.0 --port "${PORT:-8000}"
