# Open Parking AI — monthly billing

**What does this account owe, and when? Is this vehicle covered right now, and
for how many cars?** Those two questions, and nothing else.

Standalone. It runs with no parking system around it, no platform, and — for
everything that decides money — no database and no dependencies at all.

```
$ monthly-billing first-charge --garage garage.json --agreement agreement.json
$ monthly-billing covered --garage garage.json --agreement agreement.json \
      --vehicle ABC-123 --at 2026-04-01T09:00:00-06:00
```

And, against the store, the cycle: issue the period, record what was paid,
answer the lane from what is actually owed.

```
$ monthly-billing run --tenant T --garage garage-downtown --period-containing 2026-05-01
$ monthly-billing record-payment --tenant T --invoice garage-downtown/2026-04-30/payer-acme \
      --method cheque --amount-minor 14500 --received-at 2026-05-08T10:00:00-06:00 \
      --reference "cheque 1043" --recorded-by operator
$ monthly-billing covered-in-store --tenant T --garage garage-downtown \
      --vehicle ABC-123 --at 2026-05-09T09:00:00-06:00
$ monthly-billing register-vehicle --tenant T --agreement ag-fleet-0007 --vehicle "ABC 123"
$ monthly-billing release-vehicle --tenant T --agreement ag-fleet-0007 --vehicle "ABC 123"
```

Nothing wakes itself up: the run is a command the operator's platform calls on
the billing day, and the platform is an ordinary client of it. The last two are
the REGISTRATION DOOR: for an agreement whose `registrar` is `outside`, an
outside registrar puts one car on and takes one off, at every garage the
agreement covers, and is told the identity as stored at each -- this module
writes none of that agreement's registrations itself.

## The lane never learns anything about money

It asks whether a vehicle is covered and gets **covered** or **not covered** with
a plain reason. No fee, no amount, no balance ever crosses that call.

Counting how many of an account's cars are inside is **not this module's job**.
The module states the ENTITLEMENT — *this account may have ten of its twenty
registered vehicles inside at once* — and whatever runs the garage counts and
decides the eleventh car is a transient. Counting would need live session state,
and a module that needed live session state would have stopped being standalone.

**Not covered never means refuse, and it never means refuse exit.** A stay that
is not covered is an ordinary transient stay, priced like any other. No
configuration of this module can trap a car in a garage: there is no exit call,
no field on the answer that could deny one, and every not-covered reason carries
the sentence saying so.

## It refuses rather than assuming

Money is an integer of minor units with an explicit currency — no float, no bool,
no `Decimal`, refused at load at **every leaf** of an agreement, including the
leaves this version does not read. A billing day, a timezone, a currency, a grace
period and an identity rule are all REQUIRED of a garage, and none has a default:
a guessed billing day charges somebody on the wrong date, and a guessed identity
rule decides whether a monthly parker is recognised at all.

Where an agreement does not determine an answer, the module says which field is
missing. It never invents a figure. A figure somebody pays looks the same whether
it was determined or guessed.

## A calendar day is the garage's local day

Proration is by the actual days of the period being prorated, computed in the
garage's own timezone. A period carries both its days and its boundary
**instants**, and the two answer different questions — which is why the daylight
saving guarantee is written against the instants. Counting calendar days is
DST-invariant, so a guarantee phrased as "31 days in a spring-forward month"
passes under the very implementation it exists to catch.

## Payment instruments

⛔ **No card number, no bank account number, and no token that could substitute
for one, in this module's database or its logs. Ever.**

There is no processor here at all: M1 defines the interface and ships a stub that
moves no money and says so. The mandate record carries the TERMS somebody agreed
to, in the words they were shown, and nothing that could be used to charge them.

Every row written to the store is scanned, column by column, derived from the
record's own keys — so a column added next round is scanned the day it exists.
The detector holds no example of what it looks for, and it does not exempt its
own source. It found one in this repository's own documentation on its first run,
and that is recorded in `sensitive.py` rather than quietly deleted.

## Every guarantee has a control that has been proven to fire

```
python scripts/fail_controls.py --anchors   # every anchor is live, in a second
python scripts/fail_controls.py             # break each guarantee, require RED
```

A test that has never failed is a decoration. The script breaks the thing each
guarantee guards and requires its tests to go red; a guarantee with no control
fails the run, and a target already failing before anything was planted is
reported UNMEASURED rather than counted.

The guarantees, the refusal codes, the options and the worked example are all
generated into `docs/CONTRACT.md` from the registries and from running the module.
No number in it is typed.

## Install

```
pip install -e .              # the engine: no dependencies at all
pip install -e '.[store]'     # plus the Postgres store
pip install -e '.[dev]'       # plus pytest and ruff
```

Python 3.11 or newer.

## The store

Row-level security from migration 0001: every table carries `ENABLE`, `FORCE`
and an isolation policy, and every table but `tenants` carries a tenant column
(`tenants` IS the tenant, isolated by `id = current_tenant_id()`). The application connects as a role
created `NOSUPERUSER NOBYPASSRLS`, and the isolation tests assert they are
connected as a role that COULD be stopped before they assert that it was — a
superuser bypasses row-level security unconditionally, and `FORCE` does not stop
one.

```
psql -v ON_ERROR_STOP=1 "$DSN" -f migrations/0001_tenants_agreements_and_rls.sql
psql -v ON_ERROR_STOP=1 "$DSN" -f migrations/0002_billing_run_payments_and_reversals.sql
psql -v ON_ERROR_STOP=1 "$DSN" -f migrations/0003_invoice_lock_attempt_reservation_and_registrations.sql
psql -v ON_ERROR_STOP=1 "$DSN" -f migrations/0004_agreement_garages.sql
psql -v ON_ERROR_STOP=1 "$DSN" -f migrations/0005_agreements_registrar.sql
MONTHLY_BILLING_APP_PASSWORD=... python scripts/ensure-app-role.py "$DSN"
```

**The billing run is idempotent by constraint.** The database holds one invoice
per tenant, garage, payer and period; a second run of the same period issues
nothing, re-prices nothing, and says so per payer. **Paid is derived, never set
by hand**: an invoice is paid when its unreversed payments reach its total, and
that is re-derived after a payment, a reversal, or an owner's adjustment. A
reversal reopens the invoice from its ORIGINAL due date. **Payments, reversals
and charge attempts are append-only by grant** — the application role has no
`UPDATE` and no `DELETE` on them — and the charge log is the truth for retries:
three recorded non-success outcomes refuse the fourth by name, a recorded
payment-method change allows it, and a restart forgets nothing because nothing
lives outside the rows. **A charge is a reservation first**: the attempt row is
committed before the processor is called, the outcome and its card payment land
in one transaction, and a reservation with no outcome is never charged past.
**What the module does not know is never written as an outcome**: an answer
that did not arrive is an `unknown` row, the attempt stays pending, and the
next charge asks again under the same idempotency key for the amount reserved.
**A late processor answer is recorded and a late success is honoured**: an
answer that lands after the operator resolved the attempt is a `late` row
beside the operator's resolution, and a late success the operator did not
record writes the card payment for the amount reserved.
**Every money event takes the invoice's row lock first**, so two events on one
invoice at once are serialised, and **no exception leaves the lock held**. **One
car, one agreement per garage**, and a refusal writes nothing. **Every garage
reference is a composite tenant key.** **An agreement is billed at one home
garage and covers the garages the owner lists** -- coverage is membership of
that set, registrations fan out one row per covered garage under its own
identity rule, and the unpaid, exception and grace reads follow the home; money
never leaves it. **One agreement, one registrar**: an agreement states whether
this module writes its registrations from the version's own list (the default)
or an outside registrar writes them through the door, one car at a time -- and
for the latter the module writes none, the document lists none, and the door
refuses by name an agreement that is not its to write.

## What is not here

No payment processor. No enrolment, and no reconciliation of an outside
registrar's register against this one. No customer portal. No tax. No
multi-garage money — an agreement is billed at one home garage, whatever other
garages of the account the owner lists it as good at. No refund decisions — the
garage owner makes those, and records each one as an exception with an amount, a
name and a date on it.

## Contributing

Contributions are welcome under the CLA. See `CONTRIBUTING.md` and `CLA.md`.

## Licence

AGPL-3.0-or-later. See `LICENSE`.

Built by 72 Knots Method by 72Knots.ai
