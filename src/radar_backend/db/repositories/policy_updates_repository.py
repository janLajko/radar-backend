from __future__ import annotations

from psycopg import Connection


def foo(conn: Connection) -> None:
    """
    All repository functions must receive an explicit connection from the caller.
    """
    pass
