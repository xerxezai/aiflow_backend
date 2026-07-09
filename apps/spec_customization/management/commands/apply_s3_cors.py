"""
Apply S3 bucket CORS configuration required for direct-to-browser presigned
PUT uploads (Spec Customization, ~1 GB files).

Runs once after deploying the presigned-upload feature. Idempotent — safe to
re-run; it overwrites the bucket's CORS configuration with the soft-coded
ruleset defined below.

Usage
-----
    # Default (uses settings.AWS_STORAGE_BUCKET_NAME)
    python manage.py apply_s3_cors

    # Override target bucket
    python manage.py apply_s3_cors --bucket my-other-bucket

    # Inspect current CORS without writing
    python manage.py apply_s3_cors --show

Configuration (env vars, all optional — sensible defaults shipped)
------------------------------------------------------------------
    S3_CORS_ALLOWED_ORIGINS   Comma-separated list of allowed origins.
                              Default: production + localhost dev origins.
    S3_CORS_MAX_AGE_SECONDS   Browser cache TTL for preflight. Default 3000.
"""

from __future__ import annotations

import json
import os
from typing import List

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


# ─── Soft-coded CORS policy ──────────────────────────────────────────────────
# Override any of these via env vars without code changes.

_DEFAULT_ALLOWED_ORIGINS: List[str] = [
    "https://www.radai.ae",
    "https://radai.ae",
    "https://aiflowbackend-production.up.railway.app",
    "http://localhost:5173",
    "http://localhost:3000",
    "http://localhost:8000",
]

_DEFAULT_MAX_AGE_SECONDS = 3000


def _allowed_origins() -> List[str]:
    raw = os.environ.get("S3_CORS_ALLOWED_ORIGINS", "").strip()
    if not raw:
        return list(_DEFAULT_ALLOWED_ORIGINS)
    return [o.strip() for o in raw.split(",") if o.strip()]


def _max_age() -> int:
    try:
        return int(os.environ.get("S3_CORS_MAX_AGE_SECONDS", _DEFAULT_MAX_AGE_SECONDS))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_AGE_SECONDS


def _build_cors_rules() -> List[dict]:
    """Construct the CORS rule list expected by `put_bucket_cors`."""
    return [
        {
            # Rule 1 — browser PUT/GET/HEAD for presigned uploads & previews.
            "AllowedHeaders": ["*"],
            "AllowedMethods": ["PUT", "GET", "HEAD", "POST"],
            "AllowedOrigins": _allowed_origins(),
            "ExposeHeaders":  ["ETag", "x-amz-request-id", "x-amz-version-id"],
            "MaxAgeSeconds":  _max_age(),
        },
    ]


class Command(BaseCommand):
    help = "Apply (or inspect) the S3 bucket CORS rules required for presigned browser uploads."

    def add_arguments(self, parser):
        parser.add_argument(
            "--bucket",
            default=None,
            help="Override target bucket name. Defaults to settings.AWS_STORAGE_BUCKET_NAME.",
        )
        parser.add_argument(
            "--show",
            action="store_true",
            help="Print the bucket's current CORS configuration and exit (no write).",
        )

    # ──────────────────────────────────────────────────────────────────────
    def handle(self, *args, **opts):
        bucket = opts.get("bucket") or getattr(settings, "AWS_STORAGE_BUCKET_NAME", None)
        if not bucket:
            raise CommandError(
                "No bucket specified. Pass --bucket NAME or set "
                "settings.AWS_STORAGE_BUCKET_NAME (env: AWS_STORAGE_BUCKET_NAME)."
            )

        try:
            import boto3
            from botocore.exceptions import ClientError
        except ImportError as exc:
            raise CommandError(f"boto3 is not installed: {exc}")

        region = (
            getattr(settings, "AWS_S3_REGION_NAME", None)
            or os.environ.get("AWS_S3_REGION_NAME")
            or os.environ.get("AWS_REGION")
            or "us-east-1"
        )
        endpoint_url = f"https://s3.{region}.amazonaws.com"

        client = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url,
            aws_access_key_id=getattr(settings, "AWS_ACCESS_KEY_ID", None)
                or os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=getattr(settings, "AWS_SECRET_ACCESS_KEY", None)
                or os.environ.get("AWS_SECRET_ACCESS_KEY"),
        )

        # ── Show-only mode ──────────────────────────────────────────────
        if opts.get("show"):
            try:
                resp = client.get_bucket_cors(Bucket=bucket)
                rules = resp.get("CORSRules", [])
                self.stdout.write(self.style.NOTICE(f"Current CORS for s3://{bucket}:"))
                self.stdout.write(json.dumps(rules, indent=2))
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code")
                if code == "NoSuchCORSConfiguration":
                    self.stdout.write(self.style.WARNING(
                        f"s3://{bucket} has NO CORS configuration set."
                    ))
                else:
                    raise CommandError(f"AWS error: {e}")
            return

        # ── Apply-mode ──────────────────────────────────────────────────
        rules = _build_cors_rules()
        self.stdout.write(self.style.NOTICE(
            f"Applying CORS to s3://{bucket} (region {region})…"
        ))
        self.stdout.write(json.dumps(rules, indent=2))

        try:
            client.put_bucket_cors(
                Bucket=bucket,
                CORSConfiguration={"CORSRules": rules},
            )
        except ClientError as e:
            raise CommandError(f"put_bucket_cors failed: {e}")

        self.stdout.write(self.style.SUCCESS(
            f"\nCORS applied successfully to s3://{bucket}. "
            f"Browsers from {', '.join(_allowed_origins())} can now PUT directly."
        ))
