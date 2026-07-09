"""Verify timesheet config loaded + run real-time query for last 5 minutes."""
import os, sys
sys.path.insert(0, '/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django; django.setup()

from apps.timesheet import config as ts_config
from apps.timesheet import sqlserver

print("\n=== Configuration ===")
print(f"is_configured(): {ts_config.is_configured()}")
print(f"SQLSERVER     : host={ts_config.SQLSERVER['host']} db={ts_config.SQLSERVER['database']} user={ts_config.SQLSERVER['user']}")
print(f"SCHEMA.table  : {ts_config.SCHEMA['table']}")
print(f"SCHEMA.columns: {ts_config.SCHEMA['columns']}")
print(f"variant       : {ts_config._detect_schema_variant()}")

print("\n=== Health check ===")
print(ts_config.is_configured(), '|', sqlserver.health_check())

print("\n=== Real-time query: last 5 minutes of door swipes ===")
table = ts_config.SCHEMA['table']
cols  = ts_config.SCHEMA['columns']
sql = f"""
    SELECT TOP 15
           {cols['employee_code']} AS emp_code,
           {cols['employee_name']} AS emp_name,
           {cols['department']}    AS department,
           {cols['punch_time']}    AS punch_time,
           {cols['punch_type']}    AS punch_type
    FROM   {table}
    WHERE  {cols['punch_time']} >= DATEADD(MINUTE, -60, GETDATE())
    ORDER BY {cols['punch_time']} DESC
"""
with sqlserver.connect() as cur:   # uses default db = TIMESHEET_DATABASE = matrix
    cur.execute(sql)
    rows = cur.fetchall()
in_val  = str(cols.get('in_value',  '0'))
out_val = str(cols.get('out_value', '1'))
print(f"\nMatcher: IN={in_val!r}  OUT={out_val!r}")
print(f"Got {len(rows)} events in last 60 min:\n")
for r in rows:
    row = r if isinstance(r, dict) else dict(zip(['emp_code','emp_name','department','punch_time','punch_type'], r))
    direction = 'IN ' if str(row['punch_type']).strip() == in_val else 'OUT' if str(row['punch_type']).strip() == out_val else '???'
    print(f"  [{row['punch_time']}] {row['emp_code']:<8} {direction}  {(row['emp_name'] or '')[:25]:<25}  {row['department'] or ''}")
