"""Sample real data from the chosen attendance view to confirm column values."""
import os, sys
sys.path.insert(0, '/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django; django.setup()
from apps.timesheet import sqlserver

TABLE = 'dbo.Mx_VEW_UserAttendanceEvents'

print(f"\n=== Sampling {TABLE} ===\n")

# 1. Distinct EntryExitType values (the direction column)
with sqlserver.connect(database='matrix') as cur:
    cur.execute(f"""
        SELECT TOP 20 EntryExitType, COUNT_BIG(*) AS cnt
        FROM   {TABLE}
        GROUP BY EntryExitType
        ORDER BY cnt DESC
    """)
    print('Distinct EntryExitType values:')
    for r in cur.fetchall():
        row = r if isinstance(r, dict) else {'EntryExitType': r[0], 'cnt': r[1]}
        print(f"  {row['EntryExitType']!r:>15}  ({row['cnt']} events)")

# 2. Date range
with sqlserver.connect(database='matrix') as cur:
    cur.execute(f"SELECT MIN(EventDateTime) AS mn, MAX(EventDateTime) AS mx, COUNT_BIG(*) AS total FROM {TABLE}")
    r = cur.fetchone()
    row = r if isinstance(r, dict) else {'mn': r[0], 'mx': r[1], 'total': r[2]}
    print(f"\nDate range: {row['mn']}  →  {row['mx']}   (total events: {row['total']:,})")

# 3. Last 10 events (real time freshness check)
with sqlserver.connect(database='matrix') as cur:
    cur.execute(f"""
        SELECT TOP 10 UserID, UserName, FullName, DptName, EventDateTime, EntryExitType
        FROM   {TABLE}
        ORDER BY EventDateTime DESC
    """)
    print('\nMost recent 10 events:')
    for r in cur.fetchall():
        row = r if isinstance(r, dict) else dict(zip(['UserID','UserName','FullName','DptName','EventDateTime','EntryExitType'], r))
        print(f"  [{row['EventDateTime']}]  {row['UserID']:<8} {(row['FullName'] or row['UserName'] or '')[:25]:<25} {row['DptName'] or '':<20}  {row['EntryExitType']}")

# 4. Check if there's an email column or how to map to users
with sqlserver.connect(database='matrix') as cur:
    cur.execute("""
        SELECT COLUMN_NAME, DATA_TYPE
        FROM   INFORMATION_SCHEMA.COLUMNS
        WHERE  TABLE_NAME = 'Mx_VEW_UserAttendanceEvents'
          AND  (COLUMN_NAME LIKE '%mail%' OR COLUMN_NAME LIKE '%email%' OR COLUMN_NAME LIKE '%code%' OR COLUMN_NAME LIKE '%ID%')
        ORDER BY ORDINAL_POSITION
    """)
    print('\nID/email/code columns available:')
    for r in cur.fetchall():
        row = r if isinstance(r, dict) else {'COLUMN_NAME': r[0], 'DATA_TYPE': r[1]}
        print(f"  {row['COLUMN_NAME']:<35} {row['DATA_TYPE']}")
