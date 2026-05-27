import streamlit as st
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
REPORTS_DIR = ROOT / "reports" / "webui"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

st.title("Humanonn — Web Scanner UI")
st.write("Run Humanonn scans from the browser. The server must have the repo and dependencies installed.")

url = st.text_input("URL to scan", "https://example.com")
scan_btn = st.button("Start scan")

log_box = st.empty()
download_placeholder = st.empty()

if scan_btn and url:
    timestamp = int(time.time())
    out_dir = REPORTS_DIR / f"scan_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / "site.json"

    cmd = [sys.executable, "-m", "humanonn", "scan", url, "--json", str(out_json)]
    st.info(f"Running: {' '.join(cmd)}")

    with st.spinner("Scanning — this may take a few minutes depending on the target..."):
        # stream stdout from subprocess
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        logs = []
        try:
            for line in process.stdout:
                logs.append(line.rstrip())
                log_box.text("\n".join(logs[-200:]))
            process.wait()
        except Exception as exc:
            logs.append(f"ERROR: {exc}")
            log_box.text("\n".join(logs[-200:]))

    if out_json.exists():
        st.success("Scan complete — report ready")
        with open(out_json, "r", encoding="utf-8") as f:
            data = f.read()
        download_placeholder.download_button("Download report (JSON)", data, file_name=out_json.name, mime="application/json")
        st.write(f"Report path: {out_json}")
    else:
        st.error("Scan did not produce a report — check logs above.")

st.markdown("---")
st.write("Server notes: Ensure `.venv` is activated and dependencies installed. Use `python -m playwright install chromium` if needed.")
