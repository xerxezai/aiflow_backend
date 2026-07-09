#!/usr/bin/env python
"""
SOFT-CODED DYNAMIC MIGRATION HISTORY CONSISTENCY FIXER
=======================================================
Scans the full Django migration dependency graph and auto-inserts any
missing ancestor records into django_migrations for every app.

Why this exists
---------------
Django raises InconsistentMigrationHistory when a migration is marked
applied in the DB but one of its dependencies is NOT recorded.  This can
happen after manual DB operations, branch switches, or partial deploys.

How it works (soft-coded — no hardcoded migration names)
---------------------------------------------------------
1.  Load *all* migration files via Django's MigrationLoader.
2.  Query django_migrations to learn what the DB thinks is applied.
3.  For every applied migration walk ALL its dependencies recursively.
4.  If a dependency is missing from the DB record, insert it.
5.  Repeat until no more gaps are found (handles chains of any depth).

Safe by design
--------------
- Only INSERTs — never deletes or modifies existing records.
- Idempotent — safe to run multiple times.
- Works for every Django app, not just process_datasheet.
"""

import os
import sys
import django
from datetime import datetime, timezone

# ── Django bootstrap ──────────────────────────────────────────────────────────
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import connection
from django.db.migrations.loader import MigrationLoader


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now():
    """Return a tz-aware UTC datetime for the applied column."""
    return datetime.now(timezone.utc)


def get_applied_from_db():
    """Return a set of (app, name) tuples that exist in django_migrations."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT app, name FROM django_migrations")
        return {(row[0], row[1]) for row in cursor.fetchall()}


def insert_missing_record(app, name):
    """Insert a single missing migration record — idempotent."""
    with connection.cursor() as cursor:
        # Guard against race conditions / duplicate calls
        cursor.execute(
            "SELECT COUNT(*) FROM django_migrations WHERE app = %s AND name = %s",
            [app, name],
        )
        if cursor.fetchone()[0] > 0:
            return False  # already present

        cursor.execute(
            "INSERT INTO django_migrations (app, name, applied) VALUES (%s, %s, %s)",
            [app, name, _now()],
        )
        return True


def collect_all_ancestors(node, graph, visited=None):
    """
    Recursively collect every dependency of *node* from the migration graph.
    Returns a set of (app, name) tuples.
    """
    if visited is None:
        visited = set()
    if node in visited:
        return visited
    visited.add(node)
    parents = graph.get(node, [])
    for parent in parents:
        collect_all_ancestors(parent, graph, visited)
    return visited


# ── Core logic ────────────────────────────────────────────────────────────────

def fix_inconsistent_history():
    print("\n" + "=" * 70)
    print("SOFT-CODED DYNAMIC MIGRATION HISTORY FIXER".center(70))
    print("=" * 70 + "\n")

    # ── 1. Load migration graph (uses RESOLVED graph, not raw disk files) ───────
    # CRITICAL: Use loader.graph.node_map which already resolves special markers
    # like __latest__ and __first__ to concrete migration names.
    # Raw disk_migrations skip __latest__ deps, causing InconsistentMigrationHistory.
    print("🔍 Loading resolved migration graph...")
    loader = MigrationLoader(connection, ignore_no_migrations=True)

    # Build a dependency map from the RESOLVED graph (handles __latest__, __first__)
    # node → list of direct parents (all concrete migration names)
    dep_graph = {}
    for node, migration_node in loader.graph.node_map.items():
        parents = []
        for parent_node in migration_node.parents:
            parents.append(parent_node.key)
        dep_graph[node] = parents

    total_migrations = len(dep_graph)
    print(f"✅ Loaded {total_migrations} migration nodes (with __latest__/__first__ resolved)\n")

    # ── 2. Query what the DB currently considers applied ──────────────────────
    print("🔍 Querying django_migrations table...")
    applied_in_db = get_applied_from_db()
    print(f"✅ Found {len(applied_in_db)} applied migration records in DB\n")

    # ── 3. Find all missing ancestors ─────────────────────────────────────────
    print("🔍 Scanning for history inconsistencies...")
    missing = set()

    for node in applied_in_db:
        if node not in dep_graph:
            # Migration is applied but its file doesn't exist on disk — skip
            continue
        ancestors = collect_all_ancestors(node, dep_graph)
        for ancestor in ancestors:
            if ancestor not in applied_in_db and ancestor not in missing:
                missing.add(ancestor)

    if not missing:
        print("✅ No inconsistencies found — migration history is clean!\n")
        return True

    # ── 4. Report what was found ───────────────────────────────────────────────
    print(f"⚠️  INCONSISTENCIES DETECTED: {len(missing)} missing ancestor record(s)\n")
    for app, name in sorted(missing):
        print(f"   ✗  {app}.{name}  ← applied descendant exists, but this record is missing")

    # ── 5. Insert missing records ─────────────────────────────────────────────
    print("\n🔧 FIXING: Inserting missing migration records into django_migrations...")
    inserted = []
    failed = []

    for app, name in sorted(missing):
        try:
            was_inserted = insert_missing_record(app, name)
            if was_inserted:
                print(f"   ✅ Inserted  {app}.{name}")
                inserted.append((app, name))
            else:
                print(f"   ⏭️  Skipped   {app}.{name}  (already present after re-check)")
        except Exception as exc:
            print(f"   ❌ FAILED    {app}.{name}  — {exc}")
            failed.append((app, name))

    # ── 6. Summary ────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("SUMMARY".center(70))
    print("=" * 70)
    print(f"  Missing records found : {len(missing)}")
    print(f"  Successfully inserted : {len(inserted)}")
    print(f"  Already present       : {len(missing) - len(inserted) - len(failed)}")
    print(f"  Failed                : {len(failed)}")

    if failed:
        print("\n❌ Some records could not be inserted — see errors above.")
        return False

    if inserted:
        print("\n✅ All missing records inserted — history is now consistent.")
        print("   Django migrate will proceed without InconsistentMigrationHistory errors.")
    print()
    return True


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    try:
        success = fix_inconsistent_history()
        sys.exit(0 if success else 1)
    except Exception as exc:
        print(f"\n❌ UNEXPECTED ERROR: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
