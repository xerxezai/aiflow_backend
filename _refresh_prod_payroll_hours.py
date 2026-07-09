"""One-shot: refresh production payroll run hours from the live mirror attendance."""
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from apps.payroll_engine.models import PayrollRun
from apps.payroll_engine.services.run_generator import refresh_run_hours_from_timesheet

YEAR, MONTH = 2026, 6

runs = list(PayrollRun.objects.filter(year=YEAR, month=MONTH, status='draft').order_by('id'))
if not runs:
    print('No DRAFT runs to refresh.')
    raise SystemExit(0)

for r in runs:
    print(f'\n>>> Refreshing Run {r.id} ({r.cycle_code}, status={r.status})...')
    before_h = r.total_hours
    before_d = r.total_days
    result = refresh_run_hours_from_timesheet(r, zero_missing=True)
    r.refresh_from_db()
    print(f'  updated={result["updated"]}  unchanged={result["unchanged"]}  '
          f'zeroed={result.get("zeroed", 0)}  missing={len(result["missing"])}')
    print(f'  before: total_hours={before_h}  total_days={before_d}')
    print(f'  after : total_hours={r.total_hours}  total_days={r.total_days}')
    if result['missing']:
        head = result['missing'][:8]
        print(f'  missing codes (no biometric data, zeroed): {head}'
              f'{"..." if len(result["missing"]) > 8 else ""}')
