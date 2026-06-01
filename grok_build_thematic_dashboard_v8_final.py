"""
Legacy Streamlit Cloud entrypoint.
Older deployments use this filename; it loads the current app.py (v15).
"""
from pathlib import Path

_app = Path(__file__).resolve().parent / "app.py"
exec(compile(_app.read_text(encoding="utf-8"), str(_app), "exec"), {"__name__": "__main__"})