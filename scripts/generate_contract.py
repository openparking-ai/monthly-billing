#!/usr/bin/env python3
"""Generate docs/CONTRACT.md from the registries, and check it has not drifted.

    python scripts/generate_contract.py            # write the document
    python scripts/generate_contract.py --check    # fail if it would change

**WHAT ``--check`` WRITES.** Nothing to the document: it renders in memory and
compares (measured: the file's hash is identical before and after a ``--check``
that exits 1 under a plant). It DOES write the database named by
``MONTHLY_BILLING_TEST_DSN`` -- the second-month block and the grant table are
produced by dropping and rebuilding that schema -- so point it at a database the
harness may destroy, never a shared one. CI's independent check is
``git diff --exit-code docs/CONTRACT.md`` after a plain run.

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
    Agreement,
    FeeCadence,
    Registrar,
    load_agreement_file,
)
from monthly_billing.billing_run import FAILED_OUTCOMES, RUN_OUTCOME_MEANS, RunOutcome  # noqa: E402
from monthly_billing.charging import LATE  # noqa: E402
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
    REFUSAL_ATTEMPT_ALREADY_RESOLVED,
    REFUSAL_ATTEMPT_UNRESOLVED,
    REFUSAL_EXCEPTION_AMOUNT_NOT_POSITIVE,
    REFUSAL_GARAGE_NOT_COVERED,
    REFUSAL_NOTHING_OWED,
    REFUSAL_REGISTRAR_CHANGED,
    REFUSAL_REGISTRAR_IS_OUTSIDE,
    REFUSAL_REGISTRAR_IS_THIS_MODULE,
    REFUSAL_REGISTRATIONS_NOT_GIVEN,
    REFUSAL_REVERSAL_REASON_MISMATCH,
    REFUSAL_VEHICLE_NOT_REGISTERED,
    REFUSAL_VEHICLE_ON_TWO_AGREEMENTS,
    REFUSALS,
    UNPAID_IS_THE_PAYERS,
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
from monthly_billing.store.postgres import events_taking_the_lock  # noqa: E402

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
    rows.append("")
    rows.append(f"`UNPAID_PAST_GRACE` is judged per payer, per home garage. {UNPAID_IS_THE_PAYERS}")
    return "\n".join(rows)


def block_answer_fields() -> str:
    """Derived from the answer class, so this cannot fall behind it."""
    rows = ["| field | type |", "|---|---|"]
    for name, spec in Answer.__dataclass_fields__.items():
        rows.append(f"| `{name}` | `{spec.type}` |")
    rows.append("")
    rows.append(
        f"That is the whole answer: {len(Answer.__dataclass_fields__)} fields, none of "
        "which is money, and none of which could express a decision about a barrier. "
        "Beside covered and not-covered there is one third result, a REFUSAL: handed two "
        "agreement identities that list the same vehicle, the pure call refuses by name "
        f"(`{REFUSAL_VEHICLE_ON_TWO_AGREEMENTS}`) rather than picking one. A refusal is "
        "not an answer of no; the not-covered sentence does not travel on it. The store "
        "can no longer produce that state and the command line takes one agreement "
        "document, so this result reaches a library caller only."
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
    lines += _registrar_lines()
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


def _registrar_lines() -> list[str]:
    """Who writes an agreement's registrations, DERIVED: the members from the
    enum, the default from the dataclass field, and the sentence about the
    vehicle list from which member is the default -- a default moved to the
    other member changes the sentence rather than leaving it standing."""
    default = Agreement.__dataclass_fields__["registrar"].default
    lines = [
        "",
        "**Registrar** — who writes an agreement's registrations, stated on the "
        "document, defaulting to this module:",
        "",
    ]
    for registrar in Registrar:
        lines.append(f"- `{registrar.value}`" + (" (the default)" if registrar is default else ""))
    outside = Registrar.OUTSIDE
    module = Registrar.THIS_MODULE
    lines += [
        "",
        f"Under `{module.value}` the version's own vehicle list is the register -- it is "
        "required, and an agreement listing no vehicle is refused by name because it "
        f"covers nothing. Under `{outside.value}` the list is refused when present and "
        "legal when empty, on the way in and on the way back: the outside registrar "
        "registers and releases one vehicle identity at a time through the registration "
        "door (`register-vehicle`, `release-vehicle`), which fans out over the covered "
        "set under each garage's own identity rule, refuses at every covered garage "
        "before it writes anywhere, and answers with the identity AS STORED per garage. "
        "A release may instead name ONE covered garage (`release-vehicle --garage G`, "
        "the same parameter on the library call) and reaches that garage alone, under "
        "its rule, answering the one line -- for the stale row two garages that fold a "
        "plate differently can hold under the live car's text, which the fan-out cannot "
        "take without the live row; a garage the tenant does not hold is NOT FOUND, and "
        "one the latest version does not cover is refused by name "
        f"(`{REFUSAL_GARAGE_NOT_COVERED}`), each before any write. "
        "Both halves of the door refuse by name an agreement whose registrations this "
        f"module writes (`{REFUSAL_REGISTRAR_IS_THIS_MODULE}`), and the version path's "
        "writer refuses by name an agreement an outside registrar writes "
        f"(`{REFUSAL_REGISTRAR_IS_OUTSIDE}`) -- one check, reached from every writer. "
        f"A document that says nothing is `{default.value}`; the mode is never inferred "
        "from the list, and it never changes between versions "
        f"(`{REFUSAL_REGISTRAR_CHANGED}`).",
        "",
        "**What the barrier reads** is the agreement's REGISTER, decided in one place "
        f"for both coverage doors: under `{module.value}` the version's own vehicle "
        f"list; under `{outside.value}` the registration rows the door wrote. The "
        "store-backed call supplies those rows to the pure call as a stated "
        "parameter (`registrations`), empty or not; the pure call, which has no "
        "database, refuses by name an outside registrar's agreement handed in "
        f"without them (`{REFUSAL_REGISTRATIONS_NOT_GIVEN}`) rather than answering "
        "'no agreement' for a car it could not look up. A car with no row is not "
        "covered; a self-written agreement answers exactly as it did before the "
        "parameter existed. Storing a version that drops a covered garage releases "
        "the agreement's rows there under either registrar (the covered set is the "
        "version's own fact), and releasing an identity that holds no row at any "
        "garage the release reached -- every covered garage, or the one it named -- "
        f"is refused by name (`{REFUSAL_VEHICLE_NOT_REGISTERED}`).",
    ]
    return lines


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
        "**A charge is for the balance, and it is a reservation first.** The charge path "
        "reads the invoice's total minus its unreversed payments under the invoice lock, "
        "never the total; a balance of nothing is "
        f"`{REFUSAL_NOTHING_OWED}` before the processor is called and before any row is "
        "written, so a paid invoice is never charged again and a part-paid one is charged "
        "its remainder. The attempt row -- the RESERVATION, carrying the amount -- is "
        "committed BEFORE the processor is asked; the processor is handed the attempt id "
        "as its idempotency key; the OUTCOME row and, on success, the card payment for the "
        "reserved amount land in one transaction afterwards.",
        "",
        "**What the module does not know is never written as an outcome.** Three things "
        "the module can know after asking the processor: a result -- `success`, `decline` "
        "or `error`, as the processor said -- or NOTHING: the processor raised (before or "
        "after the request left; the module cannot tell which), or its answer could not "
        "be received (the instrument guard refused what it returned). Nothing is an "
        "`unknown` row saying what the module saw -- never an outcome, never counted "
        "toward the three attempts -- and the attempt stays PENDING. A pending attempt is "
        "never charged past: while its request may be in flight (its last row is the "
        "reservation or an `ask`) the next charge is "
        f"`{REFUSAL_ATTEMPT_UNRESOLVED}`, naming the attempt id, and `pending-attempts` "
        "lists it; when the module has said it does not know (its last row is `unknown`) "
        "the next charge writes an `ask` row and asks the processor again under the SAME "
        "idempotency key for the amount RESERVED -- not a new charge, so nothing new is "
        "reserved and the refusals are not re-run. There is no cap on re-asks: a processor "
        "that never answers grows the `unknown` log for as long as the scheduler calls, "
        "and one key is one charge at most -- a real processor honours the idempotency "
        "key, so a repeated request charges once. An operator records what the processor "
        "said once (`resolve-attempt`); a second answer for the same attempt is "
        f"`{REFUSAL_ATTEMPT_ALREADY_RESOLVED}`, from the check under the lock and from "
        "the database's one-outcome-per-attempt index caught by its name. The processor's "
        "own answer to an ask that was at the processor when the operator resolved is not "
        "a second answer: it is recorded beside the operator's resolution as a "
        f"`{LATE}` row carrying what the processor said, never dropped, and a late "
        "success is honoured -- the processor's word on money outranks the operator's "
        "typed one. A late SUCCESS the operator did not record writes the card payment "
        "for the amount RESERVED in the same transaction (the money moved; the invoice "
        "is paid and no fresh key is minted); a late SUCCESS on a recorded success writes "
        "no second payment; a late decline or error is the row only -- the operator's "
        "SUCCESS and its payment stand, and the contradiction is in the log for the "
        "operator's reversal. A late row is not an outcome and the attempt is not "
        "pending: `pending-attempts` does not list it. A pending "
        "attempt comes only from the library's `attempt_charge`; nothing on the command "
        "line charges.",
    ]
    return "\n".join(lines)


def block_serialised() -> str:
    """Which money events take the invoice lock, READ FROM THE SOURCE. The
    sentence has two wordings and the value decides which -- a control plants
    the lock away from one event and requires the other wording."""
    taken = events_taking_the_lock()
    names = ", ".join(f"`{n}`" for n in taken)
    missing = [n for n, ok in taken.items() if not ok]
    if not missing:
        verdict = (
            f"ALL {len(taken)} of them take the invoice row lock first, read from their "
            "source: two events on one invoice at once are serialised, and the second "
            "derives `paid_at` from the first's committed rows."
        )
        verdict += _one_place()
    else:
        verdict = (
            f"ONLY {len(taken) - len(missing)} of {len(taken)} take the invoice row lock; "
            + ", ".join(f"`{n}`" for n in missing)
            + " DOES NOT, and two of its events on one invoice at once are NOT serialised."
        )
    return f"The money events are {names}. {verdict}"


def _one_place() -> str:
    """Where the lock is taken, read from the source of every module: one site
    means every money event's lock comes with the manager's rollback, so no
    exception leaves it held. A second direct site changes this sentence."""
    from monthly_billing.store.postgres import direct_lock_call_sites

    sites = direct_lock_call_sites()
    names = ", ".join(f"`{m.rsplit('.', 1)[1]}.{f}`" for m, f in sites)
    if sites == (("monthly_billing.store.postgres", "locked_invoice"),):
        return (
            " The lock is taken in ONE place, `locked_invoice`, which rolls the "
            "transaction back if anything raises inside the block -- a refusal, the "
            "instrument guard, a driver error -- so no exception leaves the lock held on "
            "the caller's connection."
        )
    return (
        f" The lock is taken DIRECTLY in {len(sites)} places -- {names} -- so a raise at "
        "one of them can leave the lock held on the caller's connection."
    )


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
    "serialised": block_serialised,
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
