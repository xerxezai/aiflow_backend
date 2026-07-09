#!/usr/bin/env python
"""
Search for Lira Viaga in biometric database
"""
from apps.timesheet.sqlserver import connect

print("🔍 Searching for Lira Viaga in biometric database...\n")

with connect() as cur:
    # Search for Lira or Viaga
    cur.execute("""
        SELECT DISTINCT UserID, FullName, DptName
        FROM dbo.Mx_VEW_UserAttendanceEvents
        WHERE (FullName LIKE '%Lira%' OR FullName LIKE '%Viaga%')
          AND UserID != 'UserID'
        ORDER BY FullName
    """)
    
    rows = cur.fetchall()
    
    if rows:
        print(f"✅ Found {len(rows)} match(es):\n")
        for row in rows:
            print(f"  UserID: {row['UserID']}")
            print(f"  Name:   {row['FullName']}")
            print(f"  Dept:   {row['DptName']}")
            print()
    else:
        print("❌ No matches found for 'Lira' or 'Viaga'")
        print("\n🔍 Trying partial matches...")
        
        # Try broader search
        cur.execute("""
            SELECT DISTINCT TOP 10 UserID, FullName, DptName
            FROM dbo.Mx_VEW_UserAttendanceEvents
            WHERE FullName LIKE '%Tri%'
              AND UserID != 'UserID'
            ORDER BY FullName
        """)
        
        similar = cur.fetchall()
        if similar:
            print(f"\nSimilar names found ({len(similar)}):")
            for row in similar[:5]:
                print(f"  {row['FullName']}")
