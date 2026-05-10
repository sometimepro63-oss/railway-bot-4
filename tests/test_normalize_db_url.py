from app.db.utils import normalize_db_url


def test_plain_postgresql_is_converted() -> None:
    url = "postgresql://user:pass@localhost:5432/mydb"
    assert normalize_db_url(url) == "postgresql+asyncpg://user:pass@localhost:5432/mydb"


def test_already_asyncpg_is_unchanged() -> None:
    url = "postgresql+asyncpg://user:pass@localhost:5432/mydb"
    assert normalize_db_url(url) == url


def test_other_scheme_is_unchanged() -> None:
    url = "sqlite+aiosqlite:///./test.db"
    assert normalize_db_url(url) == url


def test_empty_string_is_unchanged() -> None:
    assert normalize_db_url("") == ""


def test_railway_style_url() -> None:
    url = "postgresql://postgres:secret@monorail.proxy.rlwy.net:12345/railway"
    result = normalize_db_url(url)
    assert result == "postgresql+asyncpg://postgres:secret@monorail.proxy.rlwy.net:12345/railway"

