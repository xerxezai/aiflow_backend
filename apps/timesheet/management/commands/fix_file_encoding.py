# -*- coding: utf-8 -*-
"""
Management command: fix_file_encoding
=======================================
Scans all Python (and optionally other) source files for non-UTF-8 bytes and
rewrites them as clean UTF-8 in-place, mapping Windows-1252 single-byte
characters to their correct Unicode equivalents.

This prevents the Nixpacks build error:
    Error reading <file>.py — stream did not contain valid UTF-8

Usage:
    python manage.py fix_file_encoding                  # scan all .py files
    python manage.py fix_file_encoding --dry-run        # show dirty files without writing
    python manage.py fix_file_encoding --ext py js jsx ts  # also scan JS/TS files
    python manage.py fix_file_encoding --path apps/spec_customization  # limit to subtree
    python manage.py fix_file_encoding --ascii          # also replace box-drawing
                                                        # chars with plain ASCII dashes

Soft-coded:
    DEFAULT_EXTENSIONS  — file extensions to scan  (env: FIX_ENCODING_EXTS,  default: py)
    SCAN_ROOT           — root directory to scan    (env: FIX_ENCODING_ROOT, default: <manage.py dir>)
    SKIP_DIRS           — directory names to skip   (module-level constant below)
"""
from __future__ import annotations

import os
import pathlib
import re
from typing import Optional

from django.core.management.base import BaseCommand

# ── Soft-coded constants ──────────────────────────────────────────────────────

# File extensions scanned by default (can be overridden via --ext or env var)
DEFAULT_EXTENSIONS: list[str] = os.environ.get(
    'FIX_ENCODING_EXTS', 'py'
).split(',')

# Root directory for the recursive scan (default: the Django project root = manage.py dir)
# Resolved at runtime in handle() via Django settings.BASE_DIR so it works both
# locally (Windows path) and inside the Docker/Railway container.
SCAN_ROOT: str = os.environ.get('FIX_ENCODING_ROOT', '')

# Directory names never entered during the scan
SKIP_DIRS: frozenset[str] = frozenset({
    'venv', '.venv', '__pycache__', 'node_modules',
    '.git', 'dist', 'build', 'static', 'media',
    'site-packages', 'lib', 'lib64', 'bin', 'include',
    'migrations',  # skip auto-generated migration files
})

# ── Windows-1252 byte → Unicode mapping ──────────────────────────────────────
# Bytes 0x80-0x9F that Windows-1252 defines but are absent from Latin-1.
# Each surrogate \udcXX represents raw byte 0xXX from surrogateescape.
WIN1252_MAP: dict[str, str] = {
    '\udc80': '\u20ac',  # euro sign
    '\udc82': '\u201a',  # single low-9 quotation
    '\udc83': '\u0192',  # latin small f with hook
    '\udc84': '\u201e',  # double low-9 quotation
    '\udc85': '\u2026',  # horizontal ellipsis
    '\udc86': '\u2020',  # dagger
    '\udc87': '\u2021',  # double dagger
    '\udc88': '\u02c6',  # modifier letter circumflex
    '\udc89': '\u2030',  # per mille sign
    '\udc8a': '\u0160',  # latin capital S with caron
    '\udc8b': '\u2039',  # single left angle quotation
    '\udc8c': '\u0152',  # latin capital OE ligature
    '\udc8e': '\u017d',  # latin capital Z with caron
    '\udc91': '\u2018',  # left single quotation mark
    '\udc92': '\u2019',  # right single quotation mark
    '\udc93': '\u201c',  # left double quotation mark
    '\udc94': '\u201d',  # right double quotation mark
    '\udc95': '\u2022',  # bullet
    '\udc96': '\u2013',  # en dash
    '\udc97': '\u2014',  # em dash  <- most common offender
    '\udc98': '\u02dc',  # small tilde
    '\udc99': '\u2122',  # trade mark sign
    '\udc9a': '\u0161',  # latin small s with caron
    '\udc9b': '\u203a',  # single right angle quotation
    '\udc9c': '\u0153',  # latin small oe ligature
    '\udc9e': '\u017e',  # latin small z with caron
    '\udc9f': '\u0178',  # latin capital Y with diaeresis
}

# Box-drawing / Unicode decorators often used in comment dividers.
# When --ascii is passed these are replaced with plain ASCII equivalents.
BOX_TO_ASCII: dict[str, str] = {
    '\u2500': '-',    # box drawings light horizontal
    '\u2501': '-',    # box drawings heavy horizontal
    '\u2502': '|',    # box drawings light vertical
    '\u2550': '=',    # box drawings double horizontal
    '\u2014': '--',   # em dash
    '\u2013': '-',    # en dash
    '\u2022': '*',    # bullet
    '\u00b7': '.',    # middle dot
    '\u2026': '...',  # horizontal ellipsis
    '\u2018': "'",    # left single quotation
    '\u2019': "'",    # right single quotation
    '\u201c': '"',    # left double quotation
    '\u201d': '"',    # right double quotation
}

# ─────────────────────────────────────────────────────────────────────────────


class Command(BaseCommand):
    help = (
        'Fix non-UTF-8 bytes in source files so Nixpacks can build without '
        '"stream did not contain valid UTF-8" errors.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report dirty files without rewriting them.',
        )
        parser.add_argument(
            '--ext',
            nargs='+',
            default=DEFAULT_EXTENSIONS,
            metavar='EXT',
            help='File extensions to scan (without dot). Default: %(default)s',
        )
        parser.add_argument(
            '--path',
            default=SCAN_ROOT,  # empty string → resolved at runtime from settings.BASE_DIR
            metavar='PATH',
            help='Root directory or single file to scan. Default: Django BASE_DIR.',
        )
        parser.add_argument(
            '--ascii',
            action='store_true',
            help='Also replace box-drawing and typographic Unicode with plain ASCII.',
        )

    # ── Entry point ───────────────────────────────────────────────────────────

    def handle(self, *args, **options):
        # Resolve scan root: prefer --path arg, then env var, then Django BASE_DIR
        raw_path = options['path']
        if not raw_path:
            from django.conf import settings  # noqa: PLC0415
            raw_path = str(getattr(settings, 'BASE_DIR', pathlib.Path.cwd()))
        root      = pathlib.Path(raw_path).resolve()
        dry_run   = options['dry_run']
        exts      = {e.lstrip('.').lower() for e in options['ext']}
        use_ascii = options['ascii']

        prefix = '[DRY RUN] ' if dry_run else ''
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n{prefix}fix_file_encoding — scanning {root}\n'
        ))

        if root.is_file():
            candidates = [root]
        else:
            candidates = self._iter_files(root, exts)

        dirty_count = 0
        fixed_count = 0

        for fpath in candidates:
            result = self._process_file(fpath, root=root, dry_run=dry_run, use_ascii=use_ascii)
            if result == 'dirty':
                dirty_count += 1
            elif result == 'fixed':
                dirty_count += 1
                fixed_count += 1

        if dry_run:
            if dirty_count:
                self.stdout.write(self.style.WARNING(
                    f'\n{dirty_count} file(s) have non-UTF-8 bytes. '
                    f'Run without --dry-run to fix them.\n'
                ))
            else:
                self.stdout.write(self.style.SUCCESS('\nAll files are clean UTF-8.\n'))
        else:
            if fixed_count:
                self.stdout.write(self.style.SUCCESS(
                    f'\nFixed {fixed_count} file(s). All source files are now UTF-8 clean.\n'
                ))
            else:
                self.stdout.write(self.style.SUCCESS('\nAll files are already clean UTF-8.\n'))

    # ── File iterator ─────────────────────────────────────────────────────────

    def _iter_files(self, root: pathlib.Path, exts: set[str]):
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune skipped directories in-place so os.walk skips them
            dirnames[:] = [
                d for d in dirnames
                if d not in SKIP_DIRS and not d.startswith('.')
            ]
            for fname in filenames:
                suffix = pathlib.Path(fname).suffix.lstrip('.').lower()
                if suffix in exts:
                    yield pathlib.Path(dirpath) / fname

    # ── Per-file processing ───────────────────────────────────────────────────

    def _process_file(
        self,
        fpath: pathlib.Path,
        root: pathlib.Path,
        dry_run: bool,
        use_ascii: bool,
    ) -> str:
        """
        Returns:
            'ok'    — file was already valid UTF-8 (and no ASCII conversion needed)
            'dirty' — file has issues but dry_run=True so not written
            'fixed' — file was repaired and rewritten
        """
        raw = fpath.read_bytes()

        # Fast-path: already valid UTF-8 (and not asking for ASCII conversion)
        if not use_ascii:
            try:
                raw.decode('utf-8')
                return 'ok'
            except UnicodeDecodeError:
                pass  # fall through to repair

        # Decode with surrogateescape: invalid bytes become \udcXX surrogates
        text = raw.decode('utf-8', errors='surrogateescape')

        # Identify which surrogates (= invalid bytes) are present
        surrogates = [ch for ch in text if '\udc00' <= ch <= '\udcff']
        has_surrogates = bool(surrogates)

        if not has_surrogates and not use_ascii:
            return 'ok'

        rel = fpath.relative_to(root) if fpath.is_relative_to(root) else fpath

        if has_surrogates:
            unmapped = {ch for ch in surrogates if ch not in WIN1252_MAP}
            mapping_note = (
                f'  mapped: {sorted(set(surrogates))} '
                + (f'  UNMAPPED (→ \ufffd): {sorted(unmapped)}' if unmapped else '')
            )
            verb = 'Would fix' if dry_run else 'Fixing'
            self.stdout.write(
                self.style.WARNING(f'  {verb}: {rel}') + f'\n{mapping_note}'
            )

        if dry_run:
            return 'dirty'

        # ── Repair pass ───────────────────────────────────────────────────────
        fixed_chars: list[str] = []
        for ch in text:
            if '\udc00' <= ch <= '\udcff':
                # Map Windows-1252 byte or fall back to Unicode replacement char
                fixed_chars.append(WIN1252_MAP.get(ch, '\ufffd'))
            elif use_ascii and ch in BOX_TO_ASCII:
                fixed_chars.append(BOX_TO_ASCII[ch])
            else:
                fixed_chars.append(ch)

        fixed = ''.join(fixed_chars)

        # Sanity check: the result must be clean UTF-8 before we write
        fixed.encode('utf-8')

        fpath.write_text(fixed, encoding='utf-8')
        self.stdout.write(self.style.SUCCESS(f'  Fixed:  {rel}'))
        return 'fixed'
