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

That is 16 guarantees. Every one of them has a fail control that has been proven to fire, and the count above is derived from the registry rather than typed here.
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
| `REFUSAL_CURRENCY_MISMATCH` | An invoice covers one garage and one currency. Two agreements in different currencies do not sum, and this module will not convert them: a conversion needs a rate, a date and a spread, none of which an agreement carries. |
| `REFUSAL_EXCEPTION_HAS_NO_AMOUNT` | This exception changes money and carries no amount. A note explains; an amount changes what somebody pays. They are different fields and the amount is typed, so that no free-text note can ever price anything. |
| `REFUSAL_GARAGE_MISMATCH` | An agreement belongs to one garage and was asked about another. One garage per account is the stated shape; answering across garages would require rules for an entitlement this agreement does not describe. |
| `REFUSAL_NO_BILLING_DAY` | The garage has not stated its billing day. It is one of the offered options and there is no default, because the answer differs by garage and a guessed billing day charges somebody on the wrong date. |
| `REFUSAL_NO_IDENTITY_RULE` | The garage has not stated how a vehicle identity is compared. There is no default, because the answer decides whether a monthly parker is recognised at all -- and an unrecognised monthly parker is charged as a transient. |
| `REFUSAL_NO_MANDATE` | This agreement has no mandate record, so nothing may be charged against it. The mandate records who agreed, when, and to what: the recurring charge, its timing and frequency, how the amount is determined, and how it is cancelled. An invoice may still be issued and sent -- it is the CHARGE that is refused, not the billing. |
| `REFUSAL_NO_PAYMENT_GRACE` | The garage has not stated how many days past an unpaid invoice an agreement stays covered. There is no default: a guessed grace period either strands a paying customer at a barrier or covers an unpaid one indefinitely. |
| `REFUSAL_PAUSE_OVERLAPS_A_PAID_PERIOD` | The pause covers days in a period that has already been paid. A paid period is paid, and this module does not decide refunds or credits. The owner records an exception with an amount, or moves the pause. |
| `REFUSAL_PERIOD_NOT_STARTED` | The agreement has not started, so there is no period to bill. Its start date is in the future relative to the instant asked about. |
| `REFUSAL_RETRIES_EXHAUSTED` | This invoice has reached its maximum charge attempts. The count resets when the caller reports that the payer changed payment method -- this module holds no payment method and cannot observe that for itself. |
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

**Owner exception kinds** — those marked ⊙ require an amount:

- `extend_grace`
- `waive_fee` ⊙
- `credit` ⊙
- `refund` ⊙
- `block`
- `unblock`

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
