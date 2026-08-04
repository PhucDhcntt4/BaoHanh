from contextlib import contextmanager
from typing import Iterator

import psycopg # type: ignore
from psycopg import Connection # type: ignore
from psycopg.rows import dict_row # type: ignore

from app.config import DATABASE_URL


def require_database_url() -> str:
    if not DATABASE_URL:
        raise RuntimeError(
            "Thiếu DATABASE_URL. Hãy cấu hình PostgreSQL trong file .env."
        )
    return DATABASE_URL


@contextmanager
def database_connection() -> Iterator[Connection]:
    with psycopg.connect(
        require_database_url(),
        row_factory=dict_row,
    ) as connection:
        yield connection
