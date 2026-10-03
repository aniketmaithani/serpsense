"""The one owner check for user-scoped reads (AGENTS §4, §1 future scope).

A scoped query names the user's brands as `{owned}` and binds `:user`; `scoped` puts the check in
and refuses a query that doesn't use it, so no query can read another owner's rows on its own,
and widening ownership later (teams) is a change here only. Archived brands are not owned for
reading: they read as missing.
"""

import uuid

from sqlalchemy import TextClause, TextualSelect, Uuid, column, text

OWNED = "SELECT id FROM brands WHERE owner_id = :user AND archived_at IS NULL"


def scoped(*parts: str) -> TextClause:
    """The query `parts` make, with `{owned}` replaced by the owner check; values stay bound
    parameters. Parts let queries share a fragment, such as a common table expression."""
    sql = "".join(parts)
    if "{owned}" not in sql:
        raise ValueError("a user-scoped query must reach its rows through {owned}")
    return text(sql.replace("{owned}", OWNED))


def owned_ids(user_id: uuid.UUID) -> TextualSelect:
    """The same owner check as a subquery of brand ids, for queries built with SQLAlchemy Core."""
    return text(OWNED).bindparams(user=user_id).columns(column("id", Uuid))
