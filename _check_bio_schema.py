#!/usr/bin/env python
"""
Diagnostic script to inspect biometric database schema and find employee names
"""
from apps.timesheet.sqlserver import connect

print("🔍 Inspecting Biometric Database Schema\n")

with connect() as cur:
    # Get first 3 rows with all columns
    cur.execute('SELECT TOP 3 * FROM dbo.Mx_VEW_UserAttendanceEvents ORDER BY UserID')
    rows = cur.fetchall()
    columns = [col[0] for col in cur.description]
    
    print(f"📋 Available Columns ({len(columns)} total):")
    print(", ".join(columns))
    print("\n" + "="*80 + "\n")
    
    print("📊 Sample Data (first 3 rows):\n")
    for i, row in enumerate(rows, 1):
        print(f"Row {i}:")
        for col, val in zip(columns, row):
            print(f"  {col:30s} = {val}")
        print()
    
    # Try to find employee with actual name
    print("\n" + "="*80)
    print("🔍 Looking for employees with real names (not 'UserName')...\n")
    
    cur.execute("""
        SELECT DISTINCT TOP 10 UserID, UserName, DptName 
        FROM dbo.Mx_VEW_UserAttendanceEvents 
        WHERE UserName IS NOT NULL 
        AND UserName != 'UserName'
        AND UserName != ''
        ORDER BY UserID
    """)
    
    real_names = cur.fetchall()
    if real_names:
        print(f"✅ Found {len(real_names)} employees with real names:")
        for uid, name, dept in real_names:
            print(f"  UserID={uid}, Name={name}, Dept={dept}")
    else:
        print("❌ No employees found with real names in UserName column")
        print("   All entries show 'UserName' placeholder")

print("\n✅ Schema inspection complete")
