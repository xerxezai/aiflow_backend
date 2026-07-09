"""
DRF views for the Time Sheet Analytics feature.

All endpoints are mounted under /api/v1/timesheet/ and require an
authenticated RAD AI user (uses the default JWTAuthentication +
IsAuthenticated from REST_FRAMEWORK).

Endpoints:
    GET /health/                    Connection + driver + config snapshot
    GET /discovery/databases/       List databases on the server
    GET /discovery/tables/          List tables in a database
    GET /discovery/columns/         List columns in a table
    GET /discovery/preview/         TOP N rows of any table (for column mapping)
    GET /live/                      Current punches today + IN/OUT/late summary
    GET /daily/?date=YYYY-MM-DD     Per-user hours for a date
    GET /monthly/?year=&month=      Per-user roll-up for a month
    GET /user/?employee_code=&from=&to=     Per-user history
    GET /export/daily/?date=        Excel download
    GET /export/monthly/?year=&month=       Excel download
    GET /export/monthly/pdf/?year=&month=   PDF download
"""
from __future__ import annotations

import datetime as dt
import logging

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response

from . import config as ts_config
from . import discovery as ts_discovery
from . import exports as ts_exports
from . import services as ts_services_sql
from . import mirror_services as ts_services_mirror
from . import sqlserver as ts_sql
from .mirror_views import ingest_events, ingest_users  # re-exported via urls.py
from .sqlserver import TimesheetConnectionError, TimesheetDriverError

logger = logging.getLogger(__name__)


def _svc():
    """Soft-coded backend dispatcher. Resolved at call time so flipping
    TIMESHEET_DATA_SOURCE in Railway env vars takes effect on the very next
    request without a code change."""
    return ts_services_mirror if ts_config.DATA_SOURCE == 'mirror' else ts_services_sql


# Soft-coded copy returned when the SQL Server can't be reached from this
# environment (e.g. Railway production has no route to the office LAN IP).
# Frontend treats `configured: False` as the trigger for the 'Not Configured'
# banner, so connection failures are presented gracefully instead of red.
_UNREACHABLE_MESSAGE = (
    'Time Sheet biometric server is not reachable from this environment. '
    'This feature is only available on the office network.'
)
_DRIVER_MISSING_MESSAGE = (
    'SQL Server driver is not installed on this environment.'
)


def _graceful_unavailable(exc: Exception, *, extra_keys: dict | None = None):
    """Return a 200 OK payload that mirrors the not-configured response shape
    so the existing frontend banner handles it without changes."""
    if isinstance(exc, TimesheetDriverError):
        reason, message = 'driver_missing', _DRIVER_MISSING_MESSAGE
    else:
        reason, message = 'unreachable', _UNREACHABLE_MESSAGE
    logger.info('[timesheet] gracefully degrading (%s): %s', reason, exc)
    payload = {
        'configured': False,
        'reason': reason,
        'message': message,
        'rows': [],
        'summary': {},
    }
    if extra_keys:
        payload.update(extra_keys)
    return Response(payload)


# ─────────────────────────────────────────────────────────────────────────────
# Health + Discovery (Setup wizard)
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def health(request):
    """Aggregated health snapshot used by the Setup wizard.

    Critical guarantee: this endpoint must NEVER raise an unhandled exception
    or block long enough for Railway's gunicorn worker to time out — if it
    does, Railway returns its own 502 page without CORS headers and the
    frontend reports a misleading 'CORS' error. Every code path is wrapped
    so the response always carries the project's CORS middleware headers.
    """
    try:
        cfg = ts_config.configuration_status()
    except Exception as exc:
        logger.warning('[timesheet.health] config_status failed: %s', exc)
        cfg = {'configured': False, 'data_source': ts_config.DATA_SOURCE,
               'error': str(exc)}

    # ── Mirror mode: serve health from the Postgres mirror table only.
    # No outbound SQL Server ping (Railway has no route to the office LAN).
    if ts_config.DATA_SOURCE == 'mirror':
        try:
            from .models import TimesheetEvent
            qs = TimesheetEvent.objects.all()
            event_count = qs.count()
            latest = qs.order_by('-event_time').values_list(
                'event_time', flat=True).first()
            ping = {
                'ok':           event_count > 0,
                'mode':         'mirror',
                'event_count':  event_count,
                'latest_event': latest.isoformat() if latest else None,
            }
            drv = {'driver_in_use': 'postgres-mirror', 'available': True}
        except Exception as exc:
            logger.warning('[timesheet.health] mirror read failed: %s', exc)
            ping = {'ok': False, 'mode': 'mirror', 'error': str(exc)}
            drv = {'driver_in_use': 'postgres-mirror', 'available': False,
                   'error': str(exc)}
        return Response({
            'driver':         drv,
            'config':         cfg,
            'ping':           ping,
            'data_source':    'mirror',
            'sqlserver_host': '',
            'sqlserver_port': 0,
        })

    # ── SQL Server (direct LAN) mode: original behaviour, but every call is
    # wrapped so we never bubble an exception up through the worker.
    try:
        drv = ts_sql.driver_status()
    except Exception as exc:
        logger.warning('[timesheet.health] driver_status failed: %s', exc)
        drv = {'driver_in_use': '', 'available': False, 'error': str(exc)}

    try:
        if cfg.get('configured') or drv.get('driver_in_use'):
            ping = ts_sql.health_check()
        else:
            ping = {'ok': False}
    except Exception as exc:
        logger.warning('[timesheet.health] ping failed: %s', exc)
        ping = {'ok': False, 'error': str(exc)}

    return Response({
        'driver':         drv,
        'config':         cfg,
        'ping':           ping,
        'data_source':    'sqlserver',
        'sqlserver_host': ts_config.SQLSERVER.get('host', ''),
        'sqlserver_port': ts_config.SQLSERVER.get('port', 0),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def databases(request):
    try:
        return Response({'databases': ts_discovery.list_databases()})
    except Exception as exc:
        return _error_response(exc)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def tables(request):
    db = request.GET.get('database') or ts_config.SQLSERVER['database']
    if not db:
        return Response({'error': 'database query param required'},
                        status=status.HTTP_400_BAD_REQUEST)
    try:
        return Response({'database': db, 'tables': ts_discovery.list_tables(db)})
    except Exception as exc:
        return _error_response(exc)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def columns(request):
    db = request.GET.get('database') or ts_config.SQLSERVER['database']
    tbl = request.GET.get('table')
    if not (db and tbl):
        return Response({'error': 'database + table query params required'},
                        status=status.HTTP_400_BAD_REQUEST)
    try:
        return Response({
            'database': db,
            'table': tbl,
            'columns': ts_discovery.list_columns(db, tbl),
        })
    except Exception as exc:
        return _error_response(exc)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def preview(request):
    db = request.GET.get('database') or ts_config.SQLSERVER['database']
    tbl = request.GET.get('table')
    limit = int(request.GET.get('limit') or 5)
    if not (db and tbl):
        return Response({'error': 'database + table query params required'},
                        status=status.HTTP_400_BAD_REQUEST)
    try:
        return Response({
            'database': db,
            'table': tbl,
            'rows': ts_discovery.preview_table(db, tbl, limit),
        })
    except Exception as exc:
        return _error_response(exc)


# ─────────────────────────────────────────────────────────────────────────────
# Reports
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def live(request):
    if not ts_config.is_configured():
        return Response({'configured': False, 'rows': [], 'summary': {}})
    try:
        # ── Production diagnostic logging (soft-coded via DATA_SOURCE check) ──
        # Helps diagnose "No punch events" issue on Railway while preserving
        # all existing functionality. Logs only when using mirror mode to avoid
        # noise from local SQL Server queries.
        if ts_config.DATA_SOURCE == 'mirror':
            from .models import TimesheetEvent
            event_count = TimesheetEvent.objects.count()
            logger.info(
                '[timesheet.live] Production diagnostic: DATA_SOURCE=%s, '
                'TimesheetEvent.count=%d, lookback_hours=%d',
                ts_config.DATA_SOURCE,
                event_count,
                ts_config.RULES.get('live_lookback_hours', 20)
            )
        
        result = _svc().live_status()
        
        # Log result summary for production debugging (mirror mode only)
        if ts_config.DATA_SOURCE == 'mirror':
            logger.info(
                '[timesheet.live] Result: rows=%d, summary=%s',
                len(result.get('rows', [])),
                result.get('summary', {})
            )
        
        return Response({'configured': True, **result})
    except (TimesheetConnectionError, TimesheetDriverError) as exc:
        return _graceful_unavailable(exc)
    except Exception as exc:
        return _error_response(exc)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def daily(request):
    if not ts_config.is_configured():
        return Response({'configured': False, 'rows': []})
    try:
        return Response({'configured': True, **_svc().daily_report(request.GET.get('date'))})
    except (TimesheetConnectionError, TimesheetDriverError) as exc:
        return _graceful_unavailable(exc)
    except Exception as exc:
        return _error_response(exc)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def monthly(request):
    if not ts_config.is_configured():
        return Response({'configured': False, 'rows': []})
    y = request.GET.get('year')
    m = request.GET.get('month')
    try:
        return Response({
            'configured': True,
            **_svc().monthly_report(int(y) if y else None, int(m) if m else None),
        })
    except (TimesheetConnectionError, TimesheetDriverError) as exc:
        return _graceful_unavailable(exc)
    except Exception as exc:
        return _error_response(exc)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def user_drill(request):
    if not ts_config.is_configured():
        return Response({'configured': False, 'rows': []})

    # Resolve identifiers. If the caller passes `user_id`, look up the RAD AI
    # UserProfile and use BOTH that user's email and their `employee_id` so the
    # downstream OR-match has the best chance of finding biometric rows even
    # when one of the two identifiers is missing/stale.
    employee_code = request.GET.get('employee_code')
    email         = request.GET.get('email')
    user_id       = request.GET.get('user_id')
    resolved      = {'used_user_id': False, 'employee_code': employee_code, 'email': email}
    if user_id and not (employee_code and email):
        try:
            from apps.rbac.models import UserProfile
            p = UserProfile.objects.select_related('user').filter(user_id=user_id, is_deleted=False).first()
            if p:
                if not employee_code and p.employee_id:
                    employee_code = str(p.employee_id)
                if not email and p.user and p.user.email:
                    email = p.user.email
                resolved.update({
                    'used_user_id':  True,
                    'employee_code': employee_code,
                    'email':         email,
                })
        except Exception:
            pass  # never let lookup failure break the report

    try:
        payload = _svc().user_history(
            employee_code=employee_code,
            email=email,
            from_date=request.GET.get('from'),
            to_date=request.GET.get('to'),
            include_punches=str(request.GET.get('include_punches', '')).lower() in ('1', 'true', 'yes'),
            with_trace=True,
        )
        payload['configured'] = True
        payload['resolved']   = resolved
        return Response(payload)
    except (TimesheetConnectionError, TimesheetDriverError) as exc:
        return _graceful_unavailable(exc)
    except Exception as exc:
        return _error_response(exc)


# ─────────────────────────────────────────────────────────────────────────────
# Self-Service: My Attendance (auto-scoped to current user)
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_live_attendance(request):
    """Role-based self-service endpoint: returns ONLY the current user's live
    attendance status (IN/OUT, current punch time, hours today).
    
    Returns:
        {
            'configured': bool,
            'employee_code': str,
            'email': str,
            'data': {
                'first_in': datetime,       // First IN punch time today
                'last_punch': datetime,     // Absolute last punch time
                'hours_today': float,       // Total hours worked (capped at max)
                'punch_in_count': int,      // Number of IN punches
                'punch_out_count': int,     // Number of OUT punches
                'is_in': bool,              // Whether currently checked IN
                'is_late': bool,            // Late arrival detection
            }
        }
    """
    if not ts_config.is_configured():
        return Response({'configured': False, 'data': None})
    
    try:
        from apps.rbac.models import UserProfile
        p = UserProfile.objects.select_related('user').filter(
            user=request.user, is_deleted=False
        ).first()
        
        employee_code = str(p.employee_id) if p and p.employee_id else None
        email = request.user.email if request.user else None
        
        if not employee_code:
            return Response({
                'configured': True,
                'error': 'Your profile does not have an employee_id linked. Please contact HR.',
                'data': None,
            })
        
        # Calculate detailed live metrics for current user
        metrics = _svc()._calculate_live_metrics(employee_code)
        
        return Response({
            'configured': True,
            'employee_code': employee_code,
            'email': email,
            'as_of': dt.datetime.now().isoformat(),
            'data': metrics,
        })
        
    except (TimesheetConnectionError, TimesheetDriverError) as exc:
        return _graceful_unavailable(exc)
    except Exception as exc:
        logger.exception('[timesheet.my_live_attendance] failed: %s', exc)
        return _error_response(exc)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_monthly_attendance(request):
    """Role-based self-service endpoint: returns ONLY the current user's monthly
    attendance data. Automatically resolves the user's employee_code from their
    RBAC profile and filters the timesheet data accordingly.
    
    Query params:
        year  (optional) — defaults to current year
        month (optional) — defaults to current month
    
    Returns:
        {
            'configured': bool,
            'employee_code': str,
            'email': str,
            'data': {
                'employee_code': str,
                'employee_name': str,
                'total_hours': float,
                'total_overtime': float,
                'days_present': int,
                'working_days': int,
                'days_detail': [...]  // day-by-day breakdown
            }
        }
    """
    if not ts_config.is_configured():
        return Response({'configured': False, 'data': None})
    
    # Auto-resolve from request.user (JWT-authenticated)
    try:
        from apps.rbac.models import UserProfile
        p = UserProfile.objects.select_related('user').filter(
            user=request.user, is_deleted=False
        ).first()
        
        employee_code = str(p.employee_id) if p and p.employee_id else None
        email = request.user.email if request.user else None
        
        if not employee_code and not email:
            return Response({
                'configured': True,
                'error': 'Your profile does not have an employee_id linked. Please contact HR.',
                'data': None,
            })
        
        # Get year/month from query params or default to current
        y = request.GET.get('year')
        m = request.GET.get('month')
        import datetime as dt
        now = dt.datetime.now()
        year = int(y) if y else now.year
        month = int(m) if m else now.month
        
        # Fetch monthly report for all employees, then filter to current user
        monthly_data = _svc().monthly_report(year, month)
        rows = monthly_data.get('rows', [])
        working_days = monthly_data.get('working_days_in_month')
        
        # Find current user's row
        user_data = None
        if employee_code:
            code_lower = employee_code.lower()
            user_data = next(
                (r for r in rows 
                 if str(r.get('employee_code', '')).lower() == code_lower or
                    str(r.get('code', '')).lower() == code_lower),
                None
            )
        
        # Fallback to email match if code didn't work
        if not user_data and email:
            email_lower = email.lower()
            user_data = next(
                (r for r in rows 
                 if str(r.get('email', '')).lower() == email_lower),
                None
            )
        
        # Enrich with working_days if found
        if user_data and working_days:
            user_data['working_days'] = working_days
        
        return Response({
            'configured': True,
            'employee_code': employee_code,
            'email': email,
            'year': year,
            'month': month,
            'data': user_data,
        })
        
    except (TimesheetConnectionError, TimesheetDriverError) as exc:
        return _graceful_unavailable(exc)
    except Exception as exc:
        logger.exception('[timesheet.my_monthly_attendance] failed: %s', exc)
        return _error_response(exc)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_daily_attendance(request):
    """Role-based self-service endpoint: returns ONLY the current user's daily
    attendance data.
    
    Query params:
        date (optional) — YYYY-MM-DD, defaults to today
    
    Returns:
        {
            'configured': bool,
            'employee_code': str,
            'email': str,
            'date': str,
            'data': {...}  // user's attendance record for that day
        }
    """
    if not ts_config.is_configured():
        return Response({'configured': False, 'data': None})
    
    try:
        from apps.rbac.models import UserProfile
        p = UserProfile.objects.select_related('user').filter(
            user=request.user, is_deleted=False
        ).first()
        
        employee_code = str(p.employee_id) if p and p.employee_id else None
        email = request.user.email if request.user else None
        
        if not employee_code and not email:
            return Response({
                'configured': True,
                'error': 'Your profile does not have an employee_id linked. Please contact HR.',
                'data': None,
            })
        
        # Get date from query params or default to today
        import datetime as dt
        date_str = request.GET.get('date')
        if not date_str:
            date_str = dt.datetime.now().strftime('%Y-%m-%d')
        
        # Fetch daily report
        daily_data = _svc().daily_report(date_str)
        rows = daily_data.get('rows', [])
        
        # Find current user's row
        user_data = None
        if employee_code:
            code_lower = employee_code.lower()
            user_data = next(
                (r for r in rows 
                 if str(r.get('employee_code', '')).lower() == code_lower or
                    str(r.get('code', '')).lower() == code_lower),
                None
            )
        
        # Fallback to email match
        if not user_data and email:
            email_lower = email.lower()
            user_data = next(
                (r for r in rows 
                 if str(r.get('email', '')).lower() == email_lower),
                None
            )
        
        return Response({
            'configured': True,
            'employee_code': employee_code,
            'email': email,
            'date': date_str,
            'data': user_data,
        })
        
    except (TimesheetConnectionError, TimesheetDriverError) as exc:
        return _graceful_unavailable(exc)
    except Exception as exc:
        logger.exception('[timesheet.my_daily_attendance] failed: %s', exc)
        return _error_response(exc)


# ─────────────────────────────────────────────────────────────────────────────
# Reverse lookup: biometric code → {name, email}
# Used by /hr/employees search box so HR can type a badge number (e.g. 22972)
# and the page jumps to the matching RAD AI user. Works against whichever
# backend (sqlserver or mirror) is active so it stays consistent end-to-end.
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def lookup_by_code(request):
    code = (request.GET.get('code') or '').strip()
    if not code:
        return Response({'found': False, 'error': 'code required'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        row = _svc().lookup_by_code(code)
        if not row:
            return Response({'found': False, 'code': code})
        return Response({'found': True, **row})
    except (TimesheetConnectionError, TimesheetDriverError) as exc:
        return _graceful_unavailable(exc, extra_keys={'found': False, 'code': code})
    except Exception as exc:
        return _error_response(exc)



@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdminUser])
def lookup_debug(request):
    """Admin-only diagnostic for per-user biometric lookup. Same resolver
    pipeline as ``/timesheet/user/`` but returns ONLY the diagnostic trace,
    no event rows. Use when an HR record shows the "No biometric record
    matches…" banner — the response explains exactly which step failed and
    which knob to turn (sync agent, user-master columns, RAD AI profile).

    Query params (any combination):
        email          — RAD AI / corporate email to probe
        employee_code  — RAD AI UserProfile.employee_id (NOT biometric code)
        user_id        — UserProfile.user_id; auto-resolves email + code

    Response shape:
        {
            'configured':    bool,
            'inputs':        {'email','employee_code','user_id'},
            'resolved':      {'email','employee_code','used_user_id'},
            'trace':         { … same shape as user_history diagnostic },
            'master_table':  {'total_rows', 'sample': [first 5 rows by code]}
        }
    """
    email          = (request.GET.get('email') or '').strip() or None
    employee_code  = (request.GET.get('employee_code') or '').strip() or None
    user_id        = (request.GET.get('user_id') or '').strip() or None
    resolved       = {'used_user_id': False, 'employee_code': employee_code, 'email': email}

    if user_id:
        try:
            from apps.rbac.models import UserProfile
            p = UserProfile.objects.select_related('user').filter(user_id=user_id, is_deleted=False).first()
            if p:
                if not employee_code and p.employee_id:
                    employee_code = str(p.employee_id)
                if not email and p.user and p.user.email:
                    email = p.user.email
                resolved.update({
                    'used_user_id':  True,
                    'employee_code': employee_code,
                    'email':         email,
                })
        except Exception as exc:
            resolved['profile_error'] = str(exc)[:200]

    if not (email or employee_code):
        return Response({
            'configured': bool(ts_config.is_configured()),
            'error':      'supply at least one of email / employee_code / user_id',
        }, status=status.HTTP_400_BAD_REQUEST)

    out: dict = {
        'configured':    bool(ts_config.is_configured()),
        'data_source':   ts_config.DATA_SOURCE,
        'inputs':        {'email': email, 'employee_code': employee_code, 'user_id': user_id},
        'resolved':      resolved,
    }

    # Only mirror backend exposes the structured resolver trace today.
    if ts_config.DATA_SOURCE == 'mirror':
        try:
            from . import mirror_services as msrv
            from .models import BiometricUserMaster
            _, _, trace = msrv._resolve_user_aliases_mirror(
                employee_code, email, with_trace=True,
            )
            out['trace'] = trace
            try:
                out['master_table'] = {
                    'total_rows': BiometricUserMaster.objects.count(),
                    'sample': list(
                        BiometricUserMaster.objects
                            .values('employee_code', 'full_name', 'office_email', 'personal_email')
                            .order_by('employee_code')[:5]
                    ),
                }
            except Exception as exc:
                out['master_table'] = {'error': str(exc)[:200]}
        except Exception as exc:
            out['trace_error'] = str(exc)[:500]
            logger.exception('[timesheet.lookup_debug] mirror trace failed: %s', exc)
    else:
        out['note'] = 'lookup_debug trace currently implemented for DATA_SOURCE=mirror only'

    return Response(out)



@api_view(['GET'])
@permission_classes([IsAuthenticated])
def export_daily_excel(request):
    if not ts_config.is_configured():
        return Response({'error': 'Time sheet not configured'},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)
    try:
        return ts_exports.export_daily_excel(request.GET.get('date'))
    except Exception as exc:
        return _error_response(exc)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def export_monthly_excel(request):
    if not ts_config.is_configured():
        return Response({'error': 'Time sheet not configured'},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)
    y = request.GET.get('year')
    m = request.GET.get('month')
    try:
        return ts_exports.export_monthly_excel(int(y) if y else None,
                                               int(m) if m else None)
    except Exception as exc:
        return _error_response(exc)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def export_monthly_pdf(request):
    if not ts_config.is_configured():
        return Response({'error': 'Time sheet not configured'},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)
    y = request.GET.get('year')
    m = request.GET.get('month')
    try:
        return ts_exports.export_monthly_pdf(int(y) if y else None,
                                             int(m) if m else None)
    except Exception as exc:
        return _error_response(exc)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def export_summary_excel(request):
    """Summary pivot (employee x day grid) as Excel — mirrors the HR Summary tab."""
    if not ts_config.is_configured():
        return Response({'error': 'Time sheet not configured'},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)
    y = request.GET.get('year')
    m = request.GET.get('month')
    try:
        return ts_exports.export_summary_excel(int(y) if y else None,
                                               int(m) if m else None)
    except Exception as exc:
        return _error_response(exc)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def export_summary_pdf(request):
    """Summary roll-up PDF for the selected month."""
    if not ts_config.is_configured():
        return Response({'error': 'Time sheet not configured'},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)
    y = request.GET.get('year')
    m = request.GET.get('month')
    try:
        return ts_exports.export_summary_pdf(int(y) if y else None,
                                             int(m) if m else None)
    except Exception as exc:
        return _error_response(exc)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def export_yearly_excel(request):
    """Full-year Excel: one sheet per month + a 12-month summary sheet."""
    if not ts_config.is_configured():
        return Response({'error': 'Time sheet not configured'},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)
    y = request.GET.get('year')
    try:
        return ts_exports.export_yearly_excel(int(y) if y else None)
    except Exception as exc:
        return _error_response(exc)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def export_yearly_pdf(request):
    """Full-year 12-month summary PDF."""
    if not ts_config.is_configured():
        return Response({'error': 'Time sheet not configured'},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)
    y = request.GET.get('year')
    try:
        return ts_exports.export_yearly_pdf(int(y) if y else None)
    except Exception as exc:
        return _error_response(exc)


# ─────────────────────────────────────────────────────────────────────────────
def _error_response(exc: Exception):
    # Connection or missing-driver errors mean the on-prem SQL Server is not
    # reachable from this environment (e.g. Railway production has no route to
    # an office LAN IP / ngrok tunnel offline). Surface as a graceful 200 with
    # `configured: false` so the existing frontend banner handles it without
    # showing a red error — same shape as the not-configured branch.
    if isinstance(exc, (TimesheetDriverError, TimesheetConnectionError)):
        return _graceful_unavailable(exc)
    kind = type(exc).__name__
    logger.warning('[timesheet] %s: %s', kind, exc)
    return Response(
        {'error': str(exc), 'kind': kind},
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
