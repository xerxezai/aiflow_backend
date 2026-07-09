#!/usr/bin/env python
"""
Simple check for real employee data
"""
from apps.timesheet.sqlserver import connect

with connect() as cur:
    # Count total rows
    cur.execute("SELECT COUNT(*) as cnt FROM dbo.Mx_VEW_UserAttendanceEvents")
    total = cur.fetchone()['cnt']
    print(f"Total rows in view: {total:,}")
    
    # Count rows that are NOT header placeholders
    cur.execute("""
        SELECT COUNT(DISTINCT UserID) as cnt
        FROM dbo.Mx_VEW_UserAttendanceEvents 
        WHERE UserID != 'UserID'
    """)
    distinct_users = cur.fetchone()['cnt']
    print(f"Distinct UserIDs (excluding 'UserID' header): {distinct_users}")
    
    # Get first 5 DISTINCT UserIDs
    cur.execute("""
        SELECT DISTINCT TOP 5 UserID
        FROM dbo.Mx_VEW_UserAttendanceEvents 
        WHERE UserID != 'UserID'
        ORDER BY UserID
    """)
    print(f"\nFirst 5 distinct UserIDs:")
    for row in cur.fetchall():
        print(f"  - {row['UserID']}")
    
    # Get 3 sample full records
    cur.execute("""
        SELECT TOP 3 UserID, FullName, DptName, EventDateTime
        FROM dbo.Mx_VEW_UserAttendanceEvents 
        WHERE UserID != 'UserID'
        ORDER BY EventDateTime DESC
    """)
    print(f"\nRecent 3 attendance events:")
    for row in cur.fetchall():
        print(f"  UserID={row['UserID']}, Name={row['FullName']}, Dept={row['DptName']}, Time={row['EventDateTime']}")
