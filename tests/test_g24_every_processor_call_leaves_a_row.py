"""G24 -- every call to the processor is PRECEDED by its row, and followed by a
row recorded as it came -- an outcome as an outcome, an answer that did not
arrive as UNKNOWN, never as an outcome.

The L3 found that a processor which RAISED left no row: the call ran outside
the insert, so a socket timeout after the request was sent -- the ordinary way
a card is charged with no record on our side -- recorded nothing and counted
toward nothing. The outside pass then found the row was written AFTER the
processor was called, so a crash between the two left nothing either. Its
second round found the raise, and a SUCCESS the instrument guard refused,
written as a resolved `error` outcome -- so the attempt stopped being pending
and the next charge asked again under a fresh key. Now the reservation is
committed before the processor is asked, a re-ask is committed as an `ask` row
before the processor is asked again, and what the module did not receive is an
`unknown` row: no outcome, no count, the attempt still pending.

The second branch review then ran the race in which the answer ARRIVES after
the operator has resolved the attempt -- the worker's re-ask was at the
processor when `resolve-attempt` ran -- and found the worker's answer refused
and rolled back: a call the processor received, followed by no row. Now that
answer is a `late` row beside the operator's resolution, carrying what the
processor said; so every call that reaches the processor is followed by a row
-- `outcome`, `unknown`, or `late`. (What a late SUCCESS does to the money is
G31's.)

The controls: the wrapper planted away (the raise propagates again, no row);
the reservation's commit planted away (the processor sees no row); the ask row
planted away (a re-ask leaves no row); the late row planted away (a late answer
leaves no row).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.billing_run import run_billing
from monthly_billing.charging import LATE, attempt_charge, pending_attempts, resolve_attempt
from monthly_billing.findings import REFUSAL_RETRIES_EXHAUSTED, Refused
from monthly_billing.payment import (
    DETAIL_WITHHELD,
    MAX_ATTEMPTS,
    RESULT_UNKNOWN_DETAIL,
    ChargeResult,
    Outcome,
    Unknown,
    UnknownAnswer,
)
from monthly_billing.sensitive import luhn_ok
from store_harness import needs_postgres, query, seed

pytestmark = needs_postgres

GARAGE = month_end_garage()
TZ = ZoneInfo(GARAGE.timezone)
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=TZ)


def _card_shaped() -> str:
    body = "4" + "1" * 14
    for check in "0123456789":
        if luhn_ok(body + check):
            return body + check
    raise AssertionError("unreachable")


class _Raises:
    def __init__(self) -> None:
        self.calls = 0

    def charge(self, request):
        self.calls += 1
        raise TimeoutError("socket timed out after the request was sent")


class _Declines:
    def __init__(self) -> None:
        self.calls = 0

    def charge(self, request):
        self.calls += 1
        return ChargeResult(outcome=Outcome.DECLINE, detail="declined")


class _RaisesWithAnInstrumentInTheMessage:
    def charge(self, request):
        raise RuntimeError(f"gateway said: {_card_shaped()}")


class _SucceedsWithAnInstrumentShapedReference:
    def charge(self, request):
        return ChargeResult(outcome=Outcome.SUCCESS, detail="approved", reference=_card_shaped())


def _issued(app, tenant_id) -> str:
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW).lines
    return line.reference


def _tick(n: int) -> datetime:
    return NOW + timedelta(hours=n)


def _log(app, tenant_id):
    return query(
        app, tenant_id, "SELECT kind, outcome, detail FROM charge_attempts ORDER BY sequence"
    )


@pytest.mark.guarantee("G24")
def test_a_processor_that_raises_leaves_an_unknown_row_and_the_attempt_pending(app, tenant_id):
    reference = _issued(app, tenant_id)
    processor = _Raises()
    outcome = attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(1))
    assert processor.calls == 1
    assert isinstance(outcome.result, UnknownAnswer) and outcome.unknown
    assert outcome.result.outcome is Unknown.UNKNOWN and outcome.payment_id is None
    assert outcome.result.detail.startswith("TimeoutError: socket timed out")
    assert _log(app, tenant_id) == [
        ("attempt", None, ""), ("unknown", None, outcome.result.detail),
    ], "an answer the module never received was written as an outcome"
    assert query(app, tenant_id, "SELECT count(*) FROM payments") == [(0,)]
    assert len(pending_attempts(app, tenant_id, reference)) == 1
    assert outcome.retry.attempts == 0, "an unknown counted toward the three"


@pytest.mark.guarantee("G24")
def test_a_raise_never_counts_toward_the_three_and_a_decline_does(app, tenant_id):
    """Three raises are three unknowns on ONE attempt, asked three times under
    one key; a decline on the fourth ask resolves it and counts once. Three
    declines then exhaust, and the refusal writes no row."""
    reference = _issued(app, tenant_id)
    for n in range(MAX_ATTEMPTS):
        attempt_charge(app, tenant_id, _Raises(), reference, recorded_by="cron", now=_tick(n))
    assert query(app, tenant_id, "SELECT count(*) FROM charge_attempts WHERE kind = 'outcome'") == [
        (0,)
    ]
    assert query(app, tenant_id, "SELECT count(*) FROM charge_attempts WHERE kind = 'attempt'") == [
        (1,)
    ], "a raise minted a fresh reservation"
    first = attempt_charge(app, tenant_id, _Declines(), reference, recorded_by="cron", now=_tick(4))
    assert first.result.outcome is Outcome.DECLINE and first.retry.attempts == 1
    for n in range(MAX_ATTEMPTS - 1):
        attempt_charge(app, tenant_id, _Declines(), reference, recorded_by="cron", now=_tick(5 + n))
    with pytest.raises(Refused) as refused:
        attempt_charge(app, tenant_id, _Declines(), reference, recorded_by="cron", now=_tick(9))
    assert refused.value.code == REFUSAL_RETRIES_EXHAUSTED
    kinds = [k for k, _, _ in _log(app, tenant_id)]
    assert kinds == (
        ["attempt", "unknown", "ask", "unknown", "ask", "unknown", "ask", "outcome"]
        + ["attempt", "outcome"] * (MAX_ATTEMPTS - 1)
    ), kinds


@pytest.mark.guarantee("G24")
def test_a_result_the_guard_refused_is_an_unknown_row_saying_unknown(app, tenant_id):
    """The guard fires inside the processor's own return, so the module never
    receives the SUCCESS. It records an unknown whose detail says so, and no
    payment -- it does not know money moved -- and the attempt stays pending."""
    reference = _issued(app, tenant_id)
    outcome = attempt_charge(
        app, tenant_id, _SucceedsWithAnInstrumentShapedReference(), reference,
        recorded_by="cron", now=_tick(1),
    )
    assert outcome.unknown and outcome.payment_id is None
    assert _log(app, tenant_id) == [("attempt", None, ""), ("unknown", None, RESULT_UNKNOWN_DETAIL)]
    assert _card_shaped() not in outcome.result.detail
    assert query(app, tenant_id, "SELECT count(*) FROM payments") == [(0,)]
    assert len(pending_attempts(app, tenant_id, reference)) == 1


@pytest.mark.guarantee("G24")
def test_a_row_is_never_lost_to_its_own_message(app, tenant_id):
    reference = _issued(app, tenant_id)
    outcome = attempt_charge(
        app, tenant_id, _RaisesWithAnInstrumentInTheMessage(), reference,
        recorded_by="cron", now=_tick(1),
    )
    assert outcome.unknown
    assert _log(app, tenant_id) == [("attempt", None, ""), ("unknown", None, DETAIL_WITHHELD)]


@pytest.mark.guarantee("G24")
def test_the_reservation_is_committed_before_the_processor_is_asked(app, tenant_id):
    """The processor reads the log from ANOTHER connection while it is being
    called: the reservation is already there, committed, carrying the amount it
    is being asked for and the attempt id it was handed as its idempotency key.
    The control plants the reservation's commit away and requires red."""
    from store_harness import DSN, app_connection

    reference = _issued(app, tenant_id)

    class _LooksAtTheLog:
        def __init__(self) -> None:
            self.saw: list = []

        def charge(self, request):
            other = app_connection(DSN)
            try:
                self.saw = query(
                    other, tenant_id,
                    "SELECT kind, attempt_id, amount_minor, currency FROM charge_attempts",
                )
            finally:
                other.close()
            self.key = request.idempotency_key
            return ChargeResult(outcome=Outcome.DECLINE, detail="declined")

    processor = _LooksAtTheLog()
    outcome = attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(1))
    assert processor.saw == [("attempt", outcome.attempt_id, 12000, "USD")], (
        "the processor was called before its reservation was committed"
    )
    assert processor.key == outcome.attempt_id


@pytest.mark.guarantee("G24")
def test_a_re_ask_is_committed_as_an_ask_row_before_the_processor_is_asked_again(app, tenant_id):
    """The second call on an unknown-tailed attempt: the processor, reading the
    log from another connection, sees `attempt, unknown, ask` -- the ask
    already committed -- and is handed the SAME key. The control plants the
    ask row away and requires red."""
    from store_harness import DSN, app_connection

    reference = _issued(app, tenant_id)
    first = attempt_charge(app, tenant_id, _Raises(), reference, recorded_by="cron", now=_tick(1))

    class _LooksAtTheLog:
        def charge(self, request):
            other = app_connection(DSN)
            try:
                self.saw = query(
                    other, tenant_id,
                    "SELECT kind, attempt_id FROM charge_attempts ORDER BY sequence",
                )
            finally:
                other.close()
            self.key = request.idempotency_key
            self.amount = request.amount_minor
            return ChargeResult(outcome=Outcome.DECLINE, detail="declined")

    processor = _LooksAtTheLog()
    second = attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(2))
    assert [k for k, _ in processor.saw] == ["attempt", "unknown", "ask"], (
        "the re-ask was sent before its ask row was committed"
    )
    assert {a for _, a in processor.saw} == {first.attempt_id}
    assert processor.key == first.attempt_id == second.attempt_id
    assert processor.amount == 12000


def late_answer_race(tenant_id, reference, operator_says: Outcome, processor_says: Outcome):
    """THE SECOND BRANCH L3'S `l3b_r3_late_answer` race, as a repo test.

    The first ask is lost (an `unknown` row, the attempt pending); the worker's
    re-ask is HELD at the processor; while it is held the operator resolves the
    attempt with ``operator_says``; the processor then answers the worker with
    ``processor_says``. Returns the worker's ``ChargeOutcome``, the operator's,
    and the key the processor was asked under. Two connections of its own -- a
    worker and an operator are two processes.
    """
    import threading

    from store_harness import DSN, app_connection

    worker_connection, operator_connection = app_connection(DSN), app_connection(DSN)
    try:
        attempt_charge(
            worker_connection, tenant_id, _Raises(), reference, recorded_by="cron", now=_tick(1)
        )
        (attempt_id,) = pending_attempts(worker_connection, tenant_id, reference)
        entered, proceed = threading.Event(), threading.Event()
        keys: list = []

        class _Held:
            def charge(self, request):
                keys.append(request.idempotency_key)
                entered.set()
                proceed.wait(20)
                return ChargeResult(
                    outcome=processor_says, detail="approved by the processor",
                    reference="auth-processor" if processor_says is Outcome.SUCCESS else None,
                )

        result: dict = {}

        def worker():
            try:
                result["worker"] = attempt_charge(
                    worker_connection, tenant_id, _Held(), reference,
                    recorded_by="cron", now=_tick(2),
                )
            except Exception as exc:  # noqa: BLE001 -- the shape of a failure is the finding
                result["worker"] = exc

        thread = threading.Thread(target=worker)
        thread.start()
        assert entered.wait(20), "the worker never reached the processor"
        result["operator"] = resolve_attempt(
            operator_connection, tenant_id, attempt_id,
            ChargeResult(
                outcome=operator_says, detail=f"operator says {operator_says.value}",
                reference="auth-op" if operator_says is Outcome.SUCCESS else None,
            ),
            recorded_by="operator", now=_tick(2),
        )
        proceed.set()
        thread.join(timeout=20)
        assert not isinstance(result["worker"], Exception), result["worker"]
        return result["worker"], result["operator"], keys
    finally:
        worker_connection.close()
        operator_connection.close()


@pytest.mark.guarantee("G24")
@pytest.mark.parametrize("operator_says", [Outcome.SUCCESS, Outcome.DECLINE])
@pytest.mark.parametrize("processor_says", [Outcome.SUCCESS, Outcome.DECLINE])
def test_an_answer_that_lands_after_the_operator_resolved_is_a_late_row_never_dropped(
    app, tenant_id, operator_says, processor_says
):
    """The four arms of the race: whatever the operator typed and whatever the
    processor said, the processor's answer is a `late` row beside the operator's
    `outcome` row, carrying the outcome and the detail (and the reference when
    given), and the worker's call returns normally, marked late. Two calls
    reached the processor; two rows follow them -- the `unknown` and the
    `late` -- and the operator's outcome row is the operator's. The late row is
    not an outcome: the fold does not count it, the attempt is not pending, and
    the operator's second resolution is still refused by name."""
    from monthly_billing.findings import REFUSAL_ATTEMPT_ALREADY_RESOLVED

    reference = _issued(app, tenant_id)
    worker, operator, keys = late_answer_race(tenant_id, reference, operator_says, processor_says)
    assert worker.late and worker.result.outcome is processor_says
    assert worker.attempt_id == operator.attempt_id and keys == [operator.attempt_id]
    log = query(
        app, tenant_id,
        "SELECT kind, outcome, detail, recorded_by FROM charge_attempts ORDER BY sequence",
    )
    assert [k for k, _, _, _ in log] == ["attempt", "unknown", "ask", "outcome", LATE], log
    assert log[3] == (
        "outcome", operator_says.value, f"operator says {operator_says.value}", "operator"
    )
    _, late_outcome, late_detail, late_by = log[4]
    assert late_outcome == processor_says.value and late_by == "cron"
    assert late_detail.startswith("approved by the processor")
    assert ("auth-processor" in late_detail) == (processor_says is Outcome.SUCCESS)
    followed = query(
        app, tenant_id,
        "SELECT count(*) FROM charge_attempts WHERE kind IN ('unknown', 'late')",
    )
    assert followed == [(2,)], "a call the processor received is followed by no row"
    assert pending_attempts(app, tenant_id, reference) == ()
    assert worker.retry.attempts == (1 if operator_says is Outcome.DECLINE else 0), (
        "the late row counted toward the three, or the operator's decline did not"
    )
    with pytest.raises(Refused) as refused:
        resolve_attempt(
            app, tenant_id, operator.attempt_id,
            ChargeResult(outcome=Outcome.SUCCESS, detail="again", reference="auth-again"),
            recorded_by="operator", now=_tick(3),
        )
    assert refused.value.code == REFUSAL_ATTEMPT_ALREADY_RESOLVED
