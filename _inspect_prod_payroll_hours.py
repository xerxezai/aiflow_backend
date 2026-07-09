"""Diagnostic: inspect production payroll run 2026-06 vs attendance."""
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from django.db.models import Sum, Count, Min, Max
from apps.payroll_engine.models import PayrollRun
from apps.payroll_engine.services.attendance import compute_monthly_hours
from apps.payroll_engine.config import HOURS_FROM_TIMESHEET

YEAR, MONTH = 2026, 6

print(f'HOURS_FROM_TIMESHEET = {HOURS_FROM_TIMESHEET}')
print(f'TIMESHEET_DATA_SOURCE env = {os.environ.get("TIMESHEET_DATA_SOURCE")!r}')
print()

print(f'=== Payroll Runs for {YEAR}-{MONTH:02d} ===')
runs = list(PayrollRun.objects.filter(year=YEAR, month=MONTH).order_by('id'))
for r in runs:
    agg = r.payslips.aggregate(
        h=Sum('hours'), d=Sum('days'), n=Count('id'),
        mn=Min('hours'), mx=Max('hours'),
    )
    print(f'Run id={r.id} cycle={r.cycle_code} status={r.status}')
    print(f'  run.total_hours={r.total_hours}  run.total_days={r.total_days}  emp={r.employee_count}')
    print(f'  sum(payslip.hours)={agg["h"]}  sum(payslip.days)={agg["d"]}  n={agg["n"]}')
    print(f'  hours range = [{agg["mn"]} .. {agg["mx"]}]')

print()
print('=== Top 8 payslips by hours ===')
for run in runs:
    for slip in run.payslips.order_by('-hours')[:8]:
        emp_no = slip.employee.employee_no
        name = (slip.snapshot_full_name or '')[:34]
        print(f'  emp_no={emp_no!s:10} hours={slip.hours!s:>8} days={slip.days!s:>6}  {name}')

print()
print('=== Live attendance map for the same month (compute_monthly_hours) ===')
try:
    live = compute_monthly_hours(YEAR, MONTH)
    print(f'  rows returned: {len(live)}')
    if live:
        total = sum(live.values())
        non_zero = sum(1 for v in live.values() if v and float(v) > 0)
        print(f'  sum of all hours: {total}')
        print(f'  employees with non-zero hours: {non_zero}')
        print('  first 8 codes:')
        for i, (code, hrs) in enumerate(sorted(live.items())[:8]):
            print(f'    {code!s:10} -> {hrs}')
    else:
        print('  (empty — attendance source returned nothing in this Django process)')
except Exception as e:
    print(f'  ERROR: {type(e).__name__}: {e}')

print()
print('=== Comparison: payroll snapshot vs live attendance ===')
for run in runs:
    matched = differs = missing_in_live = 0
    diffs = []
    for slip in run.payslips.select_related('employee').only('hours', 'employee__employee_no', 'snapshot_full_name'):
        code = str(slip.employee.employee_no).strip()
        snap = slip.hours
        live_v = live.get(code) if live else None
        if live_v is None:
            missing_in_live += 1
            continue
        if snap == live_v:
            matched += 1
        else:
            differs += 1
            if len(diffs) < 5:
                diffs.append((code, snap, live_v, slip.snapshot_full_name))
    print(f'Run {run.id}: matched={matched}  differs={differs}  missing_in_live={missing_in_live}')
    for code, snap, live_v, name in diffs:
        print(f'  ! {code!s:10} snapshot={snap}  live={live_v}  ({name[:30]})')
