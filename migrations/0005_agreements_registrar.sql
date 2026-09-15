-- 0005 — an agreement states WHO WRITES ITS REGISTRATIONS: this module, or an
-- outside registrar. One column on the version row; the default is this module.
--
-- Run as the database OWNER, after 0004, AND AS A ROLE THAT CAN SEE EVERY ROW --
-- BYPASSRLS or a superuser; the file checks and refuses otherwise, see below.
-- The application never connects as this role. 0001–0004 are not edited: what they created stands, and this
-- migration adds to it.
--
-- WHAT CHANGES AND WHAT DOES NOT. `vehicle_registrations` (0003) is, and
-- remains, the garage-wide fact of which agreement a vehicle identity belongs
-- to, current state, with SELECT, INSERT, UPDATE and DELETE already granted to
-- the application role -- the registration door needs no new grant and this
-- migration adds none. What is added is the STATEMENT of who keeps those rows
-- for an agreement: `agreements.registrar`, `'this_module'` (the module writes
-- them from the version's own vehicle list, as it always has) or `'outside'`
-- (an outside registrar writes them one identity at a time through the door,
-- and the module writes none). Stated on the row, never inferred: a mode
-- nobody wrote down cannot be audited and cannot be refused.
--
-- ONE COLUMN PER VERSION ROW, like `status`: the registrar is part of the
-- versioned document, a version is never edited, and a version that hands the
-- register over is a new row beside the old one.
--
-- THE BACKFILL IS REAL, NOT "MOOT ON AN EMPTY CLUSTER". Every agreement version
-- that exists when this runs is given the registrar it has today -- this
-- module, which wrote every registration row that exists -- by the column's
-- default at ADD time. Then the rows are COUNTED against each other, never
-- against a literal: every version row must read `'this_module'`, and a
-- default that said otherwise would fail the migration here rather than leave
-- a stored agreement whose registrations nothing writes. No version is left
-- unstated: the column is NOT NULL.
--
-- The CHECK is the database's backstop for the enum in agreement.py; the
-- module refuses an unknown value by name before this constraint has to.

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

ALTER TABLE agreements
  ADD COLUMN registrar text NOT NULL DEFAULT 'this_module'
  CONSTRAINT agreements_registrar_is_stated
    CHECK (registrar IN ('this_module', 'outside'));

-- ---------------------------------------------------------------------------
-- The backfill. Count, never assume.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
  versions  bigint;
  stated    bigint;
BEGIN
  SELECT count(*) INTO versions FROM agreements;
  SELECT count(*) INTO stated   FROM agreements WHERE registrar = 'this_module';
  IF stated <> versions THEN
    RAISE EXCEPTION
      'the backfill left % of % agreement version(s) reading a registrar other than '
      'this_module. Every version that exists when 0005 runs had its registrations '
      'written by this module, and every one of them must say so; the two counts must '
      'agree. Nothing is committed.',
      versions - stated, versions;
  END IF;
END
$$;

COMMIT;
