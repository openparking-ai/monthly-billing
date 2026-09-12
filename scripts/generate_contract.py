#!/usr/bin/env python3
"""Generate docs/CONTRACT.md from the registries, and check it has not drifted.

    python scripts/generate_contract.py            # write the document
    python scripts/generate_contract.py --check    # fail if it would change

**THE SECOND-MONTH EXAMPLE RUNS AGAINST A DATABASE.** The billing run, the
unpaid answer and the cheque are store-backed, so the block that shows them is
produced by running them -- against ``MONTHLY_BILLING_TEST_DSN``, whose schema
is dropped and rebuilt from ``migrations/`` exactly as the store tests do. The
generator refuses to run without it rather than rendering a block that a
machine with no database could not have produced.

**EVERY MARKED BLOCK IS DERIVED.** The guarantees come from
``tests/_guarantees.py``; the refusal codes and the not-covered reasons from
``findings.py``; the billing-day options and identity rules from the enums that
implement them; the worked example by RUNNING the module and printing what it
returned. A number or a sentence edited by hand turns ``--check`` red.

**AND GENERATION IS NOT VERIFICATION.** Moving a sentence from a document into a
template does not stop it being hand-written -- everywhere except the holes it is
still prose nobody checks. A generated block asserts only what it DERIVES from
its values. So ``tests/test_contract_is_generated.py`` plants values that
contradict the prose and requires the prose to change; anything that survives
that plant is a fixed string, and a fixed string is marked as design
documentation rather than left looking measured.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from _guarantees import GUARANTEES, guarantee_ids  # noqa: E402
from monthly_billing.agreement import (  # noqa: E402
    KNOWN_KEYS,
    FeeCadence,
    load_agreement_file,
)
from monthly_billing.billing_run import FAILED_OUTCOMES, RUN_OUTCOME_MEANS, RunOutcome  # noqa: E402
from monthly_billing.entitlement import Answer  # noqa: E402
from monthly_billing.exceptions_by_owner import (  # noqa: E402
    LANDS_A_LINE,
    NEEDS_AN_AMOUNT,
    ExceptionKind,
)
from monthly_billing.findings import (  # noqa: E402
    NOT_COVERED_MEANS,
    NOT_COVERED_REASONS,
    REFUSAL_ALREADY_REVERSED,
    REFUSAL_EXCEPTION_AMOUNT_NOT_POSITIVE,
    REFUSAL_NOTHING_OWED,
    REFUSAL_REVERSAL_REASON_MISMATCH,
    REFUSALS,
)
from monthly_billing.garage import BillingDay, IdentityRule  # noqa: E402
from monthly_billing.invoice import first_charge  # noqa: E402
from monthly_billing.payment import MAX_ATTEMPTS  # noqa: E402
from monthly_billing.payments import (  # noqa: E402
    OPERATOR_RECORDED,
    REASONS_FOR_METHOD,
    PaymentMethod,
    ReversalReason,
)

DOC = ROOT / "docs" / "CONTRACT.md"
BEGIN = "<!-- GENERATED:{name} -->"
END = "<!-- END:{name} -->"


def block_guarantees() -> str:
    rows = ["| id | what is guaranteed |", "|---|---|"]
    for gid in guarantee_ids():
        rows.append(f"| **{gid}** | {GUARANTEES[gid]} |")
    rows.append("")
    rows.append(
        f"That is {len(GUARANTEES)} guarantees. Every one of them has a fail control "
        "that has been proven to fire, and the count above is derived from the "
        "registry rather than typed here."
    )
    return "\n".join(rows)


def block_refusals() -> str:
    rows = ["| code | when, and what to do about it |", "|---|---|"]
    for code, sentence in sorted(REFUSALS.items()):
        rows.append(f"| `{code}` | {sentence} |")
    return "\n".join(rows)


def block_not_covered() -> str:
    rows = ["| reason | what the lane is told |", "|---|---|"]
    for code, sentence in sorted(NOT_COVERED_REASONS.items()):
        rows.append(f"| `{code}` | {sentence} |")
    rows.append("")
    rows.append(f"Every one of them carries this sentence: *{NOT_COVERED_MEANS}*")
    return "\n".join(rows)


def block_answer_fields() -> str:
    """Derived from the answer class, so this cannot fall behind it."""
    rows = ["| field | type |", "|---|---|"]
    for name, spec in Answer.__dataclass_fields__.items():
        rows.append(f"| `{name}` | `{spec.type}` |")
    rows.append("")
    rows.append(
        f"That is the whole answer: {len(Answer.__dataclass_fields__)} fields, none of "
        "which is money, and none of which could express a decision about a barrier."
    )
    return "\n".join(rows)


def block_options() -> str:
    lines = ["**Billing day** — one per garage, no default:", ""]
    for option in BillingDay:
        lines.append(f"- `{option.value}`")
    lines += ["", "**Identity comparison** — one per garage, no default:", ""]
    for rule in IdentityRule:
        lines.append(f"- `{rule.value}`")
    lines += ["", "**Additional fee cadence:**", ""]
    for cadence in FeeCadence:
        lines.append(f"- `{cadence.value}`")
    lines += [
        "",
        "**Owner exception kinds** — ⊙ requires a positive amount in minor units; "
        "▾ lands an `exception_adjustment` line that lowers the invoice total:",
        "",
    ]
    for kind in ExceptionKind:
        mark = (" ⊙" if kind in NEEDS_AN_AMOUNT else "") + (" ▾" if kind in LANDS_A_LINE else "")
        lines.append(f"- `{kind.value}`{mark}")
    recorded_only = sorted(k.value for k in NEEDS_AN_AMOUNT - LANDS_A_LINE)
    if recorded_only:
        lines += [
            "",
            "A kind marked ⊙ but not ▾ -- " + ", ".join(f"`{k}`" for k in recorded_only) + " -- "
            "records the owner's decision and its amount and changes NO total: the invoice "
            "stays what it was and stays paid if it was paid. The money going back is a "
            "movement, which is a collection record when collection exists, never a billing "
            "line. The direction of every amount is the kind's, never the sign's: a negative "
            f"amount is refused by name (`{REFUSAL_EXCEPTION_AMOUNT_NOT_POSITIVE}`).",
        ]
    lines += [
        "",
        "An agreement document carries exactly these keys: "
        + ", ".join(f"`{k}`" for k in sorted(KNOWN_KEYS))
        + ". Any other key is refused rather than ignored.",
        "",
        f"A charge is attempted at most {MAX_ATTEMPTS} times before the module refuses, "
        "and the count resets only when the caller reports that the payer changed "
        "payment method.",
    ]
    return "\n".join(lines)


def block_worked_example() -> str:
    """Produced by RUNNING the module. No figure here is typed."""
    from monthly_billing.cli import load_garage_file

    garage = load_garage_file(str(ROOT / "tests" / "documents" / "garage_downtown.json"))
    agreement = load_agreement_file(str(ROOT / "tests" / "documents" / "agreement_acme.json"))
    invoice = first_charge(garage, agreement)

    lines = [
        "```",
        "$ monthly-billing first-charge --garage tests/documents/garage_downtown.json \\",
        "      --agreement tests/documents/agreement_acme.json",
        invoice.rendered(),
        "```",
        "",
        f"The agreement starts {agreement.start_day} and the garage bills on "
        f"`{garage.billing_day.value}`, so the first period runs "
        f"{invoice.lines[0].period_start_day} to {invoice.lines[0].period_end_day} and "
        f"{invoice.lines[0].label.split('(')[1].rstrip(') ')} of it are billable. "
        f"The part period and the following one are separate lines, never summed.",
    ]
    return "\n".join(lines)


def block_billing_run() -> str:
    """Derived from the outcome enum and its meanings, so an outcome added
    without a sentence, or a sentence without an outcome, fails here."""
    missing = [o.value for o in RunOutcome if o not in RUN_OUTCOME_MEANS]
    extra = [o.value for o in RUN_OUTCOME_MEANS if o not in RunOutcome]
    if missing or extra:
        raise SystemExit(f"RUN_OUTCOME_MEANS disagrees with RunOutcome: {missing} {extra}")
    rows = ["| outcome | what it means |", "|---|---|"]
    for outcome in RunOutcome:
        rows.append(f"| `{outcome.value}` | {RUN_OUTCOME_MEANS[outcome]} |")
    rows.append("")
    failed = ", ".join(f"`{o.value}`" for o in RunOutcome if o in FAILED_OUTCOMES)
    rows.append(
        f"Every payer at the garage gets exactly one of these {len(RunOutcome)} outcomes, "
        f"per run. The run exits non-zero if any payer's outcome is one of {failed}, and "
        f"zero otherwise -- `{RunOutcome.ALREADY_ISSUED.value}` is an answer, not an error, "
        f"and it means exactly that a row for this garage, payer and period exists."
    )
    return "\n".join(rows)


def block_payment_methods() -> str:
    lines = ["**Payment methods** — how money is recorded as received:", ""]
    for method in PaymentMethod:
        how = (
            "recorded by the operator, one command"
            if method in OPERATOR_RECORDED
            else "written ONLY by the charge path, when the processor says SUCCESS"
        )
        lines.append(f"- `{method.value}` — {how}")
    lines += [
        "",
        f"{len(OPERATOR_RECORDED)} of the {len(PaymentMethod)} methods can be recorded by hand; "
        "the rest cannot, so a card payment nobody charged has nowhere to land.",
        "",
        "**Reversal reasons** — a payment that did not stand, as a second row:",
        "",
    ]
    fits = {
        reason: sorted(m.value for m, reasons in REASONS_FOR_METHOD.items() if reason in reasons)
        for reason in ReversalReason
    }
    for reason in ReversalReason:
        lines.append(f"- `{reason.value}` — on a {', '.join(fits[reason])} payment")
    lines += [
        "",
        f"A payment has at most one reversal (a second is `{REFUSAL_ALREADY_REVERSED}`), and "
        f"there are {len(ReversalReason)} reasons it can carry, each fitting the payment's method "
        f"and refused by name otherwise (`{REFUSAL_REVERSAL_REASON_MISMATCH}`). What happens next "
        "is the owner's decision, recorded as an exception.",
        "",
        "**A charge is for the balance.** The charge path asks the processor for the invoice's "
        "total minus its unreversed payments, never the total; a balance of nothing is "
        f"`{REFUSAL_NOTHING_OWED}` before the processor is called, so a paid invoice is never "
        "charged again and a part-paid one is charged its remainder. Every call to the "
        "processor leaves an attempt row: a raise is an `error` naming the exception, and a "
        "result the instrument guard refused inside the processor's own return is an `error` "
        "whose detail says the outcome is unknown -- a reported result is never discarded "
        "silently.",
    ]
    return "\n".join(lines)


def block_grants() -> str:
    """The application role's privileges, READ FROM THE CATALOGUE of a database
    migrated from ``migrations/``: what the grants say, not what a paragraph
    says they say. Needs ``MONTHLY_BILLING_TEST_DSN`` like the second month."""
    import os
    import sys as _sys

    _sys.path.insert(0, str(ROOT / "tests"))
    from store_harness import migrate  # noqa: E402

    dsn = os.environ.get("MONTHLY_BILLING_TEST_DSN")
    if not dsn:
        raise SystemExit(
            "the grant table is read from a migrated database's catalogue, and needs "
            "MONTHLY_BILLING_TEST_DSN. It is not rendered from memory."
        )
    owner = migrate(dsn)
    with owner.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.relname,
                   coalesce(string_agg(g.privilege_type, ', ' ORDER BY g.privilege_type), '')
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN information_schema.role_table_grants g
                   ON g.table_name = c.relname AND g.table_schema = 'public'
                  AND g.grantee = 'monthly_billing_app'
            WHERE n.nspname = 'public' AND c.relkind = 'r'
            GROUP BY c.relname ORDER BY c.relname
            """
        )
        rows = cursor.fetchall()
    owner.close()
    lines = ["| table | the application role may |", "|---|---|"]
    for name, privileges in rows:
        lines.append(f"| `{name}` | {privileges} |")
    append_only = [name for name, p in rows if p == "INSERT, SELECT"]
    no_delete = [name for name, p in rows if p == "INSERT, SELECT, UPDATE"]
    lines += [
        "",
        f"{len(rows)} tables. {len(append_only)} are append-only -- "
        + ", ".join(f"`{n}`" for n in append_only)
        + " -- and "
        + ", ".join(f"`{n}`" for n in no_delete)
        + (" keeps" if len(no_delete) == 1 else " keep")
        + " UPDATE and "
        + ("loses" if len(no_delete) == 1 else "lose")
        + " DELETE"
        + ": `paid_at` is derived by the application after every payment, reversal and "
        "adjustment, and that is the one column it writes. The rest carry the DML the "
        "application needs to store documents.",
    ]
    return "\n".join(lines)


def block_second_month() -> str:
    """Produced by RUNNING the store: the billing run, the lane's question past
    grace, the cheque, and the lane's question again. No figure here is typed."""
    import os
    import sys as _sys
    from datetime import date, datetime, timedelta
    from zoneinfo import ZoneInfo

    _sys.path.insert(0, str(ROOT / "tests"))
    from monthly_billing.billing_run import run_billing
    from monthly_billing.cli import load_garage_file
    from monthly_billing.entitlement_store import covered_from_store
    from monthly_billing.payments import PaymentMethod as Method
    from monthly_billing.payments import record_payment
    from store_harness import app_connection, migrate, new_tenant, seed  # noqa: E402

    dsn = os.environ.get("MONTHLY_BILLING_TEST_DSN")
    if not dsn:
        raise SystemExit(
            "the second-month example is produced by running the store, and needs "
            "MONTHLY_BILLING_TEST_DSN. It is not rendered from memory."
        )
    garage = load_garage_file(str(ROOT / "tests" / "documents" / "garage_downtown.json"))
    agreement = load_agreement_file(str(ROOT / "tests" / "documents" / "agreement_acme.json"))
    tz = ZoneInfo(garage.timezone)
    at = lambda d, h=9: datetime(d.year, d.month, d.day, h, 0, tzinfo=tz)  # noqa: E731

    owner = migrate(dsn)
    app = app_connection(dsn)
    tenant_id = new_tenant(owner, "worked-example")
    seed(app, tenant_id, garage, (agreement,))
    vehicle = agreement.vehicles[0]

    # The run that issues the third period -- the first charge owned the first two.
    third = date(2026, 5, 1)
    report = run_billing(app, tenant_id, garage.id, third, now=at(date(2026, 4, 30), 0))
    (line,) = report.lines
    grace_end = report.period.start_day + timedelta(days=garage.payment_grace_days)
    past_day = grace_end + timedelta(days=1)
    inside = covered_from_store(app, tenant_id, garage.id, vehicle, at(grace_end))
    past = covered_from_store(app, tenant_id, garage.id, vehicle, at(past_day))
    _, paid = record_payment(
        app, tenant_id, line.reference, Method.CHEQUE, line.total_minor, at(date(2026, 5, 8), 10),
        recorded_by="the operator", processor_reference="cheque 1043",
    )
    again = covered_from_store(app, tenant_id, garage.id, vehicle, at(date(2026, 5, 9)))
    app.close()
    owner.close()

    fmt = lambda d: d.isoformat()  # noqa: E731
    lines = [
        "```",
        f"$ monthly-billing run --garage {garage.id} --period-containing {fmt(third)}",
        report.rendered(),
        "",
        f"$ monthly-billing covered-in-store --garage {garage.id} --vehicle \"{vehicle}\" "
        f"--at {fmt(grace_end)}T09:00 (local)",
        "COVERED" if inside.covered else "NOT COVERED",
        "",
        f"$ monthly-billing covered-in-store --garage {garage.id} --vehicle \"{vehicle}\" "
        f"--at {fmt(past_day)}T09:00 (local)",
        "COVERED" if past.covered else "NOT COVERED",
        f"  {past.reason}",
        "",
        f"$ monthly-billing record-payment --invoice {line.reference} --method cheque "
        f"--amount-minor {line.total_minor} --received-at 2026-05-08T10:00 (local)",
        f"  invoice {'PAID' if paid.paid else 'UNPAID'} at {paid.paid_at.isoformat()}",
        "",
        f"$ monthly-billing covered-in-store --garage {garage.id} --vehicle \"{vehicle}\" "
        f"--at 2026-05-09T09:00 (local)",
        "COVERED" if again.covered else "NOT COVERED",
        "```",
        "",
        f"The run {_run_word(line)} {line.reference} for the period "
        f"{report.period.start_day} to {report.period.end_day}, due on its first day, "
        f"{line.total_minor} minor units. The garage states a grace of "
        f"{garage.payment_grace_days} days, so the vehicle {_coverage_word(inside)} on "
        f"{grace_end} and {_coverage_word(past)} on {past_day}. A cheque recorded on "
        f"2026-05-08 for the full amount {_paid_word(paid)}, and the vehicle "
        f"{_coverage_word(again)} on 2026-05-09. The lane was told nothing about money at "
        f"any point.",
    ]
    return "\n".join(lines)


# Every sentence in the second-month prose is rendered from the answer it
# describes, in words that differ between the two outcomes -- a value that
# contradicts the sentence CHANGES the sentence (§6: a moving number is not a
# changed assertion). The tests plant each of these the other way.


def _run_word(line) -> str:
    return "issued" if line.outcome.value == "issued" else f"did NOT issue ({line.outcome.value})"


def _coverage_word(answer) -> str:
    if answer.covered:
        return "is still covered"
    return f"is NOT covered (`{answer.reason_code}`)"


def _paid_word(paid) -> str:
    return "pays it from that instant" if paid.paid else "leaves it UNPAID"


BLOCKS = {
    "guarantees": block_guarantees,
    "refusals": block_refusals,
    "not-covered": block_not_covered,
    "answer-fields": block_answer_fields,
    "options": block_options,
    "worked-example": block_worked_example,
    "billing-run": block_billing_run,
    "payment-methods": block_payment_methods,
    "second-month": block_second_month,
    "grants": block_grants,
}


def render(template: str) -> str:
    out = template
    for name, builder in BLOCKS.items():
        begin, end = BEGIN.format(name=name), END.format(name=name)
        if begin not in out or end not in out:
            raise SystemExit(f"docs/CONTRACT.md has no {begin} ... {end} block")
        head, rest = out.split(begin, 1)
        _stale, tail = rest.split(end, 1)
        out = f"{head}{begin}\n{builder()}\n{end}{tail}"
    return out


def main(argv: list[str]) -> int:
    current = DOC.read_text()
    generated = render(current)
    if "--check" in argv:
        if current != generated:
            print(
                "docs/CONTRACT.md does not match its generator. A number or a "
                "sentence inside a generated block was edited by hand, or the "
                "registry it comes from moved. Run this script with no arguments."
            )
            return 1
        print("docs/CONTRACT.md is the generated one.")
        return 0
    DOC.write_text(generated)
    print(f"wrote {DOC.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
