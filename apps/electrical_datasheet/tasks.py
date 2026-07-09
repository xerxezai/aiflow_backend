"""Celery tasks for the Smart Electrical Datasheet generator."""
from __future__ import annotations

import logging
from typing import Optional

from celery import shared_task

logger = logging.getLogger(__name__)


def _generator_for(equipment_type: str):
    """Return a generator instance with `export_to_excel(rows, project_info)`."""
    if equipment_type == 'transformer':
        from .transformer_datasheet_generator import TransformerDatasheetGenerator
        return TransformerDatasheetGenerator()
    if equipment_type == 'dg_set':
        from .dg_set_datasheet_generator import DGSetDatasheetGenerator
        return DGSetDatasheetGenerator()
    if equipment_type == 'mv_switchgear':
        from .switchgear_datasheet_generator import SwitchgearDatasheetGenerator
        return SwitchgearDatasheetGenerator()
    raise ValueError(f"Unknown equipment_type: {equipment_type}")


@shared_task(bind=True, name='electrical_datasheet.regenerate_excel_artifact', max_retries=2)
def regenerate_excel_artifact(self, datasheet_id: str) -> Optional[str]:
    """Re-render the Excel file for a `GeneratedDatasheet` and upload to S3.

    Coalesced via Celery `countdown` from the calling endpoint so rapid edits
    only produce one regeneration.
    """
    from .models import GeneratedDatasheet
    from .smart_storage import smart_storage

    try:
        ds = GeneratedDatasheet.objects.get(id=datasheet_id)
    except GeneratedDatasheet.DoesNotExist:
        logger.warning(f"[regen_excel] datasheet {datasheet_id} not found")
        return None

    try:
        gen = _generator_for(ds.equipment_type)
        project_info = (ds.metadata or {}).get('project_info', {}) or {}
        # Make the variant title available to the export
        project_info.setdefault('variant_title', ds.title or '')
        buf = gen.export_to_excel(ds.rows or [], project_info)
        excel_bytes = buf.getvalue() if hasattr(buf, 'getvalue') else bytes(buf)

        new_key = smart_storage.upload_artifact(
            user_id      = ds.user_id,
            datasheet_id = ds.id,
            kind         = 'excel',
            content_bytes= excel_bytes,
            ext          = 'xlsx',
        )
        if new_key:
            old_key = ds.excel_s3_key
            ds.excel_s3_key = new_key
            ds.save(update_fields=['excel_s3_key', 'updated_at'])
            if old_key and old_key != new_key:
                smart_storage.delete(old_key)
            logger.info(f"[regen_excel] datasheet={datasheet_id} → {new_key}")
            return new_key
    except Exception as exc:
        logger.error(f"[regen_excel] failed for {datasheet_id}: {exc}", exc_info=True)
        try:
            self.retry(countdown=30, exc=exc)
        except self.MaxRetriesExceededError:
            return None
    return None
