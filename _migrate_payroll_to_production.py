"""One-shot migration: local Payroll Engine data → Railway production.

Reads every row from the 8 ``payroll_engine_*`` tables on the LOCAL
PostgreSQL container and bulk-inserts them into the PRODUCTION Railway
database, in FK-dependency order. ``auth_user`` foreign keys are NULLed
on the way in (local user IDs do not exist on production).

Dry-run by default — pass ``--apply`` to actually write.

    docker cp backend/_migrate_payroll_to_production.py aiflow_backend_local:/tmp/m.py
    docker exec aiflow_backend_local python /tmp/m.py              # dry-run
    docker exec aiflow_backend_local python /tmp/m.py --apply      # commit

Override DSNs with ``LOCAL_DSN`` / ``PROD_DSN`` env vars if needed.
"""
from __future__ import annotations
import os
import sys

import psycopg2
from psycopg2.extras import Json

# ── Soft-coded connection strings ──────────────────────────────────
LOCAL_DSN = os.environ.get(
    'LOCAL_DSN',
    'postgresql://aiflow_user:aiflow_local_pass_123@postgres_local:5432/aiflow_dev',
)
PROD_DSN = os.environ.get(
    'PROD_DSN',
    'postgresql://postgres:cJLHOrfvZxZXHKaMCWdLdRedgHgmIneU'
    '@shinkansen.proxy.rlwy.net:38534/railway',
)

# Tables in dependency order (parents first).
TABLES = [
    'payroll_engine_employee',
    'payroll_engine_run',
    'payroll_engine_payslip',
    'payroll_engine_line_item',
    'payroll_engine_adjustment',
    'payroll_engine_workflow_log',
    'payroll_engine_comparison',
    'payroll_engine_comparison_row',
]

# auth_user FKs blanked on the way in (those user IDs don't exist on prod).
NULL_FKS = {
    'payroll_engine_employee':       ['user_id', 'created_by_id'],
    'payroll_engine_run':            ['hr_approved_by_id', 'finance_approved_by_id',
                                      'released_by_id', 'created_by_id'],
    'payroll_engine_payslip':        [],
    'payroll_engine_line_item':      ['created_by_id'],
    'payroll_engine_adjustment':     ['created_by_id'],
    'payroll_engine_workflow_log':   ['actor_id'],
    'payroll_engine_comparison':     ['uploaded_by_id'],
    'payroll_engine_comparison_row': [],
}

INSERT_CHUNK = 200  # rows per executemany batch


def _adapt(v):
    """Wrap dict/list as psycopg2.Json so jsonb columns insert cleanly."""
    if isinstance(v, (dict, list)):
        return Json(v)
    return v


def _table_columns(cur, table):
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s "
        "ORDER BY ordinal_position",
        (table,),
    )
    return [r[0] for r in cur.fetchall()]


def _count(cur, table):
    cur.execute(f'SELECT COUNT(*) FROM {table}')
    return cur.fetchone()[0]


def report(local_conn, prod_conn):
    print('\n┌─────────────────────────────────────────────┬────────┬────────┐')
    print(  '│ TABLE                                       │  LOCAL │   PROD │')
    print(  '├─────────────────────────────────────────────┼────────┼────────┤')
    with local_conn.cursor() as lc, prod_conn.cursor() as pc:
        for t in TABLES:
            l, p = _count(lc, t), _count(pc, t)
            print(f'│ {t:<43} │ {l:>6} │ {p:>6} │')
    print(  '└─────────────────────────────────────────────┴────────┴────────┘')


def migrate_table(local_conn, prod_conn, table, null_cols):
    """Read rows from local, INSERT into prod with null-FK rewrite."""
    with local_conn.cursor() as lc:
        cols = _table_columns(lc, table)
        if not cols:
            print(f'  ⚠ {table}: table missing on local — skipped')
            return 0
        prod_cols_set = None
        with prod_conn.cursor() as pc_check:
            prod_cols = _table_columns(pc_check, table)
        prod_cols_set = set(prod_cols)
        # Use only columns that exist on BOTH sides (schema drift safety).
        common = [c for c in cols if c in prod_cols_set]
        col_list_sql = ','.join(f'"{c}"' for c in common)
        lc.execute(f'SELECT {col_list_sql} FROM {table} ORDER BY id')
        rows = lc.fetchall()

    if not rows:
        return 0

    null_idx = {common.index(c) for c in null_cols if c in common}
    # Apply NULL-FK rewrite + jsonb wrap.
    munged = []
    for r in rows:
        new = []
        for i, v in enumerate(r):
            if i in null_idx:
                new.append(None)
            else:
                new.append(_adapt(v))
        munged.append(tuple(new))

    placeholders = ','.join(['%s'] * len(common))
    sql = f'INSERT INTO {table} ({col_list_sql}) VALUES ({placeholders})'
    with prod_conn.cursor() as pc:
        for i in range(0, len(munged), INSERT_CHUNK):
            pc.executemany(sql, munged[i:i + INSERT_CHUNK])
    return len(munged)


def reset_sequence(prod_conn, table):
    """Set the id sequence to MAX(id)+1 so future inserts don't collide."""
    with prod_conn.cursor() as pc:
        pc.execute(
            f"SELECT setval(pg_get_serial_sequence(%s, 'id'), "
            f"GREATEST(COALESCE((SELECT MAX(id) FROM {table}), 1), 1), true)",
            (table,),
        )


def apply_migration(local_conn, prod_conn):
    """Run inside a single transaction on production."""
    prod_conn.rollback()  # close any implicit txn from prior SELECTs
    try:
        with prod_conn.cursor() as pc:
            tables_csv = ', '.join(TABLES)
            print(f'\n▶ TRUNCATE {tables_csv} RESTART IDENTITY CASCADE …')
            pc.execute(f'TRUNCATE {tables_csv} RESTART IDENTITY CASCADE')
        for t in TABLES:
            n = migrate_table(local_conn, prod_conn, t, NULL_FKS.get(t, []))
            print(f'  ▸ {t:<45} inserted {n:>5}')
        for t in TABLES:
            reset_sequence(prod_conn, t)
        prod_conn.commit()
        print('\n✓ COMMIT — production database updated.')
    except Exception:
        prod_conn.rollback()
        print('\n✗ FAILED — production rolled back.')
        raise


def main():
    apply = '--apply' in sys.argv
    print(f'Mode: {"APPLY (will write to production)" if apply else "DRY-RUN"}')

    print(f'\nLocal      : {LOCAL_DSN.split("@")[-1]}')
    print(f'Production : {PROD_DSN.split("@")[-1]}')

    try:
        local_conn = psycopg2.connect(LOCAL_DSN, connect_timeout=15)
    except Exception as exc:
        print(f'✗ Cannot connect to LOCAL: {exc}')
        sys.exit(1)
    try:
        prod_conn = psycopg2.connect(PROD_DSN, connect_timeout=15)
    except Exception as exc:
        print(f'✗ Cannot connect to PRODUCTION: {exc}')
        sys.exit(1)

    print('\n── BEFORE ──')
    report(local_conn, prod_conn)

    if not apply:
        print('\nDry-run only. Re-run with --apply to migrate.')
        return

    apply_migration(local_conn, prod_conn)

    print('\n── AFTER ──')
    report(local_conn, prod_conn)


if __name__ == '__main__':
    main()
