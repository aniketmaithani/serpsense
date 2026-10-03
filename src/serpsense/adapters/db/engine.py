"""Database engine factory."""

from sqlalchemy import Engine, create_engine

# A lock wait or an abandoned transaction must never stall the dispatcher, a worker or a page.
SESSION_OPTIONS = "-c lock_timeout=15000 -c idle_in_transaction_session_timeout=60000"


def create_db_engine(url: str) -> Engine:
    return create_engine(
        url,
        pool_pre_ping=True,
        # A statement error never carries its bound values (emails, payloads) into a log.
        hide_parameters=True,
        pool_size=5,
        max_overflow=5,
        connect_args={"connect_timeout": 5, "options": SESSION_OPTIONS},
    )
