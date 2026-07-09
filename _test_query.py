#!/usr/bin/env python
"""
Test SQL query with header row filtering
"""
from apps.timesheet import config as ts_config
from apps.timesheet.sqlserver import connect

table = ts_config.SCHEMA['table']
col_code = ts_config.SCHEMA['columns']['employee_code']
col_name = ts_config.SCHEMA['columns']['employee_name']
col_dept = ts_config.SCHEMA['columns']['department'] or "''"

query = f"""
SELECT DISTINCT TOP 5 {col_code}, {col_name}, {col_dept} AS dept
FROM {table}
WHERE {col_code} IS NOT NULL 
  AND {col_name} IS NOT NULL
  AND LTRIM(RTRIM({col_name})) != ''
  AND {col_code} != 'UserID'
  AND {col_name} NOT IN ('UserName', 'FullName')
ORDER BY {col_code}
"""

print("=== SQL Query ===")
print(query)
print("\n=== Results ===")

with connect() as cur:
    cur.execute(query)
    rows = cur.fetchall()
    if rows:
        for row in rows:
            print(f"UserID={row['UserID']}, Name={row[col_name]}, Dept={row.get('dept', 'N/A')}")
    else:
        print("No rows returned - all employees filtered out!")
        
# Now check without filters to see what's there
print("\n=== Without filters (first 5) ===")
with connect() as cur:
    simple_query = f"SELECT TOP 5 {col_code}, {col_name} FROM {table} ORDER BY {col_code}"
    cur.execute(simple_query)
    for row in cur.fetchall():
        print(f"UserID={row[col_code]}, Name={row[col_name]}")
