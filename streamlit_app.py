import json
import html
import streamlit as st
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
REPORTS_DIR = ROOT / "reports" / "webui"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _inject_css(path: Path):
    if path.exists():
        css = path.read_text(encoding="utf-8")
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


st.title("Humanonn — Web Scanner UI")
st.write("Run Humanonn scans from the browser. The server must have the repo and dependencies installed.")

_inject_css(REPORTS_DIR / "style.css")

url = st.text_input("URL to scan", "https://example.com")
scan_btn = st.button("Start scan")

download_placeholder = st.empty()
st.session_state.setdefault("live_logs", [])
st.session_state.setdefault("live_scan_running", False)
with st.expander("Show live logs", expanded=False):
    live_log_box = st.empty()
    if st.session_state["live_logs"]:
        live_log_box.text("\n".join(st.session_state["live_logs"][-200:]))
    elif st.session_state["live_scan_running"]:
        live_log_box.write("Live logs will appear here while the scan runs.")
    else:
        live_log_box.write("No live logs in this session. Run a scan to see them here.")

latest_run_summary = None
last_report_path = None


def _resolve_screenshot_path(raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    candidate = Path(raw_path)
    if candidate.is_absolute() and candidate.exists():
        return candidate
    # JSON reports often store relative paths such as reports\\data\\... on Windows.
    local = ROOT / raw_path
    return local if local.exists() else None


def _flagged_findings(report_data: dict) -> list[dict]:
    findings = report_data.get("findings") or report_data.get("flagged_issues") or []
    normalized: list[dict] = []
    for item in findings:
        if not isinstance(item, dict):
            continue
        if not item.get("flagged"):
            continue
        normalized.append(item)
    return normalized


def _format_finding_cli_block(finding: dict) -> str:
    tier = finding.get("tier", "?")
    name = finding.get("name") or finding.get("title") or "Unnamed finding"
    finding_id = finding.get("id") or finding.get("signal_id") or "unknown_id"
    reason = finding.get("reason") or "No reason provided."
    fix = finding.get("fix") or "No fix provided."
    return "\n".join(
        [
            f"[FLAGGED] T{tier} {name} ({finding_id})",
            f"  Reason: {reason}",
            f"  Fix: {fix}",
        ]
    )

def load_latest_report():
    reports = sorted(REPORTS_DIR.glob("**/site.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return reports[0] if reports else None


if scan_btn and url:
    timestamp = int(time.time())
    out_dir = REPORTS_DIR / f"scan_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / "site.json"
    st.session_state["live_logs"] = []
    st.session_state["live_scan_running"] = True

    cmd = [sys.executable, "-m", "humanonn", "scan", url, "--json", str(out_json)]
    st.info(f"Running: {' '.join(cmd)}")

    with st.spinner("Scanning — this may take a few minutes depending on the target..."):
        # stream stdout from subprocess
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            for line in process.stdout:
                st.session_state["live_logs"].append(line.rstrip())
                # keep the live log box compact
                live_log_box.text("\n".join(st.session_state["live_logs"][-200:]))
            process.wait()
        except Exception as exc:
            st.session_state["live_logs"].append(f"ERROR: {exc}")
            live_log_box.text("\n".join(st.session_state["live_logs"][-200:]))
        finally:
            st.session_state["live_scan_running"] = False

    if out_json.exists():
        st.success("Scan complete — report ready")
        with open(out_json, "r", encoding="utf-8") as f:
            data = f.read()
        download_placeholder.download_button("Download report (JSON)", data, file_name=out_json.name, mime="application/json")
        last_report_path = out_json
        # try to extract a brief summary from the produced report
        try:
            parsed = json.loads(data)
            score_block = parsed.get("score") if isinstance(parsed.get("score"), dict) else {}
            vibe = parsed.get("vibe_score") or score_block.get("vibe_score") or parsed.get("final_score")
            findings = parsed.get("findings") or parsed.get("flagged_issues") or []
            latest_run_summary = f"{url} — {len(findings)} findings — vibe: {vibe}"
        except Exception:
            latest_run_summary = f"{url} — report ready"
    else:
        st.error("Scan did not produce a report — check logs above.")


st.markdown("---")

# Attempt to locate a latest report if one exists
latest = last_report_path or load_latest_report()
report_data = None
if latest and latest.exists():
    try:
        report_data = json.loads(latest.read_text(encoding="utf-8"))
    except Exception:
        report_data = None

# Top-level layout: left (flagged issues) and right (metrics + summaries)
left_col, right_col = st.columns([6, 7])

with left_col:
    st.header("Findings")
    if report_data:
        flagged_findings = _flagged_findings(report_data)
        with st.expander(f"Flagged Issues ({len(flagged_findings)})", expanded=True):
            if flagged_findings:
                for f in flagged_findings:
                    block = html.escape(_format_finding_cli_block(f))
                    st.markdown(f"<div class='finding-block'>{block}</div>", unsafe_allow_html=True)
            else:
                st.write("No rule-based signals were flagged.")
    else:
        st.write("No report found. Run a scan or place a `site.json` under `reports/webui/scan_<ts>/site.json`.")

with right_col:
    st.header("Metrics")
    if report_data:
        score_block = report_data.get("score") if isinstance(report_data.get("score"), dict) else {}
        vibe = report_data.get("vibe_score") or score_block.get("vibe_score") or report_data.get("final_score")
        humanness = report_data.get("humanness_score") or score_block.get("humanness_score")
        base = report_data.get("base_score") or score_block.get("base_score") or report_data.get("score_base")
        cluster = report_data.get("cluster_bonus") or score_block.get("cluster_bonus") or report_data.get("cluster")
        score_mode = score_block.get("score_mode") or report_data.get("score_mode") or "—"
        findings = report_data.get("findings") or []
        flagged_count = len([f for f in findings if isinstance(f, dict) and f.get("flagged")])
        total_findings = len(findings) if isinstance(findings, list) else 0
        report_url = report_data.get("url") or "—"
        report_title = report_data.get("title") or "(untitled)"

        st.markdown(f"**URL:** {report_url}")
        st.markdown(f"**Title:** {report_title}")
        st.markdown(f"**Vibe Score:** {vibe if vibe is not None else '—'}/100")
        st.markdown(f"**Humanness Score:** {humanness if humanness is not None else '—'}/100")
        st.markdown(f"**Base Score:** {base if base is not None else '—'}")
        st.markdown(f"**Cluster Bonus:** {cluster if cluster is not None else '—'}")
        st.markdown(f"**Score Mode:** {score_mode}")
        st.markdown(f"**Flagged Signals:** {flagged_count}/{total_findings}")

        tier_counts = score_block.get("tier_counts")
        if isinstance(tier_counts, dict) and tier_counts:
            st.markdown("**Tier Counts:**")
            for tier in sorted(tier_counts, key=lambda x: str(x)):
                st.markdown(f"- Tier {tier}: {tier_counts[tier]}")

        screenshot_path = report_data.get("screenshot_path")
        screenshot_file = _resolve_screenshot_path(screenshot_path)
        st.markdown("**Screenshot:**")
        if screenshot_file:
            st.image(str(screenshot_file), use_container_width=True)
        else:
            st.write("Screenshot not found.")

        smart = report_data.get("smart_summary") or report_data.get("summary")
        with st.expander("Smart Summary", expanded=False):
            if smart:
                if isinstance(smart, dict):
                    st.write(smart.get("text") or json.dumps(smart, indent=2))
                else:
                    st.write(smart)
            else:
                st.write("No smart summary included in this report.")

        with st.expander("Agent Notes", expanded=True):
            notes = report_data.get("agent_notes") or report_data.get("notes") or []
            if notes:
                for note in notes:
                    st.markdown(f"- {note}")
            else:
                st.write("No agent notes.")

        with st.expander("Dynamic Findings", expanded=True):
            dynamic_findings = report_data.get("dynamic_findings") or []
            if dynamic_findings:
                for item in dynamic_findings[:8]:
                    label = item.get("label", "Finding") if isinstance(item, dict) else "Finding"
                    reason = item.get("reason", "") if isinstance(item, dict) else str(item)
                    st.markdown(f"- {label}: {reason}")
            else:
                st.write("No dynamic findings.")

    else:
        st.write("No metrics available — run a scan to populate report metrics.")

st.markdown("---")
st.write("Server notes: Ensure `.venv` is activated and dependencies installed. Use `python -m playwright install chromium` if needed.")
