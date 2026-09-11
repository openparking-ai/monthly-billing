"""The contract is DERIVED, and derivation is proven by plants, not by reading.

**GENERATION IS NOT VERIFICATION.** Moving a sentence from a document into a
template does not stop it being hand-written: everywhere except the holes it is
still prose nobody checks, and a template whose prose is fixed agrees with itself
perfectly for ever. A generated block asserts only what it DERIVES from its
values.

So each test below plants a value that CONTRADICTS the rendered prose and
requires the prose to change. Anything that survives such a plant is a fixed
string, and a fixed string is design documentation rather than a measurement --
which is fine, as long as nobody reads it as derived.

**AND THE CHECK IS AGAINST THE MEASUREMENT, NEVER AGAINST A SECOND COPY OF THE
CLAIM.** Comparing the document to another rendering of the same template would
agree happily while both carried the same error.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import generate_contract as gen  # noqa: E402


@pytest.mark.guarantee("G15")
def test_the_document_on_disk_is_the_generated_one():
    """What `--check` runs in CI, asserted here too so a local run catches it.

    The second-month block is produced by running the store, so this needs a
    database -- the same allowance G12 has, for the same reason. CI always sets
    the DSN, so CI never skips it.
    """
    pytest.importorskip("psycopg")
    if not os.environ.get("MONTHLY_BILLING_TEST_DSN"):
        pytest.skip("MONTHLY_BILLING_TEST_DSN is not set")
    current = gen.DOC.read_text()
    assert current == gen.render(current), (
        "docs/CONTRACT.md does not match its generator. Run "
        "scripts/generate_contract.py."
    )


@pytest.mark.guarantee("G15")
def test_every_block_the_document_declares_is_actually_built():
    """A marker with no builder would render as a permanently empty block, and an
    empty block reads as 'nothing to say' rather than as a broken generator."""
    text = gen.DOC.read_text()
    for name in gen.BLOCKS:
        assert gen.BEGIN.format(name=name) in text
        assert gen.END.format(name=name) in text


@pytest.mark.guarantee("G15")
def test_a_new_guarantee_appears_in_the_document():
    """PLANT: add a guarantee to the registry; the table must grow.

    Without this, the guarantee table could be a fixed list that happens to match
    today's registry.
    """
    before = gen.block_guarantees()
    gen.GUARANTEES["G99"] = "A planted guarantee that must appear in the document."
    try:
        after = gen.block_guarantees()
    finally:
        del gen.GUARANTEES["G99"]
    assert "G99" not in before
    assert "G99" in after
    assert "A planted guarantee" in after


@pytest.mark.guarantee("G15")
def test_the_guarantee_count_is_derived_and_not_typed():
    """PLANT: the sentence naming how many guarantees there are must move with
    the registry. A typed count in the file a reader opens first outlives every
    sweep, because it sits where prose reads as orientation rather than a claim."""
    before = gen.block_guarantees()
    gen.GUARANTEES["G98"] = "Another planted one."
    try:
        after = gen.block_guarantees()
    finally:
        del gen.GUARANTEES["G98"]
    assert f"That is {len(gen.GUARANTEES)} guarantees" in before
    assert f"That is {len(gen.GUARANTEES) + 1} guarantees" in after


@pytest.mark.guarantee("G15")
def test_a_new_refusal_code_appears_with_its_sentence():
    """PLANT: a refusal added to the registry must be published."""
    gen.REFUSALS["REFUSAL_PLANTED"] = "A planted refusal sentence."
    try:
        rendered = gen.block_refusals()
    finally:
        del gen.REFUSALS["REFUSAL_PLANTED"]
    assert "REFUSAL_PLANTED" in rendered
    assert "A planted refusal sentence." in rendered


@pytest.mark.guarantee("G15")
def test_a_new_not_covered_reason_appears():
    gen.NOT_COVERED_REASONS["PLANTED_REASON"] = "A planted reason a lane would read."
    try:
        rendered = gen.block_not_covered()
    finally:
        del gen.NOT_COVERED_REASONS["PLANTED_REASON"]
    assert "PLANTED_REASON" in rendered


@pytest.mark.guarantee("G15")
def test_the_answer_field_table_follows_the_class():
    """PLANT: a field added to the answer must appear, and the count must move.

    This is the same derivation the no-money guarantee rests on, so if it went
    blunt, two things would stop measuring at once.
    """
    from monthly_billing.entitlement import Answer

    before = gen.block_answer_fields()
    original = dict(Answer.__dataclass_fields__)
    Answer.__dataclass_fields__["planted_field"] = original["covered"]
    try:
        after = gen.block_answer_fields()
    finally:
        Answer.__dataclass_fields__.clear()
        Answer.__dataclass_fields__.update(original)

    assert "planted_field" not in before
    assert "planted_field" in after
    assert f"{len(original)} fields" in before
    assert f"{len(original) + 1} fields" in after


@pytest.mark.guarantee("G15")
def test_the_worked_example_is_produced_by_running_the_module():
    """The figures in the example are the module's own output, not a transcript.

    Asserted by recomputing the invoice here and requiring the rendered total to
    appear in the block -- derived from the MEASUREMENT rather than from a second
    copy of the block.
    """
    from monthly_billing.agreement import load_agreement_file
    from monthly_billing.cli import load_garage_file
    from monthly_billing.invoice import first_charge
    from monthly_billing.money import format_minor

    garage = load_garage_file(str(ROOT / "tests" / "documents" / "garage_downtown.json"))
    agreement = load_agreement_file(str(ROOT / "tests" / "documents" / "agreement_acme.json"))
    invoice = first_charge(garage, agreement)

    block = gen.block_worked_example()
    assert format_minor(invoice.total_minor, garage.currency) in block
    for line in invoice.lines:
        assert format_minor(line.amount_minor, garage.currency) in block


@pytest.mark.guarantee("G15")
def test_a_new_run_outcome_appears_with_its_sentence():
    """PLANT: an outcome added to the run's enum, with its meaning, must be
    published -- and one added WITHOUT a meaning must refuse to render."""
    from enum import Enum

    # Derived from the real enum plus one, never a typed copy of it: a copy
    # would go stale the day an outcome is added and this plant would then be
    # testing an enum that no longer exists.
    Planted = Enum(
        "Planted", {**{o.name: o.value for o in gen.RunOutcome}, "PLANTED": "planted_outcome"}
    )

    original_enum, original_means = gen.RunOutcome, gen.RUN_OUTCOME_MEANS
    original_failed = gen.FAILED_OUTCOMES
    means = {Planted(o.value): text for o, text in original_means.items()}
    gen.RunOutcome = Planted
    gen.RUN_OUTCOME_MEANS = means
    gen.FAILED_OUTCOMES = frozenset(Planted(o.value) for o in original_failed)
    try:
        with pytest.raises(SystemExit):
            gen.block_billing_run()  # an outcome with no sentence refuses
        means[Planted.PLANTED] = "A planted outcome sentence."
        rendered = gen.block_billing_run()
    finally:
        gen.RunOutcome, gen.RUN_OUTCOME_MEANS = original_enum, original_means
        gen.FAILED_OUTCOMES = original_failed
    assert "`planted_outcome`" in rendered and "A planted outcome sentence." in rendered
    assert f"exactly one of these {len(Planted)} outcomes" in rendered


@pytest.mark.guarantee("G15")
def test_the_payment_methods_block_follows_the_enums_and_the_operator_set():
    """PLANT: shrinking the operator-recorded set must change the count sentence
    and the per-method wording; every reversal reason is listed."""
    from monthly_billing.payments import PaymentMethod, ReversalReason

    before = gen.block_payment_methods()
    for reason in ReversalReason:
        assert f"`{reason.value}`" in before
    for method in PaymentMethod:
        assert f"`{method.value}`" in before
    assert f"{len(gen.OPERATOR_RECORDED)} of the {len(PaymentMethod)} methods" in before

    original = gen.OPERATOR_RECORDED
    gen.OPERATOR_RECORDED = frozenset({PaymentMethod.CHEQUE})
    try:
        after = gen.block_payment_methods()
    finally:
        gen.OPERATOR_RECORDED = original
    assert f"1 of the {len(PaymentMethod)} methods" in after
    assert "`ach` — written ONLY by the charge path" in after


@pytest.mark.guarantee("G15")
@pytest.mark.parametrize("grace", [5, 12], ids=["as-stated", "planted-grace"])
def test_the_second_month_is_produced_by_running_the_store(grace):
    """The block is the store's own output. PLANT: a garage stating a different
    grace must move the dates and the sentence -- if the block were a transcript,
    the plant would change nothing."""
    import dataclasses
    from datetime import date, timedelta

    from monthly_billing import cli

    pytest.importorskip("psycopg")
    if not os.environ.get("MONTHLY_BILLING_TEST_DSN"):
        pytest.skip("MONTHLY_BILLING_TEST_DSN is not set")

    real = cli.load_garage_file

    def planted(path: str):
        return dataclasses.replace(real(path), payment_grace_days=grace)

    cli.load_garage_file = planted
    try:
        block = gen.block_second_month()
    finally:
        cli.load_garage_file = real

    due = date(2026, 4, 30)
    assert f"grace of {grace} days" in block
    assert f"is still covered on {due + timedelta(days=grace)}" in block
    assert f"is NOT covered (`UNPAID_PAST_GRACE`) on {due + timedelta(days=grace + 1)}" in block
    assert "pays it from that instant, and the vehicle is still covered on 2026-05-09" in block
    assert "14500" in block, "the run's total, recomputed by the store, not typed"


@pytest.mark.guarantee("G15")
def test_the_second_month_prose_changes_when_the_store_answers_the_other_way():
    """THE §6 CONTROL: a moving number is not a changed assertion. Plant a lane
    that never covers and require the SENTENCES -- not just the code block above
    them -- to say so. Under the old prose 'still covered' and 'covered again'
    were fixed text whatever the store answered."""
    import monthly_billing.entitlement_store as es
    from monthly_billing.entitlement import _not_covered
    from monthly_billing.findings import NOT_COVERED_BLOCKED_BY_OWNER

    pytest.importorskip("psycopg")
    if not os.environ.get("MONTHLY_BILLING_TEST_DSN"):
        pytest.skip("MONTHLY_BILLING_TEST_DSN is not set")

    real = es.covered_from_store
    es.covered_from_store = lambda *a, **k: _not_covered(NOT_COVERED_BLOCKED_BY_OWNER)
    try:
        planted = gen.block_second_month()
    finally:
        es.covered_from_store = real
    prose = planted.rsplit("```", 1)[1]
    assert "is still covered" not in prose
    assert "is NOT covered (`BLOCKED_BY_OWNER`) on 2026-05-05" in prose
    assert "is NOT covered (`BLOCKED_BY_OWNER`) on 2026-05-09" in prose


@pytest.mark.guarantee("G15")
def test_the_options_block_follows_the_enums():
    """PLANT-free derivation check: every member of every enum is listed.

    Derived from the enums themselves rather than from a list in this test, so a
    member added next round is covered without anybody editing this file.
    """
    from monthly_billing.agreement import FeeCadence
    from monthly_billing.exceptions_by_owner import ExceptionKind
    from monthly_billing.garage import BillingDay, IdentityRule

    block = gen.block_options()
    for enum in (BillingDay, IdentityRule, FeeCadence, ExceptionKind):
        for member in enum:
            assert f"`{member.value}`" in block, f"{member} is missing from the contract"


@pytest.mark.parametrize("phrase", ["never means refuse exit", "no default"])
@pytest.mark.guarantee("G15")
def test_the_sentences_the_module_lives_by_are_in_the_document(phrase):
    """Not a derivation check -- a presence check on two sentences that must not
    quietly leave the published contract. Named as such rather than dressed up as
    a measurement."""
    assert phrase in gen.DOC.read_text()
