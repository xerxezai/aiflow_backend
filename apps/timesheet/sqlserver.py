"""
Thin SQL Server connection wrapper.

Design goals:
- Never crash Django boot if pymssql/pyodbc is missing (returns informative error).
- Lazy connection (opened on first query, closed on context-manager exit).
- Driver auto-detection: pymssql preferred (self-contained wheel), pyodbc fallback.
- All credentials come from apps.timesheet.config — never hardcoded.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any

from . import config as ts_config

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Driver detection (soft, never crashes if both missing)
# ─────────────────────────────────────────────────────────────────────────────
try:
    import pymssql  # type: ignore
    _HAS_PYMSSQL = True
except Exception:  # pragma: no cover - module just may not be installed
    pymssql = None
    _HAS_PYMSSQL = False

try:
    import pyodbc  # type: ignore
    _HAS_PYODBC = True
except Exception:  # pragma: no cover
    pyodbc = None
    _HAS_PYODBC = False


class TimesheetDriverError(RuntimeError):
    """Raised when no SQL Server driver is available."""


class TimesheetConnectionError(RuntimeError):
    """Raised when the SQL Server is reachable but credentials/permissions fail."""


def driver_in_use() -> str:
    pref = ts_config.SQLSERVER['driver']
    if pref == 'pymssql' and _HAS_PYMSSQL:
        return 'pymssql'
    if pref == 'pyodbc' and _HAS_PYODBC:
        return 'pyodbc'
    if pref == 'auto':
        if _HAS_PYMSSQL:
            return 'pymssql'
        if _HAS_PYODBC:
            return 'pyodbc'
    return ''


def driver_status() -> dict:
    """Reported by the Setup wizard so users know what to install if missing."""
    return {
        'pymssql_installed': _HAS_PYMSSQL,
        'pyodbc_installed':  _HAS_PYODBC,
        'driver_in_use':     driver_in_use(),
        'driver_preference': ts_config.SQLSERVER['driver'],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Connection
# ─────────────────────────────────────────────────────────────────────────────
@contextmanager
def connect(database: str | None = None):
    """
    Yield an open cursor. Use as:
        with connect() as cur:
            cur.execute('SELECT 1')
            row = cur.fetchone()

    `database` overrides the configured default so the discovery wizard can
    enumerate databases without forcing a selection upfront.
    """
    drv = driver_in_use()
    if not drv:
        raise TimesheetDriverError(
            'No SQL Server driver available. Install one of: '
            'pymssql (recommended) or pyodbc + ODBC Driver 17 for SQL Server.'
        )

    cfg = ts_config.SQLSERVER
    db = database or cfg['database'] or None  # None → server default db

    conn = None
    try:
        if drv == 'pymssql':
            conn = pymssql.connect(
                server=cfg['host'],
                port=str(cfg['port']),
                user=cfg['user'],
                password=cfg['password'],
                database=db or '',
                timeout=cfg['timeout'],
                login_timeout=cfg['timeout'],
                as_dict=True,
            )
        else:  # pyodbc
            conn_str = (
                'DRIVER={ODBC Driver 17 for SQL Server};'
                f"SERVER={cfg['host']},{cfg['port']};"
                f"UID={cfg['user']};PWD={cfg['password']};"
                f"DATABASE={db};"
                f"Connection Timeout={cfg['timeout']};"
            )
            conn = pyodbc.connect(conn_str, timeout=cfg['timeout'])
        cur = conn.cursor()
        yield cur
    except (TimesheetDriverError, TimesheetConnectionError):
        raise
    except Exception as exc:  # pragma: no cover - surfaces real DB errors
        logger.exception('[timesheet] SQL Server connection failed')
        raise TimesheetConnectionError(str(exc)) from exc
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def health_check() -> dict:
    """Quick `SELECT 1` for the Setup wizard."""
    result = {'ok': False, 'driver': driver_in_use(), 'error': None}
    try:
        with connect() as cur:
            cur.execute('SELECT 1 AS ok')
            row = cur.fetchone()
            result['ok'] = bool(row)
    except TimesheetDriverError as exc:
        result['error'] = str(exc)
        result['error_kind'] = 'driver_missing'
    except TimesheetConnectionError as exc:
        result['error'] = str(exc)
        result['error_kind'] = 'connection_failed'
    except Exception as exc:  # pragma: no cover
        result['error'] = str(exc)
        result['error_kind'] = 'unknown'
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Row → dict adapter (pyodbc returns Row objects; pymssql already dicts)
# ─────────────────────────────────────────────────────────────────────────────
def rows_to_dicts(cursor, rows: list[Any]) -> list[dict]:
    if not rows:
        return []
    if isinstance(rows[0], dict):
        return rows  # type: ignore[return-value]
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, r)) for r in rows]
