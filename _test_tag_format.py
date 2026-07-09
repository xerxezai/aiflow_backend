from apps.pid_analysis.instrument_index_service import InstrumentIndexService

svc = InstrumentIndexService()
samples = [
    {"tag_number": "TT-803"},
    {"tag_number": "PG-31270X-803"},
    {"tag_number": "FT-1502"},
    {"tag_number": "PSV-8501A"},
    {"tag_number": "562-LT-2721"},
    {"tag_number": "562 PG 3501"},
    {"tag_number": "LIT-1240A"},
    {"tag_number": "803-PG-3501"},
    {"tag_number": "TI-3901-01"},
]
drawing_info = {"project_category": "adnoc_gas", "pid_no": "562-PID-001", "drawing_number": "562-PID-001"}
out = svc._apply_tag_format([dict(s) for s in samples], drawing_info)
print("--- TAG FORMAT UNIT TEST ---")
for s, o in zip(samples, out):
    raw = s["tag_number"]
    new = o.get("tag_number")
    rk = o.get("instrument_remark", "")
    print(f"{raw:<20} -> {new:<22} remark='{rk}'")
