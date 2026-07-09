#!/usr/bin/env python
"""Simulate production state - remove finance.0007 to test the fix"""
import os, sys
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
sys.path.insert(0, '/app')
import django; django.setup()
from django.db import connection

with connection.cursor() as c:
    c.execute("DELETE FROM django_migrations WHERE app='finance' AND name='0007_payrollworkflow_workflownotificationlog_and_more'")
    print(f"Deleted {c.rowcount} row(s) - simulating production state where finance.0007 is missing")
    c.execute("SELECT COUNT(*) FROM django_migrations WHERE app='finance' AND name LIKE '0007%'")
    remaining = c.fetchone()[0]
    print(f"finance.0007 remaining in DB: {remaining}")
    if remaining == 0:
        print("OK: Production state simulated correctly - finance.0007 is now missing")
    else:
        print("ERROR: Could not remove finance.0007")
