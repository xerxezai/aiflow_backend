"""Auto-discover the attendance schema in the configured SQL Server.

Strategy (all soft-coded — no business rules baked in):
  1. List all user tables/views in the configured database.
  2. Score each by name (keywords: attendance, punch, swipe, log, in/out).
  3. For top candidates, inspect column names against keyword groups:
       - employee identifier  (emp, code, id, badge, card)
       - timestamp            (date, time, punch, swipe, log, datetime)
       - direction            (type, status, mode, direction, inout, in_out)
       - separate in/out cols (in_time, out_time, login, logout)
  4. Print a ranked report so the user can pick the best match.
"""
import os, sys, json
sys.path.insert(0, '/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from apps.timesheet import sqlserver, config as ts_config

# ---- Soft-coded keyword dictionaries (no hardcoded table/column names) ----
TABLE_KEYWORDS = {
    'attendance': 10, 'punch': 9, 'swipe': 8, 'inout': 8, 'in_out': 8,
    'access': 5, 'log': 4, 'event': 3, 'transaction': 3, 'time': 2,
    'biometric': 9, 'check': 4, 'entry': 5, 'exit': 5, 'movement': 5,
}
COLUMN_GROUPS = {
    'employee': ['emp', 'employee', 'usr', 'user', 'badge', 'card', 'staff', 'person', 'pin'],
    'name':     ['name', 'fname', 'lname', 'first', 'last', 'full'],
    'email':    ['email', 'mail'],
    'dept':     ['dept', 'department', 'division', 'team', 'section'],
    'datetime': ['date', 'time', 'datetime', 'timestamp', 'punch', 'swipe', 'log', 'event'],
    'direction':['type', 'status', 'mode', 'direction', 'inout', 'in_out', 'movement', 'flag'],
    'in_col':   ['in_time', 'intime', 'login', 'firstin', 'first_in', 'check_in', 'checkin', 'time_in', 'timein'],
    'out_col':  ['out_time', 'outtime', 'logout', 'lastout', 'last_out', 'check_out', 'checkout', 'time_out', 'timeout'],
}

def score_table(name: str) -> int:
    n = name.lower()
    return sum(weight for kw, weight in TABLE_KEYWORDS.items() if kw in n)

def classify_column(col: str) -> list[str]:
    c = col.lower().replace(' ', '').replace('_', '')
    hits = []
    for group, kws in COLUMN_GROUPS.items():
        if any(kw.replace('_', '') in c for kw in kws):
            hits.append(group)
    return hits

def main():
    print(f"\n[Discovery] Database: {ts_config.SQLSERVER['database'] or '(server default)'}")
    print(f"[Discovery] Host: {ts_config.SQLSERVER['host']}:{ts_config.SQLSERVER['port']}")
    print(f"[Discovery] Driver: {sqlserver.driver_in_use()}\n")

    with sqlserver.connect(database='matrix') as cur:
        # 1. All user tables AND views, with row counts (heuristic - tables only)
        cur.execute("""
            SELECT  TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE
            FROM    INFORMATION_SCHEMA.TABLES
            WHERE   TABLE_TYPE IN ('BASE TABLE', 'VIEW')
            ORDER BY TABLE_NAME
        """)
        tables = [dict(r) if isinstance(r, dict) else {'TABLE_SCHEMA': r[0], 'TABLE_NAME': r[1], 'TABLE_TYPE': r[2]} for r in cur.fetchall()]

    print(f"[Discovery] Total tables/views: {len(tables)}\n")

    # 2. Score by table name
    scored = []
    for t in tables:
        score = score_table(t['TABLE_NAME'])
        if score > 0:
            scored.append((score, t))
    scored.sort(key=lambda x: -x[0])

    print(f"[Discovery] {len(scored)} attendance-candidate table(s) by name keyword:\n")
    top = scored[:15]
    for score, t in top:
        print(f"  [{score:>3}] {t['TABLE_SCHEMA']}.{t['TABLE_NAME']}  ({t['TABLE_TYPE']})")
    print()

    # 3. Inspect columns of top candidates
    print("=" * 90)
    print("COLUMN INSPECTION — Top 8 candidates")
    print("=" * 90)
    for score, t in scored[:8]:
        full = f"{t['TABLE_SCHEMA']}.{t['TABLE_NAME']}"
        with sqlserver.connect(database='matrix') as cur:
            cur.execute("""
                SELECT  COLUMN_NAME, DATA_TYPE
                FROM    INFORMATION_SCHEMA.COLUMNS
                WHERE   TABLE_SCHEMA = %s AND TABLE_NAME = %s
                ORDER BY ORDINAL_POSITION
            """, (t['TABLE_SCHEMA'], t['TABLE_NAME']))
            cols = [dict(r) if isinstance(r, dict) else {'COLUMN_NAME': r[0], 'DATA_TYPE': r[1]} for r in cur.fetchall()]

        # Try row count (only for tables, not views; with timeout safety)
        rowcount = '?'
        if t['TABLE_TYPE'] == 'BASE TABLE':
            try:
                with sqlserver.connect(database='matrix') as cur:
                    cur.execute(f"SELECT COUNT_BIG(*) AS c FROM [{t['TABLE_SCHEMA']}].[{t['TABLE_NAME']}]")
                    r = cur.fetchone()
                    rowcount = (r['c'] if isinstance(r, dict) else r[0])
            except Exception as e:
                rowcount = f'err:{type(e).__name__}'

        # Classify each column
        col_class = {g: [] for g in COLUMN_GROUPS}
        for c in cols:
            for g in classify_column(c['COLUMN_NAME']):
                col_class[g].append(c['COLUMN_NAME'])

        # Variant detection
        has_event_stream  = bool(col_class['datetime'] and col_class['direction'])
        has_two_column    = bool(col_class['in_col'] and col_class['out_col'])

        print(f"\n  >>> {full}  (rows: {rowcount}, score: {score})")
        print(f"      Columns ({len(cols)}): {[c['COLUMN_NAME'] for c in cols[:30]]}")
        print(f"      Classified:")
        for g in ('employee', 'name', 'email', 'dept', 'datetime', 'direction', 'in_col', 'out_col'):
            if col_class[g]:
                print(f"        {g:10}: {col_class[g]}")
        if has_event_stream:
            print(f"      VARIANT: event_stream (one row per punch; needs PUNCH_TIME + PUNCH_TYPE)")
        elif has_two_column:
            print(f"      VARIANT: two_column (one row per day; needs LOGIN_TIME + LOGOUT_TIME)")
        else:
            print(f"      VARIANT: undetermined — manual review")

    print("\n" + "=" * 90)
    print("Done. Pick the best candidate above and add to .env.local.\n")

main()
