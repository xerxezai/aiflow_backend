#!/usr/bin/env python
"""
Quick script to check Lira's profile and employee_id configuration
"""
from apps.rbac.models import UserProfile
from django.contrib.auth import get_user_model

User = get_user_model()

try:
    u = User.objects.get(email='lira.viaga@rejlers.ae')
    p = UserProfile.objects.get(user=u)
    print(f'✅ User Found: {u.email}')
    print(f'   First Name: {u.first_name}')
    print(f'   Last Name: {u.last_name}')
    print(f'   Employee ID: {p.employee_id}')
    print(f'   Department: {p.department}')
    print(f'   Full Name for Matching: {u.first_name} {u.last_name}')
except User.DoesNotExist:
    print('❌ User lira.viaga@rejlers.ae does not exist')
except UserProfile.DoesNotExist:
    print(f'⚠️  User exists but no UserProfile found')
except Exception as e:
    print(f'❌ Error: {e}')

# Now check if there are any biometric records for "Lira"
print('\n🔍 Searching biometric database for "Lira"...')
import pyodbc
import os

conn_str = (
    f"DRIVER={{ODBC Driver 17 for SQL Server}};"
    f"SERVER={os.getenv('TIMESHEET_HOST')};"
    f"PORT={os.getenv('TIMESHEET_PORT', '1433')};"
    f"DATABASE={os.getenv('TIMESHEET_DATABASE')};"
    f"UID={os.getenv('TIMESHEET_USER')};"
    f"PWD={os.getenv('TIMESHEET_PASSWORD')};"
)

try:
    conn = pyodbc.connect(conn_str, timeout=10)
    cursor = conn.cursor()
    
    # Search for Lira in the biometric system
    query = """
        SELECT DISTINCT TOP 5 UserID, UserName, DptName
        FROM dbo.Mx_VEW_UserAttendanceEvents
        WHERE UserName LIKE '%Lira%'
        ORDER BY UserID
    """
    cursor.execute(query)
    rows = cursor.fetchall()
    
    if rows:
        print(f'   Found {len(rows)} matches in biometric system:')
        for row in rows:
            print(f'   - UserID: {row[0]} | Name: {row[1]} | Dept: {row[2]}')
    else:
        print('   ❌ No matches found for "Lira" in biometric system')
    
    cursor.close()
    conn.close()
except Exception as e:
    print(f'   ⚠️  Could not connect to biometric DB: {e}')
