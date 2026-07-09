"""Spec Customization App Configuration."""
from __future__ import annotations

import logging
import os
import threading

from django.apps import AppConfig


logger = logging.getLogger(__name__)


# ─── Soft-coded auto-CORS for presigned-PUT direct uploads ───────────────────
# Browser PUT against an S3 bucket requires a CORS rule allowing the frontend
# origin. Instead of asking ops to run a one-off command, apply it on startup.
# Idempotent — overwrites with the same ruleset every boot — so removing the
# env flag or fixing typos auto-heals on next deploy.
#
# Env vars (all optional, sensible defaults):
#   S3_AUTO_APPLY_CORS         '0'/'false' to disable. Default ON.
#   S3_CORS_ALLOWED_ORIGINS    Comma-separated origins. Defaults to prod + dev.
#   S3_CORS_MAX_AGE_SECONDS    Preflight TTL. Default 3000.

_DEFAULT_ALLOWED_ORIGINS = [
    "https://www.radai.ae",
    "https://radai.ae",
    "https://aiflowbackend-production.up.railway.app",
    "http://localhost:5173",
    "http://localhost:3000",
    "http://localhost:8000",
]


def _is_truthy(raw, default=True):
    if raw is None:
        return default
    return str(raw).strip().lower() not in ("0", "false", "no", "off", "")


def _apply_cors_async():
    """Run in a background thread so it never blocks Django startup."""
    try:
        from django.conf import settings
        bucket = getattr(settings, "AWS_STORAGE_BUCKET_NAME", None) \
            or os.environ.get("AWS_STORAGE_BUCKET_NAME")
        if not bucket:
            return  # silently skip — feature not provisioned

        if not _is_truthy(os.environ.get("S3_AUTO_APPLY_CORS"), default=True):
            return

        try:
            import boto3
            from botocore.exceptions import BotoCoreError, ClientError
        except ImportError:
            logger.warning("[spec_customization] boto3 not installed — skipping S3 CORS apply.")
            return

        region = (
            getattr(settings, "AWS_S3_REGION_NAME", None)
            or os.environ.get("AWS_S3_REGION_NAME")
            or os.environ.get("AWS_REGION")
            or "us-east-1"
        )

        origins_raw = os.environ.get("S3_CORS_ALLOWED_ORIGINS", "").strip()
        origins = [o.strip() for o in origins_raw.split(",") if o.strip()] \
            if origins_raw else list(_DEFAULT_ALLOWED_ORIGINS)
        try:
            max_age = int(os.environ.get("S3_CORS_MAX_AGE_SECONDS", "3000"))
        except (TypeError, ValueError):
            max_age = 3000

        rules = [{
            "AllowedHeaders": ["*"],
            "AllowedMethods": ["PUT", "GET", "HEAD", "POST"],
            "AllowedOrigins": origins,
            "ExposeHeaders":  ["ETag", "x-amz-request-id", "x-amz-version-id"],
            "MaxAgeSeconds":  max_age,
        }]

        client = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=f"https://s3.{region}.amazonaws.com",
            aws_access_key_id=getattr(settings, "AWS_ACCESS_KEY_ID", None)
                or os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=getattr(settings, "AWS_SECRET_ACCESS_KEY", None)
                or os.environ.get("AWS_SECRET_ACCESS_KEY"),
        )
        try:
            client.put_bucket_cors(Bucket=bucket, CORSConfiguration={"CORSRules": rules})
            logger.info(
                "[spec_customization] S3 CORS applied to s3://%s (origins=%s)",
                bucket, ",".join(origins),
            )
        except (ClientError, BotoCoreError) as exc:
            # Never crash startup — feature degrades to legacy multipart.
            logger.warning("[spec_customization] S3 CORS apply failed: %s", exc)
    except Exception as exc:  # noqa: BLE001 — defensive, never crash startup
        logger.warning("[spec_customization] unexpected error in CORS apply: %s", exc)


class SpecCustomizationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.spec_customization'
    verbose_name = 'Spec Customization (Paper Spec Extraction)'

    def ready(self):
        # Skip during build-time management commands.
        if os.environ.get("SPEC_SKIP_CORS_ON_READY", "").lower() in ("1", "true", "yes"):
            return
        # Fire-and-forget; daemon thread dies with the process.
        threading.Thread(
            target=_apply_cors_async,
            daemon=True,
            name="spec-customization-s3-cors",
        ).start()
