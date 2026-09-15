-- 0004 — an agreement is billed at ONE home garage and may COVER others the
-- owner lists. The covered set gets a table; the home stays where it is.
--
-- Run as the database OWNER, after 0003, AND AS A ROLE THAT CAN SEE EVERY ROW --
-- BYPASSRLS or a superuser; the file checks and refuses otherwise, see below.
-- The application never connects as this role. 0001–0003 are not edited: what they created stands, and this
-- migration adds to it.
--
-- WHAT CHANGES AND WHAT DOES NOT. `agreements.garage_id` is, and remains, the
-- HOME garage -- the money key. Every invoice, run, charge, billing day,
-- currency, timezone and grace period is keyed on it, and nothing in this
-- migration widens any of that: `invoices.garage_id`, the period lock
-- `(tenant_id, garage_id, payer_id, period_start_day)` and every money
-- predicate in the module keep reading the home. What is added is the ACCESS
-- fact: at which garages the agreement's vehicles are covered. That set is
-- stated by listing it, the home among them; there is no "everywhere" and no
-- default, because an implicit set cannot be audited and cannot be refused.
--
-- ONE ROW PER VERSION PER COVERED GARAGE, like `agreement_vehicles`: the
-- covered set is part of the versioned document, a version is never edited,
-- and a version that drops a garage is a new row set beside the old one.
--
-- THE BACKFILL IS REAL, NOT "MOOT ON AN EMPTY CLUSTER". Every agreement version
-- that exists when this runs is given a covered set of exactly the one garage
-- it has today -- its home -- read from `agreements.garage_id` before anything
-- else happens. A version whose home garage cannot be placed (no garage row in
-- its own tenant) makes the migration FAIL BY NAME, external id and version
-- included, before a row is written: a migration that silently discarded a row
-- would be wrong in the one place nobody looks afterwards. After the insert the
-- row counts are asserted against each other, never against a literal.
--
-- The garage reference is a COMPOSITE TENANT KEY, `(tenant_id, garage_id)
-- REFERENCES garages (tenant_id, id)`, for the reason 0003 gave when it gave
-- every garage reference one: a foreign-key check runs past row-level security,
-- and a bare `garage_id REFERENCES garages(id)` lets a tenant-A row name tenant
-- B's garage by a raw insert. G37 reads the catalogue and finds this column.
-- G12 reads the catalogue and finds the tenant column, ENABLE, FORCE and the
-- policy.

-- ---------------------------------------------------------------------------
-- THE ROLE THIS RUNS AS MUST SEE EVERY ROW, AND THAT IS CHECKED, NOT ASSUMED.
-- `agreements` is FORCE ROW LEVEL SECURITY (0001) and `current_tenant_id()`
-- is NULL in a migration, so a table owner that is not a superuser and has
-- no BYPASSRLS reads ZERO rows here: a backfill that reads the rows would
-- write nothing, and a count asserted "against each other" would compare 0
-- with 0 and pass. Measured, not supposed. So the first statement refuses by
-- name unless the running role bypasses row security; the counts below are
-- then real. Refused BEFORE the BEGIN so nothing is left half-applied.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
  sees_every_row boolean;
BEGIN
  SELECT rolsuper OR rolbypassrls INTO sees_every_row
  FROM pg_roles WHERE rolname = current_user;
  IF NOT coalesce(sees_every_row, false) THEN
    RAISE EXCEPTION
      'this migration is running as role %, which cannot see every row of a table '
      'under FORCE ROW LEVEL SECURITY (neither SUPERUSER nor BYPASSRLS). Its backfill '
      'reads the agreement rows and its count check compares them, and under this role '
      'both would read zero and the check would pass with nothing done. Run it as the '
      'database owner granted BYPASSRLS, or as a superuser. Nothing was written.',
      current_user;
  END IF;
END
$$;

BEGIN;

CREATE TABLE agreement_garages (
  id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  agreement_id  uuid        NOT NULL REFERENCES agreements(id) ON DELETE CASCADE,
  garage_id     uuid        NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  -- One garage once per version: a set, not a list with repeats.
  CONSTRAINT agreement_garages_one_row_per_garage_per_version
    UNIQUE (tenant_id, agreement_id, garage_id),
  CONSTRAINT agreement_garages_garage_in_tenant
    FOREIGN KEY (tenant_id, garage_id)
    REFERENCES garages (tenant_id, id) ON DELETE RESTRICT
);

CREATE INDEX agreement_garages_tenant_id_idx ON agreement_garages (tenant_id);
-- The access door reads "which agreement versions cover THIS garage".
CREATE INDEX agreement_garages_by_garage_idx
  ON agreement_garages (tenant_id, garage_id, agreement_id);
ALTER TABLE agreement_garages ENABLE ROW LEVEL SECURITY;
ALTER TABLE agreement_garages FORCE  ROW LEVEL SECURITY;
CREATE POLICY agreement_garages_tenant_isolation ON agreement_garages
  USING      (tenant_id = current_tenant_id())
  WITH CHECK (tenant_id = current_tenant_id());

-- The rows are part of a version, and a version is never edited: SELECT and
-- INSERT, as `agreement_vehicles` should have had and as the money history has.
GRANT SELECT, INSERT ON agreement_garages TO monthly_billing_app;

-- ---------------------------------------------------------------------------
-- The backfill. Refuse by name first, then write, then count.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
  orphan     record;
  versions   bigint;
  placed     bigint;
BEGIN
  -- A version whose home garage is not a garage of its own tenant cannot be
  -- given a covered set, and this migration will not guess one for it.
  SELECT a.external_id, a.version, a.tenant_id, a.garage_id INTO orphan
  FROM agreements a
  LEFT JOIN garages g ON g.tenant_id = a.tenant_id AND g.id = a.garage_id
  WHERE g.id IS NULL
  ORDER BY a.external_id, a.version
  LIMIT 1;
  IF FOUND THEN
    RAISE EXCEPTION
      'agreement % version % (tenant %) names home garage %, which is not a garage of '
      'its tenant. This migration gives every existing agreement a covered set of '
      'exactly its home garage, and it cannot place this one. Nothing was written; '
      'fix the row, then run 0004 again.',
      orphan.external_id, orphan.version, orphan.tenant_id, orphan.garage_id;
  END IF;

  SELECT count(*) INTO versions FROM agreements;

  INSERT INTO agreement_garages (tenant_id, agreement_id, garage_id)
  SELECT a.tenant_id, a.id, a.garage_id FROM agreements a;

  SELECT count(*) INTO placed FROM agreement_garages;
  IF placed <> versions THEN
    RAISE EXCEPTION
      'the backfill placed % covered-garage row(s) for % agreement version(s). Every '
      'version gets exactly one, its home; the two counts must agree. Nothing is '
      'committed.',
      placed, versions;
  END IF;
END
$$;

COMMIT;
