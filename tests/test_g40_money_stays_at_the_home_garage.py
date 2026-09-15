"""G40 -- money is not multi-garage. Every money door is keyed on the HOME.

**THE TRAP THIS ROUND COULD SHIP, AND IT IS A MONEY ONE.** ``load_agreements_at_garage``
has three callers that do not want the same thing: the billing run and the
charge are MONEY doors and must keep returning only the agreements BILLED at the
garage; the store-backed coverage call is the ACCESS door and is the one that
widened. Widening the shared loader would invoice one agreement at every garage
it is valid at. So the access door is a second function, and the tests here
prove BY EXECUTION that a run at a covered-but-not-home garage issues nothing
for the agreement -- with a payer who has another agreement billed there, so
the widened loader would have had somewhere to put the lines, and with a payer
who has not, so the widened payer read would have found a payer to report.

The reason money stays home is not caution: the billing day, currency, timezone
and grace are stated per garage with no defaults, and a multi-garage agreement
has no rule for which one governs. Inventing one would price somebody. The
fixture's two garages differ on all of them, and the invoice the home issues is
asserted to carry the HOME's currency and the HOME's period.

Controls: the money loader widened to the covered set; the payer read widened;
``_same_garage`` reading the covered set instead of the home; the store filing
an agreement under a garage that is not its home.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from fixtures import MULTI_HOME, MULTI_OTHER, multi_garage_agreement, simple_agreement
from monthly_billing.agreement import AccessHours, Status
from monthly_billing.billing_run import RunOutcome, run_billing
from monthly_billing.cycle import period_containing
from monthly_billing.entitlement_store import covered_from_store
from monthly_billing.exceptions_by_owner import ExceptionKind, OwnerException
from monthly_billing.exceptions_store import record_invoice_exception
from monthly_billing.findings import (
    NOT_COVERED_BLOCKED_BY_OWNER,
    NOT_COVERED_NO_AGREEMENT,
    REFUSAL_AGREEMENT_HOME_MOVED,
    REFUSAL_GARAGE_MISMATCH,
    REFUSAL_INVOICE_NAMES_NO_AGREEMENT,
    REFUSAL_NO_MANDATE,
    Refused,
)
from monthly_billing.invoice import first_charge, invoice_for_period
from monthly_billing.payment import ChargeResult, Outcome
from monthly_billing.store.postgres import tenant
from monthly_billing.store.records import (
    load_agreements_at_garage,
    load_payers_at_garage,
    registrations_at_garage,
    store_agreement,
)
from store_harness import needs_postgres, query, seed_garages

HOME = MULTI_HOME()  # Denver, USD, month-end
OTHER = MULTI_OTHER()  # Phoenix, JPY, the 1st
HOME_TZ = ZoneInfo(HOME.timezone)
MULTI = multi_garage_agreement(start_day=date(2026, 1, 5))
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=HOME_TZ)


def store_backed(test):
    for mark in needs_postgres:
        test = mark(test)
    return test


# ---------------------------------------------------------------------------
# The pure money doors
# ---------------------------------------------------------------------------


@pytest.mark.guarantee("G40")
def test_the_first_charge_refuses_a_covered_but_not_home_garage_by_name():
    """The same refusal as for a garage the agreement has nothing to do with,
    and the detail says which of the two it was."""
    with pytest.raises(Refused) as covered:
        first_charge(OTHER, MULTI)
    assert covered.value.code == REFUSAL_GARAGE_MISMATCH
    assert "covers but is not billed at" in covered.value.detail
    assert f"billed at garage {HOME.id!r}" in covered.value.detail
    stranger = simple_agreement(garage_id="garage-tenth", covered_garage_ids=("garage-tenth",))
    with pytest.raises(Refused) as unrelated:
        first_charge(OTHER, stranger)
    assert unrelated.value.code == REFUSAL_GARAGE_MISMATCH
    assert "covers" not in unrelated.value.detail
    # The control: at the home it prices, in the home's currency and periods.
    invoice = first_charge(HOME, MULTI)
    assert invoice.garage_id == HOME.id and invoice.currency == HOME.currency == "USD"
    assert invoice.issued_for_period_start == period_containing(HOME, MULTI.start_day).start_day


@pytest.mark.guarantee("G40")
def test_the_period_invoice_refuses_a_covered_but_not_home_garage_by_name():
    period = period_containing(OTHER, date(2026, 5, 15))
    with pytest.raises(Refused) as refused:
        invoice_for_period(OTHER, (MULTI,), period, MULTI.payer_id)
    assert refused.value.code == REFUSAL_GARAGE_MISMATCH
    at_home = invoice_for_period(HOME, (MULTI,), period_containing(HOME, date(2026, 5, 15)),
                                 MULTI.payer_id)
    assert at_home.currency == "USD" and len(at_home.lines) == 1


# ---------------------------------------------------------------------------
# The store: the run and the charge
# ---------------------------------------------------------------------------


@pytest.mark.guarantee("G40")
@store_backed
def test_a_run_at_a_covered_but_not_home_garage_issues_nothing_for_the_agreement(app, tenant_id):
    """The payer ALSO has an agreement billed at OTHER, so a widened loader
    would have put MULTI's lines on that invoice. The invoice at OTHER carries
    ag-other's line only, in OTHER's currency; MULTI's invoice is at the home."""
    other_agreement = simple_agreement(
        id="ag-other", garage_id=OTHER.id, covered_garage_ids=(OTHER.id,),
        vehicles=("OTHER-1",), monthly_price_minor=5000, start_day=date(2026, 1, 5),
    )
    assert other_agreement.payer_id == MULTI.payer_id
    seed_garages(app, tenant_id, (HOME, OTHER), (MULTI, other_agreement), now=NOW)

    at_other = run_billing(app, tenant_id, OTHER.id, date(2026, 5, 15), now=NOW)
    (line,) = at_other.lines
    assert line.outcome is RunOutcome.ISSUED and line.total_minor == 5000, line
    lines = query(
        app, tenant_id,
        "SELECT a.external_id, l.amount_minor, i.currency, g.external_id "
        "FROM invoice_lines l JOIN invoices i ON i.id = l.invoice_id "
        "JOIN agreements a ON a.id = l.agreement_id JOIN garages g ON g.id = i.garage_id "
        "ORDER BY a.external_id",
    )
    assert lines == [("ag-other", 5000, "JPY", OTHER.id)], (
        "the run at the covered-but-not-home garage priced the multi-garage agreement"
    )

    at_home = run_billing(app, tenant_id, HOME.id, date(2026, 5, 15), now=NOW)
    (home_line,) = at_home.lines
    assert home_line.outcome is RunOutcome.ISSUED and home_line.total_minor == 12000
    assert query(
        app, tenant_id,
        "SELECT g.external_id, i.currency, i.period_start_day FROM invoices i "
        "JOIN garages g ON g.id = i.garage_id JOIN invoice_lines l ON l.invoice_id = i.id "
        "JOIN agreements a ON a.id = l.agreement_id WHERE a.external_id = %s",
        (MULTI.id,),
    ) == [(HOME.id, "USD", date(2026, 4, 30))], (
        "MULTI's invoice is at its home, in the home's currency, on the home's period"
    )


@pytest.mark.guarantee("G40")
@store_backed
def test_a_run_at_a_covered_but_not_home_garage_finds_no_payer_when_nothing_is_billed_there(
    app, tenant_id
):
    """The payer's only agreement is MULTI. At OTHER there is no payer to
    report -- not NOTHING_BILLABLE, no line at all -- because the payer read is
    keyed on the home too."""
    seed_garages(app, tenant_id, (HOME, OTHER), (MULTI,), now=NOW)
    report = run_billing(app, tenant_id, OTHER.id, date(2026, 5, 15), now=NOW)
    assert report.lines == (), report.rendered()
    assert "no payer has an agreement here" in report.rendered()
    assert query(app, tenant_id, "SELECT count(*) FROM invoices") == [(0,)]
    # The control: the same run at the home issues it.
    (line,) = run_billing(app, tenant_id, HOME.id, date(2026, 5, 15), now=NOW).lines
    assert line.outcome is RunOutcome.ISSUED


@pytest.mark.guarantee("G40")
@store_backed
def test_the_homes_invoice_is_charged_through_the_home_loader(app, tenant_id):
    """The charge reads the agreements billed at the invoice's garage. For a
    multi-garage agreement that is the home, and the mandate gate finds it."""
    from monthly_billing.charging import attempt_charge

    class Succeeds:
        def __init__(self) -> None:
            self.amounts: list[int] = []

        def charge(self, request):
            self.amounts.append(request.amount_minor)
            return ChargeResult(outcome=Outcome.SUCCESS, detail="approved", reference="auth-1")

    seed_garages(app, tenant_id, (HOME, OTHER), (MULTI,), now=NOW)
    (line,) = run_billing(app, tenant_id, HOME.id, date(2026, 5, 15), now=NOW).lines
    processor = Succeeds()
    outcome = attempt_charge(
        app, tenant_id, processor, line.reference, recorded_by="cron", now=NOW + timedelta(hours=1)
    )
    assert outcome.paid is not None and outcome.paid.paid
    assert processor.amounts == [12000]
    assert query(app, tenant_id, "SELECT currency FROM payments") == [("USD",)]


@pytest.mark.guarantee("G40")
@store_backed
def test_the_store_refuses_to_file_an_agreement_under_a_garage_that_is_not_its_home(
    app, tenant_id
):
    seeded = seed_garages(app, tenant_id, (HOME, OTHER), (), now=NOW)
    try:
        with tenant(app, tenant_id) as cursor:
            from monthly_billing.store.records import store_payer

            payer = store_payer(cursor, tenant_id, MULTI.payer_id, "Acme")
            with pytest.raises(Refused) as refused:
                store_agreement(
                    cursor, tenant_id, OTHER, seeded.garage_uuids[OTHER.id], payer, MULTI, now=NOW
                )
            assert refused.value.code == REFUSAL_GARAGE_MISMATCH
            assert "stored under" in refused.value.detail
            cursor.execute("SELECT count(*) FROM agreements")
            assert cursor.fetchone() == (0,)
            # The control: under its home it is stored.
            store_agreement(
                cursor, tenant_id, HOME, seeded.garage_uuids[HOME.id], payer, MULTI, now=NOW
            )
    except BaseException:
        app.rollback()  # a red here is this test's alone; see test_g39
        raise
    app.commit()
    assert query(app, tenant_id, "SELECT count(*) FROM agreements") == [(1,)]


# ---------------------------------------------------------------------------
# The L3's F1 / the fix brief's X2: the money loaders choose the LATEST version
# FIRST and ask where it is homed second. And X4: the charge gate reads the
# agreements the invoice's lines name, and the garage does not enter it.
#
# THE STATE THESE MEASURE IS THE RESIDUE. Since the gate-fix round the store
# REFUSES a version that would move the home (X5, below), so a "moved"
# agreement can only exist as a row written PAST the module -- which is what
# ``_plant_moved_version`` writes: the raw rows, as the app role, with none of
# the store's refusals in the way. X2 and X4 go on measuring exactly that
# state, and the contract says that is the state they hold for.
# ---------------------------------------------------------------------------


class _Succeeds:
    def __init__(self) -> None:
        self.amounts: list[int] = []

    def charge(self, request):
        self.amounts.append(request.amount_minor)
        return ChargeResult(outcome=Outcome.SUCCESS, detail="approved", reference="auth-1")


def _move_home(app, tenant_id, *, v1_mandate: bool = True, v2_mandate: bool = True):
    """v1 homed at HOME (covering HOME), then v2 homed at OTHER (covering OTHER):
    the agreement's home MOVED. Both versions are filed through the store,
    each under its own home -- the store's refusal is a within-call check and
    a move passes it (the fix brief's A1.1). Returns the seed and the pair."""
    v1 = simple_agreement(
        version=1, garage_id=HOME.id, covered_garage_ids=(HOME.id,), start_day=date(2026, 1, 5),
        with_mandate=v1_mandate,
    )
    v2 = simple_agreement(
        version=2, garage_id=OTHER.id, covered_garage_ids=(OTHER.id,), start_day=date(2026, 1, 5),
        with_mandate=v2_mandate,
    )
    seeded = seed_garages(app, tenant_id, (HOME, OTHER), (v1,), now=NOW)
    return seeded, v1, v2


def _store_v2(app, tenant_id, seeded, v2, home):
    with tenant(app, tenant_id) as cursor:
        store_agreement(
            cursor, tenant_id, home, seeded.garage_uuids[home.id],
            seeded.payer_uuids[v2.payer_id], v2, now=NOW,
        )
    app.commit()


def _plant_moved_version(app, tenant_id, seeded, version):
    """A version homed at ANOTHER garage than the stored ones, written PAST the
    module: the raw agreement row, its covered set, its vehicles and its
    mandate, as the app role -- the residue the contract names. The store
    itself refuses this (``REFUSAL_AGREEMENT_HOME_MOVED``); this is the only
    way the state can come to exist."""
    home = {HOME.id: HOME, OTHER.id: OTHER}[version.garage_id]
    home_uuid = seeded.garage_uuids[home.id]
    with tenant(app, tenant_id) as cursor:
        cursor.execute(
            "INSERT INTO agreements (tenant_id, external_id, version, garage_id, payer_id, "
            "spots, monthly_price_minor, start_day, status) VALUES (%s, %s, %s, %s, %s, %s, %s, "
            "%s, %s) RETURNING id",
            (tenant_id, version.id, version.version, home_uuid,
             seeded.payer_uuids[version.payer_id], version.spots, version.monthly_price_minor,
             version.start_day, version.status.value),
        )
        (row_id,) = cursor.fetchone()
        for garage_id in version.covered_garage_ids:
            cursor.execute(
                "INSERT INTO agreement_garages (tenant_id, agreement_id, garage_id) "
                "VALUES (%s, %s, %s)",
                (tenant_id, row_id, seeded.garage_uuids[garage_id]),
            )
        for identity in version.vehicles:
            cursor.execute(
                "INSERT INTO agreement_vehicles (tenant_id, agreement_id, identity, "
                "identity_normalised) VALUES (%s, %s, %s, %s)",
                (tenant_id, row_id, identity, home.normalise_identity(identity)),
            )
        if version.mandate is not None:
            m = version.mandate
            cursor.execute(
                "INSERT INTO mandates (tenant_id, agreement_id, agreed_by, agreed_at, "
                "terms_shown, frequency_shown, amount_basis_shown, cancellation_shown) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (tenant_id, row_id, m.agreed_by, datetime.fromisoformat(m.agreed_at_iso),
                 m.terms_shown, m.frequency_shown, m.amount_basis_shown, m.cancellation_shown),
            )
    app.commit()


@pytest.mark.guarantee("G40")
@store_backed
def test_an_agreement_whose_newer_version_moved_its_home_is_billed_at_the_new_home_only(
    app, tenant_id
):
    """Filter-then-pick returned v1 at HOME (the latest of the versions homed
    THERE) and the run invoiced one agreement at two garages -- USD on v1 and
    JPY on v2, measured at c4baa88 and at main. Latest-first returns nothing at
    HOME: no payer is reported there, no invoice exists there, and the run at
    OTHER issues v2 alone. The moved version is the RESIDUE -- written past the
    module, since the store refuses the move (X5)."""
    seeded, v1, v2 = _move_home(app, tenant_id)
    _plant_moved_version(app, tenant_id, seeded, v2)
    with tenant(app, tenant_id) as cursor:
        at_home = load_agreements_at_garage(cursor, seeded.garage_uuids[HOME.id])
        at_other = load_agreements_at_garage(cursor, seeded.garage_uuids[OTHER.id])
        payers_home = load_payers_at_garage(cursor, seeded.garage_uuids[HOME.id])
        payers_other = load_payers_at_garage(cursor, seeded.garage_uuids[OTHER.id])
    app.rollback()
    assert at_home == (), [(i.agreement.id, i.agreement.version) for i in at_home]
    assert [(i.agreement.id, i.agreement.version) for i in at_other] == [(v2.id, 2)]
    assert payers_home == (), "the payer read found the payer at the OLD home on v1's row"
    assert [p for _, p in payers_other] == [v2.payer_id]

    old_home = run_billing(app, tenant_id, HOME.id, date(2026, 5, 15), now=NOW)
    assert old_home.lines == (), old_home.rendered()
    assert "no payer has an agreement here" in old_home.rendered()
    new_home = run_billing(app, tenant_id, OTHER.id, date(2026, 5, 15), now=NOW)
    (line,) = new_home.lines
    assert line.outcome is RunOutcome.ISSUED
    assert query(
        app, tenant_id,
        "SELECT g.external_id, i.currency, a.version FROM invoices i "
        "JOIN garages g ON g.id = i.garage_id JOIN invoice_lines l ON l.invoice_id = i.id "
        "JOIN agreements a ON a.id = l.agreement_id ORDER BY 1",
    ) == [(OTHER.id, "JPY", 2)], "the moved agreement was invoiced at more than its new home"


@pytest.mark.guarantee("G40")
@store_backed
def test_an_invoice_issued_before_a_home_move_is_still_charged_after_it(app, tenant_id):
    """X4. The invoice at HOME was issued on v1; then the agreement moved to
    OTHER. A garage-keyed gate now finds nothing at HOME -- and the old eager
    ``agreements[0]`` default raised IndexError under the lock. The gate reads
    the agreement the invoice's lines NAME, at its latest version wherever it
    is homed: the charge goes through, for the invoice's own amount, in the
    invoice's own currency. The moved version is the RESIDUE -- written past the
    module, since the store refuses the move (X5)."""
    seeded, v1, v2 = _move_home(app, tenant_id)
    (line,) = run_billing(app, tenant_id, HOME.id, date(2026, 5, 15), now=NOW).lines
    assert line.outcome is RunOutcome.ISSUED and line.total_minor == 12000
    _plant_moved_version(app, tenant_id, seeded, v2)
    from monthly_billing.charging import attempt_charge

    processor = _Succeeds()
    outcome = attempt_charge(
        app, tenant_id, processor, line.reference, recorded_by="cron", now=NOW + timedelta(hours=1)
    )
    assert outcome.paid is not None and outcome.paid.paid
    assert processor.amounts == [12000]
    assert query(app, tenant_id, "SELECT currency, amount_minor FROM payments") == [("USD", 12000)]


@pytest.mark.guarantee("G40")
@store_backed
def test_the_mandate_the_gate_reads_is_the_latest_versions(app, tenant_id):
    """The key of the gate's read is the agreement's IDENTITY, at its LATEST
    version -- not the version row the line names (that version PRICED the
    line, G5; it does not decide the card). v1 without a mandate issues an
    invoice that cannot be charged; v2 -- same home -- agrees to a mandate and
    the same invoice IS charged. And the mirror: a mandate withdrawn at v3 is
    not charged on the strength of v2's. Measured at c4baa88: the same answers,
    so this pins today's behaviour, which a by-line-version read would lose."""
    from monthly_billing.charging import attempt_charge

    v1 = simple_agreement(version=1, start_day=date(2026, 1, 5), with_mandate=False)
    seeded = seed_garages(app, tenant_id, (HOME,), (v1,), now=NOW)
    (line,) = run_billing(app, tenant_id, HOME.id, date(2026, 5, 15), now=NOW).lines
    with pytest.raises(Refused) as before:
        attempt_charge(app, tenant_id, _Succeeds(), line.reference, recorded_by="cron",
                       now=NOW + timedelta(hours=1))
    assert before.value.code == REFUSAL_NO_MANDATE and "version 1" in before.value.detail
    assert query(app, tenant_id, "SELECT agreement_version FROM invoice_lines") == [(1,)]

    v2 = simple_agreement(version=2, start_day=date(2026, 1, 5), with_mandate=True)
    _store_v2(app, tenant_id, seeded, v2, HOME)
    processor = _Succeeds()
    outcome = attempt_charge(app, tenant_id, processor, line.reference, recorded_by="cron",
                             now=NOW + timedelta(hours=2))
    assert outcome.paid is not None and outcome.paid.paid and processor.amounts == [12000]

    # The mirror, on a second invoice: v3 withdraws the mandate; the June
    # invoice is refused naming version 3, not charged on v2's.
    v3 = simple_agreement(version=3, start_day=date(2026, 1, 5), with_mandate=False)
    _store_v2(app, tenant_id, seeded, v3, HOME)
    (june,) = run_billing(app, tenant_id, HOME.id, date(2026, 6, 15),
                          now=datetime(2026, 5, 31, 0, 30, tzinfo=HOME_TZ)).lines
    assert june.outcome is RunOutcome.ISSUED
    with pytest.raises(Refused) as withdrawn:
        attempt_charge(app, tenant_id, _Succeeds(), june.reference, recorded_by="cron",
                       now=datetime(2026, 6, 1, 1, 0, tzinfo=HOME_TZ))
    assert withdrawn.value.code == REFUSAL_NO_MANDATE and "version 3" in withdrawn.value.detail


@pytest.mark.guarantee("G40")
@store_backed
def test_the_no_mandate_agreement_on_the_invoice_is_still_the_one_the_refusal_names(
    app, tenant_id
):
    """A1.3's control: two agreements of one payer on one invoice, one without
    a mandate; the refusal names THAT one, whichever way the loader orders them."""
    from monthly_billing.charging import attempt_charge

    with_card = simple_agreement(id="ag-card", start_day=date(2026, 1, 5), vehicles=("CARD-1",))
    no_card = simple_agreement(
        id="ag-nocard", start_day=date(2026, 1, 5), vehicles=("NOCARD-1",), with_mandate=False
    )
    assert with_card.payer_id == no_card.payer_id
    seed_garages(app, tenant_id, (HOME,), (with_card, no_card), now=NOW)
    (line,) = run_billing(app, tenant_id, HOME.id, date(2026, 5, 15), now=NOW).lines
    assert line.outcome is RunOutcome.ISSUED
    with pytest.raises(Refused) as refused:
        attempt_charge(app, tenant_id, _Succeeds(), line.reference, recorded_by="cron",
                       now=NOW + timedelta(hours=1))
    assert refused.value.code == REFUSAL_NO_MANDATE
    assert "'ag-nocard'" in refused.value.detail and "'ag-card'" not in refused.value.detail
    assert query(app, tenant_id, "SELECT count(*) FROM charge_attempts") == [(0,)]


@pytest.mark.guarantee("G40")
@store_backed
def test_an_invoice_naming_no_loadable_agreement_is_refused_by_name_never_an_indexerror(
    app, tenant_id, monkeypatch
):
    """The empty case, reached the only way it can be -- a store written past
    the module -- by handing the gate a loader that finds nothing. Refused by
    name, before any attempt row; the eager ``agreements[0]`` default that
    raised IndexError under the lock is the plant."""
    import monthly_billing.charging as charging

    seed_garages(app, tenant_id, (HOME,), (simple_agreement(start_day=date(2026, 1, 5)),), now=NOW)
    (line,) = run_billing(app, tenant_id, HOME.id, date(2026, 5, 15), now=NOW).lines
    monkeypatch.setattr(charging, "load_agreements_on_invoice", lambda cursor, invoice: ())
    with pytest.raises(Refused) as refused:
        charging.attempt_charge(app, tenant_id, _Succeeds(), line.reference, recorded_by="cron",
                                now=NOW + timedelta(hours=1))
    assert refused.value.code == REFUSAL_INVOICE_NAMES_NO_AGREEMENT
    assert line.reference in refused.value.detail and "1 line(s)" in refused.value.detail
    assert query(app, tenant_id, "SELECT count(*) FROM charge_attempts") == [(0,)]
    # The connection is usable and unlocked afterwards (G38): the real loader
    # charges the same invoice on the next call.
    monkeypatch.undo()
    processor = _Succeeds()
    outcome = charging.attempt_charge(app, tenant_id, processor, line.reference,
                                      recorded_by="cron", now=NOW + timedelta(hours=2))
    assert outcome.paid is not None and outcome.paid.paid and processor.amounts == [12000]



# ---------------------------------------------------------------------------
# The gate-fix round's X5: THE HOME NEVER MOVES. Moving an agreement's home
# garage is an operation nobody designed -- nothing states what it does to the
# month already invoiced at the old home, to an unpaid invoice or an owner's
# block sitting there, or to which garage's billing day and currency govern
# next -- so the store refuses it by name before a row is written. The gate
# measured what a stored move did: the old home's unpaid invoice and the block
# on it vanished from the barrier while the charge still collected the invoice.
# ---------------------------------------------------------------------------


def _counts(app, tenant_id):
    (row,) = query(
        app, tenant_id,
        "SELECT (SELECT count(*) FROM agreements), (SELECT count(*) FROM agreement_garages), "
        "(SELECT count(*) FROM agreement_vehicles), (SELECT count(*) FROM vehicle_registrations), "
        "(SELECT count(*) FROM mandates)",
    )
    return row


@pytest.mark.guarantee("G40")
@store_backed
def test_a_version_that_moves_the_home_is_refused_by_name_and_writes_nothing(app, tenant_id):
    """The gate's own B1 sequence, made UNCONSTRUCTIBLE through the store: v1
    homed at HOME, its May invoice issued there and BLOCKED by the owner; then
    v2 homed at OTHER (HOME still covered) is offered under OTHER -- and refused
    by name, with every table as it was. The barrier goes on reading the block
    at HOME and NO_AGREEMENT at OTHER: nothing the move would have erased is
    erased, because the move never happened."""
    seeded, v1, v2 = _move_home(app, tenant_id)
    v2 = simple_agreement(
        version=2, garage_id=OTHER.id, covered_garage_ids=(OTHER.id, HOME.id),
        start_day=date(2026, 1, 5),
    )
    (line,) = run_billing(app, tenant_id, HOME.id, date(2026, 5, 1), now=NOW).lines
    assert line.outcome is RunOutcome.ISSUED
    record_invoice_exception(
        app, tenant_id,
        OwnerException(
            id="exc-block-home", agreement_id=None, invoice_reference=line.reference,
            kind=ExceptionKind.BLOCK, recorded_by="the owner",
            recorded_at=datetime(2026, 5, 1, 12, tzinfo=HOME_TZ),
        ),
    )
    when = datetime(2026, 5, 3, 12, tzinfo=HOME_TZ)
    before = covered_from_store(app, tenant_id, HOME.id, "car 001", when)
    assert not before.covered and before.reason_code == NOT_COVERED_BLOCKED_BY_OWNER
    counts = _counts(app, tenant_id)
    registrations = {
        g.id: sorted(query(
            app, tenant_id,
            "SELECT identity_normalised, agreement_external_id FROM vehicle_registrations "
            "WHERE garage_id = %s", (seeded.garage_uuids[g.id],),
        ))
        for g in (HOME, OTHER)
    }

    try:
        with tenant(app, tenant_id) as cursor:
            with pytest.raises(Refused) as refused:
                store_agreement(
                    cursor, tenant_id, OTHER, seeded.garage_uuids[OTHER.id],
                    seeded.payer_uuids[v2.payer_id], v2, now=NOW,
                )
    except BaseException:
        app.rollback()
        raise
    app.commit()
    assert refused.value.code == REFUSAL_AGREEMENT_HOME_MOVED
    assert "'ag-0001' version 2" in refused.value.detail
    assert f"garage {OTHER.id!r}" in refused.value.detail
    assert "never moves" in refused.value.detail

    assert _counts(app, tenant_id) == counts, "the refused move wrote something"
    for g in (HOME, OTHER):
        rows = sorted(query(
            app, tenant_id,
            "SELECT identity_normalised, agreement_external_id FROM vehicle_registrations "
            "WHERE garage_id = %s", (seeded.garage_uuids[g.id],),
        ))
        assert rows == registrations[g.id], g.id
    # The barrier, after the refusal: exactly as before it.
    after = covered_from_store(app, tenant_id, HOME.id, "car 001", when)
    assert not after.covered and after.reason_code == NOT_COVERED_BLOCKED_BY_OWNER
    at_other = covered_from_store(app, tenant_id, OTHER.id, "CAR001", when)
    assert not at_other.covered and at_other.reason_code == NOT_COVERED_NO_AGREEMENT
    # And the invoice the block sits on is still the payer's, unpaid, at HOME.
    assert query(app, tenant_id, "SELECT count(*) FROM invoices WHERE paid_at IS NULL") == [(1,)]


@pytest.mark.guarantee("G40")
@store_backed
def test_the_refused_move_is_the_second_version_not_the_first_agreement_at_a_garage(
    app, tenant_id, owner
):
    """The refusal compares with the versions ALREADY STORED for the same
    identity -- a brand-new agreement stores at any garage, and the same
    external id in ANOTHER tenant is another agreement (the tenant policy scopes
    the lookup)."""
    seeded, v1, v2 = _move_home(app, tenant_id)
    fresh = simple_agreement(
        id="ag-fresh", garage_id=OTHER.id, covered_garage_ids=(OTHER.id,),
        start_day=date(2026, 1, 5), vehicles=("FRESH-1",),
    )
    _store_v2(app, tenant_id, seeded, fresh, OTHER)
    assert query(app, tenant_id, "SELECT count(*) FROM agreements") == [(2,)]
    # Another tenant, the same external id, a different home: not a move.
    from store_harness import new_tenant

    other_tenant = new_tenant(owner)
    twin = simple_agreement(
        version=1, garage_id=OTHER.id, covered_garage_ids=(OTHER.id,), start_day=date(2026, 1, 5)
    )
    seed_garages(app, other_tenant, (HOME, OTHER), (twin,), now=NOW)
    assert query(app, other_tenant, "SELECT count(*) FROM agreements") == [(1,)]


@pytest.mark.guarantee("G40")
@store_backed
def test_a_version_that_changes_anything_but_the_home_still_stores(app, tenant_id):
    """The over-reach control, must stay green: price, vehicles, covered set
    (added AND dropped -- the whole of M3), access hours, mandate withdrawn,
    cancellation -- every one a later version at the SAME home, every one
    stored. Only the home is pinned."""
    seeded, v1, _ = _move_home(app, tenant_id)
    versions = (
        simple_agreement(version=2, garage_id=HOME.id, covered_garage_ids=(HOME.id, OTHER.id),
                         start_day=date(2026, 1, 5), monthly_price_minor=15000),
        simple_agreement(version=3, garage_id=HOME.id, covered_garage_ids=(HOME.id, OTHER.id),
                         start_day=date(2026, 1, 5), vehicles=("CAR000", "NEW-1"),
                         access_hours=AccessHours(entry_from=time(6, 0), exit_by=time(20, 0))),
        simple_agreement(version=4, garage_id=HOME.id, covered_garage_ids=(HOME.id,),
                         start_day=date(2026, 1, 5), vehicles=("CAR000",), with_mandate=False),
        simple_agreement(version=5, garage_id=HOME.id, covered_garage_ids=(HOME.id,),
                         start_day=date(2026, 1, 5), vehicles=("CAR000",), with_mandate=False,
                         status=Status.CANCELLED, cancelled_effective_day=date(2026, 6, 30)),
    )
    for version in versions:
        _store_v2(app, tenant_id, seeded, version, HOME)
    assert query(app, tenant_id, "SELECT version FROM agreements ORDER BY version") == [
        (1,), (2,), (3,), (4,), (5,)
    ]
    with tenant(app, tenant_id) as cursor:
        (latest,) = load_agreements_at_garage(cursor, seeded.garage_uuids[HOME.id])
        (regs_other,) = [registrations_at_garage(cursor, seeded.garage_uuids[OTHER.id])]
    app.rollback()
    assert latest.agreement.version == 5 and latest.agreement.status is Status.CANCELLED
    assert regs_other == (), "the dropped garage's rows were not released"
