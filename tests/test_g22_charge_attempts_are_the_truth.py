"""G22 -- the charge log is the truth for retries, in the order it was recorded.

Three persisted non-success OUTCOMES since the last persisted method change
refuse the fourth by name; a persisted method change allows it -- and is
allowed while an attempt is pending; a success counts toward nothing and resets
nothing; and the state is REBUILT from rows, so a fresh caller (a restart) sees
the same count as the one that made the attempts. Every attempt is two rows
since 0003: the reservation (`attempt`, outcome NULL) and the `outcome`.

The fold walks the log in RECORDED order -- the sequence the database assigned
under the invoice lock -- never in the order of the instants callers typed, and
an outcome counts toward the method that was current when its RESERVATION was
made. The branch L3 dated one decline two ways and got two answers; now both
datings read the same, and a worker whose clock is behind the operator's still
counts against the card the operator changed to.

Controls: a rebuild that ignores the ``payment_method_changed`` rows (the reset
is a row nobody reads -- the "lie on restart"); a method change refused while
an attempt is pending; a success that resets; a fold that counts an outcome
where it sits instead of at its reservation; a load that orders by the caller's
instant instead of the log's sequence.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.billing_run import run_billing
from monthly_billing.charging import (
    attempt_charge,
    record_payment_method_changed,
    retry_state_from_rows,
)
from monthly_billing.findings import Refused
from monthly_billing.payment import MAX_ATTEMPTS, ChargeResult, Outcome, StubProcessor
from monthly_billing.payments import PaymentMethod
from store_harness import needs_postgres, query, seed

pytestmark = needs_postgres

GARAGE = month_end_garage()
TZ = ZoneInfo(GARAGE.timezone)
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=TZ)


class _Declines:
    def charge(self, request):
        return ChargeResult(outcome=Outcome.DECLINE, detail="insufficient funds")


class _Succeeds:
    def charge(self, request):
        return ChargeResult(outcome=Outcome.SUCCESS, detail="approved", reference="auth-77")


def _issued(app, tenant_id, **overrides) -> str:
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5), **overrides),))
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW).lines
    return line.reference


def _tick(n: int) -> datetime:
    return NOW + timedelta(hours=n)


@pytest.mark.guarantee("G22")
def test_three_persisted_declines_refuse_the_fourth_by_name(app, tenant_id):
    reference = _issued(app, tenant_id)
    for n in range(MAX_ATTEMPTS):
        outcome = attempt_charge(
            app, tenant_id, _Declines(), reference, recorded_by="cron", now=_tick(n)
        )
        assert outcome.result.outcome is Outcome.DECLINE
        assert outcome.retry.attempts == n + 1
    with pytest.raises(Refused) as refused:
        attempt_charge(app, tenant_id, _Declines(), reference, recorded_by="cron", now=_tick(9))
    assert refused.value.code == "REFUSAL_RETRIES_EXHAUSTED"
    rows = query(
        app, tenant_id,
        "SELECT kind, outcome FROM charge_attempts ORDER BY occurred_at, created_at",
    )
    assert rows == [("attempt", None), ("outcome", "decline")] * MAX_ATTEMPTS, (
        "the refusal wrote a fourth reservation"
    )


@pytest.mark.guarantee("G22")
def test_a_persisted_method_change_allows_the_fourth(app, tenant_id):
    reference = _issued(app, tenant_id)
    for n in range(MAX_ATTEMPTS):
        attempt_charge(app, tenant_id, _Declines(), reference, recorded_by="cron", now=_tick(n))
    state = record_payment_method_changed(
        app, tenant_id, reference, recorded_by="portal", now=_tick(5)
    )
    assert state.attempts == 0
    fourth = attempt_charge(
        app, tenant_id, _Declines(), reference, recorded_by="cron", now=_tick(6)
    )
    assert fourth.retry.attempts == 1
    rows = query(
        app, tenant_id, "SELECT kind FROM charge_attempts ORDER BY occurred_at, created_at"
    )
    assert [r[0] for r in rows] == (
        ["attempt", "outcome"] * 3 + ["payment_method_changed", "attempt", "outcome"]
    )


@pytest.mark.guarantee("G22")
def test_the_state_survives_a_restart_because_it_is_rebuilt_from_rows(app, tenant_id):
    """A fresh rebuild from the rows equals what the attempting caller was told."""
    reference = _issued(app, tenant_id)
    told = None
    for n in range(2):
        told = attempt_charge(
            app, tenant_id, _Declines(), reference, recorded_by="cron", now=_tick(n)
        ).retry
    rows = query(
        app, tenant_id,
        "SELECT kind, outcome, detail FROM charge_attempts ORDER BY occurred_at, created_at",
    )
    rebuilt = retry_state_from_rows(reference, rows)
    assert rebuilt == told
    assert rebuilt.attempts == 2


@pytest.mark.guarantee("G22")
def test_a_success_records_the_card_payment_and_pays_the_invoice(app, tenant_id):
    """THE ONLY PATH THAT WRITES A CARD PAYMENT."""
    reference = _issued(app, tenant_id)
    outcome = attempt_charge(
        app, tenant_id, _Succeeds(), reference, recorded_by="cron", now=_tick(1)
    )
    assert outcome.result.outcome is Outcome.SUCCESS
    assert outcome.payment_id is not None and outcome.paid is not None and outcome.paid.paid
    assert query(
        app, tenant_id, "SELECT method, amount_minor, processor_reference FROM payments"
    ) == [(PaymentMethod.CARD.value, 12000, "auth-77")]
    assert query(
        app, tenant_id, "SELECT kind, outcome FROM charge_attempts ORDER BY created_at"
    ) == [("attempt", None), ("outcome", "success")]


@pytest.mark.guarantee("G22")
def test_the_stub_processor_moves_no_money_and_its_attempt_is_still_recorded(app, tenant_id):
    reference = _issued(app, tenant_id)
    outcome = attempt_charge(
        app, tenant_id, StubProcessor(), reference, recorded_by="cron", now=_tick(1)
    )
    assert outcome.result.outcome is Outcome.ERROR and outcome.payment_id is None
    assert query(app, tenant_id, "SELECT count(*) FROM payments") == [(0,)]
    assert query(
        app, tenant_id, "SELECT outcome FROM charge_attempts WHERE kind = 'outcome'"
    ) == [("error",)]


@pytest.mark.guarantee("G22")
def test_a_success_counts_toward_nothing_and_resets_nothing(app, tenant_id):
    """Two declines, a success, the cheque behind it bounces -- the count is
    still two, not zero: the reset is the caller's report of a new method and
    nothing else. The outside pass's R1.3 was a persisted success resetting the
    count with no payment beside it."""
    from monthly_billing.payments import ReversalReason, record_reversal

    reference = _issued(app, tenant_id)
    for n in range(2):
        attempt_charge(app, tenant_id, _Declines(), reference, recorded_by="cron", now=_tick(n))
    won = attempt_charge(app, tenant_id, _Succeeds(), reference, recorded_by="cron", now=_tick(2))
    assert won.retry.attempts == 2, "a success reset (or counted toward) the non-success count"
    record_reversal(
        app, tenant_id, won.payment_id, ReversalReason.CHARGEBACK, _tick(3), recorded_by="op"
    )
    third = attempt_charge(app, tenant_id, _Declines(), reference, recorded_by="cron", now=_tick(4))
    assert third.retry.attempts == 3 and third.retry.exhausted
    with pytest.raises(Refused) as refused:
        attempt_charge(app, tenant_id, _Declines(), reference, recorded_by="cron", now=_tick(5))
    assert refused.value.code == "REFUSAL_RETRIES_EXHAUSTED"


@pytest.mark.guarantee("G22")
def test_a_method_change_is_allowed_while_an_attempt_is_pending(app, tenant_id):
    """It moves no money, and the operator changing a card while collection is
    stuck must not be refused. The pending attempt still blocks the CHARGE."""
    import monthly_billing.charging as ch
    from monthly_billing.findings import REFUSAL_ATTEMPT_UNRESOLVED

    reference = _issued(app, tenant_id)
    real = ch.ask_processor
    ch.ask_processor = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("worker died"))
    try:
        with pytest.raises(RuntimeError):
            attempt_charge(app, tenant_id, _Declines(), reference, recorded_by="cron", now=_tick(1))
    finally:
        ch.ask_processor = real
    assert query(app, tenant_id, "SELECT kind FROM charge_attempts") == [("attempt",)]
    state = record_payment_method_changed(
        app, tenant_id, reference, recorded_by="portal", now=_tick(2)
    )
    assert state.attempts == 0
    assert query(app, tenant_id, "SELECT count(*) FROM charge_attempts "
                 "WHERE kind = 'payment_method_changed'") == [(1,)]
    with pytest.raises(Refused) as refused:
        attempt_charge(app, tenant_id, _Declines(), reference, recorded_by="cron", now=_tick(3))
    assert refused.value.code == REFUSAL_ATTEMPT_UNRESOLVED


@pytest.mark.guarantee("G22")
def test_no_mandate_refuses_before_the_processor_and_writes_no_row(app, tenant_id):
    reference = _issued(app, tenant_id, with_mandate=False)
    processor = StubProcessor()
    with pytest.raises(Refused) as refused:
        attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(1))
    assert refused.value.code == "REFUSAL_NO_MANDATE"
    assert processor.requests == [], "the processor was called before the refusal"
    assert query(app, tenant_id, "SELECT count(*) FROM charge_attempts") == [(0,)]


def _leave_pending(app, tenant_id, reference, now):
    """A reservation the worker died with: T1 committed, the processor never
    asked. Returns the pending attempt id."""
    import monthly_billing.charging as ch
    from monthly_billing.charging import pending_attempts

    real = ch.ask_processor
    ch.ask_processor = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("worker died"))
    try:
        with pytest.raises(RuntimeError):
            attempt_charge(app, tenant_id, _Declines(), reference, recorded_by="cron", now=now)
    finally:
        ch.ask_processor = real
    (pending,) = pending_attempts(app, tenant_id, reference)
    return pending


@pytest.mark.guarantee("G22")
@pytest.mark.parametrize("dated", ["after the change", "before the change"],
                         ids=["dated-after", "dated-before"])
def test_an_outcome_counts_toward_the_method_current_at_its_reservation(app, tenant_id, dated):
    """THE BRANCH L3'S R3.4: two declines, a reservation the worker died with,
    a method change while it is pending, then the operator resolves the pending
    one as a DECLINE. That decline was asked of the OLD card, so it counts for
    nothing under the new one -- whether the operator dates the resolution
    after the change or (a typed --at) before it. Both read attempts=0."""
    from monthly_billing.charging import resolve_attempt

    reference = _issued(app, tenant_id)
    for n in range(2):
        attempt_charge(app, tenant_id, _Declines(), reference, recorded_by="cron", now=_tick(n))
    pending = _leave_pending(app, tenant_id, reference, _tick(2))
    state = record_payment_method_changed(
        app, tenant_id, reference, recorded_by="portal", now=_tick(3)
    )
    assert state.attempts == 0
    at = _tick(4) if dated == "after the change" else _tick(2) + timedelta(minutes=30)
    resolved = resolve_attempt(
        app, tenant_id, pending, ChargeResult(outcome=Outcome.DECLINE, detail="late decline"),
        recorded_by="operator", now=at,
    )
    assert resolved.retry.attempts == 0, (
        "a decline reserved under the old card counted against the new one"
    )
    # And a fresh rebuild from the rows, in recorded order, says the same.
    rows = query(
        app, tenant_id,
        "SELECT kind, outcome, detail, attempt_id FROM charge_attempts ORDER BY sequence",
    )
    assert [r[0] for r in rows] == (
        ["attempt", "outcome"] * 2 + ["attempt", "payment_method_changed", "outcome"]
    )
    assert retry_state_from_rows(reference, rows).attempts == 0


@pytest.mark.guarantee("G22")
def test_the_log_is_walked_in_recorded_order_not_by_the_callers_instant(app, tenant_id):
    """A worker whose clock is BEHIND the operator's: the method change is
    recorded at hour 5, then the worker reserves and is declined with a `now`
    of hour 1. In recorded order the attempt is after the change and counts
    (attempts=1); ordered by the typed instants it would sit before the change
    and be reset by it. The control orders the load by occurred_at and this
    reads 0."""
    reference = _issued(app, tenant_id)
    record_payment_method_changed(app, tenant_id, reference, recorded_by="portal", now=_tick(5))
    behind = attempt_charge(
        app, tenant_id, _Declines(), reference, recorded_by="cron", now=_tick(1)
    )
    assert behind.retry.attempts == 1, "the worker's clock, not the log, decided the count"
    again = attempt_charge(
        app, tenant_id, _Declines(), reference, recorded_by="cron", now=_tick(6)
    )
    assert again.retry.attempts == 2


@pytest.mark.guarantee("G22")
def test_the_fold_dates_an_outcome_by_its_shape():
    """Pure. The 3-tuple shape counts an outcome where it sits; the 4-tuple
    shape carries the attempt id and counts it at its reservation. The same
    log, both ways, and the two disagree exactly where they should."""
    from uuid import uuid4

    a, b = uuid4(), uuid4()
    D = ("outcome", "decline", "declined")
    M = ("payment_method_changed", None, "")
    # attempt a, decline a, attempt b, CHANGE, decline b: b was reserved
    # under the old method.
    where_it_sits = [("attempt", None, ""), D, ("attempt", None, ""), M, D]
    at_reservation = [
        ("attempt", None, "", a), (*D, a), ("attempt", None, "", b), (*M, None), (*D, b),
    ]
    assert retry_state_from_rows("x", where_it_sits).attempts == 1
    assert retry_state_from_rows("x", at_reservation).attempts == 0
    # And reserved AFTER the change, it counts in both shapes.
    later = [("attempt", None, "", a), (*D, a), (*M, None), ("attempt", None, "", b), (*D, b)]
    assert retry_state_from_rows("x", later).attempts == 1
    # unknown and ask rows contribute nothing in either shape.
    with_asks = [("attempt", None, "", a), ("unknown", None, "t", a), ("ask", None, "", a),
                 ("unknown", None, "t", a), (*D, a)]
    assert retry_state_from_rows("x", with_asks).attempts == 1
