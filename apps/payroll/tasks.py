"""
Payroll Celery Tasks
====================
Async background jobs triggered after synchronous request handling.

Current tasks:
  upload_master_payroll_to_s3 — generates the Excel from a saved MasterPayrollImport
                                  and uploads it to the payroll/exports/ S3 prefix.
                                  Called after generate_master_payroll saves to DB.
"""
from __future__ import annotations

import io
import logging
import uuid as uuid_lib
from decimal import Decimal

from celery import shared_task

logger = logging.getLogger(__name__)

# ── Soft-coded retry / timeout constants ─────────────────────────────────────
_MAX_RETRIES     = 3
_RETRY_BACKOFF_S = 60     # seconds between retries (exponential applied by Celery)
_TASK_TIMEOUT_S  = 300    # hard limit: 5 minutes

# Excel styling constants (keep in sync with views.py generate_master_payroll)
_HDR_COLOR   = '2563EB'   # blue-600
_HDR_FONT_CL = 'FFFFFF'


@shared_task(
    name='payroll.upload_master_payroll_to_s3',
    bind=True,
    max_retries=_MAX_RETRIES,
    default_retry_delay=_RETRY_BACKOFF_S,
    time_limit=_TASK_TIMEOUT_S,
)
def upload_master_payroll_to_s3(self, import_id: str) -> dict:
    """
    Build the 15-column master payroll Excel from the saved DB rows and
    upload it to the S3 exports bucket.

    On success: updates MasterPayrollImport.status = 'uploaded' and sets s3_key.
    On failure after retries: sets status = 'failed'.

    Args:
        import_id: UUID string of a MasterPayrollImport record.
    """
    from apps.payroll.models import MasterPayrollImport, MasterPayrollImportStatus
    from apps.payroll.storage import PayrollExportStorage, S3_AVAILABLE

    try:
        session = MasterPayrollImport.objects.get(id=import_id)
    except MasterPayrollImport.DoesNotExist:
        logger.error('upload_master_payroll_to_s3: import %s not found', import_id)
        return {'status': 'not_found', 'import_id': import_id}

    # ── 1. Build Excel in memory ──────────────────────────────────────────────
    try:
        import openpyxl
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f'Payroll Master {session.year}-{session.month:02d}'

        hdr_font  = Font(bold=True, color=_HDR_FONT_CL)
        hdr_fill  = PatternFill('solid', fgColor=_HDR_COLOR)
        thin      = Side(style='thin', color='CCCCCC')
        border    = Border(left=thin, right=thin, top=thin, bottom=thin)

        headers = [
            'Employee Code',        # 1
            'Employee Name',        # 2
            'Joining Date',         # 3
            'No. of Working Hours', # 4
            'Employee Salary',      # 5
            'Basic',                # 6
            'Allowance',            # 7
            'Transportation',       # 8
            'Home Allowance',       # 9
            'Other Allowance',      # 10
            'Other Pay',            # 11
            'Details',              # 12
            'Salary Deduction',     # 13
            'Deduction Details',    # 14
            'Final Salary',         # 15
            'Leave Enc. Days',      # 16 — encashed leave days for this period
            'Leave Enc. Pay',       # 17 — monetary encashment amount (AED)
        ]
        for col_idx, hdr in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_idx, value=hdr)
            cell.font      = hdr_font
            cell.fill      = hdr_fill
            cell.alignment = Alignment(horizontal='center')
            cell.border    = border
            ws.column_dimensions[cell.column_letter].width = max(14, len(hdr) + 4)

        rows_qs = session.rows.all().order_by('employee_name')
        for r_idx, row in enumerate(rows_qs, 2):
            vals = [
                row.employee_code,
                row.employee_name,
                row.joining_date or '',
                float(row.total_hours),
                float(row.employee_salary),
                float(row.basic_salary),
                float(row.total_allowances),
                float(row.transport_allowance),
                float(row.housing_allowance),
                float(row.other_allowances),
                float(row.other_pay),
                row.details or '',
                float(row.total_deductions),
                row.deduction_details or '',
                float(row.final_salary),
                float(row.leave_encashment_days),   # col 16
                float(row.leave_encashment_pay),    # col 17
            ]
            for col_idx, val in enumerate(vals, 1):
                cell = ws.cell(row=r_idx, column=col_idx, value=val)
                cell.border = border
                if isinstance(val, float):
                    cell.number_format = '#,##0.00'

        # Freeze header row
        ws.freeze_panes = 'A2'

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        excel_bytes = buf.read()

    except Exception as exc:
        logger.exception('upload_master_payroll_to_s3: Excel build failed for %s', import_id)
        session.status = MasterPayrollImportStatus.FAILED
        session.save(update_fields=['status'])
        raise self.retry(exc=exc)

    # ── 2. Upload to S3 ───────────────────────────────────────────────────────
    if not S3_AVAILABLE:
        # S3 not configured (local dev) — mark ready, skip upload
        session.status = MasterPayrollImportStatus.READY
        session.save(update_fields=['status'])
        logger.info(
            'upload_master_payroll_to_s3: S3 not available — import %s marked ready (no upload)',
            import_id,
        )
        return {'status': 'ready_no_s3', 'import_id': import_id}

    try:
        storage    = PayrollExportStorage()
        filename   = f'master_payroll_{session.year}_{session.month:02d}_{import_id[:8]}.xlsx'
        s3_name    = storage.save(filename, io.BytesIO(excel_bytes))
        # s3_name is just the filename within the storage location prefix
        full_key   = f'{storage.location}/{s3_name}'

        session.s3_key = full_key
        session.status = MasterPayrollImportStatus.UPLOADED
        session.save(update_fields=['s3_key', 'status'])

        logger.info(
            'upload_master_payroll_to_s3: uploaded %s → s3://%s',
            filename, full_key,
        )
        return {'status': 'uploaded', 'import_id': import_id, 's3_key': full_key}

    except Exception as exc:
        logger.exception('upload_master_payroll_to_s3: S3 upload failed for %s', import_id)
        session.status = MasterPayrollImportStatus.FAILED
        session.save(update_fields=['status'])
        raise self.retry(exc=exc)


# ─────────────────────────────────────────────────────────────────────────────
# MONTHLY LEAVE ACCRUAL — Automated Task (runs on 1st of each month)
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(
    name='payroll.run_monthly_leave_accrual',
    bind=True,
    max_retries=_MAX_RETRIES,
    default_retry_delay=_RETRY_BACKOFF_S,
    time_limit=_TASK_TIMEOUT_S,
)
def run_monthly_leave_accrual(self, year: int = None, month: int = None, triggered_by: str = 'celery_beat') -> dict:
    """
    Automated monthly leave accrual task.
    
    Runs on the 1st of each month at 00:05 AM (configured in config.py BEAT_SCHEDULE).
    Adds standard monthly accrual (1.83 days = 22 days/year ÷ 12 months) to all employees.
    
    Parameters
    ----------
    year : int, optional
        Target year (defaults to current year)
    month : int, optional
        Target month 1-12 (defaults to current month)
    triggered_by : str, default='celery_beat'
        Source identifier: 'celery_beat' (auto) | 'manual' (admin) | 'api' (HR Manager)
    
    Returns
    -------
    dict
        Execution summary with counts and status
    
    Idempotency
    -----------
    Safe to run multiple times — checks MonthlyLeaveAccrualLog to prevent duplicate processing.
    If a success/partial record exists for (year, month, triggered_by), skips execution.
    """
    from datetime import date
    from django.db import transaction
    from apps.payroll.models import (
        EmployeeLeaveRecord, EmployeeLeaveMonthly, MonthlyLeaveAccrualLog
    )
    from apps.payroll.services.leave_accrual import (
        MONTHLY_LEAVE_ACCRUAL, ANNUAL_LEAVE_DAYS, compute_monthly_earned, _dec
    )
    
    # Default to current year/month if not specified
    today = date.today()
    year = year or today.year
    month = month or today.month
    
    logger.info(
        'run_monthly_leave_accrual: Starting for %s/%02d (triggered_by=%s)',
        year, month, triggered_by
    )
    
    # ── Idempotency check: prevent duplicate runs ─────────────────────────────
    existing = MonthlyLeaveAccrualLog.objects.filter(
        year=year, month=month, triggered_by=triggered_by,
        status__in=['success', 'partial']
    ).first()
    
    if existing:
        logger.warning(
            'run_monthly_leave_accrual: Skipping — already processed for %s/%02d by %s at %s',
            year, month, triggered_by, existing.executed_at
        )
        return {
            'status': 'skipped_duplicate',
            'year': year,
            'month': month,
            'existing_log_id': str(existing.id),
            'message': f'Already processed on {existing.executed_at}'
        }
    
    # ── Process all employee leave records ────────────────────────────────────
    records_processed = 0
    records_created = 0
    records_updated = 0
    errors = []
    
    try:
        qs = EmployeeLeaveRecord.objects.filter(year=year)
        
        for record in qs:
            records_processed += 1
            
            try:
                # Compute earned leave for this month using soft-coded formula
                earned = compute_monthly_earned(
                    record.joining_date,
                    year,
                    month,
                    record.annual_entitlement or ANNUAL_LEAVE_DAYS,
                    reference_date=today
                )
                
                # Create or update monthly record
                monthly, was_created = EmployeeLeaveMonthly.objects.get_or_create(
                    record=record,
                    month=month,
                    defaults={
                        'earned': earned,
                        'taken': _dec(0),
                        'encashed': _dec(0),
                        'balance': _dec(0),
                    }
                )
                
                if was_created:
                    records_created += 1
                    logger.debug(
                        'Created monthly accrual: %s/%02d for %s (%.2f days)',
                        year, month, record.employee_code, float(earned)
                    )
                elif monthly.earned != earned:
                    # Update if earned value changed (e.g., joining date corrected)
                    monthly.earned = earned
                    monthly.save(update_fields=['earned'])
                    records_updated += 1
                    logger.debug(
                        'Updated monthly accrual: %s/%02d for %s (%.2f days)',
                        year, month, record.employee_code, float(earned)
                    )
                
            except Exception as e:
                error_msg = f'{record.employee_code}: {str(e)}'
                errors.append(error_msg)
                logger.error('run_monthly_leave_accrual: %s', error_msg)
        
        # ── Log execution ──────────────────────────────────────────────────────
        status = 'success' if not errors else ('partial' if records_created > 0 else 'failed')
        
        log = MonthlyLeaveAccrualLog.objects.create(
            year=year,
            month=month,
            executed_at=today,
            triggered_by=triggered_by,
            records_processed=records_processed,
            records_created=records_created,
            records_updated=records_updated,
            monthly_accrual_used=_dec(MONTHLY_LEAVE_ACCRUAL),
            branch_filter=None,  # Future: support branch filtering
            status=status,
            error_message='; '.join(errors[:10]) if errors else '',  # Store first 10 errors
        )
        
        logger.info(
            'run_monthly_leave_accrual: Completed for %s/%02d — %s (processed=%d, created=%d, updated=%d, errors=%d)',
            year, month, status.upper(), records_processed, records_created, records_updated, len(errors)
        )
        
        return {
            'status': status,
            'year': year,
            'month': month,
            'log_id': str(log.id),
            'records_processed': records_processed,
            'records_created': records_created,
            'records_updated': records_updated,
            'monthly_accrual': float(MONTHLY_LEAVE_ACCRUAL),
            'errors_count': len(errors),
            'errors': errors[:5],  # Return first 5 errors in response
        }
        
    except Exception as exc:
        logger.exception('run_monthly_leave_accrual: Fatal error for %s/%02d', year, month)
        
        # Log failed execution
        MonthlyLeaveAccrualLog.objects.create(
            year=year,
            month=month,
            triggered_by=triggered_by,
            records_processed=records_processed,
            records_created=records_created,
            records_updated=records_updated,
            monthly_accrual_used=_dec(MONTHLY_LEAVE_ACCRUAL),
            status='failed',
            error_message=str(exc)[:500],  # Truncate long errors
        )
        
        raise self.retry(exc=exc)
