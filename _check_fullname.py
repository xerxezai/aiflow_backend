#!/usr/bin/env python
"""
Check biometric database - skip header row and use FullName column
"""
from apps.timesheet.sqlserver import connect

print("🔍 Checking Real Employee Data (skipping header row)\n")

with connect() as cur:
    # Skip first row, get rows 2-6
    cur.execute("""
        SELECT UserID, UserName, FullName, DptName, Gender
        FROM (
            SELECT *, ROW_NUMBER() OVER (ORDER BY UserID) as RowNum
            FROM dbo.Mx_VEW_UserAttendanceEvents
        ) AS Numbered
        WHERE RowNum BETWEEN 2 AND 11
    """)
    
    rows = cur.fetchall()
    
    print("📊 Sample Employee Records (rows 2-11, skipping header):\n")
    for i, (uid, uname, fullname, dept, gender) in enumerate(rows, 1):
        print(f"{i}. UserID={uid}, UserName={uname}, FullName={fullname}, Dept={dept}, Gender={gender}")
    
    print("\n" + "="*80)
    print("🔍 Checking distinct FullName values...\n")
    
    # Get count of unique FullNames
    cur.execute("""
        SELECT COUNT(DISTINCT FullName) as UniqueNames
        FROM (
            SELECT *, ROW_NUMBER() OVER (ORDER BY UserID) as RowNum
            FROM dbo.Mx_VEW_UserAttendanceEvents
        ) AS Numbered
        WHERE RowNum > 1
    """)
    
    unique_count = cur.fetchone()[0]
    print(f"✅ Found {unique_count} unique employees in FullName column")

print("\n✅ Analysis complete")
