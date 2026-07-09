from django.urls import path

from . import views

app_name = 'timesheet'

urlpatterns = [
    # Setup / health
    path('health/',                views.health,           name='health'),

    # Discovery wizard
    path('discovery/databases/',   views.databases,        name='databases'),
    path('discovery/tables/',      views.tables,           name='tables'),
    path('discovery/columns/',     views.columns,          name='columns'),
    path('discovery/preview/',     views.preview,          name='preview'),

    # Reports
    path('live/',                  views.live,             name='live'),
    path('daily/',                 views.daily,            name='daily'),
    path('monthly/',               views.monthly,          name='monthly'),
    path('user/',                  views.user_drill,       name='user'),
    path('lookup-by-code/',        views.lookup_by_code,   name='lookup-by-code'),
    path('lookup-debug/',          views.lookup_debug,     name='lookup-debug'),
    
    # Self-Service (role-based, auto-scoped to current user)
    path('my-attendance/live/',    views.my_live_attendance,    name='my-live-attendance'),
    path('my-attendance/monthly/', views.my_monthly_attendance, name='my-monthly-attendance'),
    path('my-attendance/daily/',   views.my_daily_attendance,   name='my-daily-attendance'),

    # Exports
    path('export/daily/',          views.export_daily_excel,    name='export-daily'),
    path('export/monthly/',        views.export_monthly_excel,  name='export-monthly'),
    path('export/monthly/pdf/',    views.export_monthly_pdf,    name='export-monthly-pdf'),
    path('export/summary/',        views.export_summary_excel,  name='export-summary'),
    path('export/summary/pdf/',    views.export_summary_pdf,    name='export-summary-pdf'),
    path('export/yearly/',         views.export_yearly_excel,   name='export-yearly'),
    path('export/yearly/pdf/',     views.export_yearly_pdf,     name='export-yearly-pdf'),

    # Mirror ingest — office-side sync agent POSTs batched events here.
    # Auth via X-Timesheet-Mirror-Key header (TIMESHEET_MIRROR_API_KEY env var).
    path('mirror/ingest/',         views.ingest_events,        name='mirror-ingest'),
    path('mirror/ingest-users/',   views.ingest_users,         name='mirror-ingest-users'),
]
