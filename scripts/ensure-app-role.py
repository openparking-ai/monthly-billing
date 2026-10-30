#!/usr/bin/env python3
"""Give the application role a login and a password from the environment.

Migration 0001 creates `monthly_billing_app` NOLOGIN NOSUPERUSER NOBYPASSRLS, so
the SCHEMA carries the guarantee that the application cannot bypass row-level
security. This adds the login, and it deliberately does NOT touch the three
attributes that matter: a script that could hand the app role BYPASSRLS would be
a script that could make every isolation test pass for the wrong reason.

    MONTHLY_BILLING_APP_PASSWORD=... python scripts/ensure-app-role.py "<dsn>"

Run as the database owner. The password is read from the environment and never
from an argument, because arguments are visible in `ps` to every user on the box.
It never reaches the server either: the script computes the SCRAM-SHA-256
verifier PostgreSQL stores (``scram_verifier``) and sends that, so no statement
-- and no server log that keeps statements -- carries the password itself.
The verifier is put into the statement by ``psycopg.sql`` composition -- quoted
as a literal, never interpolated as a string -- because ``ALTER ROLE`` is a
utility statement and PostgreSQL binds no parameters into those: the first cut
passed the password as a bind parameter, so the README's install step died on
``syntax error at or near "$1"``, and nothing in the tree had ever run the
script. A test now runs it against the test cluster
(``tests/test_g12_rls_from_migration_0001.py``), and it prints nothing that
contains the password.
"""

from __future__ import annotations

import os
import sys

#: The role this script gives a login to.
ROLE = "monthly_billing_app"


def scram_verifier(connection, password: str, role: str = ROLE) -> str:
    """The password as PostgreSQL stores it: a salted, iterated SCRAM-SHA-256
    verifier, computed on THIS machine by libpq's own ``PQencryptPasswordConn``
    (the password's SASLprep included), so the statement that sets it carries
    the verifier and never the password. PostgreSQL stores a verifier it is
    handed as it is. Anything but a SCRAM-SHA-256 verifier is refused here,
    before a statement is sent."""
    verifier = connection.pgconn.encrypt_password(
        password.encode("utf-8"), role.encode("utf-8"), b"scram-sha-256"
    ).decode("ascii")
    if not verifier.startswith("SCRAM-SHA-256$"):
        raise RuntimeError("libpq did not compute a SCRAM-SHA-256 verifier; nothing was sent.")
    return verifier


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    password = os.environ.get("MONTHLY_BILLING_APP_PASSWORD")
    if not password:
        print(
            "MONTHLY_BILLING_APP_PASSWORD is not set. It is read from the "
            "environment rather than from an argument, because arguments are "
            "visible in `ps` to every user on the machine."
        )
        return 2

    import psycopg
    from psycopg import sql

    with psycopg.connect(argv[0]) as connection:
        connection.autocommit = True
        verifier = scram_verifier(connection, password)
        with connection.cursor() as cursor:
            # ALTER, never CREATE: the role's attributes are the migration's, and
            # this script may not be a route to changing them. A quoted LITERAL,
            # not a bind parameter: utility statements take none.
            cursor.execute(
                sql.SQL("ALTER ROLE monthly_billing_app LOGIN PASSWORD {}").format(
                    sql.Literal(verifier)
                )
            )
            cursor.execute(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = "
                "'monthly_billing_app'"
            )
            is_super, bypasses = cursor.fetchone()

    if is_super or bypasses:
        print(
            "monthly_billing_app can bypass row-level security. Something has "
            "granted it SUPERUSER or BYPASSRLS since migration 0001 ran, and "
            "every isolation policy is inert for it."
        )
        return 1
    print("monthly_billing_app can log in, and still cannot bypass row-level security.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
