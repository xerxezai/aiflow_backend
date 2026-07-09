"""
Valve MTO — Async Job Runner
============================

Tiny job framework on top of (a) Django's cache and (b) an on-disk JSON
file. Disk is the source of truth; cache is just a fast-path memoisation.

Why dual-layer?
---------------
On Railway / multi-worker / worker-restart environments the in-process
``locmem`` cache is lost when a worker recycles, and even Redis can evict
keys under pressure. Persisting to disk makes the job survive:
    * gunicorn worker restarts (free tier recycles aggressively)
    * cache eviction / Redis flushes
    * polls served by a different gunicorn worker than the one that
      received the upload (locmem is per-process)

Soft-coded knobs (see SECTION ``CONFIG``):
    * Cache prefix and TTL
    * Disk directory under MEDIA_ROOT
    * Stale-job sweep age
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# ─── CONFIG (soft-coded) ─────────────────────────────────────────────────
CACHE_PREFIX     = 'valve_mto:job:'
CACHE_TTL        = 60 * 60 * 6                    # 6 h fast-path TTL
DISK_TTL         = 60 * 60 * 24                   # 24 h disk retention
DEFAULT_KIND     = 'valve_mto'
# Watchdog heartbeat interval (seconds). The watcher thread touches
# `updated_at` on the snapshot at this cadence while the job is running,
# guaranteeing the frontend stall timer never fires while the worker
# thread is alive — even during silent CPU-bound phases like PDF text
# extraction or PDF→JPEG rendering on slim-CPU containers.
HEARTBEAT_INTERVAL_SECS = int(os.getenv('VALVE_MTO_HEARTBEAT_SECS', '20'))

# Disk directory — soft-coded. Falls back to system temp if MEDIA_ROOT missing.
JOB_DIR_NAME     = 'valve_mto_jobs'
_MEDIA_ROOT      = getattr(settings, 'MEDIA_ROOT', None) or tempfile.gettempdir()
JOB_DIR          = os.path.join(_MEDIA_ROOT, JOB_DIR_NAME)


def _ensure_dir() -> None:
    try:
        os.makedirs(JOB_DIR, exist_ok=True)
    except OSError as exc:                                            # pragma: no cover
        logger.warning('[ValveMTO] cannot create job dir %s: %s', JOB_DIR, exc)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_path(job_id: str) -> str:
    return os.path.join(JOB_DIR, f'{job_id}.json')


def _read_disk(job_id: str) -> Optional[Dict[str, Any]]:
    path = _job_path(job_id)
    if not os.path.exists(path):
        return None
    try:
        # Stale check
        if (time.time() - os.path.getmtime(path)) > DISK_TTL:
            try:
                os.remove(path)
            except OSError:
                pass
            return None
        with open(path, 'r', encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning('[ValveMTO] disk read failed for %s: %s', job_id, exc)
        return None


def _write_disk(job_id: str, snapshot: Dict[str, Any]) -> None:
    """Atomic write via tmp file + rename so concurrent readers never see partial JSON."""
    _ensure_dir()
    path = _job_path(job_id)
    tmp_path = f'{path}.{os.getpid()}.tmp'
    try:
        with open(tmp_path, 'w', encoding='utf-8') as fh:
            json.dump(snapshot, fh, default=str)
        os.replace(tmp_path, path)
    except OSError as exc:                                            # pragma: no cover
        logger.warning('[ValveMTO] disk write failed for %s: %s', job_id, exc)
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


# ─── Two-tier job store (cache + disk) ───────────────────────────────────
class JobStore:
    @staticmethod
    def _key(job_id: str) -> str:
        return f'{CACHE_PREFIX}{job_id}'

    @staticmethod
    def _persist(job_id: str, snapshot: Dict[str, Any]) -> None:
        # Cache first (fast); disk second (durable).
        try:
            cache.set(JobStore._key(job_id), snapshot, timeout=CACHE_TTL)
        except Exception as exc:                                      # pragma: no cover
            logger.warning('[ValveMTO] cache.set failed for %s: %s', job_id, exc)
        _write_disk(job_id, snapshot)

    @staticmethod
    def create(initial: Dict[str, Any]) -> str:
        job_id = uuid.uuid4().hex
        snapshot = {
            'status':       'queued',
            'progress':     {'current': 0, 'total': 0, 'rows': 0},
            'rows':         [],
            'project_meta': {},
            'warnings':     [],
            'error':        None,
            'engine':       'vision',
            'page_count':   0,
            'started_at':   _now_iso(),
            'updated_at':   _now_iso(),
            **initial,
        }
        JobStore._persist(job_id, snapshot)
        logger.info('[ValveMTO] created job %s (disk: %s)', job_id, _job_path(job_id))
        return job_id

    @staticmethod
    def get(job_id: str) -> Optional[Dict[str, Any]]:
        # Fast path
        try:
            snap = cache.get(JobStore._key(job_id))
        except Exception:                                             # pragma: no cover
            snap = None
        if snap:
            return snap
        # Fallback: durable disk store. Re-warm the cache so subsequent polls
        # are fast again after a worker restart.
        snap = _read_disk(job_id)
        if snap:
            try:
                cache.set(JobStore._key(job_id), snap, timeout=CACHE_TTL)
            except Exception:                                         # pragma: no cover
                pass
        return snap

    @staticmethod
    def update(job_id: str, **patch) -> None:
        snap = JobStore.get(job_id)
        if not snap:
            logger.warning('[ValveMTO] update on missing job %s', job_id)
            return
        snap.update(patch)
        snap['updated_at'] = _now_iso()
        JobStore._persist(job_id, snap)

    @staticmethod
    def merge_progress(job_id: str, *, current: int, total: int, rows_so_far: int) -> None:
        snap = JobStore.get(job_id)
        if not snap:
            return
        snap['progress']   = {'current': current, 'total': total, 'rows': rows_so_far}
        snap['updated_at'] = _now_iso()
        JobStore._persist(job_id, snap)

    @staticmethod
    def heartbeat(job_id: str) -> bool:
        """
        Touch `updated_at` without changing any other field. Returns False if
        the job no longer exists or has reached a terminal state — the caller
        should stop the watchdog in that case.
        """
        snap = JobStore.get(job_id)
        if not snap:
            return False
        if snap.get('status') in ('done', 'error'):
            return False
        snap['updated_at'] = _now_iso()
        JobStore._persist(job_id, snap)
        return True


# ─── Background runner ──────────────────────────────────────────────────
def _heartbeat_loop(job_id: str, stop_event: threading.Event) -> None:
    """Periodically refresh `updated_at` so the frontend stall timer never
    trips while the worker thread is alive but in a silent CPU-bound phase
    (PDF text extraction, page rendering, queued OpenAI calls)."""
    while not stop_event.wait(HEARTBEAT_INTERVAL_SECS):
        try:
            if not JobStore.heartbeat(job_id):
                return
        except Exception as exc:                                      # pragma: no cover
            logger.warning('[ValveMTO] heartbeat error for %s: %s', job_id, exc)
            return


def _run_in_thread(job_id: str, pdf_path: str, filename: str) -> None:
    """Execute extraction; clean up the temp file when done."""
    # Late import — keeps this module importable without the heavy deps.
    from .piping_valve_mto_extractor import extract_valve_mto_streaming

    stop_heartbeat = threading.Event()
    hb_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(job_id, stop_heartbeat),
        name=f'valve-mto-hb-{job_id[:8]}',
        daemon=True,
    )
    hb_thread.start()

    try:
        JobStore.update(job_id, status='running')
        result = extract_valve_mto_streaming(
            pdf_path=pdf_path,
            on_progress=lambda current, total, rows_so_far: JobStore.merge_progress(
                job_id, current=current, total=total, rows_so_far=rows_so_far,
            ),
            on_partial=lambda rows, meta: JobStore.update(
                job_id, rows=rows, project_meta=meta,
            ),
        )
        # Soft-coded: extractor flips status to 'error' when every batch fails
        # with a fatal OpenAI error (e.g. insufficient_quota). Propagate the
        # message so the frontend can render it instead of a silent zero-row
        # result.
        if result.get('status') == 'error' or result.get('error'):
            JobStore.update(
                job_id,
                status='error',
                error=result.get('error') or 'Vision extraction failed.',
                engine=result.get('engine', 'none'),
                page_count=result.get('page_count', 0),
                rows=result.get('rows', []),
                project_meta=result.get('project_meta', {}),
                warnings=result.get('warnings', []),
            )
        else:
            JobStore.update(
                job_id,
                status='done',
                engine=result.get('engine', 'vision'),
                page_count=result.get('page_count', 0),
                rows=result.get('rows', []),
                project_meta=result.get('project_meta', {}),
                warnings=result.get('warnings', []),
            )
    except Exception as exc:                                      # pragma: no cover
        logger.exception('[ValveMTO] job %s crashed', job_id)
        JobStore.update(job_id, status='error', error=str(exc))
    finally:
        stop_heartbeat.set()
        try:
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
        except OSError:
            pass


def start_job(pdf_path: str, filename: str) -> str:
    """Create a job snapshot in cache+disk and start a daemon thread."""
    job_id = JobStore.create({'filename': filename})
    th = threading.Thread(
        target=_run_in_thread,
        args=(job_id, pdf_path, filename),
        name=f'valve-mto-{job_id[:8]}',
        daemon=True,
    )
    th.start()
    logger.info('[ValveMTO] job %s started for %s', job_id, filename)
    return job_id

