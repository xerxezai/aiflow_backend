"""
Finance App — Celery Tasks
==========================
Background tasks for the payroll automation pipeline.

Tasks:
  generate_salary_slip_pdf  — Generate + S3-upload PDF for ONE slip.
  auto_generate_monthly_payroll — Monthly Celery Beat job:
      1. Create / re-use a PayrollRun for (month, year)
      2. Call SalaryCalculationService to generate SalarySlip rows
      3. Fan out generate_salary_slip_pdf for every slip
      4. Optionally queue emails (if PayrollSchedule.auto_send_emails)
      5. Write execution summary back to PayrollSchedule.last_run_*
"""
from __future__ import annotations

import io
import logging
from datetime import date, timedelta
from decimal import Decimal

from celery import shared_task, chord
from django.utils import timezone

logger = logging.getLogger(__name__)

# ── Soft-coded retry / timeout constants ──────────────────────────────────────
_PDF_MAX_RETRIES     = 3
_PDF_RETRY_BACKOFF_S = 30
_PDF_TASK_TIMEOUT_S  = 120   # 2 min per PDF

_AUTO_MAX_RETRIES    = 2
_AUTO_TASK_TIMEOUT_S = 900   # 15 min for the whole monthly run


# ─────────────────────────────────────────────────────────────────────────────
# Task 1: generate_salary_slip_pdf
# Generate PDF for a single SalarySlip and upload to S3.
# ─────────────────────────────────────────────────────────────────────────────
@shared_task(
    name='finance.generate_salary_slip_pdf',
    bind=True,
    max_retries=_PDF_MAX_RETRIES,
    default_retry_delay=_PDF_RETRY_BACKOFF_S,
    time_limit=_PDF_TASK_TIMEOUT_S,
)
def generate_salary_slip_pdf(self, slip_id: str) -> dict:
    """
    Generate a PDF for `SalarySlip(id=slip_id)` and upload it to S3.
    Stores the S3 key on SalarySlip.pdf_s3_key and updates pdf_generated_at.

    Returns:
        dict with keys: slip_id, slip_number, status ('ok' | 'error'), s3_key
    """
    from apps.finance.salary_models import SalarySlip
    from apps.finance.salary_pdf_service import SalarySlipPDFService
    from apps.payroll.storage import PayrollSlipStorage, S3_AVAILABLE

    try:
        slip = SalarySlip.objects.select_related(
            'employee_salary_info__user', 'payroll_run'
        ).get(id=slip_id)
    except SalarySlip.DoesNotExist:
        logger.error('generate_salary_slip_pdf: slip %s not found', slip_id)
        return {'slip_id': slip_id, 'status': 'not_found'}

    try:
        # 1. Generate PDF bytes in memory
        service = SalarySlipPDFService()
        pdf_bytes = service.generate_pdf_bytes(slip)   # returns io.BytesIO

        now = timezone.now()
        filename = (
            f"{slip.year}/{slip.month:02d}/"
            f"{slip.employee_salary_info.employee_id or str(slip.id)}/"
            f"{slip.slip_number}.pdf"
        )

        # 2. Upload to S3 (if available) or save locally
        if S3_AVAILABLE and PayrollSlipStorage:
            storage = PayrollSlipStorage()
            saved_name = storage.save(filename, io.BytesIO(pdf_bytes.getvalue()))
            slip.pdf_s3_key      = saved_name
            slip.pdf_s3_uploaded_at = now
            logger.info('generate_salary_slip_pdf: uploaded %s to S3 as %s', slip.slip_number, saved_name)
        else:
            # Fallback: write to local media
            import os
            from django.conf import settings
            local_dir = os.path.join(
                settings.MEDIA_ROOT, 'salary_slips',
                str(slip.year), f'{slip.month:02d}',
            )
            os.makedirs(local_dir, exist_ok=True)
            local_path = os.path.join(local_dir, f'{slip.slip_number}.pdf')
            with open(local_path, 'wb') as fh:
                fh.write(pdf_bytes.getvalue())
            slip.pdf_file_path = local_path
            logger.info('generate_salary_slip_pdf: saved %s locally', slip.slip_number)

        slip.pdf_generated_at = now
        slip.save(update_fields=['pdf_file_path', 'pdf_generated_at', 'pdf_s3_key', 'pdf_s3_uploaded_at'])

        return {
            'slip_id':     str(slip_id),
            'slip_number': slip.slip_number,
            'status':      'ok',
            's3_key':      slip.pdf_s3_key or slip.pdf_file_path,
        }

    except Exception as exc:
        logger.exception('generate_salary_slip_pdf: slip %s failed: %s', slip_id, exc)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {'slip_id': str(slip_id), 'status': 'error', 'error': str(exc)}


# ─────────────────────────────────────────────────────────────────────────────
# Task 2: auto_generate_monthly_payroll
# Celery Beat periodic task — fires on the configured day of month.
# ─────────────────────────────────────────────────────────────────────────────
@shared_task(
    name='finance.auto_generate_monthly_payroll',
    bind=True,
    max_retries=_AUTO_MAX_RETRIES,
    time_limit=_AUTO_TASK_TIMEOUT_S,
)
def auto_generate_monthly_payroll(self) -> dict:
    """
    Automated monthly payroll pipeline:
      1. Read PayrollSchedule singleton — bail if disabled.
      2. Determine target month (previous calendar month by default).
      3. Skip if a completed PayrollRun already exists for that month.
      4. Create PayrollRun → process (generate SalarySlips).
      5. Fan-out generate_salary_slip_pdf for every slip.
      6. Optionally queue emails.
      7. Record outcome on PayrollSchedule.last_run_*.
    """
    from apps.finance.salary_models import (
        PayrollRun, SalarySlip, SalaryStatus, PayrollSchedule,
    )
    from apps.finance.salary_service import SalaryCalculationService

    # ── 1. Load schedule config ──────────────────────────────────────────────
    schedule = PayrollSchedule.objects.order_by('created_at').first()
    if schedule is None or not schedule.enabled:
        logger.info('auto_generate_monthly_payroll: schedule disabled — skipping')
        return {'status': 'skipped', 'reason': 'disabled'}

    today = date.today()

    # ── 2. Determine target month (previous month) ───────────────────────────
    first_of_this_month = today.replace(day=1)
    last_month_last_day = first_of_this_month - timedelta(days=1)
    target_month = last_month_last_day.month
    target_year  = last_month_last_day.year

    # Apply optional offset (days_after_month_end)
    cutoff = first_of_this_month + timedelta(days=schedule.days_after_month_end)
    if today < cutoff:
        msg = f'Waiting until {cutoff} before generating {target_month}/{target_year}'
        logger.info('auto_generate_monthly_payroll: %s', msg)
        _update_schedule_last_run(schedule, 'skipped', msg)
        return {'status': 'skipped', 'reason': msg}

    # ── 3. Skip if already completed ────────────────────────────────────────
    existing = PayrollRun.objects.filter(
        month=target_month, year=target_year, status='completed',
    ).first()
    if existing:
        msg = f'PayrollRun {existing.run_code} already completed — skipping'
        logger.info('auto_generate_monthly_payroll: %s', msg)
        _update_schedule_last_run(schedule, 'skipped', msg)
        return {'status': 'skipped', 'run_code': existing.run_code}

    # ── 4. Create (or reuse draft) PayrollRun ───────────────────────────────
    import calendar
    from django.contrib.auth import get_user_model
    User = get_user_model()

    system_user = (
        User.objects.filter(is_superuser=True).order_by('date_joined').first()
        or User.objects.order_by('date_joined').first()
    )

    run_code = f'AUTO-{target_year}-{target_month:02d}'
    period_start = date(target_year, target_month, 1)
    period_end   = date(target_year, target_month,
                        calendar.monthrange(target_year, target_month)[1])

    run, created = PayrollRun.objects.get_or_create(
        month=target_month,
        year=target_year,
        defaults={
            'run_code':     run_code,
            'status':       'draft',
            'period_start': period_start,
            'period_end':   period_end,
            'created_by':   system_user,
        },
    )
    if not created and run.status == 'failed':
        # Reset a previously failed run so we can retry
        run.status    = 'draft'
        run.error_log = ''
        run.save(update_fields=['status', 'error_log', 'updated_at'])

    logger.info('auto_generate_monthly_payroll: using run %s (created=%s)', run.run_code, created)

    # ── 5. Process the run — generate SalarySlip rows ───────────────────────
    from apps.finance.salary_models import EmployeeSalaryInfo
    from decimal import Decimal as D

    run.status = 'processing'
    run.processing_started_at = timezone.now()
    run.save(update_fields=['status', 'processing_started_at', 'updated_at'])

    employees = EmployeeSalaryInfo.objects.filter(is_active=True)
    total = employees.count()
    run.total_employees = total
    run.save(update_fields=['total_employees', 'updated_at'])

    service = SalaryCalculationService()
    processed = 0
    errors = []

    for emp in employees:
        try:
            service.generate_salary_slip(
                employee=emp,
                payroll_run=run,
                month=target_month,
                year=target_year,
                generated_by=system_user,
            )
            processed += 1
            run.processed_employees = processed
            run.save(update_fields=['processed_employees', 'updated_at'])
        except Exception as exc:
            err_msg = f'{emp.employee_id}: {exc}'
            errors.append(err_msg)
            logger.error('auto_generate_monthly_payroll: slip error — %s', err_msg)

    # Recalculate totals
    slips_qs = SalarySlip.objects.filter(payroll_run=run)
    from django.db.models import Sum
    totals = slips_qs.aggregate(
        gross=Sum('gross_salary'),
        deductions=Sum('total_deductions'),
        net=Sum('net_salary'),
    )
    run.total_gross_salary = totals['gross']   or D('0.00')
    run.total_deductions   = totals['deductions'] or D('0.00')
    run.total_net_salary   = totals['net']      or D('0.00')
    run.status = 'failed' if (processed == 0 and total > 0) else 'completed'
    run.processing_completed_at = timezone.now()
    run.error_log = '\n'.join(errors)
    run.save()

    if run.status == 'failed':
        msg = f'Run {run.run_code} failed — 0/{total} slips generated. Errors: {"; ".join(errors[:3])}'
        logger.error('auto_generate_monthly_payroll: %s', msg)
        _update_schedule_last_run(schedule, 'failed', msg)
        return {'status': 'failed', 'run_code': run.run_code, 'errors': errors}

    logger.info(
        'auto_generate_monthly_payroll: run %s completed — %d/%d slips generated',
        run.run_code, processed, total,
    )

    # ── 6. Fan-out PDF generation tasks (one per slip) ───────────────────────
    slip_ids = list(slips_qs.values_list('id', flat=True))
    for sid in slip_ids:
        generate_salary_slip_pdf.delay(str(sid))

    # ── 7. Auto-send emails if configured ───────────────────────────────────
    if schedule.auto_send_emails:
        from apps.finance.salary_views import _queue_bulk_emails_for_run
        try:
            _queue_bulk_emails_for_run(run, system_user)
        except Exception as exc:
            logger.warning('auto_generate_monthly_payroll: email queue failed: %s', exc)

    summary = (
        f'Run {run.run_code}: {processed}/{total} slips generated, '
        f'{len(slip_ids)} PDFs queued, '
        f'net={run.total_net_salary} AED'
    )
    _update_schedule_last_run(schedule, 'success', summary)
    return {
        'status':     'success',
        'run_code':   run.run_code,
        'slips':      processed,
        'pdfs_queued': len(slip_ids),
        'net_total':  str(run.total_net_salary),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _update_schedule_last_run(schedule, status: str, details: str) -> None:
    schedule.last_run_at      = timezone.now()
    schedule.last_run_status  = status
    schedule.last_run_details = details
    schedule.save(update_fields=['last_run_at', 'last_run_status', 'last_run_details', 'updated_at'])
