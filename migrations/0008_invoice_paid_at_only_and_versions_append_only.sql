-- 0008 — an issued invoice takes an UPDATE of `paid_at` and of no other
-- column, and a stored agreement version is never edited or removed.
--
-- Run as the database OWNER, after 0007. The application never connects as
-- this role. 0001–0007 are not edited. Grants only: no row is read or changed,
-- so this is safe on a populated store and needs no BYPASSRLS check.
--
-- INVOICES. 0002 kept TABLE-WIDE UPDATE on `invoices` because `paid_at` is
-- derived and written, and 0003 says that grant must not be tightened because
-- the invoice lock (`SELECT ... FOR UPDATE`) needs it. FOR UPDATE needs the
-- UPDATE privilege on AT LEAST ONE COLUMN, not on the table, so the lock and
-- the derivation both stand on a column grant of `paid_at` alone -- and the
-- payer, the period, the due instant and the reference of an issued invoice
-- stop being writable by the application. 0006's trigger on the tax position
-- stays as a second lock on those three columns.
--
-- AGREEMENT VERSIONS. 0001 granted the application UPDATE and DELETE on every
-- table a version is made of, and on its mandate. A new version is a new row
-- (`store_agreement`), and an invoice line names the version that priced it,
-- so the version an invoice was billed at, and the terms a mandate was agreed
-- in, must stay as stored. Nothing in the module updates or deletes them.
-- `agreement_garages` (0004) was never granted either.

BEGIN;

REVOKE UPDATE ON invoices FROM monthly_billing_app;
GRANT UPDATE (paid_at) ON invoices TO monthly_billing_app;

REVOKE UPDATE, DELETE ON
  agreements, agreement_vehicles, agreement_pauses, agreement_fees, mandates
FROM monthly_billing_app;

COMMIT;
