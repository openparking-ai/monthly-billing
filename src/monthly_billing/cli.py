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
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from .agreement import InvalidAgreement, load_agreement_file
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
    print("COVERED" if answer.covered else "NOT COVERED")
    print(f"  {answer.reason}")
    if answer.entitlement is not None:
        print(f"  entitlement: {answer.entitlement} vehicles at once")
    if answer.agreement_id:
        print(f"  under agreement {answer.agreement_id} version {answer.agreement_version}")
    if answer.unchecked:
        print(f"  NOT YET EVALUATED: {', '.join(answer.unchecked)}")
    # A not-covered answer exits non-zero, so a shell script can branch on it
    # without parsing prose. It is not an error -- it is an answer -- and the
    # difference is that a refusal exits 2 and prints its code.
    return 0 if answer.covered else 1


def _check_agreement(args: argparse.Namespace) -> int:
    agreement = load_agreement_file(args.agreement)
    print(f"{agreement.id} version {agreement.version}: loads cleanly.")
    print(f"  {agreement.spots} spots, {len(agreement.vehicles)} vehicles listed")
    print(f"  starts {agreement.start_day}, status {agreement.status.value}")
    print(f"  mandate: {'present' if agreement.mandate else 'ABSENT — cannot be charged'}")
    return 0


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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
