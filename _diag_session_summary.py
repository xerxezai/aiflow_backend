"""Summary diag: show row counts + sample populated columns across every
sheet enriched in this session."""
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.spec_customization.models import PaperSpecExtractionJob
from apps.spec_customization.services.exporters.workbook_preview import build_preview

TARGET_SHEETS = [
    'PMCD',
    'MaterialsData',
    'PipeBranch',
    'ThicknessDataRule',
    'NutSelectionFilter',
    'AllowablePipingMaterialsClass',
    'PipingCommodityFilter',
    'GasketSelectionFilter',
    'BoltSelectionFilter',
]

# Columns we explicitly enriched per sheet (user-requested verification subset)
WATCH_COLS = {
    'PipeBranch': ['ShortCode', 'AngleLow', 'AngleHigh'],
    'ThicknessDataRule': ['PreferredSchedule1', 'PreferredSchedule2', 'ThreadThickness'],
    'NutSelectionFilter': ['BoltType', 'MaximumTemperature', 'Comments', 'PipingNote1'],
    'AllowablePipingMaterialsClass': ['FluidCode'],
    'PipingCommodityFilter': ['PreferredPipeLength', 'PipingNote1', 'QuantityOfAltReportableParts'],
    'GasketSelectionFilter': ['MaximumTemperature', 'MinimumTemperature', 'FluidCode',
                              'Priority', 'Comments', 'PipingNote1',
                              'QuantityOfReportableParts', 'QuantityOfAltReportableParts',
                              'AlternateEndPreparation', 'AlternatePressureRating',
                              'AlternateEndStandard', 'ScheduleThickness', 'RingNumber',
                              'AltReportableCommodityCode', 'ReportableCommodityCode'],
    'BoltSelectionFilter': ['AlternateEndPreparation', 'AlternatePressureRating',
                            'AlternateEndStandard', 'Priority', 'Comments',
                            'PipingNote1', 'LubricationRequirements'],
}

job = PaperSpecExtractionJob.objects.order_by('-created_at').first()
print(f'\n=== Job: {job.id} ===')
preview = build_preview(job, 'spec')
sheets = {s['name']: s for s in preview['sheets']}

print(f'\n{"Sheet":<35} {"Rows":>6}  Sample Enriched Columns (first data row)')
print('-' * 110)

for name in TARGET_SHEETS:
    s = sheets.get(name)
    if not s:
        print(f'{name:<35} MISSING')
        continue
    rows = s['rows']
    # find first data row (skip Start sentinel)
    sample = None
    for r in rows[1:]:
        cells = r['cells']
        if any(v not in (None, '', 'None') for k, v in cells.items() if k != 'Head'):
            sample = cells
            break
    print(f'{name:<35} {len(rows):>6}')
    cols = WATCH_COLS.get(name, [])
    for c in cols:
        v = (sample or {}).get(c, '')
        status = 'OK ' if v not in (None, '', 'None') else '-- '
        print(f'                                          {status}{c}: {v!r}')

print('\n=== DONE ===')
