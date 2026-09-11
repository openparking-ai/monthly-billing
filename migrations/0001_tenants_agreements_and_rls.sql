-- 0001 — tenants, the module's tables, and the row-level security foundation
-- every later migration inherits.
--
-- Run as the database OWNER. The application never connects as this role.
--
-- The shape of the tenant column, the policies and the app role is the one a
-- sibling module in this project already ships, copied rather than reinvented:
-- two shapes would mean two sets of failure modes, and the second one is always
-- the one nobody tested.

BEGIN;

-- ---------------------------------------------------------------------------
-- The application role.
--
-- NOSUPERUSER and NOBYPASSRLS are the whole point. A superuser bypasses
-- row-level security unconditionally -- FORCE ROW LEVEL SECURITY does not stop
-- one, it only closes the table-owner hole. If the application (or a test)
-- connects as a superuser, every policy below is inert and every isolation test
-- passes for the wrong reason.
--
-- Created NOLOGIN here so the schema carries the guarantee; scripts/ensure-app-role.py
-- adds LOGIN and a password from the environment.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'monthly_billing_app') THEN
    CREATE ROLE monthly_billing_app NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  ELSE
    ALTER ROLE monthly_billing_app NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- Tenant context.
--
-- Unset resolves to NULL, and `tenant_id = NULL` is NULL, not true -- so a
-- connection that forgets to set the context sees nothing. Fail closed.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS uuid
  LANGUAGE sql STABLE
  AS $$ SELECT NULLIF(current_setting('monthly_billing.tenant_id', true), '')::uuid $$;

CREATE TABLE tenants (
  id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  slug        text        NOT NULL UNIQUE,
  name        text        NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE  ROW LEVEL SECURITY;

CREATE POLICY tenants_self_only ON tenants
  USING      (id = current_tenant_id())
  WITH CHECK (id = current_tenant_id());

-- ---------------------------------------------------------------------------
-- garages — the billing day, the timezone, the currency, the grace period and
-- the identity rule. Every one of them decides money or coverage, and every one
-- is NOT NULL: this module refuses rather than defaulting, and a nullable
-- column would be a default of NULL wearing a constraint's clothes.
-- ---------------------------------------------------------------------------
CREATE TABLE garages (
  id                    uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id             uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  external_id           text        NOT NULL,
  timezone              text        NOT NULL,
  currency              char(3)     NOT NULL,
  billing_day           text        NOT NULL
                        CHECK (billing_day IN ('first_day_of_month',
                                               'last_day_of_month',
                                               'nth_day_of_month')),
  -- 1..28 only. 29, 30 and 31 do not exist in every month; a garage that means
  -- the end of the month says so with `last_day_of_month`, which always exists.
  billing_day_of_month  smallint    CHECK (billing_day_of_month BETWEEN 1 AND 28),
  payment_grace_days    smallint    NOT NULL CHECK (payment_grace_days >= 0),
  identity_rule         text        NOT NULL
                        CHECK (identity_rule IN ('exact', 'folded_alphanumeric')),
  created_at            timestamptz NOT NULL DEFAULT now(),

  -- The two fields have to agree, and the database is where that is enforced:
  -- an application that forgot would write a garage with no billing day at all.
  CONSTRAINT garages_nth_day_is_stated_iff_used CHECK (
    (billing_day =  'nth_day_of_month' AND billing_day_of_month IS NOT NULL) OR
    (billing_day <> 'nth_day_of_month' AND billing_day_of_month IS NULL)
  ),
  UNIQUE (tenant_id, external_id)
);

CREATE INDEX garages_tenant_id_idx ON garages (tenant_id);
ALTER TABLE garages ENABLE ROW LEVEL SECURITY;
ALTER TABLE garages FORCE  ROW LEVEL SECURITY;
CREATE POLICY garages_tenant_isolation ON garages
  USING      (tenant_id = current_tenant_id())
  WITH CHECK (tenant_id = current_tenant_id());

-- ---------------------------------------------------------------------------
-- payers — a separate party from the parker, FROM THE FIRST MIGRATION.
--
-- A company pays one invoice for many parkers. Retrofitting that later would
-- rewrite every table below, which is the whole reason it is here in M1 rather
-- than deferred to the round that needs it.
-- ---------------------------------------------------------------------------
CREATE TABLE payers (
  id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  external_id text        NOT NULL,
  name        text        NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, external_id)
);

CREATE INDEX payers_tenant_id_idx ON payers (tenant_id);
ALTER TABLE payers ENABLE ROW LEVEL SECURITY;
ALTER TABLE payers FORCE  ROW LEVEL SECURITY;
CREATE POLICY payers_tenant_isolation ON payers
  USING      (tenant_id = current_tenant_id())
  WITH CHECK (tenant_id = current_tenant_id());

-- ---------------------------------------------------------------------------
-- agreements — versioned, and a version is never edited.
--
-- A price change writes a new row with the next version. That is what makes
-- "a paid period is paid" enforceable rather than merely intended: the invoice
-- line records the version it was computed from, and the version it was
-- computed from still exists, unchanged, to be compared against.
--
-- The money column is bigint of MINOR UNITS. Not numeric, not money: one money
-- type all the way down, and the type system carrying the decision.
-- ---------------------------------------------------------------------------
CREATE TABLE agreements (
  id                       uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id                uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  external_id              text        NOT NULL,
  version                  integer     NOT NULL CHECK (version >= 1),
  garage_id                uuid        NOT NULL REFERENCES garages(id) ON DELETE RESTRICT,
  payer_id                 uuid        NOT NULL REFERENCES payers(id) ON DELETE RESTRICT,
  spots                    integer     NOT NULL CHECK (spots >= 1),
  monthly_price_minor      bigint      NOT NULL CHECK (monthly_price_minor >= 0),
  start_day                date        NOT NULL,
  status                   text        NOT NULL DEFAULT 'active'
                           CHECK (status IN ('active', 'cancelled')),
  cancelled_effective_day  date,
  access_entry_from        time,
  access_exit_by           time,
  created_at               timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT agreements_cancellation_has_a_date CHECK (
    (status = 'cancelled' AND cancelled_effective_day IS NOT NULL) OR
    (status = 'active'    AND cancelled_effective_day IS NULL)
  ),
  -- Both halves or neither. One half alone is silently unbounded on the other,
  -- and "unbounded" is a thing an operator should have to write down.
  CONSTRAINT agreements_access_hours_are_both_or_neither CHECK (
    (access_entry_from IS NULL) = (access_exit_by IS NULL)
  ),
  UNIQUE (tenant_id, external_id, version)
);

CREATE INDEX agreements_tenant_id_idx ON agreements (tenant_id);
CREATE INDEX agreements_garage_idx    ON agreements (tenant_id, garage_id);
CREATE INDEX agreements_payer_idx     ON agreements (tenant_id, payer_id);
ALTER TABLE agreements ENABLE ROW LEVEL SECURITY;
ALTER TABLE agreements FORCE  ROW LEVEL SECURITY;
CREATE POLICY agreements_tenant_isolation ON agreements
  USING      (tenant_id = current_tenant_id())
  WITH CHECK (tenant_id = current_tenant_id());

-- ---------------------------------------------------------------------------
-- agreement_vehicles — the list, whose size is INDEPENDENT of `spots`.
--
-- Twenty registered against ten bought is a legal agreement. There is
-- deliberately no constraint tying the row count to `spots`, and this comment is
-- here so nobody adds one believing they are fixing an oversight.
--
-- `identity_normalised` is what the unique index is on: the garage states how
-- identities compare, and two rows that compare equal under that rule are the
-- same vehicle enrolled twice.
-- ---------------------------------------------------------------------------
CREATE TABLE agreement_vehicles (
  id                   uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id            uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  agreement_id         uuid        NOT NULL REFERENCES agreements(id) ON DELETE CASCADE,
  identity             text        NOT NULL CHECK (length(btrim(identity)) > 0),
  identity_normalised  text        NOT NULL CHECK (length(btrim(identity_normalised)) > 0),
  created_at           timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, agreement_id, identity_normalised)
);

CREATE INDEX agreement_vehicles_tenant_id_idx ON agreement_vehicles (tenant_id);
CREATE INDEX agreement_vehicles_lookup_idx
  ON agreement_vehicles (tenant_id, identity_normalised);
ALTER TABLE agreement_vehicles ENABLE ROW LEVEL SECURITY;
ALTER TABLE agreement_vehicles FORCE  ROW LEVEL SECURITY;
CREATE POLICY agreement_vehicles_tenant_isolation ON agreement_vehicles
  USING      (tenant_id = current_tenant_id())
  WITH CHECK (tenant_id = current_tenant_id());

-- ---------------------------------------------------------------------------
-- agreement_pauses — nothing billed, nothing covered. Half-open.
-- ---------------------------------------------------------------------------
CREATE TABLE agreement_pauses (
  id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  agreement_id  uuid        NOT NULL REFERENCES agreements(id) ON DELETE CASCADE,
  from_day      date        NOT NULL,
  until_day     date        NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT agreement_pauses_are_half_open CHECK (until_day > from_day)
);

CREATE INDEX agreement_pauses_tenant_id_idx ON agreement_pauses (tenant_id);
ALTER TABLE agreement_pauses ENABLE ROW LEVEL SECURITY;
ALTER TABLE agreement_pauses FORCE  ROW LEVEL SECURITY;
CREATE POLICY agreement_pauses_tenant_isolation ON agreement_pauses
  USING      (tenant_id = current_tenant_id())
  WITH CHECK (tenant_id = current_tenant_id());

-- ---------------------------------------------------------------------------
-- agreement_fees — the owner's free-form lines. A label, an amount, a cadence.
-- A guaranteed or reserved space is one of these, not a special mechanism.
-- ---------------------------------------------------------------------------
CREATE TABLE agreement_fees (
  id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  agreement_id    uuid        NOT NULL REFERENCES agreements(id) ON DELETE CASCADE,
  label           text        NOT NULL CHECK (length(btrim(label)) > 0),
  amount_minor    bigint      NOT NULL CHECK (amount_minor >= 0),
  cadence         text        NOT NULL CHECK (cadence IN ('one_time', 'recurring')),
  effective_from  date        NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, agreement_id, label)
);

CREATE INDEX agreement_fees_tenant_id_idx ON agreement_fees (tenant_id);
ALTER TABLE agreement_fees ENABLE ROW LEVEL SECURITY;
ALTER TABLE agreement_fees FORCE  ROW LEVEL SECURITY;
CREATE POLICY agreement_fees_tenant_isolation ON agreement_fees
  USING      (tenant_id = current_tenant_id())
  WITH CHECK (tenant_id = current_tenant_id());

-- ---------------------------------------------------------------------------
-- mandates — who agreed, when, and to what.
--
-- ⛔ THERE IS NO COLUMN HERE THAT COULD HOLD A CARD NUMBER, A BANK ACCOUNT
-- NUMBER, OR A TOKEN THAT COULD SUBSTITUTE FOR ONE, AND THERE NEVER WILL BE.
-- The columns record the TERMS somebody agreed to, in the words they were
-- shown. Whatever can actually move money lives with the processor, in the
-- module that talks to one, and that module is not this one.
--
-- The application refuses an instrument-shaped value on the way into every text
-- column here. The absence of a column for one is the structural half of that
-- rule; the refusal is the half that catches a paste into `terms_shown`.
-- ---------------------------------------------------------------------------
CREATE TABLE mandates (
  id                   uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id            uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  agreement_id         uuid        NOT NULL REFERENCES agreements(id) ON DELETE CASCADE,
  agreed_by            text        NOT NULL CHECK (length(btrim(agreed_by)) > 0),
  agreed_at            timestamptz NOT NULL,
  terms_shown          text        NOT NULL CHECK (length(btrim(terms_shown)) > 0),
  frequency_shown      text        NOT NULL CHECK (length(btrim(frequency_shown)) > 0),
  amount_basis_shown   text        NOT NULL CHECK (length(btrim(amount_basis_shown)) > 0),
  cancellation_shown   text        NOT NULL CHECK (length(btrim(cancellation_shown)) > 0),
  created_at           timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, agreement_id)
);

CREATE INDEX mandates_tenant_id_idx ON mandates (tenant_id);
ALTER TABLE mandates ENABLE ROW LEVEL SECURITY;
ALTER TABLE mandates FORCE  ROW LEVEL SECURITY;
CREATE POLICY mandates_tenant_isolation ON mandates
  USING      (tenant_id = current_tenant_id())
  WITH CHECK (tenant_id = current_tenant_id());

-- ---------------------------------------------------------------------------
-- invoices and invoice_lines — one garage, one period, one currency.
--
-- `agreement_version` on the LINE is what makes a paid period unchangeable by a
-- price change: the line records which version priced it, and that version is
-- still there to be read.
-- ---------------------------------------------------------------------------
CREATE TABLE invoices (
  id                 uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  reference          text        NOT NULL,
  payer_id           uuid        NOT NULL REFERENCES payers(id) ON DELETE RESTRICT,
  garage_id          uuid        NOT NULL REFERENCES garages(id) ON DELETE RESTRICT,
  currency           char(3)     NOT NULL,
  period_start_day   date        NOT NULL,
  issued_at          timestamptz NOT NULL DEFAULT now(),
  due_at             timestamptz NOT NULL,
  paid_at            timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, reference)
);

CREATE INDEX invoices_tenant_id_idx ON invoices (tenant_id);
CREATE INDEX invoices_unpaid_idx ON invoices (tenant_id, payer_id) WHERE paid_at IS NULL;
ALTER TABLE invoices ENABLE ROW LEVEL SECURITY;
ALTER TABLE invoices FORCE  ROW LEVEL SECURITY;
CREATE POLICY invoices_tenant_isolation ON invoices
  USING      (tenant_id = current_tenant_id())
  WITH CHECK (tenant_id = current_tenant_id());

CREATE TABLE invoice_lines (
  id                 uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  invoice_id         uuid        NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
  kind               text        NOT NULL
                     CHECK (kind IN ('partial_period', 'full_period', 'recurring_fee',
                                     'one_time_fee', 'exception_adjustment')),
  label              text        NOT NULL CHECK (length(btrim(label)) > 0),
  amount_minor       bigint      NOT NULL,
  agreement_id       uuid        NOT NULL REFERENCES agreements(id) ON DELETE RESTRICT,
  agreement_version  integer     NOT NULL CHECK (agreement_version >= 1),
  period_start_day   date        NOT NULL,
  period_end_day     date        NOT NULL,
  exception_id       uuid,
  created_at         timestamptz NOT NULL DEFAULT now(),

  -- A figure nobody can account for may not appear on an invoice: an adjustment
  -- line names the exception that made it, and no other kind of line may.
  CONSTRAINT invoice_lines_adjustments_name_their_exception CHECK (
    (kind =  'exception_adjustment' AND exception_id IS NOT NULL) OR
    (kind <> 'exception_adjustment' AND exception_id IS NULL)
  ),
  CONSTRAINT invoice_lines_periods_are_half_open CHECK (period_end_day > period_start_day)
);

CREATE INDEX invoice_lines_tenant_id_idx ON invoice_lines (tenant_id);
CREATE INDEX invoice_lines_invoice_idx ON invoice_lines (tenant_id, invoice_id);
ALTER TABLE invoice_lines ENABLE ROW LEVEL SECURITY;
ALTER TABLE invoice_lines FORCE  ROW LEVEL SECURITY;
CREATE POLICY invoice_lines_tenant_isolation ON invoice_lines
  USING      (tenant_id = current_tenant_id())
  WITH CHECK (tenant_id = current_tenant_id());

-- ---------------------------------------------------------------------------
-- owner_exceptions — who, when, what changed, and why.
--
-- ⛔ `note` AND `amount_minor` ARE DIFFERENT COLUMNS AND THE AMOUNT IS TYPED.
-- A note explains; an amount changes money. Nothing joins them, nothing derives
-- one from the other, and no query in this module reads `note`.
--
-- The CHECK below is the structural half of "an exception that changes money
-- carries an amount": the kinds that move money may not be recorded without one,
-- and the kinds that do not may not carry one.
-- ---------------------------------------------------------------------------
CREATE TABLE owner_exceptions (
  id                 uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  agreement_id       uuid        REFERENCES agreements(id) ON DELETE CASCADE,
  invoice_id         uuid        REFERENCES invoices(id) ON DELETE CASCADE,
  kind               text        NOT NULL
                     CHECK (kind IN ('extend_grace', 'waive_fee', 'credit',
                                     'refund', 'block', 'unblock')),
  recorded_by        text        NOT NULL CHECK (length(btrim(recorded_by)) > 0),
  recorded_at        timestamptz NOT NULL,
  note               text        NOT NULL DEFAULT '',
  amount_minor       bigint,
  extra_grace_days   smallint    CHECK (extra_grace_days >= 1),
  created_at         timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT owner_exceptions_attach_to_exactly_one CHECK (
    (agreement_id IS NOT NULL) <> (invoice_id IS NOT NULL)
  ),
  CONSTRAINT owner_exceptions_money_kinds_carry_an_amount CHECK (
    (kind IN ('waive_fee', 'credit', 'refund') AND amount_minor IS NOT NULL) OR
    (kind IN ('extend_grace', 'block', 'unblock') AND amount_minor IS NULL)
  ),
  CONSTRAINT owner_exceptions_grace_days_iff_extending CHECK (
    (kind =  'extend_grace' AND extra_grace_days IS NOT NULL) OR
    (kind <> 'extend_grace' AND extra_grace_days IS NULL)
  )
);

CREATE INDEX owner_exceptions_tenant_id_idx ON owner_exceptions (tenant_id);
ALTER TABLE owner_exceptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE owner_exceptions FORCE  ROW LEVEL SECURITY;
CREATE POLICY owner_exceptions_tenant_isolation ON owner_exceptions
  USING      (tenant_id = current_tenant_id())
  WITH CHECK (tenant_id = current_tenant_id());

-- ---------------------------------------------------------------------------
-- charge_attempts — the retry state. Three, and the fourth needs a new payment
-- method first.
--
-- `detail` is what the processor said. It is a text column somebody's error
-- string lands in, which makes it exactly the column an instrument arrives in by
-- accident, so the application refuses one on the way in.
-- ---------------------------------------------------------------------------
CREATE TABLE charge_attempts (
  id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  invoice_id   uuid        NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
  attempts     smallint    NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  last_outcome text        CHECK (last_outcome IN ('success', 'decline', 'error')),
  last_detail  text        NOT NULL DEFAULT '',
  updated_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, invoice_id)
);

CREATE INDEX charge_attempts_tenant_id_idx ON charge_attempts (tenant_id);
ALTER TABLE charge_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE charge_attempts FORCE  ROW LEVEL SECURITY;
CREATE POLICY charge_attempts_tenant_isolation ON charge_attempts
  USING      (tenant_id = current_tenant_id())
  WITH CHECK (tenant_id = current_tenant_id());

-- ---------------------------------------------------------------------------
-- Grants. The app role gets DML and nothing structural.
-- ---------------------------------------------------------------------------
GRANT USAGE ON SCHEMA public TO monthly_billing_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON
  tenants, garages, payers, agreements, agreement_vehicles, agreement_pauses,
  agreement_fees, mandates, invoices, invoice_lines, owner_exceptions,
  charge_attempts
TO monthly_billing_app;
GRANT EXECUTE ON FUNCTION current_tenant_id() TO monthly_billing_app;

COMMIT;
