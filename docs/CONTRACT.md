# The monthly billing contract

This module answers two questions and nothing else:

1. **What does this account owe, and when?** — the agreement, the cycle, the
   proration, the additional fees, the invoice.
2. **Is this vehicle covered right now, and for how many cars?** — the single
   fact a parking lane reads.

**The lane never learns anything about money.** It asks whether a vehicle is
covered and gets covered or not-covered with a plain reason. Counting how many of
an account's cars are inside is not this module's job: the module states the
ENTITLEMENT, and the platform counts and decides that the next car is a transient.
Counting would need live session state, and a module that needed live session
state would have stopped being standalone.

> **Every block below marked GENERATED is derived** — from the guarantee
> registry, the refusal registry, the enums that implement the options, and, for
> the worked example, from running the module and printing what it returned. No
> number and no sentence inside one is typed. `scripts/generate_contract.py
> --check` fails if one was edited by hand, and `tests/test_contract_is_generated.py`
> plants values contradicting the prose and requires the prose to change --
> because generation is not verification, and a template whose prose is fixed
> everywhere except the holes agrees with itself perfectly.

## What is NOT in this module

Stated first, so nothing here is later read as a promise:

- **No payment processor.** No card, no bank account, no live key. The interface
  is defined and the implementation is a stub that moves no money and says so.
  Collection is its own module.
- **No enrolment.** This module holds the vehicle list; it does not decide how a
  physical car gets onto it.
- **No customer portal.** No screens of any kind.
- **No tax.**
- **No multi-garage account.** One garage per agreement.
- **No refund decisions.** Where an agreement does not determine the answer, the
  module refuses and names the field. The garage owner decides, and records the
  decision as an exception with an amount on it.

## The guarantees

<!-- GENERATED:guarantees -->
| id | what is guaranteed |
|---|---|
| **G1** | Money is an integer of minor units with an explicit currency. A float, a bool or a Decimal is refused at load at EVERY LEAF of an agreement -- including leaves this version does not read -- not only at the fields the module happens to consult. |
| **G2** | Proration is by the actual days of the period being prorated, and a billing period's boundaries are INSTANTS in the garage's own timezone. A spring-forward period really is an hour shorter and a fall-back period an hour longer, while both still contain a whole number of calendar days. |
| **G3** | A month-end billing day resolves to the last day the month actually has, so February is not a special case: the 28th, or the 29th in a leap year. |
| **G4** | The first charge emits the period containing the start date and the following one as SEPARATE LINES, never one summed figure. The line count does not depend on the start date: an agreement starting ON the billing day is charged two full periods, and one starting the day before the next billing day is charged one day plus a full period, and both are two lines. |
| **G5** | A price change never alters a period already invoiced. Every invoice line records the agreement VERSION that priced it, and that version is what a recomputation is checked against. |
| **G6** | The entitlement answer carries NO MONETARY FIELD. Not a fee, not an amount, not a balance -- the field set is derived from the answer class itself, so a monetary field added tomorrow is caught the day it exists. |
| **G7** | An unpaid invoice past the garage's grace period returns not-covered, and NO CONFIGURATION OF THIS MODULE CAN REFUSE AN EXIT. There is no exit call, no field on the answer that could deny one, and every not-covered reason carries the sentence saying the stay is priced as an ordinary transient. |
| **G8** | An agreement with no mandate record refuses to be CHARGED, by name, before any processor is called. Its invoice may still be issued -- a payer who pays by cheque has no mandate and is still billed. |
| **G9** | No value shaped like a payment card or a bank account survives into the store or into a log line, and NO TRACKED FILE IN THE REPOSITORY CONTAINS ONE. The guard scans every column of every row from the record's own keys; the repository sweep derives its file set from git and exempts nothing, tests and fixtures included. |
| **G10** | An owner's exception records who made it and when, and a NOTE CAN NEVER CHANGE AN AMOUNT. The note reaches no arithmetic anywhere in the module; the amount is a separate, typed field, and a monetary exception without one is refused by name. |
| **G11** | The vehicle list is INDEPENDENT of the spots bought. Twenty vehicles against ten spots is a legal agreement, and the module states the entitlement rather than counting what is inside. |
| **G12** | Every table this module's migrations create carries a tenant column, ENABLE and FORCE row-level security and an isolation policy -- read from the database catalogue, never from a list of table names. |
| **G13** | A pause covering days in a period that has already been paid is REFUSED by name rather than credited or ignored. This module does not decide refunds; the owner records an exception with an amount. |
| **G14** | Every fixture carries its own control asserting it holds the property it claims to represent, and those controls run as tests. A DST fixture whose zone does not shift, or two identity rules that agree, read as coverage while sampling one point on the axis that decides. |
| **G15** | docs/CONTRACT.md is GENERATED from this registry and from the enums and refusal codes that implement it, and its prose is derived rather than fixed: a value that contradicts a published sentence changes the sentence, because generation is not verification. |
| **G16** | Every test module contributes at least one registered guarantee, derived from the filesystem and read from the AST -- so a module cannot be added, skipped or deleted without a guard noticing. A module that PLANTS a defect may not be excused at all. |
| **G17** | A billing run is idempotent BY CONSTRAINT: the database holds one invoice per tenant, garage, payer and period, so a second run of the same period issues nothing, re-prices nothing, and says so per payer rather than erroring or duplicating. |
| **G18** | A period is owned by exactly one of the first charge and the billing run, decided by PERIOD and never by line count: the run does not re-issue the two periods the first charge covered, and it does issue every period after them. |
| **G19** | An invoice is paid only by unreversed payments summing to its total, and paid_at is DERIVED after each of the three events that can change that -- a payment, a reversal, an owner adjustment that moves the total. A reversal reopens the invoice from its ORIGINAL due date, never from the reversal. |
| **G20** | Payments, reversals and charge attempts are append-only BY GRANT: the application role has no UPDATE and no DELETE on them, so money history is never edited, only added to -- and the store's instrument guard scans every row of theirs on the way in, as it does everywhere. The invoice's own lines and the owner's recorded decisions are history the same way -- no UPDATE, no DELETE -- and an invoice cannot be deleted; it keeps UPDATE for the one derived column, paid_at. |
| **G21** | The store-backed entitlement call returns the pure function's answer and nothing else -- the same field set, no money -- with 'unpaid since' derived as the earliest unpaid due date, and it HONOURS the owner's grace extensions and blocks rather than reading the garage's base figure alone. |
| **G22** | The charge log is the truth for retries: the retry state is rebuilt from the persisted attempts since the last persisted payment-method change, so three recorded non-success attempts refuse the fourth by name, a recorded method change allows it, and a restart forgets nothing. |
| **G23** | A charge is for the BALANCE -- the invoice's total minus its unreversed payments, read through the same derivation that decides paid -- never the total; and a balance of nothing is refused by name BEFORE the processor is called and before any attempt is recorded, so a paid invoice is never charged again, a part-paid one is charged its remainder, and a fully waived one is not charged at all. |
| **G24** | Every call to the processor leaves an attempt row, whatever the processor did: a result is recorded as it came, a raise is recorded as an error naming the exception, and a result the instrument guard refused inside the processor's own return is recorded as an error whose detail says the outcome is unknown -- a reported result is never discarded silently, and a row is never lost to its own message. |
| **G25** | An owner's exception is read by the agreement's IDENTITY across every version of it: a block or a grace extension recorded against one version still reaches the barrier after a price change has stored the next. |
| **G26** | An owner exception's amount is a POSITIVE number of minor units, refused by name by the module and by CHECK in the database, so no kind can be turned into its opposite by a sign -- and a refund records the decision and its amount and moves NO total: a paid period is paid. |
| **G27** | ALREADY_ISSUED means exactly that a row for this garage, payer and period exists: a unique violation on any other constraint is reported as its own outcome, naming the constraint, and makes the run exit non-zero -- the run never says a period was invoiced when it was not. |
| **G28** | Every money-history row points into its OWN tenant by composite key -- a payment at its invoice, a reversal at its payment, an attempt at its invoice -- so a row cannot reference another tenant's even by a raw INSERT that the policy would let past, because a foreign-key check runs past row-level security and the key does not. |
| **G29** | A payment is reversed at most once, for a reason that fits its method, and card fields sit only on a card payment -- each refused BY NAME by the module before the database has to, so an operator is never handed a raw driver error for a state the module can name. |

That is 29 guarantees. Every one of them has a fail control that has been proven to fire, and the count above is derived from the registry rather than typed here.
<!-- END:guarantees -->

## The entitlement answer

One call: given a vehicle identity, a garage and an instant, is this vehicle
covered? The answer is an ACCESS FACT and carries no money.

<!-- GENERATED:answer-fields -->
| field | type |
|---|---|
| `covered` | `bool` |
| `reason` | `str` |
| `reason_code` | `str | None` |
| `entitlement` | `int | None` |
| `agreement_id` | `str | None` |
| `agreement_version` | `int | None` |
| `unchecked` | `tuple[str, ...]` |

That is the whole answer: 7 fields, none of which is money, and none of which could express a decision about a barrier.
<!-- END:answer-fields -->

**`unchecked` is what makes the answer honest at a barrier.** Access hours are a
condition on a STAY — entry no earlier than, exit no later than — and at the
moment a car arrives the exit half is unknowable. An answer that quietly reported
"covered" on the entry half alone would be read as a promise about the whole
stay, and it is not one. So the conditions this call could not evaluate are
named, and the list is empty only when it really did evaluate all of them.

### Not covered, and what it means

<!-- GENERATED:not-covered -->
| reason | what the lane is told |
|---|---|
| `BLOCKED_BY_OWNER` | The garage owner has blocked this agreement by exception. |
| `CANCELLED` | This agreement was cancelled. |
| `NOT_STARTED` | This vehicle's agreement has not started yet. |
| `NO_AGREEMENT` | No agreement at this garage lists this vehicle. |
| `OUTSIDE_ACCESS_HOURS` | This agreement buys entry and exit within stated hours, and this is outside them. |
| `PAUSED` | This agreement is paused. Nothing is billed during a pause and nothing is covered by it. |
| `UNPAID_PAST_GRACE` | This agreement has an unpaid invoice past the garage's grace period. |

Every one of them carries this sentence: *Not covered means this stay is an ordinary transient stay and is priced like any other. It does not mean refuse entry, and it never means refuse exit.*
<!-- END:not-covered -->

**The seam with pricing is one-directional.** A covered stay has no fee. A stay
that is not covered — the car beyond the entitlement, an out-of-hours stay, an
unpaid account past grace, a cancelled agreement — is an ordinary transient stay
priced by whatever prices transient stays. This module never calls a rate engine
and no rate engine calls this module.

## Refusals

The module refuses when the agreement does not determine the answer, and names
the field that would let it answer. A refusal is a first-class result, not an
error: inventing a figure instead is the one behaviour this module may never
have, because a figure somebody pays looks the same whether it was determined or
guessed.

<!-- GENERATED:refusals -->
| code | when, and what to do about it |
|---|---|
| `REFUSAL_ALREADY_REVERSED` | This payment already has a reversal recorded. A payment that did not stand cannot un-stand twice; the first reversal is the record. |
| `REFUSAL_CARD_FIELDS_WITHOUT_A_CARD` | A card brand or last four digits were given on a payment that is not a card payment. Those fields describe the card a processor charged, and a cheque or an ACH debit has none. |
| `REFUSAL_CURRENCY_MISMATCH` | An invoice covers one garage and one currency. Two agreements in different currencies do not sum, and this module will not convert them: a conversion needs a rate, a date and a spread, none of which an agreement carries. |
| `REFUSAL_EXCEPTION_AMOUNT_NOT_POSITIVE` | This exception changes money and its amount is not a positive number of minor units. A waived fee, a credit and a refund each name how much; the direction is the kind's, never the sign's, so a negative amount is refused rather than read as the opposite kind. |
| `REFUSAL_EXCEPTION_HAS_NO_AMOUNT` | This exception changes money and carries no amount. A note explains; an amount changes what somebody pays. They are different fields and the amount is typed, so that no free-text note can ever price anything. |
| `REFUSAL_GARAGE_MISMATCH` | An agreement belongs to one garage and was asked about another. One garage per account is the stated shape; answering across garages would require rules for an entitlement this agreement does not describe. |
| `REFUSAL_NOTHING_OWED` | Nothing is owed on this invoice: its unreversed payments already reach its total, so there is no balance to charge. The processor is not called and no attempt is recorded, because nothing was attempted. A charge is always for the balance, never for the total. |
| `REFUSAL_NO_BILLING_DAY` | The garage has not stated its billing day. It is one of the offered options and there is no default, because the answer differs by garage and a guessed billing day charges somebody on the wrong date. |
| `REFUSAL_NO_IDENTITY_RULE` | The garage has not stated how a vehicle identity is compared. There is no default, because the answer decides whether a monthly parker is recognised at all -- and an unrecognised monthly parker is charged as a transient. |
| `REFUSAL_NO_MANDATE` | This agreement has no mandate record, so nothing may be charged against it. The mandate records who agreed, when, and to what: the recurring charge, its timing and frequency, how the amount is determined, and how it is cancelled. An invoice may still be issued and sent -- it is the CHARGE that is refused, not the billing. |
| `REFUSAL_NO_PAYMENT_GRACE` | The garage has not stated how many days past an unpaid invoice an agreement stays covered. There is no default: a guessed grace period either strands a paying customer at a barrier or covers an unpaid one indefinitely. |
| `REFUSAL_PAUSE_OVERLAPS_A_PAID_PERIOD` | The pause covers days in a period that has already been paid. A paid period is paid, and this module does not decide refunds or credits. The owner records an exception with an amount, or moves the pause. |
| `REFUSAL_RETRIES_EXHAUSTED` | This invoice has reached its maximum charge attempts. The count resets when the caller reports that the payer changed payment method -- this module holds no payment method and cannot observe that for itself. |
| `REFUSAL_REVERSAL_REASON_MISMATCH` | The reversal's reason does not fit the payment's method: a cheque bounces, an ACH debit is returned, a card payment is charged back or reversed by the processor. A reason from the wrong column is a record somebody assembled wrong, and it is refused rather than stored. |
<!-- END:refusals -->

## The options a garage states

Every one of these is REQUIRED and none has a default.

<!-- GENERATED:options -->
**Billing day** — one per garage, no default:

- `first_day_of_month`
- `last_day_of_month`
- `nth_day_of_month`

**Identity comparison** — one per garage, no default:

- `exact`
- `folded_alphanumeric`

**Additional fee cadence:**

- `one_time`
- `recurring`

**Owner exception kinds** — ⊙ requires a positive amount in minor units; ▾ lands an `exception_adjustment` line that lowers the invoice total:

- `extend_grace`
- `waive_fee` ⊙ ▾
- `credit` ⊙ ▾
- `refund` ⊙
- `block`
- `unblock`

A kind marked ⊙ but not ▾ -- `refund` -- records the owner's decision and its amount and changes NO total: the invoice stays what it was and stays paid if it was paid. The money going back is a movement, which is a collection record when collection exists, never a billing line. The direction of every amount is the kind's, never the sign's: a negative amount is refused by name (`REFUSAL_EXCEPTION_AMOUNT_NOT_POSITIVE`).

An agreement document carries exactly these keys: `access_hours`, `additional_fees`, `cancelled_effective_day`, `garage_id`, `id`, `mandate`, `monthly_price_minor`, `pauses`, `payer_id`, `spots`, `start_day`, `status`, `vehicles`, `version`. Any other key is refused rather than ignored.

A charge is attempted at most 3 times before the module refuses, and the count resets only when the caller reports that the payer changed payment method.
<!-- END:options -->

## The cycle, the proration and the first charge

- **A calendar day is the garage's local day.** Proration and the billing day are
  computed in the garage's own timezone. A billing period carries both its days
  and its boundary INSTANTS, and the two answer different questions: the days
  decide the arithmetic, the instants decide when the period actually begins and
  ends in the world.
- **Proration is by actual days.** The divisor is the actual number of days in the
  period being prorated. For a month-end garage the period IS a calendar month,
  so that is the actual days of that month — February by 28, March by 31. For any
  other billing day the period is not a calendar month, and its own length is the
  only defined reading.
- **The remainder goes to the payer.** Integer division truncates toward zero,
  which is the direction that cannot overcharge.
- **The first charge is two lines.** The period containing the start date and the
  one after it, never one summed figure.
- **A paid period is paid.** A price change writes a new agreement version and
  takes effect at the next cycle. Every invoice line records the version that
  priced it.
- **Cancellation is use-it-or-lose-it**, with no refund by default. The owner may
  decide otherwise and records it as an exception.

### A worked example

<!-- GENERATED:worked-example -->
```
$ monthly-billing first-charge --garage tests/documents/garage_downtown.json \
      --agreement tests/documents/agreement_acme.json
Invoice to payer payer-acme for garage garage-downtown
  Part period 2026-02-28 to 2026-03-31 (21 of 31 days): 81.29 USD
  Reserved space 12 (part month): 16.93 USD
  Access card (one-time): 15.00 USD
  Period 2026-03-31 to 2026-04-30: 120.00 USD
  Reserved space 12 (monthly): 25.00 USD
  TOTAL: 258.22 USD
```

The agreement starts 2026-03-10 and the garage bills on `last_day_of_month`, so the first period runs 2026-02-28 to 2026-03-31 and 21 of 31 days of it are billable. The part period and the following one are separate lines, never summed.
<!-- END:worked-example -->

## The billing run

M1 priced a period as a pure function. The run is what issues it: one command,
called by the operator's platform on the billing day -- the platform is an
ordinary client and nothing in this module wakes itself up. For every payer
with an agreement at the garage, the period's invoice is built with the same
function and written, with its lines, in one transaction. **It is idempotent by
constraint**: the database holds one invoice per tenant, garage, payer and
period, so a second run of the same period issues nothing and says so. The
first charge, taken on the binding day, owns the period containing the start
day and the one after it; the run owns every period after those, by period and
never by line count.

<!-- GENERATED:billing-run -->
| outcome | what it means |
|---|---|
| `issued` | The invoice and its lines were written, in one transaction. |
| `already_issued` | This period was already invoiced for this payer. Nothing was issued and nothing was re-priced; the database refused the duplicate and the run reports it. |
| `nothing_billable` | No agreement of this payer's has a billable day in the period -- paused, cancelled, not started, or owned by the first charge -- so no invoice exists. |
| `refused` | The module could not price this payer and says why. The payers after it were still processed; the command exits non-zero. |
| `constraint_violated` | The database refused this payer's invoice on a constraint OTHER than the one-invoice-per-period lock, and the line names it. Nothing was issued for this payer and nothing is claimed about the period; the payers after it were still processed; the command exits non-zero. |

Every payer at the garage gets exactly one of these 5 outcomes, per run. The run exits non-zero if any payer's outcome is one of `refused`, `constraint_violated`, and zero otherwise -- `already_issued` is an answer, not an error, and it means exactly that a row for this garage, payer and period exists.
<!-- END:billing-run -->

An invoice is **due at the start instant of its period** -- the billing day,
garage-local, paid in advance -- and the garage's grace period counts from
there.

## Payments received

**Paid is derived, never set by hand.** An invoice is paid when the sum of its
unreversed payments reaches its total, and `paid_at` is the instant of the
event that made that true. It is re-derived after each of the three events
that can change it: a payment, a reversal, and an owner's adjustment that moves
the total. A reversal reopens the invoice **from its original due date** -- a
cheque that bounced was never money -- and is a second row beside the payment,
never an edit to it. A partial payment leaves the invoice unpaid; the module
decides nothing about it, and the owner records an exception with an amount
if they decide something. An owner's exception is read by the agreement's
IDENTITY across every version of it: a block or a grace extension recorded
against one version still holds after a price change stores the next.

<!-- GENERATED:payment-methods -->
**Payment methods** — how money is recorded as received:

- `card` — written ONLY by the charge path, when the processor says SUCCESS
- `cheque` — recorded by the operator, one command
- `ach` — recorded by the operator, one command

2 of the 3 methods can be recorded by hand; the rest cannot, so a card payment nobody charged has nowhere to land.

**Reversal reasons** — a payment that did not stand, as a second row:

- `bounced_cheque` — on a cheque payment
- `ach_returned` — on a ach payment
- `chargeback` — on a card payment
- `processor_reversed` — on a card payment

A payment has at most one reversal (a second is `REFUSAL_ALREADY_REVERSED`), and there are 4 reasons it can carry, each fitting the payment's method and refused by name otherwise (`REFUSAL_REVERSAL_REASON_MISMATCH`). What happens next is the owner's decision, recorded as an exception.

**A charge is for the balance.** The charge path asks the processor for the invoice's total minus its unreversed payments, never the total; a balance of nothing is `REFUSAL_NOTHING_OWED` before the processor is called, so a paid invoice is never charged again and a part-paid one is charged its remainder. Every call to the processor leaves an attempt row: a raise is an `error` naming the exception, and a result the instrument guard refused inside the processor's own return is an `error` whose detail says the outcome is unknown -- a reported result is never discarded silently.
<!-- END:payment-methods -->

Money history is **append-only by grant**, and the invoice's own lines and the
owner's recorded decisions are history the same way. What the application role
may do to each table, read from the catalogue of a database migrated from
`migrations/`:

<!-- GENERATED:grants -->
| table | the application role may |
|---|---|
| `agreement_fees` | DELETE, INSERT, SELECT, UPDATE |
| `agreement_pauses` | DELETE, INSERT, SELECT, UPDATE |
| `agreement_vehicles` | DELETE, INSERT, SELECT, UPDATE |
| `agreements` | DELETE, INSERT, SELECT, UPDATE |
| `charge_attempts` | INSERT, SELECT |
| `garages` | DELETE, INSERT, SELECT, UPDATE |
| `invoice_lines` | INSERT, SELECT |
| `invoices` | INSERT, SELECT, UPDATE |
| `mandates` | DELETE, INSERT, SELECT, UPDATE |
| `owner_exceptions` | INSERT, SELECT |
| `payers` | DELETE, INSERT, SELECT, UPDATE |
| `payment_reversals` | INSERT, SELECT |
| `payments` | INSERT, SELECT |
| `tenants` | DELETE, INSERT, SELECT, UPDATE |

14 tables. 5 are append-only -- `charge_attempts`, `invoice_lines`, `owner_exceptions`, `payment_reversals`, `payments` -- and `invoices` keeps UPDATE and loses DELETE: `paid_at` is derived by the application after every payment, reversal and adjustment, and that is the one column it writes. The rest carry the DML the application needs to store documents.
<!-- END:grants -->

The charge log is the truth for retries -- the retry state is rebuilt from the
persisted attempts since the last persisted payment-method change, so nothing
is forgotten on a restart.

### The second month

<!-- GENERATED:second-month -->
```
$ monthly-billing run --garage garage-downtown --period-containing 2026-05-01
Billing run for garage garage-downtown, period 2026-04-30 to 2026-05-31
  payer-acme: ISSUED — 2 line(s), total 14500 USD minor

$ monthly-billing covered-in-store --garage garage-downtown --vehicle "ABC-123" --at 2026-05-05T09:00 (local)
COVERED

$ monthly-billing covered-in-store --garage garage-downtown --vehicle "ABC-123" --at 2026-05-06T09:00 (local)
NOT COVERED
  This agreement has an unpaid invoice past the garage's grace period. Not covered means this stay is an ordinary transient stay and is priced like any other. It does not mean refuse entry, and it never means refuse exit.

$ monthly-billing record-payment --invoice garage-downtown/2026-04-30/payer-acme --method cheque --amount-minor 14500 --received-at 2026-05-08T10:00 (local)
  invoice PAID at 2026-05-08T10:00:00-06:00

$ monthly-billing covered-in-store --garage garage-downtown --vehicle "ABC-123" --at 2026-05-09T09:00 (local)
COVERED
```

The run issued garage-downtown/2026-04-30/payer-acme for the period 2026-04-30 to 2026-05-31, due on its first day, 14500 minor units. The garage states a grace of 5 days, so the vehicle is still covered on 2026-05-05 and is NOT covered (`UNPAID_PAST_GRACE`) on 2026-05-06. A cheque recorded on 2026-05-08 for the full amount pays it from that instant, and the vehicle is still covered on 2026-05-09. The lane was told nothing about money at any point.
<!-- END:second-month -->

## Payment instruments

⛔ **No card number, no bank account number, and no token that could substitute
for one, in this module's database or its logs. Ever.**

This is built as a guarantee with a control that fires, not as a sentence in a
document. Every row written to the store is scanned, column by column, from the
record's own keys — so a column added next round is scanned the day it exists.
The detector holds no example of what it looks for, and it does not exempt its
own source.

The mandate record carries the TERMS somebody agreed to, in the words they were
shown, and nothing that could be used to charge them. Whatever can actually move
money lives with the processor, in the module that talks to one.

## Row-level security

Every table these migrations create carries a tenant column, `ENABLE` and `FORCE`
row-level security and an isolation policy, from migration 0001. The coverage
check reads the database catalogue rather than a list of table names, because a
check that walked a list could not notice a table added without protection.

The application connects as a role created `NOSUPERUSER NOBYPASSRLS`. A superuser
bypasses row-level security unconditionally — `FORCE` does not stop one — so the
isolation tests assert they are connected as a role that could be stopped BEFORE
they assert that it was.

## Licence

AGPL-3.0-or-later. See `LICENSE`, `CONTRIBUTING.md` and `CLA.md`.

Built by 72 Knots Method by 72Knots.ai
