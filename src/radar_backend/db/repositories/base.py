from __future__ import annotations


class BaseRepository:
    """Base class for table repositories.

    Query methods must receive an explicit connection from the caller.
    """
