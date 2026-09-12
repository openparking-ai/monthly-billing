"""G31 -- a charge is a persisted RESERVATION, then the processor, then an
OUTCOME with its payment; a reservation with no outcome is never charged past
-- it is REFUSED while its request may be in flight, and RESUMED under the
same key when the module has said it does not know.

The outside pass killed the old charge path at two points: after the processor
said yes and before any row (nothing recorded, a restart charged again), and
after a committed SUCCESS row and before its payment (the success reset the
count, a restart charged again). Here the same two deaths are a raise planted
into the process at the same two points -- after the reservation, and after the
processor -- and the restart is a second call. Both read: a pending attempt,
refused by name naming the attempt id, until an operator resolves it, once.

The second outside round added the third window: a processor that charges and
then raises, or answers a SUCCESS the guard refuses, was settled as `error` and
the next charge asked again under a FRESH key. Now that answer is UNKNOWN, the
attempt stays pending, and the next call asks again with the SAME key for the
amount RESERVED -- once with no answer (a second unknown, still pending), once
with an answer (the outcome, one payment of the reserved amount).

The second branch review then ran the race in which the operator resolves the
attempt while the worker's re-ask is at the processor and the processor then
ANSWERS: the worker's answer was refused and rolled back, and with the operator
having typed DECLINE and the processor having said SUCCESS, the next charge
minted a fresh key -- money moved twice. The processor's word on money
outranks the operator's typed one: a late SUCCESS the operator did not record
writes the card payment for the amount RESERVED, in the same transaction as
its `late` row, so the invoice is paid and no fresh key is minted; a late
SUCCESS on a recorded success writes no second payment; a late decline or
error writes the row only and the operator's payment stands. The operator's
second resolution stays refused by name.

Controls: the reservation's commit planted away (G24's test sees no row from
the processor); T2 split into two transactions (the outcome row survives a
crash before the payment -- the atomicity test here goes red); a pending
attempt charged past; an unknown settled as an error outcome; a resume that
mints a fresh key; the one-outcome-per-attempt index not caught by name; the
refusal without the attempt id; the listing that cannot tell in-flight from
unknown; a late success not honoured (the payment planted away -- the next
charge mints a fresh key again).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import monthly_billing.charging as ch
from fixtures import month_end_garage, simple_agreement
from monthly_billing.billing_run import run_billing
from monthly_billing.charging import (
    IN_FLIGHT,
    UNKNOWN,
    AttemptNotFound,
    attempt_charge,
    list_pending_attempts,
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
    _charge_and_die(tenant_id, _Succeeds(), reference, "ask_processor", _tick(1))
    rows = query(app, tenant_id, "SELECT kind, outcome, amount_minor FROM charge_attempts")
    assert rows == [("attempt", None, 12000)], "the reservation was not committed on its own"
    (pending,) = pending_attempts(app, tenant_id, reference)

    processor = _Succeeds()
    with pytest.raises(Refused) as refused:
        attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(2))
    assert refused.value.code == REFUSAL_ATTEMPT_UNRESOLVED
    assert str(pending) in refused.value.detail, "the refusal does not name the attempt id"
    assert processor.calls == 0, "the restart called the processor past a pending attempt"
    assert query(app, tenant_id, "SELECT count(*) FROM charge_attempts") == [(1,)]
    (listed,) = list_pending_attempts(app, tenant_id, reference)
    assert (listed.attempt_id, listed.amount_minor, listed.currency, listed.state, listed.asks) == (
        pending, 12000, "USD", IN_FLIGHT, 0
    )

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


class _ChargesThenLosesTheAnswer:
    """The R3.0 shape: the money moves (the call is recorded with its key) and
    the answer never arrives. Optionally answers on the n-th call."""

    def __init__(self, answers_on: int | None = None) -> None:
        self.calls: list = []
        self.answers_on = answers_on

    def charge(self, request):
        self.calls.append((request.amount_minor, request.idempotency_key))
        if self.answers_on is not None and len(self.calls) >= self.answers_on:
            return ChargeResult(outcome=Outcome.SUCCESS, detail="approved", reference="auth-late")
        raise TimeoutError("read timed out waiting for the processor's answer")


def _kinds(app, tenant_id):
    rows = query(app, tenant_id, "SELECT kind FROM charge_attempts ORDER BY sequence")
    return [k for (k,) in rows]


@pytest.mark.guarantee("G31")
def test_an_unknown_answer_leaves_the_attempt_pending_and_the_next_call_asks_again_under_one_key(
    app, tenant_id
):
    """THE BRANCH L3'S R3.0, arm 1: the processor raises on every call. Two
    calls are two asks of ONE attempt under ONE key: `[attempt, unknown, ask,
    unknown]`, pending 1, no payment, no fresh reservation."""
    reference = _issued(app, tenant_id)
    processor = _ChargesThenLosesTheAnswer()
    first = attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(1))
    assert first.unknown and first.payment_id is None
    assert _kinds(app, tenant_id) == ["attempt", "unknown"]
    assert pending_attempts(app, tenant_id, reference) == (first.attempt_id,)
    (listed,) = list_pending_attempts(app, tenant_id, reference)
    assert listed.state == UNKNOWN and listed.asks == 0
    assert listed.last_detail.startswith("TimeoutError")

    second = attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(2))
    assert second.unknown and second.attempt_id == first.attempt_id
    assert _kinds(app, tenant_id) == ["attempt", "unknown", "ask", "unknown"]
    assert len(pending_attempts(app, tenant_id, reference)) == 1
    assert [a for a, _ in processor.calls] == [12000, 12000]
    assert {k for _, k in processor.calls} == {first.attempt_id}, "the re-ask minted a fresh key"
    assert query(app, tenant_id, "SELECT count(*) FROM payments") == [(0,)]
    (listed,) = list_pending_attempts(app, tenant_id, reference)
    assert listed.state == UNKNOWN and listed.asks == 1


@pytest.mark.guarantee("G31")
def test_a_re_ask_that_is_answered_settles_the_one_attempt_with_one_payment(app, tenant_id):
    """Arm 2: the processor answers on the re-ask. `[attempt, unknown, ask,
    outcome success]`, ONE payment of 12000 -- the reserved amount -- paid_at
    set, and the next call has nothing to charge."""
    reference = _issued(app, tenant_id)
    processor = _ChargesThenLosesTheAnswer(answers_on=2)
    first = attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(1))
    assert first.unknown
    second = attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(2))
    assert not second.unknown and second.result.outcome is Outcome.SUCCESS
    assert second.attempt_id == first.attempt_id and second.paid is not None and second.paid.paid
    assert query(app, tenant_id, "SELECT kind, outcome FROM charge_attempts ORDER BY sequence") == [
        ("attempt", None), ("unknown", None), ("ask", None), ("outcome", "success"),
    ]
    assert query(
        app, tenant_id, "SELECT method, amount_minor, processor_reference FROM payments"
    ) == [(PaymentMethod.CARD.value, 12000, "auth-late")]
    assert {k for _, k in processor.calls} == {first.attempt_id}
    assert pending_attempts(app, tenant_id, reference) == ()
    with pytest.raises(Refused) as refused:
        attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(3))
    assert refused.value.code == REFUSAL_NOTHING_OWED
    assert len(processor.calls) == 2


@pytest.mark.guarantee("G31")
def test_a_re_ask_is_for_the_reserved_amount_whatever_was_paid_meanwhile(app, tenant_id):
    """A method change, and a cheque, between the unknown and the re-ask: the
    re-ask is not a new charge. It carries the RESERVED amount under the same
    key, the three refusals are not re-run, and the success is recorded for
    the reserved amount -- the invoice then reads overpaid, never silently."""
    from monthly_billing.charging import record_payment_method_changed
    from monthly_billing.payments import record_payment

    reference = _issued(app, tenant_id)
    processor = _ChargesThenLosesTheAnswer(answers_on=2)
    first = attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(1))
    record_payment_method_changed(app, tenant_id, reference, recorded_by="portal", now=_tick(2))
    record_payment(
        app, tenant_id, reference, PaymentMethod.CHEQUE, 12000, _tick(3), recorded_by="op"
    )
    second = attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(4))
    assert second.attempt_id == first.attempt_id
    assert [a for a, _ in processor.calls] == [12000, 12000], "the re-ask re-read the balance"
    assert second.paid is not None and second.paid.overpaid_minor == 12000


@pytest.mark.guarantee("G31")
def test_a_control_processor_that_answers_cleanly_first_time_is_two_rows_and_nothing_owed(
    app, tenant_id
):
    """The control on the arms above: a clean SUCCESS is `[attempt, outcome]`
    and the next call is REFUSAL_NOTHING_OWED with the processor not called."""
    reference = _issued(app, tenant_id)
    processor = _Succeeds()
    attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(1))
    assert _kinds(app, tenant_id) == ["attempt", "outcome"]
    with pytest.raises(Refused) as refused:
        attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(2))
    assert refused.value.code == REFUSAL_NOTHING_OWED and processor.calls == 1


@pytest.mark.guarantee("G31")
def test_an_unknown_tailed_attempt_can_be_resolved_by_the_operator_and_then_asks_no_more(
    app, tenant_id
):
    """THE BRANCH L3'S R3.0b: the guard refuses the processor's reference on
    every ask, the log grows `unknown, ask, unknown`, pending stays 1 -- and
    the operator's resolve-attempt with a clean reference is the way out: one
    payment of the reserved amount, and the next charge is nothing owed."""
    from monthly_billing.sensitive import luhn_ok

    body = "4" + "1" * 14
    card = next(body + c for c in "0123456789" if luhn_ok(body + c))

    class _SucceedsCardShaped:
        def __init__(self) -> None:
            self.calls: list = []

        def charge(self, request):
            self.calls.append(request.idempotency_key)
            return ChargeResult(outcome=Outcome.SUCCESS, detail="approved", reference=card)

    reference = _issued(app, tenant_id)
    processor = _SucceedsCardShaped()
    first = attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(1))
    second = attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(2))
    assert first.unknown and second.unknown and first.attempt_id == second.attempt_id
    assert _kinds(app, tenant_id) == ["attempt", "unknown", "ask", "unknown"]
    assert len(set(processor.calls)) == 1 and len(processor.calls) == 2
    assert len(pending_attempts(app, tenant_id, reference)) == 1
    resolved = resolve_attempt(
        app, tenant_id, first.attempt_id,
        ChargeResult(outcome=Outcome.SUCCESS, detail="reconciled", reference="auth-op"),
        recorded_by="operator", now=_tick(3),
    )
    assert resolved.paid is not None and resolved.paid.paid
    assert query(app, tenant_id, "SELECT amount_minor, processor_reference FROM payments") == [
        (12000, "auth-op")
    ]
    assert pending_attempts(app, tenant_id, reference) == ()
    with pytest.raises(Refused) as refused:
        attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(4))
    assert refused.value.code == REFUSAL_NOTHING_OWED and len(processor.calls) == 2


@pytest.mark.guarantee("G31")
def test_the_one_outcome_per_attempt_index_is_caught_by_name_when_reached(app, tenant_id):
    """THE BRANCH L3'S R3.2b CONTROL: two resolvers of one attempt with the
    lock bypassed in-process and a rendezvous at the outcome insert. Both pass
    the check; one hits the index -- which is caught BY ITS NAME as
    REFUSAL_ATTEMPT_ALREADY_RESOLVED, never a driver error. The control plants
    the catch away and this goes red."""
    import threading

    import monthly_billing.store.postgres as pg

    reference = _issued(app, tenant_id)
    _charge_and_die(tenant_id, _Succeeds(), reference, "_settle", _tick(1))
    (pending,) = pending_attempts(app, tenant_id, reference)
    arrived = {"A": threading.Event(), "B": threading.Event()}
    real_lock, real_insert = pg.lock_invoice, ch.guarded_insert
    pg.lock_invoice = lambda cursor, invoice_uuid: None  # the lock bypassed in-process

    def insert(cursor, table, record):
        me = threading.current_thread().name
        if table == "charge_attempts" and record.get("kind") == "outcome" and me in arrived:
            arrived[me].set()
            arrived["B" if me == "A" else "A"].wait(2)
        return real_insert(cursor, table, record)

    ch.guarded_insert = insert
    results: dict[str, object] = {}

    def resolver():
        c = app_connection(DSN)
        try:
            resolve_attempt(
                c, tenant_id, pending,
                ChargeResult(outcome=Outcome.SUCCESS, detail="approved", reference="ref-1"),
                recorded_by=threading.current_thread().name, now=_tick(2),
            )
            results[threading.current_thread().name] = "resolved"
        except Exception as exc:  # noqa: BLE001 -- the shape of the failure is the finding
            results[threading.current_thread().name] = exc
        finally:
            c.close()

    try:
        threads = [threading.Thread(target=resolver, name=n) for n in ("A", "B")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
    finally:
        pg.lock_invoice, ch.guarded_insert = real_lock, real_insert
    outcomes = sorted(type(r).__name__ if isinstance(r, Exception) else r for r in results.values())
    assert outcomes == ["Refused", "resolved"], results
    refused = next(r for r in results.values() if isinstance(r, Refused))
    assert refused.code == REFUSAL_ATTEMPT_ALREADY_RESOLVED
    assert query(app, tenant_id, "SELECT count(*) FROM charge_attempts WHERE kind = 'outcome'") == [
        (1,)
    ]
    assert query(app, tenant_id, "SELECT count(*) FROM payments") == [(1,)]


@pytest.mark.guarantee("G31")
def test_the_command_line_lists_pending_attempts_with_their_ids_and_state(app, tenant_id, capsys):
    """The operator can find a pending attempt from the command line: its id,
    what was reserved, in flight or unknown, how many re-asks, what was seen."""
    reference = _issued(app, tenant_id)
    dsn = f"{DSN} user=monthly_billing_app password={APP_PASSWORD}"
    argv = ["pending-attempts", "--tenant", str(tenant_id), "--invoice", reference, "--dsn", dsn]
    assert main(argv) == 0
    assert "no pending attempts" in capsys.readouterr().out

    processor = _ChargesThenLosesTheAnswer()
    unknown = attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(1))
    attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(2))
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert "1 pending attempt(s)" in out
    assert (
        f"attempt {unknown.attempt_id}: 12000 USD reserved, {UNKNOWN}, asked again 1 time(s)"
    ) in out
    assert "last: TimeoutError" in out

    _charge_and_die(tenant_id, _Succeeds(), _issued_second(app, tenant_id), "_settle", _tick(3))
    argv2 = [*argv[:4], _issued_second.reference, *argv[5:]]
    assert main(argv2) == 0
    out = capsys.readouterr().out
    assert f"reserved, {IN_FLIGHT}, asked again 0 time(s)" in out and "last:" not in out


def _issued_second(app, tenant_id) -> str:
    """A second invoice for the same payer: the following period."""
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 6, 1), now=_tick(2)).lines
    _issued_second.reference = line.reference
    return line.reference


@pytest.mark.guarantee("G31")
def test_a_late_success_the_operator_did_not_record_is_paid_and_no_fresh_key_is_minted(
    app, tenant_id
):
    """THE SECOND BRANCH L3'S FINDING (i), the arm with money in it: the
    operator resolves DECLINE while the re-ask is at the processor; the
    processor says SUCCESS. The late row carries the SUCCESS and the card
    payment for the RESERVED amount lands with it, so the invoice is paid, and
    the next charge is nothing owed with the processor not asked -- no fresh
    key. The operator's decline still counts one toward the three."""
    from test_g24_every_processor_call_leaves_a_row import late_answer_race

    reference = _issued(app, tenant_id)
    worker, operator, keys = late_answer_race(
        tenant_id, reference, Outcome.DECLINE, Outcome.SUCCESS
    )
    assert worker.late and worker.payment_id is not None
    assert worker.paid is not None and worker.paid.paid and worker.paid.overpaid_minor == 0
    assert operator.payment_id is None and operator.result.outcome is Outcome.DECLINE
    assert query(
        app, tenant_id, "SELECT amount_minor, method, processor_reference FROM payments"
    ) == [(12000, "card", "auth-processor")]
    assert _kinds(app, tenant_id) == ["attempt", "unknown", "ask", "outcome", "late"]
    assert query(app, tenant_id, "SELECT paid_at IS NOT NULL FROM invoices") == [(True,)]
    assert worker.retry.attempts == 1
    processor = _Succeeds()
    with pytest.raises(Refused) as refused:
        attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(4))
    assert refused.value.code == REFUSAL_NOTHING_OWED and processor.calls == 0, (
        "a late success was not honoured: the next charge minted a fresh key"
    )
    assert _kinds(app, tenant_id) == ["attempt", "unknown", "ask", "outcome", "late"]


@pytest.mark.guarantee("G31")
@pytest.mark.parametrize("processor_says", [Outcome.SUCCESS, Outcome.DECLINE])
def test_a_late_answer_on_a_recorded_success_writes_no_second_payment(
    app, tenant_id, processor_says
):
    """The operator resolved SUCCESS; the processor then says SUCCESS (the same
    money -- no second payment) or DECLINE (the operator's SUCCESS and its
    payment stand, and the contradiction is in the log for the operator's
    reversal). One payment either way, the operator's, and nothing owed."""
    from test_g24_every_processor_call_leaves_a_row import late_answer_race

    reference = _issued(app, tenant_id)
    worker, operator, _ = late_answer_race(tenant_id, reference, Outcome.SUCCESS, processor_says)
    assert worker.late and worker.payment_id is None and worker.paid is None
    assert operator.payment_id is not None
    assert query(app, tenant_id, "SELECT amount_minor, processor_reference FROM payments") == [
        (12000, "auth-op")
    ]
    assert query(
        app, tenant_id, "SELECT outcome FROM charge_attempts WHERE kind = 'late'"
    ) == [(processor_says.value,)]
    with pytest.raises(Refused) as refused:
        attempt_charge(app, tenant_id, _Succeeds(), reference, recorded_by="cron", now=_tick(4))
    assert refused.value.code == REFUSAL_NOTHING_OWED


@pytest.mark.guarantee("G31")
def test_a_late_decline_on_a_recorded_decline_leaves_the_invoice_owed_and_the_next_charge_fresh(
    app, tenant_id
):
    """Nobody said SUCCESS: no payment, the late decline is the row only, the
    operator's decline counts one, and the next charge is a FRESH reservation
    -- the one arm in which that is right."""
    from test_g24_every_processor_call_leaves_a_row import late_answer_race

    reference = _issued(app, tenant_id)
    worker, operator, _ = late_answer_race(tenant_id, reference, Outcome.DECLINE, Outcome.DECLINE)
    assert worker.late and worker.payment_id is None and operator.payment_id is None
    assert query(app, tenant_id, "SELECT count(*) FROM payments") == [(0,)]
    assert worker.retry.attempts == 1
    processor = _Succeeds()
    third = attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(4))
    assert not third.late and third.attempt_id != worker.attempt_id and processor.calls == 1
    assert _kinds(app, tenant_id) == [
        "attempt", "unknown", "ask", "outcome", "late", "attempt", "outcome"
    ]
    assert query(app, tenant_id, "SELECT count(*) FROM payments") == [(1,)]
