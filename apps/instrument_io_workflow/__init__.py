"""
Instrument IO List Workflow — CRS-style multi-revision workflow for
Instrument I/O List deliverables (Comments Resolution Sheet + IO Table).

Design goals:
- Re-use apps.crs and apps.crs_documents helpers as libraries (no core changes)
- Cost-optimised extraction pipeline:
    1. PyMuPDF text + table extraction (FREE)
    2. Heuristic page classifier (regex, FREE)
    3. Regex-based comment ↔ row linker (FREE)
    4. Pure-Python revision diff (FREE)
    5. GPT-4o-mini vision fallback — OPT-IN, page-targeted only
- Result caching by SHA-256 hash of uploaded PDF (avoids re-processing)
"""

default_app_config = 'apps.instrument_io_workflow.apps.InstrumentIOWorkflowConfig'
