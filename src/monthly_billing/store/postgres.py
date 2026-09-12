"""Connecting, setting the tenant, and asking the catalogue what is protected.

**THE APPLICATION NEVER CONNECTS AS THE OWNER, AND NEVER AS A SUPERUSER.** A
superuser bypasses row-level security unconditionally; ``FORCE`` does not stop
one, it only closes the table-owner hole. So an isolation test run as the stock
``postgres`` service user sees every tenant's rows whether the policies exist or
not -- it fails on correct code, and the tempting fix is to weaken the assertion.
``assert_role_cannot_bypass_rls`` is run BEFORE any isolation assertion for
exactly that reason: the test proves it is connected as a role that could be
stopped, before it proves it was stopped.

**THE INVOICE ROW IS THE LOCK, AND IT IS TAKEN IN ONE PLACE.** Every event
that changes an invoice's money -- a payment, a reversal, an owner's adjustment,
both halves of a charge, the resolution of a pending attempt -- runs inside a
transaction that first enters ``locked_invoice``: ``SELECT ... FOR UPDATE`` on
the invoice row, and a ROLLBACK if anything raises inside the block. The outside
pass showed what happens without the lock: two charges reading the same balance
and both collecting it; a payment and a credit each deriving ``paid_at`` from a
snapshot that could not see the other. Under the lock the second writer WAITS,
and derives from committed rows. Its second round showed what happens when a
refusal is raised under the lock with no rollback: the lock stays held on the
caller's connection, and every money event on that invoice from any other
connection waits until the caller happens to end its transaction. So the lock
and the rollback are one context manager and not two habits: no exception --
a refusal, the instrument guard, a driver error -- leaves the lock held. The
contract's sentence about this is derived from the source -- which money events
take the lock, through which name -- and a control removes the lock and requires
the collision back, and another removes the rollback and requires the leak back.

``FOR UPDATE`` needs the UPDATE privilege on ``invoices``, which migration 0002
keeps for the one derived column ``paid_at``. That grant is not to be tightened;
0003's header says so in as many words.

**THE COVERAGE CHECK ASKS THE CATALOGUE, NOT A LIST.** ``tables_without_rls``
reads ``pg_class`` and ``pg_policies``. A check that walked a list of table names
somebody typed could not notice a table added without RLS, which is the entire
failure it exists to catch.

This module imports ``psycopg`` at call time. The engine never imports this file,
so a machine with no driver can still price an agreement and answer an
entitlement question.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

#: Everything the module's own migrations create. Read from the CATALOGUE at
#: runtime; this constant exists only so a test can assert the catalogue and the
#: migration file agree, which is a different question from either alone.
APP_ROLE = "monthly_billing_app"
TENANT_SETTING = "monthly_billing.tenant_id"


def connect(dsn: str) -> Any:
    """A connection, with the driver imported here rather than at module scope."""
    import psycopg  # imported lazily: not a runtime dependency of the engine

    return psycopg.connect(dsn)


def set_tenant(cursor: Any, tenant_id: Any) -> None:
    """Put this TRANSACTION into one tenant's context.

    ``set_config(..., true)`` is transaction-local, so the context cannot leak
    into the next statement on a pooled connection -- which is how one tenant
    ends up reading another's rows through a connection that was reused. The
    corollary binds every caller that commits: a commit ends the transaction and
    the context with it, so the next transaction sets it again or reads nothing.
    """
    cursor.execute(f"SELECT set_config('{TENANT_SETTING}', %s, true)", (str(tenant_id),))


@contextmanager
def tenant(connection: Any, tenant_id: Any) -> Iterator[Any]:
    """A cursor inside one tenant's context, for the current transaction."""
    with connection.cursor() as cursor:
        set_tenant(cursor, tenant_id)
        yield cursor


def lock_invoice(cursor: Any, invoice_uuid: Any) -> None:
    """Take the invoice's row lock for the rest of this transaction.

    Called in ONE place: ``locked_invoice`` below, which is what every money
    event enters. A money event that called this directly would take the lock
    without the rollback that hands it back on an exception, and a guarantee
    reads the source to say that none does. Raises ``LookupError`` if the
    invoice is not visible in this tenant's context, so a wrong tenant is a
    refusal and never a silent no-op.
    """
    cursor.execute("SELECT id FROM invoices WHERE id = %s FOR UPDATE", (invoice_uuid,))
    if cursor.fetchone() is None:
        raise LookupError(f"no invoice with id {invoice_uuid!r} in this tenant's context.")


@contextmanager
def locked_invoice(connection: Any, cursor: Any, invoice_uuid: Any) -> Iterator[None]:
    """The block every money event runs in: the invoice's row lock first, and
    a ROLLBACK on the way out if ANYTHING raised inside.

    Every money event enters this FIRST, before it reads anything it will act
    on: the balance, the reversal check, the committed lines. A reader that
    took the lock after reading would act on a snapshot the lock does not
    protect.

    The rollback is here and not at each raise site because the second outside
    round found the sites that had none: a refusal in ``record_reversal`` and
    the instrument guard's raise inside every locked block. A rollback added by
    hand is a rollback the next raise site forgets; one manager forgets
    nothing. What raised is re-raised unchanged -- the caller sees the same
    refusal, the same guard, the same driver error -- and its connection is
    back in an idle transaction state with no lock held. The caller commits on
    the way out of a block that did not raise.
    """
    try:
        lock_invoice(cursor, invoice_uuid)
        yield
    except BaseException:
        connection.rollback()
        raise


#: Every event that changes an invoice's money, by module and public name. The
#: contract's sentence about serialisation is DERIVED from the source of these:
#: ``events_taking_the_lock`` reads each function's AST and reports whether it
#: (or a helper it calls in its own module) enters ``locked_invoice``. A function
#: added here without the lock changes the published sentence the day it exists.
#: ``record_payment_method_changed`` is deliberately not here: it moves no money
#: and takes the lock only so that its row lands in the log where it happened.
MONEY_EVENTS: tuple[tuple[str, str], ...] = (
    ("monthly_billing.payments", "record_payment"),
    ("monthly_billing.payments", "record_reversal"),
    ("monthly_billing.exceptions_store", "record_invoice_exception"),
    ("monthly_billing.charging", "attempt_charge"),
    ("monthly_billing.charging", "resolve_attempt"),
)


def _calls(node: Any) -> set[str]:
    import ast

    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            if isinstance(sub.func, ast.Name):
                names.add(sub.func.id)
            elif isinstance(sub.func, ast.Attribute):
                names.add(sub.func.attr)
    return names


def _defs(module_name: str) -> dict[str, Any]:
    import ast
    import importlib
    import inspect

    module = importlib.import_module(module_name)
    tree = ast.parse(inspect.getsource(module))
    return {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


def lock_taking_names() -> frozenset[str]:
    """The names a call to which takes the invoice lock, read from THIS module's
    source: ``lock_invoice`` itself and every function here whose body calls
    it (``locked_invoice``). Derived, so a second manager added here would count
    the day it exists and a typed list could not go stale."""
    defs = _defs(__name__)
    return frozenset({"lock_invoice"} | {n for n, d in defs.items() if "lock_invoice" in _calls(d)})


def events_taking_the_lock() -> dict[str, bool]:
    """Which money events take the invoice lock, read from the SOURCE.

    Not a list somebody typed: each function in ``MONEY_EVENTS`` is parsed and
    reported as taking the lock if its body calls one of ``lock_taking_names``
    or calls a same-module function that does, transitively. The contract
    prints the result and a control plants the lock away and requires the prose
    to change.
    """
    takers = lock_taking_names()

    def takes(defs: dict[str, Any], name: str, seen: frozenset[str]) -> bool:
        if name not in defs or name in seen:
            return False
        called = _calls(defs[name])
        if called & takers:
            return True
        return any(takes(defs, c, seen | {name}) for c in called if c in defs)

    out: dict[str, bool] = {}
    for module_name, function_name in MONEY_EVENTS:
        out[function_name] = takes(_defs(module_name), function_name, frozenset())
    return out


def direct_lock_call_sites() -> tuple[tuple[str, str], ...]:
    """Every function in the package whose body calls ``lock_invoice`` DIRECTLY,
    as (module, function), read from the source of every module.

    The guarantee that no exception leaves the lock held rests on every lock
    being taken through ``locked_invoice``; this is the measurement of that.
    The expected answer is exactly one site -- the manager itself.
    """
    import pkgutil

    import monthly_billing

    sites: list[tuple[str, str]] = []
    for info in pkgutil.walk_packages(monthly_billing.__path__, "monthly_billing."):
        for name, node in sorted(_defs(info.name).items()):
            if "lock_invoice" in _calls(node):
                sites.append((info.name, name))
    return tuple(sites)


def assert_role_cannot_bypass_rls(connection: Any) -> None:
    """Refuse to proceed unless this connection could actually be stopped.

    Called at the top of every isolation assertion. Without it, a green isolation
    test proves the connection is a superuser and nothing else.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        row = cursor.fetchone()
    if row is None:
        raise AssertionError("current_user is not in pg_roles, which should be impossible.")
    is_super, bypasses = row
    if is_super or bypasses:
        raise AssertionError(
            f"this connection is {'a superuser' if is_super else 'BYPASSRLS'}, so every "
            "row-level security policy is inert for it and an isolation test would "
            f"pass for the wrong reason. Connect as {APP_ROLE}, which migration 0001 "
            "creates NOSUPERUSER NOBYPASSRLS."
        )


def tables_without_rls(connection: Any) -> list[str]:
    """Every ordinary table in `public` missing ENABLE, FORCE or a policy.

    Derived from the catalogue. A table shipped without protection appears here
    the moment its migration runs, without anybody adding it to a list.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.relname,
                   c.relrowsecurity,
                   c.relforcerowsecurity,
                   (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r'
            ORDER BY c.relname
            """
        )
        rows = cursor.fetchall()

    failures = []
    for name, enabled, forced, policies in rows:
        missing = []
        if not enabled:
            missing.append("ENABLE ROW LEVEL SECURITY")
        if not forced:
            missing.append("FORCE ROW LEVEL SECURITY")
        if not policies:
            missing.append("a policy")
        if missing:
            failures.append(f"{name}: missing {', '.join(missing)}")
    return failures


def tables_without_tenant_column(connection: Any) -> list[str]:
    """Every ordinary table in `public` with no ``tenant_id``, except ``tenants``.

    A policy on a table with no tenant column can only ever be a policy that
    compares something else, and "something else" is where a cross-tenant read
    lives. ``tenants`` is the one exception and it is named, not inferred.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.relname
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relkind = 'r'
              AND c.relname <> 'tenants'
              AND NOT EXISTS (
                    SELECT 1 FROM pg_attribute a
                    WHERE a.attrelid = c.oid
                      AND a.attname = 'tenant_id'
                      AND a.attnum > 0
                      AND NOT a.attisdropped)
            ORDER BY c.relname
            """
        )
        return [row[0] for row in cursor.fetchall()]
