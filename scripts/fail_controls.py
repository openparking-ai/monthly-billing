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
        "    grace = applied_grace_days(home.garage.payment_grace_days, exceptions)",
        "    grace = home.garage.payment_grace_days  # PLANTED: the owner's extension is ignored",
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
        "from .entitlement import Answer, is_covered, register_of",
        source(
            "from .entitlement import Answer, register_of",
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
    "G24/late-row": (
        "tests/test_g24_every_processor_call_leaves_a_row.py",
        "charging.py",
        source(
            "            if recorded is not None and from_the_processor:",
            "                payment_id, paid = _record_late(",
        ),
        source(
            "            if recorded is not None and from_the_processor:",
            "                payment_id, paid = (lambda *a, **k: (None, None))(  # PLANTED",
        ),
        "the processor's answer to an ask that was at the processor when the "
        "operator resolved the attempt is DROPPED again -- the second branch L3's "
        "finding (i): a call the processor received, followed by no row, and "
        "nothing in the store says what the processor said under that key",
    ),
    "G31/late-success-honoured": (
        "tests/test_g31_a_charge_is_a_reservation_first.py",
        "charging.py",
        "    if result.outcome is Outcome.SUCCESS and recorded_outcome is not Outcome.SUCCESS:",
        "    if False:  # PLANTED: a late SUCCESS the operator did not record is not paid",
        "a late SUCCESS the operator recorded as a decline writes its row and no "
        "payment: the money moved at the processor, the invoice still reads owed, "
        "and the next charge reserves a FRESH key -- the payer charged twice, one "
        "row later than the finding that found it",
    ),
    "G32/writes-nothing": (
        "tests/test_g32_one_car_one_agreement_per_garage.py",
        "store/records.py",
        source(
            "    plan: list[tuple[StoredGarage, datetime, set[str], dict[str, UUID]]] = []",
            "    for stored in covered:",
        ),
        source(
            "    plan: list[tuple[StoredGarage, datetime, set[str], dict[str, UUID]]] = []",
            "    cursor.execute(  # PLANTED: the home's release DELETE runs before the refusals",
            '        "DELETE FROM vehicle_registrations WHERE garage_id = %s "',
            '        "AND agreement_external_id = %s AND NOT (identity_normalised = ANY(%s))",',
            "        (covered[0].uuid, agreement_id,",
            (
                "         sorted({covered[0].garage.normalise_identity(v) for v in "
                "vehicles})),"
            ),
            "    )",
            "    for stored in covered:",
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
    # -- M3: an agreement covers many garages; money stays at the home --
    "G14/covered-garages": (
        "tests/test_fixture_axes.py",
        "tests/fixtures.py",
        "MULTI_OTHER = first_of_month_garage",
        "MULTI_OTHER = tenth_of_month_garage  # PLANTED: the home's zone and currency",
        "the multi-garage fixture's second garage shares the home's timezone and "
        "currency, so every number the fan-out, grace and money tests take across "
        "'two garages' is taken across one point on two of the three axes -- the "
        "fixture is part of the measurement",
    ),
    "G39/membership": (
        "tests/test_g39_coverage_follows_the_covered_set.py",
        "entitlement.py",
        "        if a.covers_garage(garage.id)",
        "        if a.garage_id == garage.id  # PLANTED: equality, the pre-0004 door",
        "the coverage door is the home-garage equality again, so an agreement "
        "covering a second garage answers NO_AGREEMENT there -- the shape this "
        "round exists to change",
    ),
    "G39/grace-home": (
        "tests/test_g39_coverage_follows_the_covered_set.py",
        "entitlement.py",
        "        if (day_of(at, home_tz) - due_day).days > home.payment_grace_days:",
        "        if (day_of(at, home_tz) - due_day).days > garage.payment_grace_days:  # PLANTED",
        "the asking garage's grace is applied to the home's invoice, so one unpaid "
        "invoice lapses on two different days depending on which door the car is "
        "at -- the alternative C5 rejected",
    ),
    "G39/grace-clock": (
        "tests/test_g39_coverage_follows_the_covered_set.py",
        "entitlement.py",
        "        home_tz = zone(home.timezone)",
        "        home_tz = tz  # PLANTED: the days of grace counted on the asking garage's clock",
        "the days since the invoice fell due are counted on the asking garage's "
        "clock, so a door one zone away reads one more day than the home does and "
        "lapses the car an hour before the invoice's own garage would",
    ),
    "G39/across-set": (
        "tests/test_g39_coverage_follows_the_covered_set.py",
        "entitlement.py",
        '                f" across the {len(agreement.covered_garage_ids)} garages it covers."',
        '                " at this garage."  # PLANTED: the entitlement stated per garage',
        "the covered answer tells the platform the entitlement is per garage -- ten "
        "at each -- instead of across the covered set; the NUMBER at each door is the "
        "same either way, so only the sentence the platform reads can carry the rule",
    ),
    "G39/home-required": (
        "tests/test_g39_coverage_follows_the_covered_set.py",
        "entitlement.py",
        source(
            "    if home_garage is None:",
            "        if agreement.garage_id == garage.id:",
            "            return garage",
        ),
        source(
            "    if home_garage is None:",
            "        if True:  # PLANTED: the asking garage stands in for the home",
            "            return garage",
        ),
        "a library caller asking at a non-home garage with an unpaid instant and no "
        "home garage is answered with the asking garage's grace instead of refused "
        "by name -- a default in the one place this module refuses to have one",
    ),
    "G39/unpaid-home": (
        "tests/test_g39_coverage_follows_the_covered_set.py",
        "entitlement_store.py",
        "        unpaid_since = _earliest_unpaid_due_at(cursor, chosen.payer_uuid, home.uuid)",
        (
            "        unpaid_since = _earliest_unpaid_due_at(cursor, chosen.payer_uuid, "
            "stored.uuid)  "
            "# PLANTED"
        ),
        "THE DEFECT THIS ROUND WOULD OTHERWISE SHIP: the unpaid read is keyed on the "
        "ASKING garage, where the invoice does not live, so an unpaid monthly reads "
        "COVERED at every garage except the one that bills it. The query compiles "
        "and every single-garage test stays green",
    ),
    "G39/exceptions-home": (
        "tests/test_g39_coverage_follows_the_covered_set.py",
        "entitlement_store.py",
        (
            "        exceptions = _exceptions_for(cursor, chosen.agreement.id, chosen.payer_uuid, "
            "home.uuid)"
        ),
        (
            "        exceptions = _exceptions_for(cursor, chosen.agreement.id, chosen.payer_uuid, "
            "stored.uuid)  # PLANTED"
        ),
        "the exceptions read is keyed on the asking garage, so an owner's block or "
        "grace extension recorded on the home's invoice does not reach the other "
        "doors",
    ),
    "G39/unpaid-payer-wide": (
        "tests/test_g39_coverage_follows_the_covered_set.py",
        "entitlement_store.py",
        '        "WHERE payer_id = %s AND garage_id = %s AND paid_at IS NULL",',
        (
            '        "WHERE payer_id = %s AND (garage_id = %s OR TRUE) AND paid_at IS NULL",  # '
            'PLANTED'
        ),
        "the unpaid read aggregates across every home the payer has, so an unpaid "
        "invoice on one agreement lapses the payer's OTHER agreement, homed "
        "elsewhere -- a blocking relationship A1.2 says does not exist",
    ),
    "G39/access-door": (
        "tests/test_g39_coverage_follows_the_covered_set.py",
        "entitlement_store.py",
        "            for item in load_agreements_covering_garage(cursor, stored.uuid)",
        source(
            "            for item in __import__(",
            '                "monthly_billing.store.records", fromlist=["x"]',
            (
                "            ).load_agreements_at_garage(cursor, stored.uuid)  # PLANTED: the "
                "money "
                "door"
            ),
        ),
        "the coverage call reads the MONEY loader -- agreements billed here -- so a "
        "car on an agreement homed elsewhere is NO_AGREEMENT at every garage it "
        "covers but the home",
    ),
    "G39/fanout": (
        "tests/test_g39_coverage_follows_the_covered_set.py",
        "store/records.py",
        source(
            "    for stored in covered:",
            "        tz = zone(stored.garage.timezone)",
        ),
        source(
            "    for stored in covered[:1]:  # PLANTED: registrations at the home only",
            "        tz = zone(stored.garage.timezone)",
        ),
        "the registrations are written at the home alone, so the other covered "
        "garage holds no row for the plate and its door reads NO_AGREEMENT",
    ),
    "G39/fanout-rule": (
        "tests/test_g39_coverage_follows_the_covered_set.py",
        "store/records.py",
        "        listed = {stored.garage.normalise_identity(v) for v in vehicles}",
        (
            "        listed = {covered[0].garage.normalise_identity(v) for v in "
            "vehicles}  "
            "# PLANTED"
        ),
        "every garage's row is normalised under the HOME's rule, so at an exact-rule "
        "garage the stored form is the folded one and the barrier's own lookup never "
        "finds it",
    ),
    "G39/collisions-at-home-only": (
        "tests/test_g39_coverage_follows_the_covered_set.py",
        "store/records.py",
        (
            "        held = _held_elsewhere(cursor, stored.garage, stored.uuid, agreement_id, "
            "listed, today)"
        ),
        source(
            "        held = (  # PLANTED: collisions checked at the home only",
            (
                "            _held_elsewhere(cursor, stored.garage, stored.uuid, agreement_id, "
                "listed, "
                "today)"
            ),
            "            if stored is covered[0] else {}",
            "        )",
        ),
        "a plate another agreement holds at a covered-but-not-home garage is not "
        "refused by name before the writes: the home's rows land, the other garage's "
        "insert hits the UNIQUE, and the caller's transaction is aborted with rows "
        "already written in it -- the 0003 rule not met at each garage the agreement "
        "now covers (A1.3)",
    ),
    "G39/dropped-garage": (
        "tests/test_g39_coverage_follows_the_covered_set.py",
        "store/records.py",
        '        "AND NOT (garage_id = ANY(%s))",',
        (
            '        "AND NOT (garage_id = ANY(%s)) AND FALSE",  # PLANTED: a dropped garage keeps '
            'its rows'
        ),
        "a version that drops a garage from the covered set leaves the agreement's "
        "registrations there, so the door at the dropped garage keeps answering "
        "covered on a version that no longer lists it",
    ),
    "G39/released-by-home-rows": (
        "tests/test_g39_coverage_follows_the_covered_set.py",
        "store/records.py",
        "        frees_on = _released_on(cursor, holder)",
        source(
            (
                "        cursor.execute(  # PLANTED: the holder's release read on the asking "
                "garage's "
                "home rows"
            ),
            (
                '            "SELECT status, cancelled_effective_day FROM agreements WHERE '
                'garage_id '
                '= %s "'
            ),
            '            "AND external_id = %s ORDER BY version DESC LIMIT 1",',
            "            (garage_uuid, holder),",
            "        )",
            "        _row = cursor.fetchone()",
            (
                "        frees_on = None if _row is None or _row[0] != Status.CANCELLED.value else "
                "_row[1]"
            ),
        ),
        "the holder's release day is read from the agreements homed at the ASKING "
        "garage, so a cancelled holder homed elsewhere has no row there and is called "
        "active forever -- its plate never frees at the garages it merely covered",
    ),
    "G39/document-defaults": (
        "tests/test_g39_coverage_follows_the_covered_set.py",
        "agreement.py",
        '        {"id", "version", "garage_id", "covered_garage_ids", "payer_id", "spots",',
        (
            '        {"id", "version", "garage_id", "payer_id", "spots",  # PLANTED: the set is '
            'optional'
        ),
        "a document that omits covered_garage_ids is no longer refused by name at the "
        "required-fields check; it falls through to a KeyError, which is not the "
        "refusal the contract publishes",
    ),
    "G39/home-in-set": (
        "tests/test_g39_coverage_follows_the_covered_set.py",
        "agreement.py",
        "        if self.garage_id not in self.covered_garage_ids:",
        "        if False:  # PLANTED: the home need not be covered",
        "an agreement billed at a garage its covered set does not hold is accepted, "
        "paid for and good nowhere it pays",
    ),
    "G40/loader-widened": (
        "tests/test_g40_money_stays_at_the_home_garage.py",
        "store/records.py",
        "        WHERE a.garage_id = %s AND (%s::uuid IS NULL OR a.payer_id = %s::uuid)",
        source(
            (
                "        WHERE a.id IN (SELECT agreement_id FROM agreement_garages WHERE garage_id "
                "= "
                "%s)"
            ),
            (
                "          AND (%s::uuid IS NULL OR a.payer_id = %s::uuid)  -- PLANTED: every "
                "agreement valid here"
            ),
        ),
        "THE TRAP OF §4: the money loader returns every agreement VALID at the garage "
        "instead of every agreement BILLED there, so the run at a covered-but-not-home "
        "garage prices the multi-garage agreement onto that garage's invoice -- in "
        "that garage's currency, on that garage's period",
    ),
    "G40/payers-widened": (
        "tests/test_g40_money_stays_at_the_home_garage.py",
        "store/records.py",
        '        "WHERE a.garage_id = %s ORDER BY p.external_id",',
        source(
            (
                '        "WHERE a.id IN (SELECT agreement_id FROM agreement_garages WHERE '
                'garage_id = '
                '%s) "'
            ),
            (
                '        "ORDER BY p.external_id",  # PLANTED: every payer with an agreement valid '
                'here'
            ),
        ),
        "the run finds a payer at a garage their agreement merely covers and reports "
        "a line for them there; nothing is issued only because the agreement loader "
        "still holds, and the report no longer says what is true",
    ),
    "G40/money-door-membership": (
        "tests/test_g40_money_stays_at_the_home_garage.py",
        "invoice.py",
        "    if agreement.garage_id != garage.id:",
        (
            "    if not agreement.covers_garage(garage.id):  # PLANTED: the money door reads the "
            "covered set"
        ),
        "the first charge and the period invoice price an agreement against a garage "
        "it merely covers -- that garage's billing day, currency and proration over "
        "the home's, with no rule saying which governs",
    ),
    "G40/home-move-allowed": (
        "tests/test_g40_money_stays_at_the_home_garage.py",
        "store/records.py",
        "        if home_already != garage_uuid:",
        "        if False:  # PLANTED: the home may move",
        "THE GATE'S B1 PLANTED BACK: a later version billed at another garage is "
        "stored, the agreement's home moves, and the old home's unpaid invoice and "
        "the owner's block on it stop reaching every barrier while the charge still "
        "collects that invoice -- an operation nobody designed, answered differently "
        "at every door",
    ),
    "G40/store-under-any-garage": (
        "tests/test_g40_money_stays_at_the_home_garage.py",
        "store/records.py",
        "    if garage.id != agreement.garage_id:",
        "    if False:  # PLANTED: an agreement may be filed under a garage that is not its home",
        "the store files an agreement's version row under whatever garage the caller "
        "named, so agreements.garage_id -- the money key -- stops being the home the "
        "document states",
    ),
    # -- M3 fix round: the LATEST version is chosen before any garage question --
    "G14/dropping-versions": (
        "tests/test_fixture_axes.py",
        "tests/fixtures.py",
        "        version=2, covered_garage_ids=(MULTI_HOME().id,), **overrides",
        "        version=2, **overrides  # PLANTED: v2 drops nothing",
        "the 'dropping' fixture's second version covers the same two garages as its "
        "first, so every test that says it crosses 'a version that dropped a garage' "
        "samples a version that dropped nothing -- the fixture is part of the "
        "measurement",
    ),
    "G39/latest-after-filter": (
        "tests/test_g39_coverage_follows_the_covered_set.py",
        "entitlement.py",
        "    return tuple(latest.values())",
        "    return tuple(agreements)  # PLANTED: every version reaches the coverage filter",
        "THE L3'S BLOCKER B1 PLANTED BACK: every version survives to the coverage "
        "filter and the latest SURVIVOR wins, so a garage the newer version dropped is "
        "answered COVERED on the older version that listed it -- the withdrawn "
        "coverage still opens the barrier",
    ),
    "G40/loader-order": (
        "tests/test_g40_money_stays_at_the_home_garage.py",
        "store/records.py",
        source(
            "        SELECT a.id, a.external_id, a.version, a.payer_id, p.external_id, a.spots,",
            (
                "               a.monthly_price_minor, a.start_day, a.status, "
                "a.cancelled_effective_day,"
            ),
            "               a.access_entry_from, a.access_exit_by, g.external_id, a.registrar",
            "        FROM (",
            "            SELECT DISTINCT ON (external_id) *",
            "            FROM agreements",
            "            ORDER BY external_id, version DESC",
            "        ) a",
            "        JOIN payers p ON p.id = a.payer_id",
            "        JOIN garages g ON g.id = a.garage_id",
            "        WHERE a.garage_id = %s AND (%s::uuid IS NULL OR a.payer_id = %s::uuid)",
            "        ORDER BY a.external_id",
        ),
        source(
            "        SELECT DISTINCT ON (a.external_id)  -- PLANTED: garage filter first",
            "               a.id, a.external_id, a.version, a.payer_id, p.external_id, a.spots,",
            (
                "               a.monthly_price_minor, a.start_day, a.status, "
                "a.cancelled_effective_day,"
            ),
            "               a.access_entry_from, a.access_exit_by, g.external_id, a.registrar",
            "        FROM agreements a",
            "        JOIN payers p ON p.id = a.payer_id",
            "        JOIN garages g ON g.id = a.garage_id",
            "        WHERE a.garage_id = %s AND (%s::uuid IS NULL OR a.payer_id = %s::uuid)",
            "        ORDER BY a.external_id, a.version DESC",
        ),
        "THE L3'S F1 PLANTED BACK: the money loader filters on the garage and then "
        "takes the latest survivor, so at a garage an agreement's newer version has "
        "LEFT the older version still homed there is returned, and the run invoices "
        "one agreement at two garages",
    ),
    "G40/payers-order": (
        "tests/test_g40_money_stays_at_the_home_garage.py",
        "store/records.py",
        source(
            '        "SELECT DISTINCT p.id, p.external_id FROM ("',
            (
                '        "  SELECT DISTINCT ON (external_id) * FROM agreements '
                'ORDER BY external_id, version DESC"'
            ),
            '        ") a JOIN payers p ON p.id = a.payer_id "',
        ),
        source(
            (
                '        "SELECT DISTINCT p.id, p.external_id FROM agreements a JOIN payers p ON '
                'p.id = a.payer_id "  # PLANTED: every version'
            ),
        ),
        "the payer read judges every version rather than the latest, so a run at the "
        "OLD home of a moved agreement finds its payer there on the version that used "
        "to be, loads no agreement, and reports NOTHING_BILLABLE for a payer who has "
        "nothing billed there -- the report no longer says what is true",
    ),
    "G40/gate-by-garage": (
        "tests/test_g40_money_stays_at_the_home_garage.py",
        "charging.py",
        (
            "                    item.agreement for item in load_agreements_on_invoice(cursor, "
            "invoice_uuid)"
        ),
        source(
            "                    item.agreement  # PLANTED: the garage-keyed read",
            (
                '                    for item in __import__("monthly_billing.store.records", '
                'fromlist=["x"]).load_agreements_at_garage(cursor, _garage_uuid)'
            ),
            (
                "                    if item.agreement.id in {line.agreement_id for line in "
                "invoice.lines}"
            ),
        ),
        "the charge gate reads the agreements homed at the INVOICE'S garage: an "
        "invoice issued before the agreement moved its home finds nothing there and "
        "cannot be charged, though the payer agreed to be charged for it",
    ),
    "G40/gate-eager-default": (
        "tests/test_g40_money_stays_at_the_home_garage.py",
        "charging.py",
        source(
            "                if not agreements:",
            "                    raise Refused(",
        ),
        source(
            "                if not agreements and False:  # PLANTED: the empty gate falls through",
            "                    raise Refused(",
        ),
        "an invoice whose lines name no loadable agreement reaches the eager "
        "``agreements[0]`` default and raises IndexError under the invoice lock "
        "instead of refusing by name -- a crash where a refusal was promised",
    ),
    "G40/gate-by-line-version": (
        "tests/test_g40_money_stays_at_the_home_garage.py",
        "store/records.py",
        source(
            "            WHERE external_id IN (",
            "                SELECT named.external_id",
            "                FROM invoice_lines l",
            "                JOIN agreements named ON named.id = l.agreement_id",
            "                WHERE l.invoice_id = %s",
            "            )",
        ),
        source(
            (
                "            WHERE id IN (SELECT agreement_id FROM invoice_lines WHERE "
                "invoice_id = %s)"
            ),
            "              -- PLANTED: the version row the line names, not the identity's latest",
        ),
        "the charge gate reads the VERSION ROW each line names -- the one that PRICED "
        "it -- so a mandate the payer agreed to in a later version is never seen and "
        "the invoice can never be charged, and a mandate withdrawn since is charged "
        "anyway",
    ),
    "G41/orphan-unnamed": (
        "tests/test_g41_migration_0004_backfills_the_covered_set.py",
        "migrations/0004_agreement_garages.sql",
        source(
            "  IF FOUND THEN",
            "    RAISE EXCEPTION",
        ),
        source(
            "  IF FOUND AND FALSE THEN  -- PLANTED: the orphan is not refused by name",
            "    RAISE EXCEPTION",
        ),
        "a version whose home cannot be placed is no longer refused by the "
        "agreement's name; the insert then fails on the foreign key, by constraint "
        "name, and the operator reading the failure has no row to fix",
    ),
    "G41/backfill-narrowed": (
        "tests/test_g41_migration_0004_backfills_the_covered_set.py",
        "migrations/0004_agreement_garages.sql",
        "  SELECT a.tenant_id, a.id, a.garage_id FROM agreements a;",
        (
            "  SELECT a.tenant_id, a.id, a.garage_id FROM agreements a WHERE a.version = 1;  -- "
            "PLANTED"
        ),
        "the backfill places a covered set for version-1 rows only; the migration's "
        "own count assertion fires on a seeded cluster and the seeded test goes red "
        "on it -- and a plant that also removed the assertion would leave every later "
        "version covering nothing, which the pair-wise comparison catches",
    ),
    # -- M4: the registration door and the single-writer switch --
    "G42/switch": (
        "tests/test_g42_the_registration_door_and_the_single_writer.py",
        "store/records.py",
        "    if agreement.registrar is Registrar.OUTSIDE:",
        "    if False:  # PLANTED: every agreement's version walks the writer, whoever owns it",
        "THE SWITCH PLANTED AWAY: storing a version of an outside registrar's agreement "
        "hands its empty list to the version path's writer -- which now refuses it by "
        "name, so the version cannot be stored at all: the switch is where the store "
        "decides not to write, and the writer's own check is the backstop behind it",
    ),
    "G42/door-any-registrar": (
        "tests/test_g42_the_registration_door_and_the_single_writer.py",
        "store/records.py",
        source("    if registrar is expected:", "        return"),
        source("    if True:  # PLANTED: any registrar, at every writer", "        return"),
        "THE ONE CHECK PLANTED AWAY: both halves of the door accept an agreement whose "
        "registrations this module writes, AND the version path's writer accepts an "
        "outside registrar's agreement -- one plant, and every writer stops asking "
        "who owns the rows; a control that reddened only one of them would be two "
        "implementations again",
    ),
    "G42/list-accepted": (
        "tests/test_g42_the_registration_door_and_the_single_writer.py",
        "agreement.py",
        "        elif self.registrar is Registrar.OUTSIDE:",
        "        elif False:  # PLANTED: an outside registrar's document may carry a list",
        "an outside registrar's document carrying a vehicle list is accepted, so the "
        "document is a second writer's word on rows it does not own -- and, with the "
        "switch, a list nothing registers",
    ),
    "G42/empty-list-refused": (
        "tests/test_g42_the_registration_door_and_the_single_writer.py",
        "agreement.py",
        (
            "            if not (isinstance(self.vehicles, tuple) and self.registrar is "
            "Registrar.OUTSIDE):"
        ),
        "            if True:  # PLANTED: no mode makes an empty list legal",
        "the empty-vehicle refusal stops reading the mode, so an outside registrar's "
        "agreement cannot be written with no vehicles and cannot be loaded back once "
        "stored: the round trip refuses at both ends",
    ),
    "G42/registrar-not-read-back": (
        "tests/test_g42_the_registration_door_and_the_single_writer.py",
        "store/records.py",
        "                    registrar=Registrar(registrar),",
        "                    # PLANTED: the row's registrar is not read; the default stands",
        "the hydration site constructs every stored agreement with the default "
        "registrar whatever the row says, so an outside registrar's version -- stored "
        "with no vehicles, legally -- refuses to load back through every loader, and "
        "its row says outside to nobody",
    ),
    "G42/document-key-unknown": (
        "tests/test_g42_the_registration_door_and_the_single_writer.py",
        "agreement.py",
        source(
            '        "additional_fees",',
            '        "registrar",',
            "    }",
        ),
        source(
            '        "additional_fees",',
            "        # PLANTED: the registrar cannot be stated on a document",
            "    }",
        ),
        "a document that states its registrar is refused as carrying an unknown key, "
        "so the mode can be stated on rows but on no document -- the operator's file "
        "cannot say what the store will hold",
    ),
    "G42/answer-raw": (
        "tests/test_g42_the_registration_door_and_the_single_writer.py",
        "store/records.py",
        "    return _stored_forms((stored, listed) for stored, _at, listed, _held in plan)",
        source(
            "    return _stored_forms(  # PLANTED: the answer echoes what was handed in",
            "        (stored, {identity}) for stored, _at, listed, _held in plan",
            "    )",
        ),
        "the door answers with the identity as the caller spelt it rather than as "
        "each garage stored it, so the registrar on the other side cannot see that "
        "the folded garage joined two of its cars or the exact one split one -- the "
        "module is opaque again",
    ),
    "G42/release-home-only": (
        "tests/test_g42_the_registration_door_and_the_single_writer.py",
        "store/records.py",
        "    for stored, listed in forms:",
        "    for stored, listed in forms[:1]:  # PLANTED: released at the home only",
        "the release half of the door deletes the row at the home garage alone, so a "
        "car the outside registrar took off its agreement is still registered -- and "
        "still another agreement's refusal -- at every other garage the agreement "
        "covers",
    ),
    "G42/door-refusals-skipped": (
        "tests/test_g42_the_registration_door_and_the_single_writer.py",
        "store/records.py",
        "    plan = _plan_registrations(cursor, covered, agreement_id, (identity,), now)",
        source(
            "    plan = [  # PLANTED: no refusal walk; straight to the writes",
            "        (s, now if now is not None else datetime.now(zone(s.garage.timezone)),",
            "         {s.garage.normalise_identity(identity)}, {})",
            "        for s in covered",
            "    ]",
        ),
        "the door skips the refusal walk and writes at once: a car another agreement "
        "holds at a covered garage is refused by the UNIQUE at that garage -- named "
        "as a race, not as the holder -- after the home's row has already landed in "
        "the caller's transaction",
    ),
    "G42/handover": (
        "tests/test_g42_the_registration_door_and_the_single_writer.py",
        "store/records.py",
        "        if frees_on is None or frees_on > today:",
        "        if True:  # PLANTED: a cancelled holder never frees the vehicle",
        "a cancelled holder's row never passes to the outside registrar's agreement, "
        "on its day or ever: the car that moved from an agreement this module writes "
        "to one an outside registrar writes is refused at the door forever, naming a "
        "holder that has already let it go",
    ),
    "G43/default-outside": (
        "tests/test_g43_migration_0005_states_the_registrar.py",
        "migrations/0005_agreements_registrar.sql",
        "  ADD COLUMN registrar text NOT NULL DEFAULT 'this_module'",
        "  ADD COLUMN registrar text NOT NULL DEFAULT 'outside'  -- PLANTED",
        "every existing version is backfilled as an OUTSIDE registrar's -- an "
        "agreement whose registrations nothing writes; the migration's own count "
        "assertion fires on a seeded cluster and the seeded test goes red on it",
    ),
    "G43/count-removed": (
        "tests/test_g43_migration_0005_states_the_registrar.py",
        "migrations/0005_agreements_registrar.sql",
        "  IF stated <> versions THEN",
        "  IF FALSE THEN  -- PLANTED: the backfill is not counted",
        "the migration no longer asserts the stated rows against the version rows, "
        "so a wrong default commits silently -- the test that applies a copy with the "
        "default flipped and requires the count to fail it goes red",
    ),
    "G43/repair-removed": (
        "tests/test_g43_migration_0005_states_the_registrar.py",
        "migrations/0005_agreements_registrar.sql",
        source(
            "FROM agreements a",
            "WHERE NOT EXISTS (",
        ),
        source(
            "FROM agreements a",
            "WHERE false AND NOT EXISTS (  -- PLANTED: 0004's missing rows stay missing",
        ),
        "the forward repair places nothing: a database where 0004 ran blind keeps its "
        "versions without a home row, and the module cannot load one of them -- the "
        "test that seeds that database and counts the rows 0005 placed goes red",
    ),
    "G43/repair-widened": (
        "tests/test_g43_migration_0005_states_the_registrar.py",
        "migrations/0005_agreements_registrar.sql",
        source(
            "FROM agreements a",
            "WHERE NOT EXISTS (",
        ),
        source(
            "FROM agreements a",
            "WHERE true OR NOT EXISTS (  -- PLANTED: every version, present or not",
        ),
        "the repair places a home row for EVERY version, the ones 0004 already placed "
        "included, so on a database that needed nothing the unique constraint fails the "
        "whole file -- the tests that require zero rows placed there go red",
    ),
    "G43/blind-owner-allowed": (
        "tests/test_g43_migration_0005_states_the_registrar.py",
        "migrations/0005_agreements_registrar.sql",
        "  IF NOT coalesce(sees_every_row, false) THEN",
        "  IF false THEN  -- PLANTED: any role may run this, blind or not",
        "THE L3's F-A PLANTED BACK: an owner that is neither superuser nor BYPASSRLS "
        "reads zero version rows, the count check compares 0 with 0, and the flipped "
        "default APPLIES with every version reading 'outside' on disk -- the test that "
        "runs the file as that role and requires the refusal by name goes red",
    ),
    "G42/registrar-change-allowed": (
        "tests/test_g42_the_registration_door_and_the_single_writer.py",
        "store/records.py",
        "        if registrar_already is not agreement.registrar:",
        "        if False:  # PLANTED: the register may change hands",
        "F3 PLANTED BACK: a version naming the other registrar is stored, and nothing "
        "says what becomes of the rows the first writer wrote -- an operation nobody "
        "designed, accepted silently",
    ),
    "G42/dropped-garage-kept-under-outside": (
        "tests/test_g42_the_registration_door_and_the_single_writer.py",
        "store/records.py",
        source(
            "        _release_at_dropped_garages(cursor, agreement.id, covered)",
            "    else:",
        ),
        source(
            "        pass  # PLANTED: an outside registrar's rows stay at a dropped garage",
            "    else:",
        ),
        "F2 PLANTED BACK: the version that drops a garage leaves the outside registrar's "
        "rows there, where the door -- which fans out over the CURRENT covered set -- can "
        "never reach them again",
    ),
    "G42/release-no-row-silent": (
        "tests/test_g42_the_registration_door_and_the_single_writer.py",
        "store/records.py",
        source(
            "    if not released:",
            "        raise Refused(",
            "            REFUSAL_VEHICLE_NOT_REGISTERED,",
        ),
        source(
            "    if False:  # PLANTED: a release that found no row is reported as done",
            "        raise Refused(",
            "            REFUSAL_VEHICLE_NOT_REGISTERED,",
        ),
        "F4 PLANTED BACK: releasing an identity the agreement holds at no covered garage "
        "answers with an empty tuple and exit 0, and the divergence between the two "
        "registers that the single-writer rule exists to surface goes unreported",
    ),
    "G18/door-commands": (
        "tests/test_g42_the_registration_door_and_the_single_writer.py",
        "cli.py",
        "    except ValueError as bad:",
        source(
            "    except ValueError as bad:  # PLANTED: the sentence is not rendered",
            "        raise",
        ),
        "the two door commands show a traceback for a vehicle identity that normalises "
        "to nothing under the garage's rule, instead of the refusal sentence and exit 2 "
        "every other refusal on the command line gets",
    ),
    "G44/reads-the-list": (
        "tests/test_g44_the_barrier_reads_the_register.py",
        "entitlement.py",
        "    if agreement.registrar is not Registrar.OUTSIDE:",
        "    if True:  # PLANTED: B1 -- the version's list, whoever writes the rows",
        "THE L3's B1 PLANTED BACK: both coverage doors read the version's own vehicle "
        "list, which an outside registrar's agreement leaves empty by rule, so a car the "
        "door registered reads NO_AGREEMENT at every garage with the stranger's exact "
        "reason string -- the money state never reaches a connected monthly",
    ),
    "G44/refusal-skipped": (
        "tests/test_g44_the_barrier_reads_the_register.py",
        "entitlement.py",
        source(
            "    if registrations is None:",
            "        raise _Refused(",
        ),
        source(
            "    if registrations is None:",
            "        return ()  # PLANTED: an unasked question is answered 'no agreement'",
            "    if False:",
            "        raise _Refused(",
        ),
        "the pure call handed an outside registrar's agreement WITHOUT the register "
        "answers 'no agreement' for every car instead of refusing by name -- the "
        "fail-open shape B1 was",
    ),
    "G44/every-car-covered": (
        "tests/test_g44_the_barrier_reads_the_register.py",
        "entitlement.py",
        source(
            "        and any(",
            "            garage.identities_match(v, vehicle_identity)",
            "            for v in register_of(a, garage, registrations)",
            "        )",
        ),
        source(
            "        and (a.registrar is Registrar.OUTSIDE or any(  # PLANTED: every car is theirs",
            "            garage.identities_match(v, vehicle_identity)",
            "            for v in register_of(a, garage, registrations)",
            "        ))",
        ),
        "THE OVER-REACH: an outside registrar's agreement covers every car presented at "
        "a garage it covers, registered or not -- a fix that made every car covered "
        "would be worse than the defect, and the no-row control is what catches it",
    ),
    "G44/store-passes-nothing": (
        "tests/test_g44_the_barrier_reads_the_register.py",
        "entitlement_store.py",
        (
            "        registrations: dict[str, tuple[str, ...]] = "
            "{holder: (normalised,)} if holder else {}"
        ),
        "        registrations = None  # PLANTED: the store asks without its own rows",
        "the store-backed door hands the pure call no register, so a car the door "
        "registered is REFUSED by name at the barrier rather than answered -- fail "
        "closed, and still not the answer the lane needs",
    ),
    "G45/own-version-rule": (
        "tests/test_g45_the_register_can_be_read.py",
        "store/records.py",
        source(
            "    latest = _latest_version_of(cursor, tenant_id, agreement_id)",
            "    _registrar_must_be(agreement_id, Registrar(latest.registrar), Registrar.OUTSIDE)",
        ),
        source(
            "    cursor.execute(  # PLANTED: the door picks its version on its own",
            '        "SELECT id, registrar FROM agreements WHERE external_id = %s "',
            '        "ORDER BY version DESC LIMIT 1",',
            "        (agreement_id,),",
            "    )",
            "    row = cursor.fetchone()",
            "    if row is None:",
            '        raise AgreementNotFound(f"no agreement {agreement_id!r}")',
            "    latest = _LatestVersion(as_uuid(row[0]), 0, row[1], \"\", None, as_uuid(row[0]))",
            "    _registrar_must_be(agreement_id, Registrar(latest.registrar), Registrar.OUTSIDE)",
        ),
        "the door and the read each pick 'the latest version' with a SELECT of their "
        "own -- two rules that agree today and can drift apart the day one is edited, "
        "which is R5's whole point",
    ),
    "G45/read-writes": (
        "tests/test_g45_the_register_can_be_read.py",
        "store/records.py",
        "    home_garage = _garage_external_id(cursor, latest.home_uuid)",
        source(
            "    cursor.execute(  # PLANTED: a read that writes -- no value changes, xmin does",
            '        "UPDATE vehicle_registrations SET registered_at = registered_at "',
            '        "WHERE agreement_external_id = %s", (agreement_id,),',
            "    )",
            "    home_garage = _garage_external_id(cursor, latest.home_uuid)",
        ),
        "the read touches a row on its way through: no value changes, so a count or "
        "a digest of the values would stay green -- the xmin in the digest and the "
        "cluster's tuple counters are what see it",
    ),
    "G45/tenant-predicate": (
        "tests/test_g45_the_register_can_be_read.py",
        "store/records.py",
        '        "FROM agreements WHERE tenant_id = %s AND external_id = %s "',
        '        "FROM agreements WHERE %s::uuid IS NOT NULL AND external_id = %s "  # PLANTED',
        "the version helper scopes by the row policy alone: with the policy off, "
        "LIMIT 1 hands the read another tenant's higher version of the same id, "
        "and the read shows that tenant's register under this tenant's name",
    ),
    "G45/rows-tenant-predicate": (
        "tests/test_g45_the_register_can_be_read.py",
        "store/records.py",
        '        "WHERE r.tenant_id = %s AND r.agreement_external_id = %s",',
        '        "WHERE %s::uuid IS NOT NULL AND r.agreement_external_id = %s",  # PLANTED',
        "the registrations are read by the agreement id alone, which is text shared "
        "across tenants: with the policy off, another tenant's cars appear on this "
        "tenant's register",
    ),
    "G45/outside-rows-hidden": (
        "tests/test_g45_the_register_can_be_read.py",
        "store/records.py",
        "    not_covered = tuple(sorted({e.garage_id for e in registrations} - set(covered)))",
        source(
            "    registrations = tuple(  # PLANTED: only rows inside the covered set are shown",
            "        e for e in registrations if e.garage_id in covered",
            "    )",
            "    not_covered = ()",
        ),
        "a row at a garage the latest version does not cover is dropped from the "
        "answer and its garage never named -- exactly the row a reconciliation "
        "exists to find, hidden by the read that exists to show it",
    ),
    "G45/through-load-garage": (
        "tests/test_g45_the_register_can_be_read.py",
        "store/records.py",
        "    covered = tuple(sorted(_covered_garage_ids(cursor, latest.uuid)))",
        source(
            "    covered = tuple(sorted(  # PLANTED: the covered set built as Garage values",
            "        load_garage(cursor, g).garage.id",
            "        for g in _covered_garage_ids(cursor, latest.uuid)",
            "    ))",
        ),
        "the read builds each covered garage through the loader, which validates "
        "the garage's options: a garage stored with an unreadable timezone makes "
        "the read refuse, and the register is hidden from the one reader that "
        "needs it",
    ),
    "G45/covered-unsorted": (
        "tests/test_g45_the_register_can_be_read.py",
        "store/records.py",
        "    covered = tuple(sorted(_covered_garage_ids(cursor, latest.uuid)))",
        "    covered = tuple(_covered_garage_ids(cursor, latest.uuid))  # PLANTED: store order",
        "the covered set is published in the order the store returns it -- the home "
        "first, then the database's collation -- which is one order on this machine "
        "and another in CI",
    ),
    "G45/rows-unsorted": (
        "tests/test_g45_the_register_can_be_read.py",
        "store/records.py",
        "            key=lambda entry: (entry.identity_normalised, entry.garage_id),",
        "            key=lambda entry: 0,  # PLANTED: the heap's order, whatever it is",
        "the registrations are published in the order they came off the heap; the "
        "fixture's premise assertion is that this is not the published order",
    ),
    "G45/tenth-key": (
        "tests/test_g45_the_register_can_be_read.py",
        "store/records.py",
        "        return document",
        source(
            '        document["monthly_price_minor"] = 0  # PLANTED: a tenth key, the price',
            "        return document",
        ),
        "the answer carries a tenth key -- the price -- and the field-set test, which "
        "derives the nine from the class and reads the document's keys, sees it",
    ),
    "G45/refusal-swallowed": (
        "tests/test_g45_the_register_can_be_read.py",
        "store/records.py",
        source(
            "    latest = _latest_version_of(cursor, tenant_id, agreement_id)",
            "    home_garage = _garage_external_id(cursor, latest.home_uuid)",
        ),
        source(
            "    try:",
            "        latest = _latest_version_of(cursor, tenant_id, agreement_id)",
            "    except AgreementNotFound:  # PLANTED: an unknown agreement is an empty register",
            '        return AgreementRegister(agreement_id, 0, "", "", None, "", (), (), ())',
            "    home_garage = _garage_external_id(cursor, latest.home_uuid)",
        ),
        "an agreement the store does not hold answers an empty register, exit 0, "
        "instead of NOT FOUND -- a typo in the id reads as 'no cars registered'",
    ),
    "G45/registrar-refused": (
        "tests/test_g45_the_register_can_be_read.py",
        "store/records.py",
        "    covered = tuple(sorted(_covered_garage_ids(cursor, latest.uuid)))",
        source(
            "    _registrar_must_be(  # PLANTED: the read refuses on the single-writer rule",
            "        agreement_id, Registrar(latest.registrar), Registrar.OUTSIDE",
            "    )",
            "    covered = tuple(sorted(_covered_garage_ids(cursor, latest.uuid)))",
        ),
        "the read refuses a self-written agreement by the door's name: the "
        "single-writer rule, which governs writes, applied to a read",
    ),
    "G45/stderr": (
        "tests/test_g45_the_register_can_be_read.py",
        "cli.py",
        "    print(json.dumps(register.as_document(), sort_keys=True, indent=2))",
        source(
            '    print(f"register of {args.agreement}", file=sys.stderr)  # PLANTED',
            "    print(json.dumps(register.as_document(), sort_keys=True, indent=2))",
        ),
        "the verb writes a line to stderr on success, so a program reading the "
        "answer sees noise where the contract says nothing",
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
