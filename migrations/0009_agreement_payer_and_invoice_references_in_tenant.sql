-- 0009 — every agreement, payer and invoice reference is half of a composite
-- tenant key, as every garage reference has been since 0003.
--
-- Run as the database OWNER, after 0008. The application never connects as
-- this role. 0001–0008 are not edited.
--
-- WHY. 0001 made the references to `agreements`, `payers` and `invoices`
-- single-column. A foreign-key check runs past row-level security, so a raw
-- INSERT as the application role inside tenant B could hang a vehicle, a
-- pause, a fee, a mandate, a covered garage, an owner's exception or an
-- invoice line on tenant A's agreement or invoice -- the policy's WITH CHECK
-- reads the new row's own `tenant_id` and nothing it points at. Each table now
-- carries `(tenant_id, <column>)` to the parent's `(tenant_id, id)` beside the
-- old key, with the old key's ON DELETE action. The old keys stay: dropping a
-- constraint is not additive, and two keys on one fact is how 0003 left the
-- garage references too.
--
-- SAFE ON EXISTING ROWS, OR IT REFUSES. Adding a key validates every row
-- already stored (the check runs past the row policy, so it sees them all
-- whoever runs it). A row that already points into another tenant fails the
-- statement, the transaction rolls back, and nothing here has changed: such a
-- row is corruption somebody has to look at, not something to carry forward.
-- A NULL reference -- an exception on an invoice has no agreement, a tax line
-- names none -- is not checked, as the single-column key does not check it.

BEGIN;

-- The pairs the new keys point at. `garages` and `invoices` have theirs (0002,
-- 0003).
ALTER TABLE payers     ADD CONSTRAINT payers_tenant_id_id_key     UNIQUE (tenant_id, id);
ALTER TABLE agreements ADD CONSTRAINT agreements_tenant_id_id_key UNIQUE (tenant_id, id);

ALTER TABLE agreements
  ADD CONSTRAINT agreements_payer_in_tenant
    FOREIGN KEY (tenant_id, payer_id) REFERENCES payers (tenant_id, id) ON DELETE RESTRICT;

ALTER TABLE agreement_vehicles
  ADD CONSTRAINT agreement_vehicles_agreement_in_tenant
    FOREIGN KEY (tenant_id, agreement_id) REFERENCES agreements (tenant_id, id) ON DELETE CASCADE;

ALTER TABLE agreement_pauses
  ADD CONSTRAINT agreement_pauses_agreement_in_tenant
    FOREIGN KEY (tenant_id, agreement_id) REFERENCES agreements (tenant_id, id) ON DELETE CASCADE;

ALTER TABLE agreement_fees
  ADD CONSTRAINT agreement_fees_agreement_in_tenant
    FOREIGN KEY (tenant_id, agreement_id) REFERENCES agreements (tenant_id, id) ON DELETE CASCADE;

ALTER TABLE mandates
  ADD CONSTRAINT mandates_agreement_in_tenant
    FOREIGN KEY (tenant_id, agreement_id) REFERENCES agreements (tenant_id, id) ON DELETE CASCADE;

ALTER TABLE agreement_garages
  ADD CONSTRAINT agreement_garages_agreement_in_tenant
    FOREIGN KEY (tenant_id, agreement_id) REFERENCES agreements (tenant_id, id) ON DELETE CASCADE;

ALTER TABLE invoices
  ADD CONSTRAINT invoices_payer_in_tenant
    FOREIGN KEY (tenant_id, payer_id) REFERENCES payers (tenant_id, id) ON DELETE RESTRICT;

ALTER TABLE invoice_lines
  ADD CONSTRAINT invoice_lines_invoice_in_tenant
    FOREIGN KEY (tenant_id, invoice_id) REFERENCES invoices (tenant_id, id) ON DELETE CASCADE,
  ADD CONSTRAINT invoice_lines_agreement_in_tenant
    FOREIGN KEY (tenant_id, agreement_id) REFERENCES agreements (tenant_id, id) ON DELETE RESTRICT;

ALTER TABLE owner_exceptions
  ADD CONSTRAINT owner_exceptions_agreement_in_tenant
    FOREIGN KEY (tenant_id, agreement_id) REFERENCES agreements (tenant_id, id) ON DELETE CASCADE,
  ADD CONSTRAINT owner_exceptions_invoice_in_tenant
    FOREIGN KEY (tenant_id, invoice_id) REFERENCES invoices (tenant_id, id) ON DELETE CASCADE;

COMMIT;
