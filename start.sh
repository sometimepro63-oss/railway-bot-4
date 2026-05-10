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

alembic upgrade head
exec uvicorn app.main_api:app --host 0.0.0.0 --port "${PORT:-8000}"
