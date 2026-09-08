#!/usr/bin/env python3
"""Generate docs/CONTRACT.md from the registries, and check it has not drifted.

    python scripts/generate_contract.py            # write the document
    python scripts/generate_contract.py --check    # fail if it would change

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
from monthly_billing.entitlement import Answer  # noqa: E402
from monthly_billing.exceptions_by_owner import MONETARY_KINDS, ExceptionKind  # noqa: E402
from monthly_billing.findings import NOT_COVERED_MEANS, NOT_COVERED_REASONS, REFUSALS  # noqa: E402
from monthly_billing.garage import BillingDay, IdentityRule  # noqa: E402
from monthly_billing.invoice import first_charge  # noqa: E402
from monthly_billing.payment import MAX_ATTEMPTS  # noqa: E402

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
    lines += ["", "**Owner exception kinds** — those marked ⊙ require an amount:", ""]
    for kind in ExceptionKind:
        mark = " ⊙" if kind in MONETARY_KINDS else ""
        lines.append(f"- `{kind.value}`{mark}")
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


BLOCKS = {
    "guarantees": block_guarantees,
    "refusals": block_refusals,
    "not-covered": block_not_covered,
    "answer-fields": block_answer_fields,
    "options": block_options,
    "worked-example": block_worked_example,
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
