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
from plant import planted, resolve  # noqa: E402


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
