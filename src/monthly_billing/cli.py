"""The command line: someone with no parking system points this at a document.

That is the definition of done for a standalone module, and it is a check rather
than a claim -- CI runs these commands against the committed examples with nothing
installed but this package, and asserts BOTH exit codes. A command line that only
ever runs the happy path cannot tell a working refusal from one that reports
nothing.

    monthly-billing first-charge --garage G.json --agreement A.json
    monthly-billing covered --garage G.json --agreement A.json \\
        --vehicle ABC123 --at 2026-04-01T09:00:00-06:00
    monthly-billing check-agreement --agreement A.json

Against the store (the `store` extra, `MONTHLY_BILLING_DSN`, `--tenant`):

    monthly-billing run --tenant T --garage G --period-containing 2026-05-01
    monthly-billing record-payment --tenant T --invoice REF --method cheque \\
        --amount-minor 12000 --received-at 2026-05-08T10:00:00-06:00 \\
        --reference "cheque 1043" --recorded-by operator
    monthly-billing record-reversal --tenant T --payment ID --reason bounced_cheque \\
        --reversed-at 2026-05-12T10:00:00-06:00 --recorded-by operator
    monthly-billing covered-in-store --tenant T --garage G --vehicle ABC123 \\
        --at 2026-05-07T09:00:00-06:00
    monthly-billing pending-attempts --tenant T --invoice REF
    monthly-billing resolve-attempt --tenant T --attempt ID --outcome success \\
        --at 2026-05-07T09:05:00-06:00 --recorded-by operator [--reference AUTH]
    monthly-billing register-vehicle --tenant T --agreement AG --vehicle ABC123 \\
        [--at 2026-05-07T09:00:00-06:00]
    monthly-billing release-vehicle --tenant T --agreement AG --vehicle ABC123
    monthly-billing show-register --tenant T --agreement AG

``register-vehicle`` and ``release-vehicle`` are THE REGISTRATION DOOR: one
vehicle identity, on or off an agreement whose registrar is OUTSIDE, at every
garage the agreement covers. They print the identity as stored at each garage,
because two garages fold one plate differently and the registrar on the other
side needs the stored form to reconcile. An agreement whose registrations this
module writes is refused by name at both.

``show-register`` is THE REGISTER READ, for any reader and either registrar:
the agreement's latest version -- registrar, status, cancellation day, home
and covered garages -- and every registration row that names the agreement,
at any garage, as the identity stored there. JSON with sorted keys on stdout,
exit 0; it writes nothing, takes no instant and validates nothing it does not
return, so a version the loaders refuse still shows its register. An
agreement the store does not hold is NOT FOUND, exit 2.

A pending attempt comes only from the library's ``attempt_charge`` -- the
platform, as an ordinary client, charges; nothing on this command line does.
``pending-attempts`` lists each one with the id ``resolve-attempt`` takes, what
was reserved, whether its request may be in flight or the module has said it
does not know, and what it saw last. ``resolve-attempt`` is how an operator
records what the processor said when the worker that asked did not live to
record it.

Nothing here wakes itself up. The run is a command the operator's platform
calls on the billing day, and the platform is an ordinary client of it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime

from .agreement import InvalidAgreement, Registrar, load_agreement_file
from .currency import UnpriceableCurrency
from .entitlement import is_covered
from .findings import Refused
from .garage import BillingDay, Garage, IdentityRule
from .invoice import first_charge
from .localday import UnknownTimezone
from .money import NotMinorUnits
from .sensitive import InstrumentLike


def load_garage_file(path: str) -> Garage:
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
    try:
        return Garage(
            id=document["id"],
            timezone=document["timezone"],
            currency=document["currency"],
            billing_day=BillingDay(document["billing_day"]),
            billing_day_of_month=document.get("billing_day_of_month"),
            payment_grace_days=document["payment_grace_days"],
            identity_rule=IdentityRule(document["identity_rule"]),
        )
    except KeyError as exc:
        raise Refused(
            "REFUSAL_NO_BILLING_DAY",
            f"the garage document is missing {exc}. Every field on a garage decides "
            "money or coverage and none of them has a default.",
        ) from None


def _first_charge(args: argparse.Namespace) -> int:
    garage = load_garage_file(args.garage)
    agreement = load_agreement_file(args.agreement)
    print(first_charge(garage, agreement).rendered())
    return 0


def _covered(args: argparse.Namespace) -> int:
    garage = load_garage_file(args.garage)
    agreement = load_agreement_file(args.agreement)
    answer = is_covered(
        garage=garage,
        agreements=(agreement,),
        vehicle_identity=args.vehicle,
        at=datetime.fromisoformat(args.at),
        stay_entered_at=datetime.fromisoformat(args.entered_at) if args.entered_at else None,
    )
    # A not-covered answer exits non-zero, so a shell script can branch on it
    # without parsing prose. It is not an error -- it is an answer -- and the
    # difference is that a refusal exits 2 and prints its code.
    return _print_answer(answer)


def _check_agreement(args: argparse.Namespace) -> int:
    agreement = load_agreement_file(args.agreement)
    print(f"{agreement.id} version {agreement.version}: loads cleanly.")
    if agreement.registrar is Registrar.OUTSIDE:
        print(
            f"  {agreement.spots} spots; vehicles registered by an OUTSIDE registrar, "
            "none listed here"
        )
    else:
        print(f"  {agreement.spots} spots, {len(agreement.vehicles)} vehicles listed")
    others = [g for g in agreement.covered_garage_ids if g != agreement.garage_id]
    print(
        f"  billed at garage {agreement.garage_id}"
        + (f", also covers {', '.join(others)}" if others else ", covers no other garage")
    )
    print(f"  starts {agreement.start_day}, status {agreement.status.value}")
    print(f"  mandate: {'present' if agreement.mandate else 'ABSENT — cannot be charged'}")
    return 0


# ---------------------------------------------------------------------------
# Against the store
# ---------------------------------------------------------------------------


def _connection(args: argparse.Namespace):
    from .store.postgres import connect

    dsn = args.dsn or os.environ.get("MONTHLY_BILLING_DSN")
    if not dsn:
        raise SystemExit("a store command needs --dsn or MONTHLY_BILLING_DSN.")
    connection = connect(dsn)
    connection.autocommit = False
    return connection


def _print_answer(answer) -> int:
    print("COVERED" if answer.covered else "NOT COVERED")
    print(f"  {answer.reason}")
    if answer.entitlement is not None:
        print(f"  entitlement: {answer.entitlement} vehicles at once")
    if answer.agreement_id:
        print(f"  under agreement {answer.agreement_id} version {answer.agreement_version}")
    if answer.unchecked:
        print(f"  NOT YET EVALUATED: {', '.join(answer.unchecked)}")
    return 0 if answer.covered else 1


def _run(args: argparse.Namespace) -> int:
    from .billing_run import run_billing

    report = run_billing(
        _connection(args),
        args.tenant,
        args.garage,
        date.fromisoformat(args.period_containing),
        now=datetime.fromisoformat(args.now) if args.now else datetime.now().astimezone(),
    )
    print(report.rendered())
    # Per payer, and non-zero if ANY payer was refused. Already-issued is an
    # answer and exits zero; the operator can run it twice without a shell
    # script deciding that was an error.
    return 1 if report.refused else 0


def _record_payment(args: argparse.Namespace) -> int:
    from .payments import PaymentMethod, record_payment

    payment_id, state = record_payment(
        _connection(args),
        args.tenant,
        args.invoice,
        PaymentMethod(args.method),
        args.amount_minor,
        datetime.fromisoformat(args.received_at),
        recorded_by=args.recorded_by,
        processor_reference=args.reference,
    )
    print(f"payment {payment_id} recorded")
    print(_paid_line(state))
    return 0


def _record_reversal(args: argparse.Namespace) -> int:
    from .payments import ReversalReason, record_reversal

    state = record_reversal(
        _connection(args),
        args.tenant,
        args.payment,
        ReversalReason(args.reason),
        datetime.fromisoformat(args.reversed_at),
        recorded_by=args.recorded_by,
        note=args.note or "",
    )
    print(f"reversal recorded against payment {args.payment}")
    print(_paid_line(state))
    return 0


def _paid_line(state) -> str:
    if state.paid:
        line = f"  invoice PAID at {state.paid_at.isoformat()}"
        if state.overpaid_minor:
            line += f", overpaid by {state.overpaid_minor} minor"
        return line
    return (
        f"  invoice UNPAID: {state.paid_minor} of {state.total_minor} minor received "
        "(unpaid from its original due date)"
    )


#: Printed when the lane's question is asked about a PAST instant. The agreement
#: axes are answered at that instant; the payment state is the store's now.
PAYMENT_STATE_IS_NOW = (
    "  NOTE: --at is in the past; the payment state is as of now, not that instant"
)


def _covered_in_store(args: argparse.Namespace) -> int:
    from .entitlement_store import covered_from_store

    at = datetime.fromisoformat(args.at)
    answer = covered_from_store(
        _connection(args),
        args.tenant,
        args.garage,
        args.vehicle,
        at,
        stay_entered_at=datetime.fromisoformat(args.entered_at) if args.entered_at else None,
    )
    code = _print_answer(answer)
    if at < datetime.now().astimezone():
        print(PAYMENT_STATE_IS_NOW)
    return code


def _resolve_attempt(args: argparse.Namespace) -> int:
    from .charging import resolve_attempt
    from .payment import ChargeResult, Outcome

    outcome = resolve_attempt(
        _connection(args),
        args.tenant,
        args.attempt,
        ChargeResult(
            outcome=Outcome(args.outcome),
            detail=args.detail or f"recorded by {args.recorded_by}",
            reference=args.reference,
        ),
        recorded_by=args.recorded_by,
        now=datetime.fromisoformat(args.at),
    )
    print(f"attempt {args.attempt} resolved: {outcome.result.outcome.value}")
    if outcome.paid is not None:
        print(f"payment {outcome.payment_id} recorded")
        print(_paid_line(outcome.paid))
    return 0


def _pending_attempts(args: argparse.Namespace) -> int:
    from .charging import list_pending_attempts

    pending = list_pending_attempts(_connection(args), args.tenant, args.invoice)
    if not pending:
        print(f"invoice {args.invoice}: no pending attempts")
        return 0
    print(f"invoice {args.invoice}: {len(pending)} pending attempt(s)")
    for p in pending:
        line = (
            f"  attempt {p.attempt_id}: {p.amount_minor} {p.currency} reserved, {p.state}, "
            f"asked again {p.asks} time(s)"
        )
        if p.last_detail:
            line += f", last: {p.last_detail}"
        print(line)
    return 0


def _register_vehicle(args: argparse.Namespace) -> int:
    from .store.postgres import tenant
    from .store.records import register_from_outside

    connection = _connection(args)
    with tenant(connection, args.tenant) as cursor:
        try:
            stored = register_from_outside(
                cursor, args.tenant, args.agreement, args.vehicle,
                now=datetime.fromisoformat(args.at) if args.at else None,
            )
        except BaseException:
            connection.rollback()
            raise
    connection.commit()
    print(f"vehicle registered to agreement {args.agreement}")
    _print_stored_forms(stored)
    return 0


def _release_vehicle(args: argparse.Namespace) -> int:
    from .store.postgres import tenant
    from .store.records import release_from_outside

    connection = _connection(args)
    with tenant(connection, args.tenant) as cursor:
        try:
            released = release_from_outside(cursor, args.tenant, args.agreement, args.vehicle)
        except BaseException:
            connection.rollback()
            raise
    connection.commit()
    print(f"vehicle released from agreement {args.agreement}")
    _print_stored_forms(released)
    return 0


def _show_register(args: argparse.Namespace) -> int:
    from .store.postgres import tenant
    from .store.records import show_register

    connection = _connection(args)
    try:
        with tenant(connection, args.tenant) as cursor:
            register = show_register(cursor, args.tenant, args.agreement)
    finally:
        # A read: there is nothing to commit, and the transaction the tenant
        # context opened is ended either way.
        connection.rollback()
    print(json.dumps(register.as_document(), sort_keys=True, indent=2))
    return 0


def _print_stored_forms(entries) -> None:
    """Per covered garage, the identity in the form that garage stores it --
    the caller's means of reconciling, and the whole of the door's answer."""
    for entry in entries:
        print(f"  at garage {entry.garage_id}: {entry.identity_normalised}")


def _outcomes():
    """The outcome enum's values and nothing else -- the CLI accepts no other spelling."""
    from .payment import Outcome

    return list(Outcome)


def _store_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tenant", required=True, help="the tenant id (uuid)")
    parser.add_argument("--dsn", help="or MONTHLY_BILLING_DSN")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="monthly-billing", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    charge = sub.add_parser("first-charge", help="the partial period and the next one")
    charge.add_argument("--garage", required=True)
    charge.add_argument("--agreement", required=True)
    charge.set_defaults(run=_first_charge)

    covered = sub.add_parser("covered", help="is this vehicle covered at this instant")
    covered.add_argument("--garage", required=True)
    covered.add_argument("--agreement", required=True)
    covered.add_argument("--vehicle", required=True)
    covered.add_argument("--at", required=True, help="ISO instant with an offset")
    covered.add_argument("--entered-at", help="the stay's entry instant, when known")
    covered.set_defaults(run=_covered)

    check = sub.add_parser("check-agreement", help="load a document and report it")
    check.add_argument("--agreement", required=True)
    check.set_defaults(run=_check_agreement)

    run = sub.add_parser("run", help="issue one period's invoices to every payer at a garage")
    _store_arguments(run)
    run.add_argument("--garage", required=True, help="the garage id")
    run.add_argument("--period-containing", required=True, help="a day inside the period")
    run.add_argument("--now", help="the issue instant (ISO with offset); default: now")
    run.set_defaults(run=_run)

    payment = sub.add_parser("record-payment", help="a cheque or ACH payment received")
    _store_arguments(payment)
    payment.add_argument("--invoice", required=True, help="the invoice reference")
    payment.add_argument("--method", required=True, choices=["cheque", "ach"])
    payment.add_argument("--amount-minor", required=True, type=int)
    payment.add_argument("--received-at", required=True, help="ISO instant with an offset")
    payment.add_argument("--reference", help="the cheque number or the ACH trace")
    payment.add_argument("--recorded-by", required=True)
    payment.set_defaults(run=_record_payment)

    reversal = sub.add_parser("record-reversal", help="a payment that did not stand")
    _store_arguments(reversal)
    reversal.add_argument("--payment", required=True, help="the payment id")
    reversal.add_argument(
        "--reason", required=True,
        choices=["bounced_cheque", "ach_returned", "chargeback", "processor_reversed"],
    )
    reversal.add_argument("--reversed-at", required=True, help="ISO instant with an offset")
    reversal.add_argument("--recorded-by", required=True)
    reversal.add_argument("--note")
    reversal.set_defaults(run=_record_reversal)

    in_store = sub.add_parser("covered-in-store", help="the lane's question, from the store")
    _store_arguments(in_store)
    in_store.add_argument("--garage", required=True, help="the garage id")
    in_store.add_argument("--vehicle", required=True)
    in_store.add_argument("--at", required=True, help="ISO instant with an offset")
    in_store.add_argument("--entered-at", help="the stay's entry instant, when known")
    in_store.set_defaults(run=_covered_in_store)

    pending = sub.add_parser(
        "pending-attempts", help="list the PENDING attempts on an invoice, with their ids"
    )
    _store_arguments(pending)
    pending.add_argument("--invoice", required=True, help="the invoice reference")
    pending.set_defaults(run=_pending_attempts)

    resolve = sub.add_parser(
        "resolve-attempt", help="record what the processor said about a PENDING attempt"
    )
    _store_arguments(resolve)
    resolve.add_argument("--attempt", required=True, help="the attempt id (uuid)")
    resolve.add_argument("--outcome", required=True, choices=[o.value for o in _outcomes()])
    resolve.add_argument("--at", required=True, help="ISO instant with an offset")
    resolve.add_argument("--recorded-by", required=True)
    resolve.add_argument("--reference", help="the processor's reference for a success")
    resolve.add_argument("--detail", help="what the processor said, for a person")
    resolve.set_defaults(run=_resolve_attempt)

    register = sub.add_parser(
        "register-vehicle",
        help="register one vehicle to an agreement whose registrar is OUTSIDE",
    )
    _store_arguments(register)
    register.add_argument("--agreement", required=True, help="the agreement id")
    register.add_argument("--vehicle", required=True)
    register.add_argument("--at", help="the registration instant (ISO with offset); default: now")
    register.set_defaults(run=_register_vehicle)

    release = sub.add_parser(
        "release-vehicle",
        help="release one vehicle from an agreement whose registrar is OUTSIDE",
    )
    _store_arguments(release)
    release.add_argument("--agreement", required=True, help="the agreement id")
    release.add_argument("--vehicle", required=True)
    release.set_defaults(run=_release_vehicle)

    show = sub.add_parser(
        "show-register",
        help="an agreement's registrations and the covered set they are kept against, as JSON",
    )
    _store_arguments(show)
    show.add_argument("--agreement", required=True, help="the agreement id")
    show.set_defaults(run=_show_register)

    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except Refused as refusal:
        print(f"REFUSED — {refusal}", file=sys.stderr)
        return 2
    except (InvalidAgreement, NotMinorUnits, UnpriceableCurrency, UnknownTimezone) as bad:
        print(f"REFUSED — {bad}", file=sys.stderr)
        return 2
    except InstrumentLike as bad:
        print(f"REFUSED — {bad}", file=sys.stderr)
        return 2
    except LookupError as missing:
        # A garage, invoice or payment id that names nothing in the store.
        print(f"NOT FOUND — {missing}", file=sys.stderr)
        return 2
    except ValueError as bad:
        # The module's own value refusals that carry a sentence rather than a
        # code -- a vehicle identity that normalises to nothing under the
        # garage's rule, an instant that is not an ISO instant. G18: a command
        # renders a refusal, never a traceback; the sentence was written for a
        # person and is printed as it is.
        print(f"REFUSED — {bad}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
