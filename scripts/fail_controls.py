#!/usr/bin/env python3
"""Every guarantee, proven able to FAIL.

A test that has never failed is a decoration. For each registered guarantee this
script breaks the thing the guarantee guards, runs that guarantee's tests in a
fresh interpreter, and requires them to go RED. If a test stays green with its
subject broken, it was not measuring its subject and this script says so.

    python scripts/fail_controls.py            # every control
    python scripts/fail_controls.py G2 G9      # a subset
    python scripts/fail_controls.py --anchors  # anchors only, in a second

**The anchor pre-flight.** ``--anchors`` counts every plant's ``from`` string in
its file without running a single test. An anchor is a string in a source file,
and editing the line it sits on silently retires the control that depends on it --
a sibling repository in this project had five dead controls killed exactly that
way, by a fix round that edited the anchor lines and reported the result as
controls that worked. The pre-flight answers that whole failure mode in a second
where the full run takes a minute, and run against an older tree it is its own
positive control.

**Restores are written back, never `git checkout`.** Each plant is a context
manager whose ``finally`` writes the original bytes and verifies them. `checkout`
has been broken twice on this project and would take a co-resident session's
uncommitted work with it.

**AND A CONTROL THAT REPORTS UNMEASURED IS REPORTING ON THE RUNNER.** If a target
is already red before anything is planted, or ran no tests at all, this says
UNMEASURED rather than counting a pass -- because a suite that cannot run cannot
tell you whether a control fired. Check the runner (is the package installed? is
a database reachable for the ones that need one?) before reading anything into
the subject.

**A TARGET WHOSE TESTS ALL SKIP IS REPORTED "NOT A CONTROL", NOT UNMEASURED, AND
THAT IS THIS SCRIPT'S OWN LIMIT RATHER THAN A JUDGEMENT.** `_NOTHING_RAN` matches
"0 passed", "no tests ran" and "collected 0 items"; an all-skipped target prints
none of those -- it prints "8 skipped" -- so the run proceeds, the target stays
green with its subject broken, and the verdict is DEAD. That is what happens to
G12 on a machine with no database. The exit status is 1 either way, so nothing
goes falsely green; the word is simply the wrong one, and it is recorded here
rather than in a comment that promises otherwise. Read a DEAD verdict on a
database-backed control as "check whether you gave it a database" first.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

from _guarantees import GUARANTEES  # noqa: E402
from monthly_billing.sensitive import luhn_ok  # noqa: E402
from plant import planted, resolve  # noqa: E402


def card_shaped() -> str:
    """A card-SHAPED string, BUILT from the checksum. Never written down.

    The repository-wide sweep this builds a plant for asserts that no
    card-shaped value is in ANY tracked file -- and this script is a tracked
    file. A literal here would be a specimen planted permanently in the very
    place the guarantee forbids, by the control that proves the guarantee. So it
    is constructed, exactly as tests/test_g9_no_instrument_survives.py does it.
    """
    body = "4" + "1" * 14
    for check in "0123456789":
        if luhn_ok(body + check):
            return body + check
    raise AssertionError("no check digit satisfies Luhn, which is arithmetically impossible")


def source(*lines: str) -> str:
    """A block of source code, one argument per line.

    An anchor is frequently several lines long, and written as a single literal
    with escaped newlines it becomes a three-hundred-character line that nobody
    can read and no diff can review. Same string, legible.
    """
    return "\n".join(lines)


def guarantee_of(control_id: str) -> str:
    """The guarantee a control id names. ``G2/instants`` -> ``G2``.

    One guarantee can need more than one plant, and G2's does: the DST claim and
    the proration claim fail in different code, and a single plant carrying both
    would go red if it caught EITHER -- which is the arrangement that lets one
    half sit unmeasured behind the other.
    """
    return control_id.split("/", 1)[0]


#: control id -> (test target, source file, anchor, replacement, what breaks)
CONTROLS: dict[str, tuple[str, str, str, str, str]] = {
    "G1": (
        "tests/test_g1_money_is_minor_units.py",
        "money.py",
        "    if isinstance(node, Decimal):",
        "    if False and isinstance(node, Decimal):  # PLANTED",
        "the leaf walk stops refusing a Decimal, so a Decimal sits accepted at "
        "every leaf no other check types",
    ),
    "G1/whole-document": (
        "tests/test_g1_money_is_minor_units.py",
        "agreement.py",
        '    refuse_non_integer_money(document, "agreement")',
        source(
            "    refuse_non_integer_money(  # PLANTED: only the field this version reads",
            '        document.get("monthly_price_minor"), "agreement.monthly_price_minor"',
            "    )",
        ),
        "the walk covers only the money field the module actually consults, "
        "instead of every leaf. THIS IS THE DEFECT A SIBLING MODULE SHIPPED: "
        "every field-level test stays true, and a bool or a Decimal sits accepted "
        "at the leaves no other check types, under four published sentences "
        "saying otherwise. The first attempt at this control planted a no-op and "
        "reported GREEN, which is why it plants where the claim can actually break",
    ),
    "G14": (
        "tests/test_fixture_axes.py",
        "tests/fixtures.py",
        'SHIFTING_ZONE = "America/Denver"',
        'SHIFTING_ZONE = "America/Phoenix"  # PLANTED: the DST fixture stops shifting',
        "the zone the DST guarantee is measured against no longer observes "
        "daylight saving, so every hour figure G2 takes against it is 24 and the "
        "spring-forward assertions are measuring a fixture with nothing in it -- "
        "a fixture is part of the measurement",
    ),
    "G15": (
        "tests/test_contract_is_generated.py",
        "scripts/generate_contract.py",
        '        f"That is {len(GUARANTEES)} guarantees. Every one of them has a fail control "',
        '        f"That is 13 guarantees. Every one of them has a fail control "  # PLANTED',
        "the published guarantee COUNT stops being derived and becomes a typed "
        "number, which is the shape that goes stale silently: the document keeps "
        "agreeing with itself while the registry moves underneath it",
    ),
    "G16": (
        "tests/test_guarantee_guard.py",
        "tests/test_guarantee_guard.py",
        "        elif name not in UNGUARANTEED_MODULES:",
        "        elif False:  # PLANTED: a module with no mark is accepted",
        "a test module carrying no guarantee mark stops being reported, which is "
        "the hole that hid 16 tests here and 21 in a sibling repository -- skip "
        "or delete such a module and every gate stays green",
    ),
    "G2/proration": (
        "tests/test_g2_proration_and_dst.py",
        "cycle.py",
        "    return (full_amount_minor * days_billed) // days_in_period",
        "    return (full_amount_minor // days_in_period) * days_billed  # PLANTED",
        "proration divides before it multiplies, losing the remainder before it "
        "can be scaled -- a minor unit off on every part period, in the garage's "
        "favour, which is the direction nobody complains about",
    ),
    "G2/instants": (
        "tests/test_g2_proration_and_dst.py",
        "cycle.py",
        source(
            "        start_instant=day_start(start, tz),",
            "        end_instant=day_start(end, tz),",
        ),
        source(
            "        start_instant=day_start(start, tz),",
            "        end_instant=(  # PLANTED: a fixed 24 hours a day, in UTC",
            '            day_start(start, tz).astimezone(__import__("datetime").timezone.utc)',
            '            + __import__("datetime").timedelta(days=days_between(start, end))',
            "        ),",
        ),
        "THE ONE THAT MATTERS: the period's end instant is a fixed 24 hours per "
        "day from the start, computed in UTC, instead of local midnight resolved "
        "in the garage's zone. Every DAY COUNT in the suite is unchanged -- which "
        "is why a day-counting DST test cannot see this at all -- and the elapsed "
        "hours across a transition go wrong by one. AND THE FIRST FORM OF THIS "
        "PLANT WENT THROUGH NO UTC AND REPORTED GREEN, correctly: adding a "
        "timedelta to a zone-aware datetime is WALL-CLOCK arithmetic in Python, "
        "so it lands on the right local midnight by itself. The naive "
        "implementation this guards against is specifically one that works in "
        "absolute time, which is why the plant has to",
    ),
    "G3": (
        "tests/test_g3_month_end_resolves.py",
        "garage.py",
        "        return last_day_of_month(year, month)",
        "        return date(year, month, 28)  # PLANTED",
        "month-end is a fixed 28 rather than the last day the month actually has, "
        "so every month but February bills three days early -- the exact bug the "
        "option set exists to make impossible",
    ),
    "G4": (
        "tests/test_g4_first_charge_is_two_lines.py",
        "invoice.py",
        source(
            "    lines.append(",
            "        InvoiceLine(",
            "            kind=LineKind.FULL_PERIOD,",
        ),
        source(
            '    lines[0] = __import__("dataclasses").replace(  # PLANTED',
            "        lines[0],",
            "        amount_minor=lines[0].amount_minor",
            "        + prorate(agreement.monthly_price_minor, following_days, following.days),",
            "    )",
            "    lines.append(",
            "        InvoiceLine(",
            "            kind=LineKind.FULL_PERIOD,",
        ),
        "the part period and the following one are SUMMED into one figure -- the "
        "customer sees a number they cannot decompose, which is the confusion the "
        "whole module is shaped to avoid",
    ),
    "G5": (
        "tests/test_g5_a_paid_period_is_paid.py",
        "invoice.py",
        source(
            "                agreement_version=agreement.version,",
            "                period_start_day=period.start_day,",
            "                period_end_day=period.end_day,",
            "            )",
            "        )",
            "        lines.extend(_fee_lines(agreement, period, prorated=days != period.days))",
        ),
        source(
            "                agreement_version=1,  # PLANTED: every line claims v1",
            "                period_start_day=period.start_day,",
            "                period_end_day=period.end_day,",
            "            )",
            "        )",
            "        lines.extend(_fee_lines(agreement, period, prorated=days != period.days))",
        ),
        "an invoice line records version 1 whatever priced it, so a paid period "
        "can no longer be checked against the document that priced it",
    ),
    "G6": (
        "tests/test_g6_no_money_crosses_the_entitlement_call.py",
        "entitlement.py",
        source(
            "    #: Conditions this call could not evaluate from what it was given.",
            "    unchecked: tuple[str, ...] = ()",
        ),
        source(
            "    #: Conditions this call could not evaluate from what it was given.",
            "    unchecked: tuple[str, ...] = ()",
            "    #: PLANTED: a monetary field on an access fact.",
            "    outstanding_balance_minor: int | None = None",
        ),
        "a monetary field is added to the entitlement answer. The guarantee "
        "derives its field set from the class, so this is caught on the day it is "
        "added rather than on the day somebody reads the class",
    ),
    "G7/per-reason": (
        "tests/test_g7_unpaid_and_the_exit.py",
        "entitlement.py",
        "        return _not_covered(NOT_COVERED_PAUSED, **cited)",
        source(
            "        return Answer(  # PLANTED: this ONE path loses the sentence",
            "            covered=False,",
            "            reason=NOT_COVERED_REASONS[NOT_COVERED_PAUSED],",
            "            reason_code=NOT_COVERED_PAUSED,",
            "            **cited,",
            "        )",
        ),
        "ONE not-covered path -- PAUSED, not all seven -- stops carrying the "
        "transient sentence. This is the control the previous G7 test could not "
        "produce: it was parametrised over all seven codes and rendered only "
        "NO_AGREEMENT, so a fault planted in a single path left ruff, 129 tests, "
        "every control and the contract check green. A control that breaks every "
        "code at once passes under exactly that defect",
    ),
    "G7": (
        "tests/test_g7_unpaid_and_the_exit.py",
        "findings.py",
        source(
            '    "like any other. It does not mean refuse entry, and it never means refuse "',
            '    "exit."',
        ),
        source(
            '    "like any other."  # PLANTED: the exit half of the sentence is gone',
            '    ""',
        ),
        "the sentence saying a not-covered stay is priced as an ordinary "
        "transient, and that exit is never refused, stops travelling with the "
        "answer -- so a barrier reads 'not covered' with nothing telling it what "
        "that means",
    ),
    "G8": (
        "tests/test_g8_no_mandate_no_charge.py",
        "payment.py",
        "    if agreement.mandate is None:",
        "    if agreement.mandate is None and False:  # PLANTED",
        "an agreement with no mandate record is charged anyway, off-session, with "
        "no record of anybody having agreed to a recurring charge",
    ),
    "G9/detector": (
        "tests/test_g9_no_instrument_survives.py",
        "sensitive.py",
        "        if 13 <= len(digits) <= 19 and luhn_ok(digits):",
        "        if False and 13 <= len(digits) <= 19 and luhn_ok(digits):  # PLANTED",
        "the card detector stops recognising a card, so an instrument reaches the "
        "store and the logs",
    ),
    "G9/separators": (
        "tests/test_g9_no_instrument_survives.py",
        "sensitive.py",
        source(
            "def _digits(value: str) -> str:",
            '    return "".join(c for c in value if c.isdigit())',
        ),
        source(
            "def _digits(value: str) -> str:",
            "    return value  # PLANTED: separators are no longer stripped",
        ),
        "separators stop being stripped, so the same number written in groups of "
        "four sails through -- one space away from blind, in the form these values "
        "are most often pasted",
    ),
    "G9/repository": (
        "tests/test_g9_no_instrument_survives.py",
        "money.py",
        "def format_minor(minor: int, currency: str) -> str:",
        source(
            f"# a support note somebody pasted: {card_shaped()}",
            "def format_minor(minor: int, currency: str) -> str:",
        ),
        "THE ONE THE LAST SPECIMEN GOT PAST: a card-shaped value is planted in a "
        "tracked file that is NOT the detector's own source. The old check read "
        "one path and could not see it -- the suite, both node scanners and the "
        "anchor pre-flight all stayed green with a real specimen in the tree",
    ),
    "G9/store": (
        "tests/test_g9_no_instrument_survives.py",
        "store/writes.py",
        "    refuse_instrument_in_record(table, record)",
        "    pass  # PLANTED: rows are written without being scanned",
        "the store's chokepoint stops scanning, so a column added next round -- "
        "the case the derivation exists for -- reaches the database unread",
    ),
    "G10": (
        "tests/test_g10_a_note_can_never_price.py",
        "exceptions_by_owner.py",
        "    return sum(e.amount_minor or 0 for e in exceptions if e.changes_money)",
        source(
            "    return sum(  # PLANTED: a free-text note now prices something",
            '        (e.amount_minor or 0) + (1000 if "credit" in e.note else 0)',
            "        for e in exceptions",
            "        if e.changes_money",
            "    )",
        ),
        "a free-text note buys money -- the exact defect a sibling module shipped, "
        "where a decisions note could buy a surcharge",
    ),
    "G11": (
        "tests/test_g11_vehicles_are_independent_of_spots.py",
        "agreement.py",
        "        if not isinstance(self.vehicles, tuple) or not self.vehicles:",
        source(
            "        if len(self.vehicles) > self.spots:",
            '            raise InvalidAgreement("PLANTED: list may not exceed the spots")',
            "        if not isinstance(self.vehicles, tuple) or not self.vehicles:",
        ),
        "the vehicle list is tied to the spots bought, so twenty cars against ten "
        "spots -- the shape the specification names in his own words -- is refused",
    ),
    "G12": (
        "tests/test_g12_rls_from_migration_0001.py",
        "migrations/0001_tenants_agreements_and_rls.sql",
        "ALTER TABLE payers FORCE  ROW LEVEL SECURITY;",
        "-- PLANTED: FORCE removed from payers",
        "one table ships without FORCE ROW LEVEL SECURITY. The coverage check "
        "reads the catalogue rather than a list of table names, so it finds this "
        "without anybody adding the table to anything",
    ),
    "G13": (
        "tests/test_g13_a_pause_over_a_paid_period_refuses.py",
        "cycle.py",
        "        overlaps = pause_from < period.end_day and pause_until > period.start_day",
        "        overlaps = pause_from < period.start_day and pause_until > period.end_day",
        "a pause reaching into a paid period is accepted unless it swallows the "
        "period whole, so an owner pauses an account mid-period and the module "
        "silently decides a refund nobody authorised",
    ),
    "G17": (
        "tests/test_g17_the_run_issues_once.py",
        "migrations/0002_billing_run_payments_and_reversals.sql",
        source(
            "ALTER TABLE invoices",
            "  ADD CONSTRAINT invoices_one_per_payer_per_period",
            "  UNIQUE (tenant_id, garage_id, payer_id, period_start_day);",
        ),
        "-- PLANTED: the one-invoice-per-period constraint is gone",
        "the period lock from 0002 is missing from the catalogue. The behavioural "
        "tests stay green -- the reference lock from 0001 still stops the duplicate, "
        "which is what having two independent locks means -- so this is caught by "
        "the catalogue test that reads both constraints by their columns",
    ),
    "G17/both-locks": (
        "tests/test_g17_the_run_issues_once.py",
        "migrations/0002_billing_run_payments_and_reversals.sql",
        source(
            "ALTER TABLE invoices",
            "  ADD CONSTRAINT invoices_one_per_payer_per_period",
            "  UNIQUE (tenant_id, garage_id, payer_id, period_start_day);",
        ),
        source(
            "-- PLANTED: BOTH locks gone -- the period lock is never added and the",
            "-- reference lock from 0001 is dropped, so nothing in the database stops",
            "-- a second invoice for the same period",
            "ALTER TABLE invoices DROP CONSTRAINT invoices_tenant_id_reference_key;",
        ),
        "with no constraint left, a second run of the same period issues a second "
        "invoice. If the run were idempotent by convention -- remembering what it "
        "had issued -- this plant would change nothing and the control would be "
        "dead; it is idempotent by constraint, and this is the proof",
    ),
    "G18": (
        "tests/test_g17_the_run_issues_once.py",
        "billing_run.py",
        "    return period.start_day in (partial.start_day, following.start_day)",
        "    return False  # PLANTED: the run owns every period, the first charge none",
        "the ownership rule says the run owns every period, so the two periods the "
        "first charge already billed are billed again by the next run -- a new "
        "parker charged twice for the same month",
    ),
    "G19/reopen": (
        "tests/test_g19_paid_is_derived.py",
        "payments.py",
        '        guarded_update(cursor, "invoices", {"paid_at": None}, {"id": invoice_uuid})',
        source(
            "        guarded_update(  # PLANTED: unpaid from the REVERSAL, not the billing day",
            '            cursor, "invoices", {"paid_at": None, "due_at": event_at},',
            '            {"id": invoice_uuid},',
            "        )",
        ),
        "a reversal moves the due date to the day the bank said no, so a payer "
        "whose cheque bounced gets a fresh grace period they never paid for -- the "
        "other reading of 'reopens', planted to prove the test tells them apart",
    ),
    "G19/adjustment": (
        "tests/test_g19_paid_is_derived.py",
        "exceptions_store.py",
        "        state = rederive_paid_at(cursor, invoice_uuid, exception.recorded_at)",
        "        state = paid_state(cursor, invoice_uuid)  # PLANTED: read, never re-derived",
        "the third event stops re-deriving: an owner waives the remainder of a "
        "partly paid invoice and paid_at stays NULL, so the credited payer reads "
        "as unpaid at the barrier -- the omission that is easy to make",
    ),
    "G20": (
        "tests/test_g20_money_history_is_append_only.py",
        "migrations/0002_billing_run_payments_and_reversals.sql",
        source(
            "GRANT SELECT, INSERT ON payments, payment_reversals, charge_attempts "
            "TO monthly_billing_app;",
        ),
        source(
            "GRANT SELECT, INSERT, UPDATE, DELETE  -- PLANTED: append-only in a comment only",
            "  ON payments, payment_reversals, charge_attempts TO monthly_billing_app;",
        ),
        "the three money-history tables are granted UPDATE and DELETE, so a payment "
        "can be edited or removed and 'append-only' is a sentence above the table "
        "rather than a property of it",
    ),
    "G9/payments": (
        "tests/test_g20_money_history_is_append_only.py",
        "payments.py",
        source(
            "    guarded_insert(",
            "        cursor,",
            '        "payments",',
            "        {",
        ),
        source(
            "    (lambda cursor, table, record: cursor.execute(  # PLANTED: no chokepoint",
            "        f\"INSERT INTO {table} ({', '.join(record)}) \"",
            "        f\"VALUES ({', '.join(['%s'] * len(record))}) RETURNING id\",",
            "        tuple(record.values()),",
            "    ))(",
            "        cursor,",
            '        "payments",',
            "        {",
        ),
        "the payment row goes to the database around the chokepoint, so a "
        "card-shaped processor reference lands in `payments` unscanned -- the new "
        "table is exactly the column the guard exists to cover",
    ),
    "G21/grace": (
        "tests/test_g21_covered_from_the_store.py",
        "entitlement_store.py",
        "    grace = applied_grace_days(garage.payment_grace_days, exceptions)",
        "    grace = garage.payment_grace_days  # PLANTED: the owner's extension is ignored",
        "the store-backed call reads the garage's base grace and never the owner's "
        "extend_grace exceptions, so an owner who gave a customer another week "
        "watches the lane call them transient on day six",
    ),
    "G21/block": (
        "tests/test_g21_covered_from_the_store.py",
        "entitlement_store.py",
        "        blocked_by_owner=is_blocked(exceptions),",
        "        blocked_by_owner=False,  # PLANTED: a block is never read",
        "the store-backed call never reads a block, so the owner's decision to "
        "block an agreement reaches no barrier",
    ),
    "G22": (
        "tests/test_g22_charge_attempts_are_the_truth.py",
        "charging.py",
        '        if kind == "payment_method_changed":',
        "        if False:  # PLANTED: the method-change rows are never read",
        "the rebuild ignores the persisted method-change rows, so the reset is a "
        "row nobody reads: three old declines refuse a charge on the new card for "
        "ever -- the lie on restart the log exists to prevent",
    ),
    "G12/0002": (
        "tests/test_g12_rls_from_migration_0001.py",
        "migrations/0002_billing_run_payments_and_reversals.sql",
        "ALTER TABLE payments FORCE  ROW LEVEL SECURITY;",
        "-- PLANTED: FORCE removed from payments",
        "a table added by the SECOND migration ships without FORCE ROW LEVEL "
        "SECURITY. The coverage check reads the catalogue, so it finds this without "
        "anybody adding the new table to anything -- which is the promise G12 makes",
    ),
    # -- the L3-fix round: each of these is the L3's finding, planted back --
    "G23": (
        "tests/test_g23_a_charge_is_for_the_balance.py",
        "charging.py",
        "    balance = owed.total_minor - owed.paid_minor",
        "    balance = owed.total_minor  # PLANTED: the total, whatever was paid",
        "the charge is for the TOTAL again: a paid invoice is charged a second "
        "time in full and a part-paid one is charged more than it owes -- the L3's "
        "first blocker, 24000 for a 12000 invoice recorded as two honest payments",
    ),
    "G24": (
        "tests/test_g24_every_processor_call_leaves_a_row.py",
        "payment.py",
        source(
            "    try:",
            "        return processor.charge(request)",
        ),
        source(
            "    return processor.charge(request)  # PLANTED: the raise propagates, no row",
            "    try:",
            "        return processor.charge(request)",
        ),
        "a processor that raises escapes the wrapper, so no attempt row is written "
        "for a call that was made -- a socket timeout after the request was sent "
        "leaves no record and counts toward nothing",
    ),
    "G25": (
        "tests/test_g25_exceptions_follow_the_agreement_identity.py",
        "entitlement_store.py",
        "        WHERE a.external_id = %s",
        source(
            "        WHERE e.agreement_id = (SELECT id FROM agreements WHERE external_id = %s",
            "                                ORDER BY version DESC LIMIT 1)  -- PLANTED",
        ),
        "the owner's exceptions are read from ONE version's row again, so a block "
        "lifts and a grace extension vanishes on the day a price change stores the "
        "next version",
    ),
    "G26": (
        "tests/test_g26_an_amount_is_positive_and_a_refund_moves_no_total.py",
        "exceptions_by_owner.py",
        "            if self.amount_minor <= 0:",
        "            if False:  # PLANTED: the sign is not checked",
        "the module stops refusing a non-positive amount by name -- the CHECK "
        "still refuses the row, so a test asserting the NAMED refusal goes red, "
        "which is the difference between a refusal and a driver error",
    ),
    "G26/refund": (
        "tests/test_g26_an_amount_is_positive_and_a_refund_moves_no_total.py",
        "exceptions_store.py",
        "        if exception.kind in LANDS_A_LINE:",
        "        if exception.kind in LANDS_A_LINE | {ExceptionKind.REFUND}:  # PLANTED",
        "a refund lands a negative adjustment line again, so a paid invoice reads "
        "OVERPAID with no row for the money going back -- the L3's F0, settled as "
        "'a refund moves no total'",
    ),
    "G27": (
        "tests/test_g27_already_issued_means_the_period_row.py",
        "billing_run.py",
        "            if constraint == PERIOD_LOCK or issued:",
        "            if True:  # PLANTED: every unique violation reads as already issued",
        "a unique violation on the reference lock is reported as 'this period was "
        "already invoiced' when the period was not -- the run says a false "
        "sentence in a report somebody acts on, and exits 0",
    ),
    "G28": (
        "tests/test_g28_money_history_points_into_its_own_tenant.py",
        "migrations/0002_billing_run_payments_and_reversals.sql",
        "    FOREIGN KEY (tenant_id, invoice_id) REFERENCES invoices (tenant_id, id) "
        "ON DELETE RESTRICT,",
        "    FOREIGN KEY (invoice_id) REFERENCES invoices (id) ON DELETE RESTRICT,  "
        "-- PLANTED: no tenant in the key",
        "payments points at an invoice by id alone, so a row in tenant B can name "
        "tenant A's invoice: the policy checks payments.tenant_id and the "
        "foreign-key check runs past row-level security",
    ),
    "G29/reason": (
        "tests/test_g29_reversals_and_card_fields_are_refused_by_name.py",
        "payments.py",
        "        if reason not in REASONS_FOR_METHOD[method]:",
        "        if False:  # PLANTED: any reason on any method",
        "a bounced cheque can be recorded against a card payment; the reason table "
        "is a document again",
    ),
    "G29/twice": (
        "tests/test_g29_reversals_and_card_fields_are_refused_by_name.py",
        "payments.py",
        "        if cursor.fetchone() is not None:",
        "        if False:  # PLANTED: a second reversal reaches the database",
        "the second reversal of a payment reaches the UNIQUE and comes back as a "
        "driver error with a traceback instead of a refusal by name",
    ),
    "G20/history": (
        "tests/test_g20_money_history_is_append_only.py",
        "migrations/0002_billing_run_payments_and_reversals.sql",
        "REVOKE UPDATE, DELETE ON invoice_lines, owner_exceptions FROM monthly_billing_app;",
        "-- PLANTED: the revoke is gone; 0001's grant of everything stands",
        "the invoice's lines and the owner's decisions can be edited and deleted by "
        "the application role again, so 'a paid period is paid' rests on code alone",
    ),
    "G21/prose": (
        "tests/test_g21_covered_from_the_store.py",
        "entitlement_store.py",
        "from .entitlement import Answer, is_covered",
        source(
            "from .entitlement import Answer",
            "from .entitlement import is_covered as _pure",
            "",
            "",
            "def is_covered(**kwargs):  # PLANTED: a balance sentence rides on the answer",
            "    a = _pure(**kwargs)",
            '    return replace(a, reason=a.reason + " Balance $120.00 owed.")',
        ),
        "the store-backed call appends an amount to the answer's prose. G6 judges "
        "the CLASS, so the field set stays clean and only a test that reads the "
        "STORE answer's prose can see this -- the L3's F1",
    ),
    "G15/prose": (
        "tests/test_contract_is_generated.py",
        "scripts/generate_contract.py",
        '        return "is still covered"',
        source(
            '        return "is still covered"',
            '    return "is still covered"  # PLANTED: one rendering whatever the store said',
        ),
        "the second-month prose says 'still covered' whatever the store answered: "
        "the number moves, the assertion does not -- the L3's B6 and §6's rule",
    ),
    # -- the outside pass's fix round: each of these is a settled finding, planted back --
    "G30": (
        "tests/test_g30_every_money_event_takes_the_invoice_lock.py",
        "store/postgres.py",
        '    cursor.execute("SELECT id FROM invoices WHERE id = %s FOR UPDATE", (invoice_uuid,))',
        '    cursor.execute("SELECT id FROM invoices WHERE id = %s", (invoice_uuid,))  # PLANTED',
        "the lock ITSELF is gone -- lock_invoice reads the row and locks nothing -- so "
        "a payment and a credit at once each derive paid_at from a snapshot that "
        "cannot see the other: the outside pass's N1, paid 10000 of 10000 and paid_at "
        "NULL, a paid invoice read UNPAID_PAST_GRACE at the lane. THIS is the plant "
        "that turns the concurrency test red; the branch L3 showed that the previous "
        "plant (the lock off record_payment alone) left it green 5/5, because the "
        "payment insert's foreign-key KEY SHARE on the invoice row still serialised "
        "against the credit's FOR UPDATE -- that plant proved the source reader, not "
        "the serialisation, and it is G30/source now",
    ),
    "G30/source": (
        "tests/test_g30_every_money_event_takes_the_invoice_lock.py",
        "payments.py",
        source(
            "        invoice_uuid = invoice_uuid_for(cursor, invoice_reference)",
            "        with locked_invoice(connection, cursor, invoice_uuid):",
            "            payment_uuid = insert_payment(",
        ),
        source(
            "        invoice_uuid = invoice_uuid_for(cursor, invoice_reference)",
            '        with __import__("contextlib").nullcontext():  # PLANTED: no lock here',
            "            payment_uuid = insert_payment(",
        ),
        "record_payment stops entering the locked block, so the SOURCE READER reports "
        "one of the five money events without the lock and the contract's derived "
        "sentence flips to ONLY 4 of 5 -- the AST tests go red. The concurrency test "
        "stays green under this plant by itself (the foreign key's KEY SHARE still "
        "serialises the two sides), which is why it is not the control for the "
        "serialisation sentence -- G30 above is",
    ),
    "G31/reservation-commit": (
        "tests/test_g24_every_processor_call_leaves_a_row.py",
        "charging.py",
        source(
            "    connection.commit()",
            "    return reservation",
        ),
        source(
            "    pass  # PLANTED: the reservation is not committed before the processor",
            "    return reservation",
        ),
        "the reservation row is written but NOT committed before the processor is "
        "called, so a processor can be asked with no row on our side that survives "
        "the worker -- the outside pass's R1.2, where a crash after the processor "
        "said yes left nothing and a restart charged again. G24's test reads the log "
        "from another connection while the processor is being called and sees no row",
    ),
    "G31/atomic-outcome": (
        "tests/test_g31_a_charge_is_a_reservation_first.py",
        "charging.py",
        source(
            "            cursor.fetchone()",
            "            payment_id = paid = None",
            "            if result.outcome is Outcome.SUCCESS:",
        ),
        source(
            "            cursor.fetchone()",
            "            connection.commit()  # PLANTED: outcome committed before its payment",
            '            pg = __import__("monthly_billing.store.postgres", fromlist=["x"])',
            "            pg.set_tenant(cursor, tenant_id)",
            "            pg.lock_invoice(cursor, invoice_uuid)",
            "            payment_id = paid = None",
            "            if result.outcome is Outcome.SUCCESS:",
        ),
        "T2 is split into two transactions: the outcome row is committed on its own, "
        "then the payment in a second -- the outside pass's R1.3 window, a SUCCESS on "
        "record with no money beside it, and the worker dying between the two leaves "
        "exactly that",
    ),
    "G31/pending": (
        "tests/test_g31_a_charge_is_a_reservation_first.py",
        "charging.py",
        "            pending = _pending_attempt(cursor, invoice_uuid)",
        "            pending = None  # PLANTED: a pending attempt is charged past",
        "a reservation with no outcome no longer refuses the next charge, so a card "
        "the processor may already have charged is charged again on the restart",
    ),
    "G22/pending-method-change": (
        "tests/test_g22_charge_attempts_are_the_truth.py",
        "charging.py",
        source(
            "        with locked_invoice(connection, cursor, invoice_uuid):",
            "            guarded_insert(",
            "                cursor,",
            '                "charge_attempts",',
            "                {",
            '                    "tenant_id": tenant_id,',
            '                    "invoice_id": invoice_uuid,',
            '                    "kind": "payment_method_changed",',
        ),
        source(
            "        with locked_invoice(connection, cursor, invoice_uuid):",
            "            if _pending_attempt(cursor, invoice_uuid) is not None:  # PLANTED",
            '                raise Refused(REFUSAL_ATTEMPT_UNRESOLVED, "planted")',
            "            guarded_insert(",
            "                cursor,",
            '                "charge_attempts",',
            "                {",
            '                    "tenant_id": tenant_id,',
            '                    "invoice_id": invoice_uuid,',
            '                    "kind": "payment_method_changed",',
        ),
        "a payment-method change is refused while an attempt is pending, so an "
        "operator changing a card while collection is stuck is refused for a row "
        "that moves no money",
    ),
    "G22/success-resets": (
        "tests/test_g22_charge_attempts_are_the_truth.py",
        "payment.py",
        "            return replace(self, last_outcome=result.outcome, last_detail=\"\")",
        source(
            "            return replace(  # PLANTED: a success resets the count",
            "                self, attempts=0, last_outcome=result.outcome, last_detail=\"\"",
            "            )",
        ),
        "a success resets the non-success count again -- M1's fold -- so two declines, "
        "a success and a chargeback leave the payer three fresh attempts they did not "
        "earn, the count the outside pass's R1.3 saw reset by a success with no payment",
    ),
    "G23/cheque-between": (
        "tests/test_g23_a_charge_is_for_the_balance.py",
        "charging.py",
        "        if result.outcome is Outcome.SUCCESS:",
        source(
            "        if result.outcome is Outcome.SUCCESS and (  # PLANTED: dropped if paid",
            "            paid_state(cursor, invoice_uuid).paid_minor",
            "            < paid_state(cursor, invoice_uuid).total_minor",
            "        ):",
        ),
        "T2 drops the card payment when the invoice was paid meanwhile -- the money "
        "the processor moved for the reservation is recorded nowhere, silently, "
        "instead of as an overpayment",
    ),
    "G29/concurrent-backstop": (
        "tests/test_g29_reversals_and_card_fields_are_refused_by_name.py",
        "payments.py",
        "            if violation.diag.constraint_name != ONE_REVERSAL_PER_PAYMENT:",
        "            if True:  # PLANTED: the backstop is not caught by name",
        "the one-reversal-per-payment constraint comes back as a raw UniqueViolation "
        "again when two reversals race past the check -- the outside pass's R4, an "
        "operator handed a driver error for a state the module can name",
    ),
    "G32/store": (
        "tests/test_g32_one_car_one_agreement_per_garage.py",
        "store/records.py",
        "        if frees_on is None or frees_on > today:",
        "        if False:  # PLANTED: the second agreement takes the vehicle",
        "the store no longer refuses a vehicle another agreement holds: the second "
        "agreement takes it, silently, and the first is still covering it until its "
        "own version says otherwise -- one car on two agreements again",
    ),
    "G32/pure": (
        "tests/test_g32_one_car_one_agreement_per_garage.py",
        "entitlement.py",
        "    if len(identities) > 1:",
        "    if False:  # PLANTED: two identities, and the latest version of whichever wins",
        "the pure call picks again when handed two agreement identities for one "
        "vehicle -- the outside pass's R2, where the highest id decided coverage "
        "against the wrong agreement both ways",
    ),
    "G32/registration": (
        "tests/test_g32_one_car_one_agreement_per_garage.py",
        "entitlement_store.py",
        "            if item.agreement.id == holder",
        "            if True  # PLANTED: every agreement listing the plate, registered or not",
        "the store-backed call goes back to scanning every agreement's vehicle list "
        "instead of reading the registration, so a row written past the module, or "
        "a version that has released the car, decides coverage again",
    ),
    "G33": (
        "tests/test_g33_a_total_never_goes_below_zero.py",
        "exceptions_store.py",
        "            if owed.total_minor - amount < 0:",
        "            if False:  # PLANTED: the total may go below zero",
        "an adjustment past zero lands: a 15000 credit on a 12000 invoice makes the "
        "total -3000 and paid_at is set with zero payments -- the outside pass's "
        "Grok 10, money and not an opinion",
    ),
    "G34": (
        "tests/test_g34_a_block_on_an_invoice_survives_the_payment.py",
        "entitlement_store.py",
        "               AND (i.paid_at IS NULL OR e.kind IN ('block', 'unblock')))",
        "               AND i.paid_at IS NULL)  -- PLANTED: paying the invoice lifts its block",
        "invoice-attached exceptions are read only while the invoice is unpaid "
        "again, so a cheque lifts the owner's block with no unblock recorded -- the "
        "outside pass's N2",
    ),
    "G35": (
        "tests/test_g35_overpayment_is_a_recorded_fact.py",
        "payments.py",
        "        return max(0, self.paid_minor - self.total_minor)",
        "        return 0  # PLANTED: an overpayment is a fact nowhere again",
        "the paid state reports no overpayment whatever was paid, so a 30000 cheque "
        "on a 12000 invoice reads PAID and the excess is recorded nowhere -- the "
        "outside pass's Grok 9",
    ),
    "G36": (
        "tests/test_g36_the_command_line_says_when_payment_state_is_now.py",
        "cli.py",
        "    if at < datetime.now().astimezone():",
        "    if False:  # PLANTED: a past --at is answered as of now without saying so",
        "the command line answers a past instant with today's payment state and says "
        "nothing -- the outside pass's ChatGPT 5 claim, unsaid again",
    ),
    # -- the outside pass's SECOND fix round: each of these is a settled finding, planted back --
    "G31/unknown-is-not-an-outcome": (
        "tests/test_g31_a_charge_is_a_reservation_first.py",
        "charging.py",
        source(
            "    if isinstance(answer, UnknownAnswer):",
            "        retry = _record_unknown(",
        ),
        source(
            "    if isinstance(answer, UnknownAnswer):  # PLANTED: an unknown becomes an error",
            "        answer = ChargeResult(outcome=Outcome.ERROR, detail=answer.detail)",
            "    if isinstance(answer, UnknownAnswer):",
            "        retry = _record_unknown(",
        ),
        "an answer the module never received is written as a resolved `error` "
        "outcome again: the attempt stops being pending and the next charge reserves "
        "a FRESH key and asks again -- the branch L3's Blocker 1, a processor that "
        "charged and then raised, charged twice",
    ),
    "G31/resume-reuses-the-key": (
        "tests/test_g31_a_charge_is_a_reservation_first.py",
        "charging.py",
        source(
            "                reservation = _Reservation(",
            "                    pending.attempt_id, invoice_uuid, invoice, pending.amount_minor,",
        ),
        source(
            "                reservation = _Reservation(  # PLANTED: a fresh key on the re-ask",
            "                    uuid4(), invoice_uuid, invoice, pending.amount_minor,",
        ),
        "the re-ask of a pending attempt goes to the processor under a NEW "
        "idempotency key, so a processor that honours keys sees a second charge -- "
        "the same double charge, one row later",
    ),
    "G24/ask-row": (
        "tests/test_g24_every_processor_call_leaves_a_row.py",
        "charging.py",
        source(
            "                _log_row(",
            '                    cursor, tenant_id, invoice_uuid, "ask", pending.attempt_id,',
        ),
        source(
            "                (lambda *a, **k: None)(  # PLANTED: the re-ask leaves no row",
            '                    cursor, tenant_id, invoice_uuid, "ask", pending.attempt_id,',
        ),
        "a re-ask is sent with no `ask` row committed before it, so a call the "
        "processor received has no row on our side saying it was made -- the shape "
        "G24 exists to forbid, back on the second ask",
    ),
    "G22/fold-at-reservation": (
        "tests/test_g22_charge_attempts_are_the_truth.py",
        "charging.py",
        source(
            "                if attempt_id not in reserved_since_change:",
            "                    continue  # reserved under the method the change retired",
        ),
        source(
            "                if False:  # PLANTED: an outcome counts where it sits",
            "                    continue  # reserved under the method the change retired",
        ),
        "an outcome counts toward whichever method is current when it is RECORDED, "
        "not when it was reserved -- the branch L3's finding (d): a decline asked of "
        "the old card, resolved after the operator changed it, counts against the "
        "new card",
    ),
    "G22/log-order": (
        "tests/test_g22_charge_attempts_are_the_truth.py",
        "charging.py",
        '        "ORDER BY sequence",',
        '        "ORDER BY occurred_at, created_at",  # PLANTED: the typed instant decides',
        "the fold walks the log in the order of the instants callers typed instead "
        "of the order it was recorded, so a worker whose clock is behind the "
        "operator's has its decline reset by a method change recorded BEFORE it "
        "asked -- the count depends on a --at somebody typed",
    ),
    "G31/index-by-name": (
        "tests/test_g31_a_charge_is_a_reservation_first.py",
        "charging.py",
        "                if violation.diag.constraint_name != ONE_OUTCOME_PER_ATTEMPT:",
        "                if True:  # PLANTED: the index is not caught by name",
        "the one-outcome-per-attempt index comes back as a raw UniqueViolation when "
        "it is reached -- the branch L3's finding (b), a resolver handed a driver "
        "error for a state the module can name",
    ),
    "G31/refusal-names-the-id": (
        "tests/test_g31_a_charge_is_a_reservation_first.py",
        "charging.py",
        source(
            '                    f"{pending.attempt_id} whose request may be in flight; "',
        ),
        source(
            '                    "whose request may be in flight; "  # PLANTED: no id',
        ),
        "the refusal stops naming the attempt id -- the branch L3's finding (a): "
        "the operator is told to resolve an attempt whose id the refusal does not "
        "carry",
    ),
    "G31/pending-state": (
        "tests/test_g31_a_charge_is_a_reservation_first.py",
        "charging.py",
        '            state=UNKNOWN if last_kind == "unknown" else IN_FLIGHT,',
        "            state=IN_FLIGHT,  # PLANTED: unknown and in-flight read the same",
        "a pending attempt the module has said it does not know about reads as "
        "in flight: the listing cannot tell the two apart and the next charge "
        "refuses instead of asking again under the same key",
    ),
    "G32/writes-nothing": (
        "tests/test_g32_one_car_one_agreement_per_garage.py",
        "store/records.py",
        source(
            "    held = _held_elsewhere(cursor, garage, garage_uuid, agreement, listed, today)",
            "    # A version that drops a vehicle releases it -- AFTER every refusal.",
        ),
        source(
            "    cursor.execute(  # PLANTED: the release DELETE runs before the refusals",
            '        "DELETE FROM vehicle_registrations WHERE garage_id = %s "',
            '        "AND agreement_external_id = %s AND NOT (identity_normalised = ANY(%s))",',
            "        (garage_uuid, agreement.id, sorted(listed)),",
            "    )",
            "    held = _held_elsewhere(cursor, garage, garage_uuid, agreement, listed, today)",
            "    # A version that drops a vehicle releases it -- AFTER every refusal.",
        ),
        "the release DELETE runs before the refusal check again -- the branch L3's "
        "Blocker 2: a version that drops one car and is refused on another leaves "
        "the dropped car's registration deleted in the caller's open transaction, "
        "and a caller that commits after the refusal strands a covered car at the "
        "lane",
    ),
    "G37": (
        "tests/test_g37_every_garage_reference_is_a_composite_tenant_key.py",
        "migrations/0003_invoice_lock_attempt_reservation_and_registrations.sql",
        source(
            "ALTER TABLE agreements",
            "  ADD CONSTRAINT agreements_garage_in_tenant",
            "  FOREIGN KEY (tenant_id, garage_id)",
            "  REFERENCES garages (tenant_id, id) ON DELETE RESTRICT;",
        ),
        "-- PLANTED: agreements keeps only 0001's bare garage reference",
        "agreements points at a garage by id alone again, so a tenant-A row can name "
        "tenant B's garage by a raw insert -- the branch L3's A1.2 / ChatGPT 14 on "
        "0001's table. The catalogue read finds the bare reference, and the raw "
        "insert is accepted",
    ),
    "G37/registrations": (
        "tests/test_g37_every_garage_reference_is_a_composite_tenant_key.py",
        "migrations/0003_invoice_lock_attempt_reservation_and_registrations.sql",
        source(
            "  CONSTRAINT vehicle_registrations_garage_in_tenant",
            "    FOREIGN KEY (tenant_id, garage_id)",
            "    REFERENCES garages (tenant_id, id) ON DELETE CASCADE",
        ),
        source(
            "  CONSTRAINT vehicle_registrations_garage_in_tenant",
            "    FOREIGN KEY (garage_id)",
            "    REFERENCES garages (id) ON DELETE CASCADE  -- PLANTED: no tenant in the key",
        ),
        "the registrations table's garage key loses its tenant half -- the shape of "
        "the first cut of this table, which the branch L3 showed accepting a "
        "tenant-A row at tenant B's garage",
    ),
    "G38": (
        "tests/test_g38_no_exception_leaves_the_invoice_lock_held.py",
        "store/postgres.py",
        source(
            "    except BaseException:",
            "        connection.rollback()",
            "        raise",
        ),
        source(
            "    except BaseException:",
            "        raise  # PLANTED: the lock is left held on the caller's connection",
        ),
        "the manager re-raises without rolling back, so every refusal and every "
        "guard raise inside a locked block leaves the invoice lock held on the "
        "caller's connection -- the branch L3's Blocker 3, a cheque on a second "
        "connection waiting for as long as the first caller idles",
    ),
    "G12/tenants": (
        "tests/test_g12_rls_from_migration_0001.py",
        "store/postgres.py",
        "                      AND a.attname = 'tenant_id'",
        "                      AND a.attname = 'tenant_id_planted'",
        "the tenant-column guard looks for a column no table has, so it reports every "
        "table but tenants as lacking one -- proving the catalogue read is a "
        "measurement of the column and the tenants exemption is the only one",
    ),
}


def check_anchors() -> int:
    """Count every anchor. Zero or two is a dead control, and it is silent."""
    bad = 0
    for gid, (_target, path, anchor, _to, _why) in sorted(CONTROLS.items()):
        count = resolve(path).read_text().count(anchor)
        status = "ok" if count == 1 else "DEAD"
        if count != 1:
            bad += 1
        print(f"  {status:4}  {gid}  {path}  anchor appears {count}x")
    if bad:
        print(
            f"\n{bad} control(s) have no live anchor. An anchor that matches zero times "
            "plants nothing, and the control then reports green against unmodified "
            "source. Fix the anchors before trusting any result from this script."
        )
        return 1
    print(f"\nall {len(CONTROLS)} anchors live.")
    return 0


def _pytest(target: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", target, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


#: ``"0 passed" in stdout`` was the first form of this and it was WRONG:
#: "10 passed" contains it, so three live controls reported UNMEASURED and the run
#: said 14/17. A substring match confirms only that a substring was present -- the
#: rule this project already has, broken inside the machinery built to enforce it.
_NOTHING_RAN = re.compile(r"(?<!\d)0 passed|no tests ran|collected 0 items")


def run_control(gid: str) -> bool:
    target, path, anchor, replacement, why = CONTROLS[gid]
    print(f"\n=== {gid} — {GUARANTEES[guarantee_of(gid)]}")
    print(f"    plant: {path}")
    print(f"    breaks: {why}")

    green = _pytest(target)
    if green.returncode != 0:
        print(f"    UNMEASURED: {target} is already failing before anything was planted.")
        print(green.stdout[-1500:])
        return False
    if _NOTHING_RAN.search(green.stdout):
        print(f"    UNMEASURED: {target} ran no tests, so nothing here can go red.")
        print(green.stdout[-800:])
        return False

    with planted(path, anchor, replacement):
        red = _pytest(target)

    tail = [ln for ln in red.stdout.splitlines() if ln.startswith(("FAILED", "ERROR"))]
    summary = red.stdout.strip().splitlines()[-1] if red.stdout.strip() else ""
    if red.returncode == 0:
        print(f"    NOT A CONTROL: {target} stayed GREEN with its subject broken.")
        return False
    print(f"    RED, as required — {summary}")
    for line in tail[:6]:
        print(f"      {line}")
    return True


def main(argv: list[str]) -> int:
    if "--anchors" in argv:
        return check_anchors()

    named = [a for a in argv if not a.startswith("-")]
    wanted = [c for c in sorted(CONTROLS) if c in named or guarantee_of(c) in named]
    unknown = [
        a for a in named
        if a not in CONTROLS and a not in {guarantee_of(c) for c in CONTROLS}
    ]
    if unknown:
        print(f"no such control: {', '.join(unknown)}")
        return 2
    wanted = wanted or sorted(CONTROLS)

    missing = sorted(set(GUARANTEES) - {guarantee_of(c) for c in CONTROLS})
    if missing:
        print(
            f"registered guarantees with no fail-control: {', '.join(missing)}. "
            "Every guarantee is proven able to fail, or it is not a guarantee."
        )
        return 1

    if check_anchors():
        return 1

    results = {gid: run_control(gid) for gid in wanted}
    dead = [gid for gid, ok in results.items() if not ok]
    print(f"\n{len(results) - len(dead)}/{len(results)} controls fired.")
    if dead:
        print(f"DEAD CONTROLS: {', '.join(dead)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
