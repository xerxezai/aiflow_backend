"""
Celery Tasks – Wrench S3 Export
================================
Two task entry points:
  - wrench_s3_batch_export       – one-off full export (batch mode)
  - wrench_s3_realtime_tick      – incremental tick (real-time mode, safe to re-queue)

Both are async: views dispatch them immediately and return the job ID.
"""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, name='wrench_integration.s3_batch_export', max_retries=2)
def wrench_s3_batch_export(self, job_id: int):
    """
    Celery task – Batch export of all Wrench data to S3.
    Runs once per job, processes all pages, then marks job complete.
    """
    from .models import WrenchS3SyncJob
    from .s3_service import run_batch_export

    try:
        job = WrenchS3SyncJob.objects.select_related('config').get(pk=job_id)
    except WrenchS3SyncJob.DoesNotExist:
        logger.error('[S3 Task] batch: job_id=%d not found', job_id)
        return

    # Track the Celery task ID so we can revoke if needed
    job.celery_task_id = self.request.id or ''
    job.save(update_fields=['celery_task_id', 'updated_at'])

    logger.info('[S3 Task] batch: starting job_id=%d', job_id)
    try:
        run_batch_export(job)
        logger.info('[S3 Task] batch: job_id=%d finished with status=%s', job_id, job.status)
    except Exception as exc:
        logger.exception('[S3 Task] batch: job_id=%d unexpected error: %s', job_id, exc)
        raise self.retry(exc=exc, countdown=30)


@shared_task(bind=True, name='wrench_integration.s3_realtime_tick', max_retries=5)
def wrench_s3_realtime_tick(self, job_id: int):
    """
    Celery task – One tick of the real-time export loop.
    Processes the next page from Wrench and uploads to S3.
    The view re-queues this task automatically after each tick (countdown = 30s by default).
    Use `stop_realtime_job()` to halt the chain.
    """
    from .models import WrenchS3SyncJob
    from .s3_service import run_realtime_export_tick

    # Soft-coded: seconds before next tick
    REALTIME_TICK_INTERVAL = 30

    try:
        job = WrenchS3SyncJob.objects.select_related('config').get(pk=job_id)
    except WrenchS3SyncJob.DoesNotExist:
        logger.error('[S3 Task] realtime: job_id=%d not found', job_id)
        return

    if job.status == WrenchS3SyncJob.STATUS_STOPPED:
        logger.info('[S3 Task] realtime: job_id=%d stopped — not re-queuing.', job_id)
        return

    job.celery_task_id = self.request.id or ''
    job.save(update_fields=['celery_task_id', 'updated_at'])

    logger.info('[S3 Task] realtime tick: job_id=%d page_from=%d', job_id, job.last_page_exported + 1)
    try:
        run_realtime_export_tick(job)
    except Exception as exc:
        logger.exception('[S3 Task] realtime: job_id=%d error: %s', job_id, exc)
        raise self.retry(exc=exc, countdown=60)

    # Refresh from DB to get latest status
    job.refresh_from_db()
    if job.status not in (WrenchS3SyncJob.STATUS_STOPPED,
                          WrenchS3SyncJob.STATUS_FAILED,
                          WrenchS3SyncJob.STATUS_SUCCESS):
        # Re-queue next tick
        wrench_s3_realtime_tick.apply_async(
            args=[job_id],
            countdown=REALTIME_TICK_INTERVAL,
        )
        logger.info('[S3 Task] realtime: re-queued job_id=%d, next in %ds',
                    job_id, REALTIME_TICK_INTERVAL)


# ─────────────────────────────────────────────────────────────────────────────
# Project Document Mirror tasks (additive — copy actual file bytes to S3)
# ─────────────────────────────────────────────────────────────────────────────

# Soft-coded re-queue interval for the realtime project export tick.
_PROJECT_REALTIME_TICK_INTERVAL_SEC = 30


@shared_task(bind=True, name='wrench_integration.s3_project_export', max_retries=2)
def wrench_s3_project_export(self, job_id: int):
    """
    Celery task – BATCH mirror of every document of a Wrench project to S3.
    """
    from .models import WrenchS3SyncJob
    from .s3_documents_service import run_project_export

    try:
        job = WrenchS3SyncJob.objects.select_related('config').get(pk=job_id)
    except WrenchS3SyncJob.DoesNotExist:
        logger.error('[S3 ProjDocs Task] batch: job_id=%d not found', job_id)
        return

    job.celery_task_id = self.request.id or ''
    job.save(update_fields=['celery_task_id', 'updated_at'])

    logger.info('[S3 ProjDocs Task] batch: starting job_id=%d', job_id)
    try:
        run_project_export(job)
    except Exception as exc:
        logger.exception('[S3 ProjDocs Task] batch: job_id=%d error: %s', job_id, exc)
        raise self.retry(exc=exc, countdown=60)


@shared_task(bind=True, name='wrench_integration.s3_project_export_tick', max_retries=5)
def wrench_s3_project_export_tick(self, job_id: int):
    """
    Celery task – One REALTIME tick of the project document mirror.
    Re-queues itself until cursor reaches end-of-list or job is stopped.
    """
    from .models import WrenchS3SyncJob
    from .s3_documents_service import run_project_export_tick

    try:
        job = WrenchS3SyncJob.objects.select_related('config').get(pk=job_id)
    except WrenchS3SyncJob.DoesNotExist:
        logger.error('[S3 ProjDocs Task] tick: job_id=%d not found', job_id)
        return

    if job.status == WrenchS3SyncJob.STATUS_STOPPED:
        logger.info('[S3 ProjDocs Task] tick: job_id=%d stopped — not re-queuing.', job_id)
        return

    job.celery_task_id = self.request.id or ''
    job.save(update_fields=['celery_task_id', 'updated_at'])

    try:
        run_project_export_tick(job)
    except Exception as exc:
        logger.exception('[S3 ProjDocs Task] tick: job_id=%d error: %s', job_id, exc)
        raise self.retry(exc=exc, countdown=60)

    job.refresh_from_db()
    if job.status == WrenchS3SyncJob.STATUS_IN_PROGRESS:
        wrench_s3_project_export_tick.apply_async(
            args=[job_id],
            countdown=_PROJECT_REALTIME_TICK_INTERVAL_SEC,
        )
        logger.info('[S3 ProjDocs Task] tick: re-queued job_id=%d in %ds',
                    job_id, _PROJECT_REALTIME_TICK_INTERVAL_SEC)


@shared_task(bind=True, name='wrench_integration.s3_library_watcher_tick', max_retries=5)
def wrench_s3_library_watcher_tick(self, job_id: int):
    """
    Celery task — continuous **library mirror watcher** for one Wrench project.

    Each tick:
      1) Lists the project's documents from Wrench
      2) Diffs against the S3 fingerprint state file
      3) Mirrors only ADDED + CHANGED documents (keeps library in sync)
      4) Re-queues itself after `watcher_interval_sec()` for true realtime

    Stops automatically when the job is set to STATUS_STOPPED.
    """
    from .models import WrenchS3SyncJob
    from .s3_documents_service import run_library_watcher_tick, watcher_interval_sec

    try:
        job = WrenchS3SyncJob.objects.select_related('config').get(pk=job_id)
    except WrenchS3SyncJob.DoesNotExist:
        logger.error('[S3 LibWatcher Task] job_id=%d not found', job_id)
        return

    if job.status == WrenchS3SyncJob.STATUS_STOPPED:
        logger.info('[S3 LibWatcher Task] job_id=%d stopped — halting.', job_id)
        return

    job.celery_task_id = self.request.id or ''
    job.save(update_fields=['celery_task_id', 'updated_at'])

    try:
        run_library_watcher_tick(job)
    except Exception as exc:
        logger.exception('[S3 LibWatcher Task] job_id=%d error: %s', job_id, exc)
        raise self.retry(exc=exc, countdown=watcher_interval_sec())

    job.refresh_from_db()
    if job.status == WrenchS3SyncJob.STATUS_IN_PROGRESS:
        wrench_s3_library_watcher_tick.apply_async(
            args=[job_id],
            countdown=watcher_interval_sec(),
        )
        logger.info('[S3 LibWatcher Task] job_id=%d re-queued in %ds',
                    job_id, watcher_interval_sec())
