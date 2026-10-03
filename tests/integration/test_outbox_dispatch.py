"""The outbox dispatcher on real Postgres (ADR-0010): due emails sent once, retries backing off
until dead, and two dispatchers never sending the same email. Each test has a database of its
own, cloned from a migrated template, since the dispatcher claims any due email."""

import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Engine, make_url, select, text

from serpsense.adapters.db.engine import create_db_engine
from serpsense.adapters.db.outbox import SqlOutbox
from serpsense.adapters.db.unit_of_work import SqlUnitOfWork
from serpsense.adapters.mail.fake import FakeMailer
from serpsense.domain import outbox as rules
from serpsense.domain.enums import OutboxOutcome, OutboxStatus
from serpsense.ports.mailer import Email, MailFailed
from serpsense.services.outbox import FOOTER, OutboxDispatcher
from tests.fakes import FixedClock
from tests.integration.conftest import alembic_config
from tests.integration.db_helpers import NOW, add, add_brand, add_scan, add_user, table

pytestmark = pytest.mark.integration

MESSAGES, ATTEMPTS = table("outbox_messages"), table("outbox_attempts")
TIMEOUT = MailFailed("smtp.timeout", retryable=True)


class NoJobs:
    def run_scan(self, scan_id: uuid.UUID) -> None:
        raise AssertionError("the dispatcher queues no scans")


def _url(postgres_url: str, database: str) -> str:
    return make_url(postgres_url).set(database=database).render_as_string(hide_password=False)


@pytest.fixture(scope="module")
def template(postgres_url: str) -> str:
    admin = create_db_engine(postgres_url).execution_options(isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text("CREATE DATABASE outbox_template"))
    admin.dispose()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", _url(postgres_url, "outbox_template"))
        command.upgrade(alembic_config(), "head")
    return "outbox_template"


@pytest.fixture
def engine(postgres_url: str, template: str) -> Iterator[Engine]:
    name = f"outbox_{uuid.uuid4().hex[:12]}"
    admin = create_db_engine(postgres_url).execution_options(isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f"CREATE DATABASE {name} TEMPLATE {template}"))
    admin.dispose()
    engine = create_db_engine(_url(postgres_url, name))
    yield engine
    engine.dispose()


def queue(engine: Engine, *, at: datetime = NOW, **values: Any) -> uuid.UUID:
    """An alert email for a brand of its own, due at `at`."""
    with engine.begin() as conn:
        owner = add_user(conn, f"{uuid.uuid4().hex[:8]}@example.com")
        scan_id = add_scan(conn, add_brand(conn, owner), status="succeeded")
        alert = add(conn, table("alerts"), scan_id=scan_id, rule="level_increase", created_at=at)
        row: dict[str, Any] = {
            "kind": "alert_email",
            "user_id": owner,
            "alert_id": alert,
            "recipient_email": "owner@example.com",
            "template": "alert",
            "template_data": {"title": "Ola: crisis level rose to high", "body": "Crisis 74."},
            "dedupe_key": f"alert:{alert}:email",
            "status": "pending",
            "next_attempt_at": at,
            "created_at": at,
        }
        return add(conn, MESSAGES, **{**row, **values})


def dispatcher(engine: Engine, mailer: FakeMailer, at: datetime = NOW) -> OutboxDispatcher:
    return OutboxDispatcher(lambda: SqlUnitOfWork(engine, NoJobs()), mailer, FixedClock(at))


def message(engine: Engine, message_id: uuid.UUID) -> Any:
    with engine.connect() as conn:
        return conn.execute(select(MESSAGES).where(MESSAGES.c.id == message_id)).one()


def assert_statuses_follow_their_attempts(engine: Engine) -> None:
    """The consistency rule for the one stored status (data-model §8)."""
    with engine.connect() as conn:
        for row in conn.execute(select(MESSAGES.c.id, MESSAGES.c.status)):
            outcomes = conn.execute(
                select(ATTEMPTS.c.outcome)
                .where(ATTEMPTS.c.outbox_message_id == row.id)
                .order_by(ATTEMPTS.c.attempted_at, ATTEMPTS.c.id)
            ).scalars()
            assert OutboxStatus(row.status) is rules.status([OutboxOutcome(o) for o in outcomes])


def test_due_emails_are_sent_once_and_the_rest_wait(engine: Engine) -> None:
    due, later = queue(engine), queue(engine, at=NOW + timedelta(hours=1))
    mailer = FakeMailer()
    assert dispatcher(engine, mailer).dispatch() == 1
    text_ = "Crisis 74." + FOOTER
    assert mailer.sent == [Email("owner@example.com", "Ola: crisis level rose to high", text_)]
    assert (message(engine, due).status, message(engine, later).status) == ("sent", "pending")
    assert dispatcher(engine, mailer).dispatch() == 0  # sent stays sent
    assert_statuses_follow_their_attempts(engine)


def test_a_retryable_error_backs_off_until_the_email_is_dead(engine: Engine) -> None:
    message_id = queue(engine, sensitive_data_encrypted=b"ciphertext")
    mailer, at = FakeMailer(*[TIMEOUT] * rules.MAX_RETRYABLE), NOW
    for retries in range(1, rules.MAX_RETRYABLE):
        assert dispatcher(engine, mailer, at).dispatch() == 0
        row = message(engine, message_id)
        assert (row.status, row.next_attempt_at) == ("pending", rules.next_attempt(at, retries))
        assert (
            dispatcher(engine, mailer, row.next_attempt_at - timedelta(seconds=1)).dispatch() == 0
        )
        at = row.next_attempt_at
    dispatcher(engine, mailer, at).dispatch()
    row = message(engine, message_id)
    assert (row.status, row.sensitive_data_encrypted) == ("dead", None)
    assert_statuses_follow_their_attempts(engine)


def test_a_permanent_error_or_an_unknown_template_is_dead_at_once(engine: Engine) -> None:
    rejected = queue(engine, sensitive_data_encrypted=b"ciphertext")
    unknown = queue(engine, at=NOW + timedelta(seconds=1), template="newsletter")
    mailer = FakeMailer(MailFailed("smtp.rejected", retryable=False))
    dispatcher(engine, mailer, NOW + timedelta(minutes=1)).dispatch()
    assert [message(engine, m).status for m in (rejected, unknown)] == ["dead", "dead"]
    assert message(engine, rejected).sensitive_data_encrypted is None
    with engine.connect() as conn:
        codes = conn.execute(select(ATTEMPTS.c.error_code).order_by(ATTEMPTS.c.attempted_at))
        assert sorted(codes.scalars()) == ["outbox.unknown_template", "smtp.rejected"]
    assert mailer.sent == []


def test_two_dispatchers_never_send_the_same_email(engine: Engine) -> None:
    first, second = queue(engine), queue(engine, at=NOW + timedelta(seconds=1))
    other_mailer = FakeMailer()
    other = dispatcher(engine, other_mailer, NOW + timedelta(minutes=1))

    class Interleaving(FakeMailer):
        """While the first dispatcher holds its email's lock, the other one runs to the end."""

        def send(self, email: Email) -> None:
            if not self.sent:
                assert other.dispatch() == 1  # it skips the locked email and takes the next
            super().send(email)

    mailer = Interleaving()
    assert dispatcher(engine, mailer, NOW + timedelta(minutes=1)).dispatch() == 1
    assert len(mailer.sent) + len(other_mailer.sent) == 2
    assert [message(engine, m).status for m in (first, second)] == ["sent", "sent"]
    with engine.connect() as conn:
        assert len(conn.execute(select(ATTEMPTS.c.id)).all()) == 2  # one send each
    assert_statuses_follow_their_attempts(engine)


def test_an_email_that_cant_be_rendered_doesnt_hold_up_the_others(engine: Engine) -> None:
    broken = queue(engine, template_data={"title": "Ola"})  # no body
    fine = queue(engine, at=NOW + timedelta(seconds=1))
    mailer = FakeMailer()
    assert dispatcher(engine, mailer, NOW + timedelta(minutes=1)).dispatch() == 1
    assert [message(engine, m).status for m in (broken, fine)] == ["dead", "sent"]
    with engine.connect() as conn:
        failed = select(ATTEMPTS.c.error_code).where(ATTEMPTS.c.outbox_message_id == broken)
        assert conn.execute(failed).scalar_one() == "outbox.render_failed"


def test_only_a_pending_email_takes_an_attempt(engine: Engine) -> None:
    message_id = queue(engine)
    dispatcher(engine, FakeMailer()).dispatch()
    with engine.begin() as conn, pytest.raises(LookupError, match="pending"):
        SqlOutbox(conn).record(message_id, OutboxOutcome.DROPPED, at=NOW)
