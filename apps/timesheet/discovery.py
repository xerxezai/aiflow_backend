"""
Discovery helpers — let the user point the system at the right table from the
Setup wizard, without having to know SQL Server internals.

Endpoints:
    GET /api/v1/timesheet/discovery/databases/
    GET /api/v1/timesheet/discovery/tables/?database=X
    GET /api/v1/timesheet/discovery/columns/?database=X&table=Y
    GET /api/v1/timesheet/discovery/preview/?database=X&table=Y&limit=5
"""
from __future__ import annotations

from typing import Optional

from .sqlserver import connect, rows_to_dicts


# ─────────────────────────────────────────────────────────────────────────────
# Soft-coded filter — hide system databases the user shouldn't browse.
# ─────────────────────────────────────────────────────────────────────────────
SYSTEM_DATABASES = {'master', 'tempdb', 'model', 'msdb'}


def list_databases() -> list[dict]:
    sql = (
        "SELECT name AS database_name, "
        "CAST(create_date AS NVARCHAR(50)) AS created_at "
        "FROM sys.databases "
        "WHERE state_desc = 'ONLINE' "
        "ORDER BY name"
    )
    with connect() as cur:
        cur.execute(sql)
        rows = rows_to_dicts(cur, cur.fetchall())
    return [r for r in rows if r['database_name'] not in SYSTEM_DATABASES]


def list_tables(database: str) -> list[dict]:
    if not database:
        return []
    sql = (
        f"SELECT TABLE_SCHEMA + '.' + TABLE_NAME AS qualified_name, "
        f"TABLE_SCHEMA AS schema_name, TABLE_NAME AS table_name, TABLE_TYPE AS table_type "
        f"FROM [{_safe_ident(database)}].INFORMATION_SCHEMA.TABLES "
        f"ORDER BY TABLE_SCHEMA, TABLE_NAME"
    )
    with connect(database=database) as cur:
        cur.execute(sql)
        rows = rows_to_dicts(cur, cur.fetchall())
    return rows


def list_columns(database: str, table: str) -> list[dict]:
    if not database or not table:
        return []
    # 'schema.table' or bare 'table'
    if '.' in table:
        schema, tbl = table.split('.', 1)
    else:
        schema, tbl = 'dbo', table
    sql = (
        f"SELECT COLUMN_NAME AS column_name, DATA_TYPE AS data_type, "
        f"IS_NULLABLE AS is_nullable, ORDINAL_POSITION AS ordinal "
        f"FROM [{_safe_ident(database)}].INFORMATION_SCHEMA.COLUMNS "
        f"WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
        f"ORDER BY ORDINAL_POSITION"
    )
    with connect(database=database) as cur:
        cur.execute(sql, (schema, tbl))
        rows = rows_to_dicts(cur, cur.fetchall())
    return rows


def preview_table(database: str, table: str, limit: int = 5) -> list[dict]:
    if not database or not table:
        return []
    limit = max(1, min(int(limit or 5), 100))
    if '.' in table:
        schema, tbl = table.split('.', 1)
    else:
        schema, tbl = 'dbo', table
    sql = (
        f"SELECT TOP {limit} * "
        f"FROM [{_safe_ident(database)}].[{_safe_ident(schema)}].[{_safe_ident(tbl)}]"
    )
    with connect(database=database) as cur:
        cur.execute(sql)
        rows = rows_to_dicts(cur, cur.fetchall())
    # Stringify non-JSON-serialisable values (datetime, Decimal, bytes)
    return [_jsonify_row(r) for r in rows]


def _safe_ident(name: str) -> str:
    # Allow only [A-Za-z0-9_], strip everything else. Defence-in-depth alongside
    # bracket-quoting; prevents tricks like  "X] DROP TABLE foo --".
    return ''.join(c for c in (name or '') if c.isalnum() or c == '_')


def _jsonify_row(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if v is None:
            out[k] = None
        elif isinstance(v, (str, int, float, bool)):
            out[k] = v
        elif isinstance(v, (bytes, bytearray)):
            out[k] = f'<{len(v)} bytes>'
        else:
            out[k] = str(v)
    return out
