"""Compare Payroll Engine record counts between LOCAL and PRODUCTION Postgres.

Read-only — issues SELECT COUNT(*) on each payroll_engine_* table and
prints a side-by-side table. Designed to answer one question:
    "Does production actually have the data I see locally?"

Run from host PowerShell:
    docker cp backend/_verify_payroll_parity.py aiflow_backend_local:/tmp/v.py
    docker exec aiflow_backend_local python /tmp/v.py
"""
from __future__ import annotations
import os
import sys

import psycopg2

# ── Soft-coded connection strings (override via env if you want) ───
LOCAL_DSN = os.environ.get(
    'LOCAL_DSN',
    'postgresql://aiflow_user:aiflow_local_pass_123@postgres_local:5432/aiflow_dev',
)
PROD_DSN = os.environ.get(
    'PROD_DSN',
    'postgresql://postgres:cJLHOrfvZxZXHKaMCWdLdRedgHgmIneU'
    '@shinkansen.proxy.rlwy.net:38534/railway',
)

# Tables we want to inventory (in dependency order).
PAYROLL_TABLES = [
    'payroll_engine_employee',
    'payroll_engine_run',
    'payroll_engine_payslip',
    'payroll_engine_line_item',
    'payroll_engine_adjustment',
    'payroll_engine_workflow_log',
    'payroll_engine_comparison',
    'payroll_engine_comparison_row',
]


def _scalar(cur, sql, params=()):
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


def _table_exists(cur, table):
    return bool(_scalar(
        cur,
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name=%s",
        (table,),
    ))


def inventory(label, dsn):
    """Return dict[table] = count|None|'MISSING'."""
    print(f'\n── Connecting to {label} ──')
    print(f'   DSN: {dsn.split("@")[-1]}')
    try:
        conn = psycopg2.connect(dsn, connect_timeout=15)
    except Exception as exc:
        print(f'   ✗ FAILED: {exc}')
        return None
    out = {}
    with conn, conn.cursor() as cur:
        for t in PAYROLL_TABLES:
            if not _table_exists(cur, t):
                out[t] = 'MISSING'
                continue
            try:
                out[t] = _scalar(cur, f'SELECT COUNT(*) FROM {t}')
            except Exception as exc:
                out[t] = f'ERR: {exc}'
    # Also pull a peek at the runs themselves
    extras = {}
    with conn, conn.cursor() as cur:
        if _table_exists(cur, 'payroll_engine_run'):
            cur.execute(
                "SELECT id, cycle_code, status, employee_count, "
                "total_gross, total_net, created_at "
                "FROM payroll_engine_run ORDER BY year DESC, month DESC LIMIT 12"
            )
            extras['runs'] = cur.fetchall()
        if _table_exists(cur, 'payroll_engine_employee'):
            cur.execute(
                "SELECT COUNT(*) FROM payroll_engine_employee WHERE is_active = TRUE"
            )
            extras['active_employees'] = cur.fetchone()[0]
    conn.close()
    return {'counts': out, 'extras': extras}


def main():
    local = inventory('LOCAL', LOCAL_DSN)
    prod = inventory('PRODUCTION (Railway)', PROD_DSN)

    if not local or not prod:
        print('\n✗ Could not connect to one side — aborting comparison.')
        sys.exit(1)

    print('\n' + '═' * 78)
    print(f'{"TABLE":<38} {"LOCAL":>14} {"PRODUCTION":>14} {"DIFF":>10}')
    print('═' * 78)
    for t in PAYROLL_TABLES:
        lc = local['counts'].get(t)
        pc = prod['counts'].get(t)
        diff = ''
        if isinstance(lc, int) and isinstance(pc, int):
            d = lc - pc
            if d == 0:
                diff = '✓'
            else:
                diff = f'{d:+d}'
        print(f'{t:<38} {str(lc):>14} {str(pc):>14} {diff:>10}')
    print('═' * 78)
    print(f'Active employees   — local: {local["extras"].get("active_employees")}  '
          f'production: {prod["extras"].get("active_employees")}')

    print('\n── LOCAL: last 12 runs ──')
    for row in local['extras'].get('runs', []) or []:
        print(f'  id={row[0]:>4}  {row[1]}  status={row[2]:<12}  '
              f'employees={row[3]:>4}  gross={row[4]}  net={row[5]}  '
              f'created={row[6]}')
    print('\n── PRODUCTION: last 12 runs ──')
    for row in prod['extras'].get('runs', []) or []:
        print(f'  id={row[0]:>4}  {row[1]}  status={row[2]:<12}  '
              f'employees={row[3]:>4}  gross={row[4]}  net={row[5]}  '
              f'created={row[6]}')
    if not prod['extras'].get('runs'):
        print('  (production has zero PayrollRun rows)')


if __name__ == '__main__':
    main()
