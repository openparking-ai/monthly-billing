"""G31 -- a charge is a persisted RESERVATION, then the processor, then an
OUTCOME with its payment; a reservation with no outcome is never charged past.

The outside pass killed the old charge path at two points: after the processor
said yes and before any row (nothing recorded, a restart charged again), and
after a committed SUCCESS row and before its payment (the success reset the
count, a restart charged again). Here the same two deaths are a raise planted
into the process at the same two points -- after the reservation, and after the
processor -- and the restart is a second call. Both read: a pending attempt,
refused by name until an operator resolves it, once.

Controls: the reservation's commit planted away (G24's test sees no row from
the processor); T2 split into two transactions (the outcome row survives a
crash before the payment -- the atomicity test here goes red).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import monthly_billing.charging as ch
from fixtures import month_end_garage, simple_agreement
from monthly_billing.billing_run import run_billing
from monthly_billing.charging import (
    AttemptNotFound,
    attempt_charge,
    pending_attempts,
    resolve_attempt,
)
from monthly_billing.cli import main
from monthly_billing.findings import (
    REFUSAL_ATTEMPT_ALREADY_RESOLVED,
    REFUSAL_ATTEMPT_UNRESOLVED,
    REFUSAL_NOTHING_OWED,
    Refused,
)
from monthly_billing.payment import ChargeResult, Outcome
from monthly_billing.payments import PaymentMethod
from store_harness import APP_PASSWORD, DSN, app_connection, needs_postgres, query, seed

pytestmark = needs_postgres

GARAGE = month_end_garage()
TZ = ZoneInfo(GARAGE.timezone)
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=TZ)


class _WorkerDied(RuntimeError):
    """Stands in for the process dying: nothing after the raise runs."""


class _Succeeds:
    def __init__(self) -> None:
        self.calls = 0

    def charge(self, request):
        self.calls += 1
        return ChargeResult(outcome=Outcome.SUCCESS, detail="approved", reference="auth-9")


def _issued(app, tenant_id) -> str:
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW).lines
    return line.reference


def _tick(n: int) -> datetime:
    return NOW + timedelta(hours=n)


def _die_at(attribute: str):
    """Replace one step of the charge with the worker dying; returns a restorer."""
    real = getattr(ch, attribute)

    def dying(*args, **kwargs):
        raise _WorkerDied(attribute)

    setattr(ch, attribute, dying)
    return lambda: setattr(ch, attribute, real)


def _charge_and_die(tenant_id, processor, reference, at: str, now: datetime) -> None:
    """The worker: its own connection, which dies with it -- an open transaction
    rolls back exactly as it would when the process is killed."""
    victim = app_connection(DSN)
    restore = _die_at(at)
    try:
        with pytest.raises(_WorkerDied):
            attempt_charge(victim, tenant_id, processor, reference, recorded_by="cron", now=now)
    finally:
        restore()
        victim.close()


@pytest.mark.guarantee("G31")
def test_a_death_after_the_reservation_leaves_a_pending_attempt_that_refuses_the_next_charge(
    app, tenant_id
):
    reference = _issued(app, tenant_id)
    _charge_and_die(tenant_id, _Succeeds(), reference, "result_of", _tick(1))
    rows = query(app, tenant_id, "SELECT kind, outcome, amount_minor FROM charge_attempts")
    assert rows == [("attempt", None, 12000)], "the reservation was not committed on its own"
    (pending,) = pending_attempts(app, tenant_id, reference)

    processor = _Succeeds()
    with pytest.raises(Refused) as refused:
        attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(2))
    assert refused.value.code == REFUSAL_ATTEMPT_UNRESOLVED
    assert processor.calls == 0, "the restart called the processor past a pending attempt"
    assert query(app, tenant_id, "SELECT count(*) FROM charge_attempts") == [(1,)]

    # The operator records what the processor said: it declined. The invoice
    # may be charged again, and the decline counted toward the three.
    resolved = resolve_attempt(
        app, tenant_id, pending, ChargeResult(outcome=Outcome.DECLINE, detail="declined"),
        recorded_by="operator", now=_tick(3),
    )
    assert resolved.retry.attempts == 1 and resolved.payment_id is None
    again = attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(4))
    assert again.result.outcome is Outcome.SUCCESS and processor.calls == 1
    assert again.paid is not None and again.paid.paid


@pytest.mark.guarantee("G31")
def test_a_death_after_the_processor_answered_leaves_a_pending_attempt_paid_once(app, tenant_id):
    """The R1.2 window of the outside pass, the other way round: the processor
    charged the card, the worker died before the outcome row. Nothing on our
    side says success -- and nothing charges again until the operator says
    what happened; the success is then recorded for the RESERVED amount."""
    reference = _issued(app, tenant_id)
    processor = _Succeeds()
    _charge_and_die(tenant_id, processor, reference, "_settle", _tick(1))
    assert processor.calls == 1
    assert query(app, tenant_id, "SELECT count(*) FROM payments") == [(0,)]
    (pending,) = pending_attempts(app, tenant_id, reference)

    with pytest.raises(Refused) as refused:
        attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(2))
    assert refused.value.code == REFUSAL_ATTEMPT_UNRESOLVED
    assert processor.calls == 1, "the restart charged the card again"

    resolved = resolve_attempt(
        app, tenant_id, pending,
        ChargeResult(outcome=Outcome.SUCCESS, detail="approved", reference="auth-9"),
        recorded_by="operator", now=_tick(3),
    )
    assert resolved.paid is not None and resolved.paid.paid and resolved.payment_id is not None
    assert query(
        app, tenant_id, "SELECT method, amount_minor, processor_reference FROM payments"
    ) == [(PaymentMethod.CARD.value, 12000, "auth-9")]
    with pytest.raises(Refused) as refused:
        attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(4))
    assert refused.value.code == REFUSAL_NOTHING_OWED
    assert processor.calls == 1


@pytest.mark.guarantee("G31")
def test_the_outcome_row_and_the_payment_land_together_or_not_at_all(app, tenant_id):
    """T2 is ONE transaction: a death between the outcome row and the payment
    rolls the outcome row back, so there is never a success on record with no
    money beside it. The control splits T2 in two and this goes red."""
    reference = _issued(app, tenant_id)
    _charge_and_die(tenant_id, _Succeeds(), reference, "insert_payment", _tick(1))
    rows = query(app, tenant_id, "SELECT kind, outcome FROM charge_attempts ORDER BY created_at")
    assert rows == [("attempt", None)], "a success was recorded with no payment beside it"
    assert query(app, tenant_id, "SELECT count(*) FROM payments") == [(0,)]
    assert len(pending_attempts(app, tenant_id, reference)) == 1


@pytest.mark.guarantee("G31")
def test_an_attempt_is_resolved_once_and_an_unknown_id_is_not_found(app, tenant_id):
    reference = _issued(app, tenant_id)
    _charge_and_die(tenant_id, _Succeeds(), reference, "_settle", _tick(1))
    (pending,) = pending_attempts(app, tenant_id, reference)
    declined = ChargeResult(outcome=Outcome.DECLINE, detail="declined")
    resolve_attempt(app, tenant_id, pending, declined, recorded_by="operator", now=_tick(2))
    with pytest.raises(Refused) as refused:
        resolve_attempt(app, tenant_id, pending, declined, recorded_by="operator", now=_tick(3))
    assert refused.value.code == REFUSAL_ATTEMPT_ALREADY_RESOLVED
    assert query(
        app, tenant_id, "SELECT count(*) FROM charge_attempts WHERE kind = 'outcome'"
    ) == [(1,)]
    from uuid import uuid4

    with pytest.raises(AttemptNotFound):
        resolve_attempt(app, tenant_id, uuid4(), declined, recorded_by="operator", now=_tick(4))


@pytest.mark.guarantee("G31")
def test_the_command_line_resolves_a_pending_attempt_and_accepts_only_the_enums_outcomes(
    app, tenant_id, capsys
):
    """A pending attempt comes only from the library; the command line records
    what the processor said. `success | decline | error` and nothing else."""
    reference = _issued(app, tenant_id)
    _charge_and_die(tenant_id, _Succeeds(), reference, "_settle", _tick(1))
    (pending,) = pending_attempts(app, tenant_id, reference)
    dsn = f"{DSN} user=monthly_billing_app password={APP_PASSWORD}"
    argv = ["resolve-attempt", "--tenant", str(tenant_id), "--attempt", str(pending),
            "--outcome", "success", "--at", "2026-05-01T10:00:00-06:00",
            "--recorded-by", "operator", "--reference", "auth-cli", "--dsn", dsn]
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert "resolved: success" in out and "invoice PAID" in out
    assert query(app, tenant_id, "SELECT amount_minor, processor_reference FROM payments") == [
        (12000, "auth-cli")
    ]
    assert main(argv) == 2
    err = capsys.readouterr().err
    assert err.startswith("REFUSED") and REFUSAL_ATTEMPT_ALREADY_RESOLVED in err
    assert "Traceback" not in err
    with pytest.raises(SystemExit) as stopped:
        main([*argv[:5], "--outcome", "declined", *argv[7:]])
    assert stopped.value.code == 2
    assert "invalid choice: 'declined'" in capsys.readouterr().err
