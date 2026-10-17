-- 0007 — the folded identity rule reads the letter O as the digit 0 and the
-- letter I as the digit 1, and every identity already STORED in the folded
-- form is re-keyed to it.
--
-- Run as the database OWNER, after 0006, AND AS A ROLE THAT CAN SEE EVERY ROW --
-- BYPASSRLS or a superuser; the file checks and refuses otherwise, exactly as
-- 0005 does and for 0005's reason. The application never connects as this
-- role. 0001–0006 are not edited.
--
-- WHY. The plate reader's alphabet has no I and no O: it reads a plate
-- registered as GO4IT as G041T. `folded_alphanumeric` ignored only case and
-- punctuation, so that holder never matched and paid as a transient on every
-- visit. The rule now folds o to 0 and i to 1 after case folding
-- (garage.normalise_identity), on both sides of the comparison -- and a
-- comparison is only that if the STORED side was folded the same way, so the
-- forms already stored are re-keyed here. `exact` garages are untouched: their
-- identities are opaque tokens, compared byte for byte.
--
-- WHAT IS RE-KEYED, AND UNDER WHICH GARAGE'S RULE.
--   * `vehicle_registrations.identity_normalised` -- under the rule of the
--     row's own garage, the garage that compares it.
--   * `agreement_vehicles.identity_normalised` -- under the rule of the
--     version's HOME garage, which is the rule `store_agreement` writes it under.
-- A folded form is already lower case with no punctuation, so the new form is
-- `translate(form, 'oi', '01')` -- exactly what the module now computes from
-- the identity as typed.
--
-- A COLLISION IS REFUSED BY NAME, BEFORE ANY ROW CHANGES. Two rows that were
-- different forms and fold to one -- GO4IT and G04IT registered at one garage,
-- or listed on one version -- cannot both stand under the UNIQUE each table
-- carries, and picking one would decide which car is covered. The file names
-- the first such pair and writes nothing; whoever owns those registrations
-- decides, and the file is run again. (No transient collision is possible
-- while the rows change: a new form contains no o or i, so it can only equal
-- a form that does not change -- which is the collision refused above.)
--
-- Count, never assume: after the re-key no folded form may still hold an o or
-- an i, or the file fails and nothing is committed.

-- ---------------------------------------------------------------------------
-- THE ROLE THIS RUNS AS MUST SEE EVERY ROW. 0005's guard, for 0005's reason:
-- under a role that cannot, every read below sees zero rows, the collision
-- check finds none, the re-key changes nothing, and the counts agree, zero
-- with zero. Refused BEFORE the BEGIN so nothing is left half-applied.
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
      'under FORCE ROW LEVEL SECURITY (neither SUPERUSER nor BYPASSRLS). It re-keys '
      'the stored vehicle identities and checks them for collisions, and under this '
      'role it would read zero rows and re-key none. Run it as the database owner '
      'granted BYPASSRLS, or as a superuser. Nothing was written.',
      current_user;
  END IF;
END
$$;

BEGIN;

-- ---------------------------------------------------------------------------
-- The collisions, both tables, before anything changes.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
  clash record;
BEGIN
  SELECT r.tenant_id, g.external_id AS garage,
         translate(r.identity_normalised, 'oi', '01') AS folded,
         string_agg(r.identity_normalised || ' (' || r.agreement_external_id || ')', ', '
                    ORDER BY r.identity_normalised) AS forms
    INTO clash
    FROM vehicle_registrations r
    JOIN garages g ON g.tenant_id = r.tenant_id AND g.id = r.garage_id
   WHERE g.identity_rule = 'folded_alphanumeric'
   GROUP BY r.tenant_id, g.external_id, translate(r.identity_normalised, 'oi', '01')
  HAVING count(*) > 1
   ORDER BY 1, 2, 3
   LIMIT 1;
  IF FOUND THEN
    RAISE EXCEPTION
      'vehicle_registrations: at garage % of tenant %, the registered forms % all '
      'become % once O is read as 0 and I as 1. One car, one agreement per garage: '
      'the re-key cannot keep both, and choosing would decide which car is covered. '
      'Release one of them and run this migration again. Nothing was written.',
      clash.garage, clash.tenant_id, clash.forms, clash.folded;
  END IF;

  SELECT v.tenant_id, a.external_id AS agreement, a.version,
         translate(v.identity_normalised, 'oi', '01') AS folded,
         string_agg(v.identity, ', ' ORDER BY v.identity) AS forms
    INTO clash
    FROM agreement_vehicles v
    JOIN agreements a ON a.id = v.agreement_id
    JOIN garages g ON g.tenant_id = a.tenant_id AND g.id = a.garage_id
   WHERE g.identity_rule = 'folded_alphanumeric'
   GROUP BY v.tenant_id, a.external_id, a.version, translate(v.identity_normalised, 'oi', '01')
  HAVING count(*) > 1
   ORDER BY 1, 2, 3, 4
   LIMIT 1;
  IF FOUND THEN
    RAISE EXCEPTION
      'agreement_vehicles: agreement % version % of tenant % lists %, which all '
      'become % once O is read as 0 and I as 1. A version cannot list one car twice, '
      'and choosing would decide which car it covers. Store a version that lists '
      'one of them and run this migration again. Nothing was written.',
      clash.agreement, clash.version, clash.tenant_id, clash.forms, clash.folded;
  END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- The re-key.
-- ---------------------------------------------------------------------------
UPDATE vehicle_registrations r
   SET identity_normalised = translate(r.identity_normalised, 'oi', '01')
  FROM garages g
 WHERE g.tenant_id = r.tenant_id AND g.id = r.garage_id
   AND g.identity_rule = 'folded_alphanumeric'
   AND r.identity_normalised ~ '[oi]';

UPDATE agreement_vehicles v
   SET identity_normalised = translate(v.identity_normalised, 'oi', '01')
  FROM agreements a, garages g
 WHERE a.id = v.agreement_id
   AND g.tenant_id = a.tenant_id AND g.id = a.garage_id
   AND g.identity_rule = 'folded_alphanumeric'
   AND v.identity_normalised ~ '[oi]';

-- ---------------------------------------------------------------------------
-- Count, never assume.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
  left_behind bigint;
BEGIN
  SELECT count(*) INTO left_behind
    FROM vehicle_registrations r
    JOIN garages g ON g.tenant_id = r.tenant_id AND g.id = r.garage_id
   WHERE g.identity_rule = 'folded_alphanumeric' AND r.identity_normalised ~ '[oi]';
  SELECT left_behind + count(*) INTO left_behind
    FROM agreement_vehicles v
    JOIN agreements a ON a.id = v.agreement_id
    JOIN garages g ON g.tenant_id = a.tenant_id AND g.id = a.garage_id
   WHERE g.identity_rule = 'folded_alphanumeric' AND v.identity_normalised ~ '[oi]';
  IF left_behind <> 0 THEN
    RAISE EXCEPTION
      'the re-key left % folded identity form(s) still holding an o or an i. Every '
      'folded form must read O as 0 and I as 1 after this migration: a stored form '
      'the module no longer produces is a car that never matches. Nothing is committed.',
      left_behind;
  END IF;
END
$$;

COMMIT;
