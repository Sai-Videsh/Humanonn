import json
import html
import streamlit as st
import threading
import queue
import subprocess
import sys
import time
from pathlib import Path
import os

ROOT = Path(__file__).parent
REPORTS_DIR = ROOT / "reports" / "webui"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _inject_css(path: Path):
    if path.exists():
        css = path.read_text(encoding="utf-8")
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def _render_live_logs(placeholder, logs: list[str], running: bool) -> None:
    if logs:
        body = "\n".join(html.escape(line) for line in logs[-300:])
        # body = "\n".join(html.escape(line) for line in reversed(logs[-300:]))
    elif running:
        body = "Live logs will appear here while the scan runs."
    else:
        body = "No live logs in this session. Run a scan to see them here."

    log_html = """
    <div id="humanonn-live-log" style="
        height: 260px;
        overflow-y: auto;
        background: #0f1115;
        color: #e7e9ee;
        border: 1px solid #252935;
        border-radius: 6px;
        padding: 12px;
        font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        font-size: 12px;
        line-height: 1.45;
        white-space: pre-wrap;
        ">__BODY__</div>
    <script>
      const logBox = document.getElementById("humanonn-live-log");
      if (logBox) {
        const threshold = 48;
        const storageKey = "humanonn-live-log-autoscroll";
        # const nearBottom = logBox.scrollHeight - logBox.scrollTop - logBox.clientHeight < threshold;
        # const shouldStick = localStorage.getItem(storageKey) !== "paused";
        # if (shouldStick || nearBottom) {
        #   logBox.scrollTop = 0;
        # }
        if (!logBox.dataset.bound) {
          logBox.dataset.bound = "true";
          logBox.addEventListener("scroll", () => {
            const isNearBottom = logBox.scrollHeight - logBox.scrollTop - logBox.clientHeight < threshold;
            localStorage.setItem(storageKey, isNearBottom ? "auto" : "paused");
          }, { passive: true });
        }
          // Auto-scroll to bottom unless user explicitly scrolled up
            const shouldStick = localStorage.getItem(storageKey) !== "paused";
            if (shouldStick) {
                logBox.scrollTop = logBox.scrollHeight;
            }

      }
    </script>
        """.replace("__BODY__", body)
    with placeholder.container():
        st.html(log_html, unsafe_allow_javascript=True)


def _render_log_window(placeholder, title: str, logs: list[str], empty_running_text: str, empty_idle_text: str) -> None:
    if logs:
        body = "\n".join(html.escape(line) for line in logs[-300:])
    else:
        body = empty_running_text if st.session_state.get("live_scan_running") else empty_idle_text

    log_html = """
    <div id="__ID__" style="
        height: 260px;
        overflow-y: auto;
        background: #0f1115;
        color: #e7e9ee;
        border: 1px solid #252935;
        border-radius: 6px;
        padding: 12px;
        font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        font-size: 12px;
        line-height: 1.45;
        white-space: pre-wrap;
        ">__BODY__</div>
    """.replace("__ID__", title).replace("__BODY__", body)
    with placeholder.container():
        st.html(log_html, unsafe_allow_javascript=True)


def _is_source_scan_log_line(line: str) -> bool:
    prefixes = (
        "Starting source-code scan",
        "Fetched ",
        "Checked source rule ",
        "Computed raw source code score ",
        "Added normalized source code score ",
        "Boosted DOM confidence to 1.0 from source-code agreement:",
    )
    return line.startswith(prefixes)


def _scan_stdout_reader(process: subprocess.Popen[str], output_queue: queue.Queue[tuple[str, str | int]]) -> None:
    try:
        assert process.stdout is not None
        for line in iter(process.stdout.readline, ""):
            if line:
                output_queue.put(("line", line.rstrip()))
        return_code = process.wait()
        output_queue.put(("done", return_code))
    except Exception as exc:
        output_queue.put(("error", str(exc)))


def _scan_mode(url: str, repo_url: str | None) -> str:
    has_live_url = bool(url.strip())
    has_repo_url = bool(repo_url and repo_url.strip())
    if has_live_url and has_repo_url:
        return "combined"
    if has_live_url:
        return "live_only"
    if has_repo_url:
        return "source_only"
    return "invalid"


def _start_scan(url: str, repo_url: str | None = None) -> None:
    timestamp = int(time.time())
    out_dir = REPORTS_DIR / f"scan_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / "site.json"

    mode = _scan_mode(url, repo_url)
    if mode == "invalid":
        return

    st.session_state["live_logs"] = []
    st.session_state["source_logs"] = []
    st.session_state["live_scan_running"] = True
    st.session_state["scan_stop_requested"] = False
    st.session_state["scan_report_data"] = None
    st.session_state["scan_report_path"] = None
    st.session_state["latest_run_summary"] = None
    st.session_state["scan_mode"] = mode

    cmd = [sys.executable, "-u", "-m", "humanonn", "scan", "--json", str(out_json)]
    env = os.environ.copy()
    if mode == "source_only":
        env["HUMANONN_LIVE_SITE_SCRAPING"] = "false"
        cmd.append("--no-llm")
        cmd.append("--source-only")
    else:
        cmd.insert(5, url.strip())
    if repo_url and repo_url.strip():
        cmd.extend(["--repo-url", repo_url])
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        creationflags=creationflags,
    )
    output_queue: queue.Queue[tuple[str, str | int]] = queue.Queue()
    reader = threading.Thread(target=_scan_stdout_reader, args=(process, output_queue), daemon=True)
    reader.start()

    st.session_state["scan_process"] = process
    st.session_state["scan_output_queue"] = output_queue
    st.session_state["scan_reader_thread"] = reader
    st.session_state["scan_output_path"] = out_json
    st.session_state["scan_command"] = cmd


def _stop_scan() -> None:
    process = st.session_state.get("scan_process")
    if not process:
        return
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except Exception:
                process.kill()
    finally:
        st.session_state["scan_stop_requested"] = True
        st.session_state["live_scan_running"] = False


def _drain_scan_output() -> None:
    output_queue = st.session_state.get("scan_output_queue")
    if not output_queue:
        return
    finished = False
    while True:
        try:
            kind, payload = output_queue.get_nowait()
        except queue.Empty:
            break
        if kind == "line":
            line = str(payload)
            if _is_source_scan_log_line(line):
                st.session_state.setdefault("source_logs", []).append(line)
            else:
                st.session_state["live_logs"].append(line)
        elif kind == "error":
            st.session_state["live_logs"].append(f"ERROR: {payload}")
            finished = True
        elif kind == "done":
            finished = True
    if finished:
        st.session_state["live_scan_running"] = False
        st.session_state["scan_process"] = None
        st.session_state["scan_output_queue"] = None
        st.session_state["scan_reader_thread"] = None
        out_json = st.session_state.get("scan_output_path")
        if not st.session_state.get("scan_stop_requested") and out_json:
            report_path = Path(out_json)
            if report_path.exists():
                st.session_state["scan_report_path"] = report_path
                try:
                    report_data = json.loads(report_path.read_text(encoding="utf-8"))
                except Exception:
                    report_data = None
                st.session_state["scan_report_data"] = report_data
                if report_data:
                    score_block = report_data.get("score") if isinstance(report_data.get("score"), dict) else {}
                    vibe = report_data.get("vibe_score") or score_block.get("vibe_score") or report_data.get("final_score")
                    findings = report_data.get("findings") or report_data.get("flagged_issues") or []
                    st.session_state["latest_run_summary"] = f"{report_data.get('url', '')} — {len(findings)} findings — vibe: {vibe}"
        else:
            st.session_state["scan_report_data"] = None
            st.session_state["scan_report_path"] = None


def _source_code_log_lines(report_data: dict | None, running: bool = False) -> list[str]:
    session_logs = [str(line) for line in st.session_state.get("source_logs", [])]
    if running and session_logs:
        return session_logs
    if not report_data:
        return session_logs
    if isinstance(report_data.get("scan_code_log"), list) and report_data.get("scan_code_log"):
        return [str(line) for line in report_data.get("scan_code_log", [])]
    source_code = report_data.get("source_code") if isinstance(report_data.get("source_code"), dict) else {}
    score_block = report_data.get("score") if isinstance(report_data.get("score"), dict) else {}
    logs = source_code.get("scan_log") if isinstance(source_code.get("scan_log"), list) else []
    if logs:
        rendered = score_block.get("rendered_vibe_score")
        source_score = source_code.get("normalized_source_code_score", source_code.get("source_code_score"))
        return [
            *( [f"Source code score: {source_score}/100"] if source_score is not None else [] ),
            *( [f"Live site score: {rendered}/100"] if rendered is not None else [] ),
            *[str(line) for line in logs],
        ]
    findings = source_code.get("findings") or []
    fallback: list[str] = []
    if source_code:
        repo_url = source_code.get("repo_url", "—")
        fallback.append(f"Starting source-code scan for {repo_url}.")
        fallback.append(f"Fetched {source_code.get('files_scanned', 0)} source files for scanning.")
        for item in findings:
            if not isinstance(item, dict):
                continue
            state = "FLAGGED" if item.get("flagged") else "clear"
            fallback.append(f"[{state}] {item.get('id', 'unknown')} - {item.get('reason', '')}")
        if source_code.get("source_code_score") is not None:
            fallback.append(f"Source code score: {source_code.get('normalized_source_code_score', source_code.get('source_code_score', 0))}/100")
            fallback.append(f"Computed raw source code score {source_code.get('source_code_score', 0)}/{source_code.get('score_cap', 25)}.")
    return fallback


def _live_site_log_lines(report_data: dict | None, scan_mode: str | None = None) -> list[str]:
    if not report_data:
        if scan_mode == "source_only":
            return ["No live link was provided, so live scoring is not included."]
        return []
    if isinstance(report_data.get("scan_live_log"), list) and report_data.get("scan_live_log"):
        return [str(line) for line in report_data.get("scan_live_log", [])]
    score_block = report_data.get("score") if isinstance(report_data.get("score"), dict) else {}
    report_mode = report_data.get("scan_metadata", {}).get("scan_mode") if isinstance(report_data.get("scan_metadata"), dict) else None
    vibe = report_data.get("vibe_score") or score_block.get("vibe_score") or report_data.get("final_score")
    rendered = score_block.get("rendered_vibe_score")
    lines: list[str] = []
    if scan_mode == "source_only" or report_mode == "source_only":
        lines.append("No live link was provided, so live scoring is not included.")
        return lines
    if rendered is not None:
        lines.append(f"Live site score: {rendered}/100")
    if vibe is not None:
        lines.append(f"Merged vibe score: {vibe}/100")
    return lines


def _render_scan_controls() -> None:
    left, right = st.columns(2)
    mode = _scan_mode(url, github_repo_url)
    start_label = {
        "combined": "Start combined scan",
        "live_only": "Start live scan",
        "source_only": "Start source scan",
    }.get(mode, "Start scan")
    start_clicked = left.button(start_label, disabled=st.session_state["live_scan_running"] or mode == "invalid")
    stop_clicked = right.button("Stop scan", disabled=not st.session_state["live_scan_running"])
    if start_clicked:
        _start_scan(url, github_repo_url.strip() or None)
        st.rerun()
    if stop_clicked:
        _stop_scan()
        st.rerun()


st.title("Humanonn — Web Scanner UI")
st.write("Run Humanonn scans from the browser. The server must have the repo and dependencies installed.")

_inject_css(REPORTS_DIR / "style.css")

url = st.text_input("URL to scan", "https://example.com")
github_repo_url = st.text_input(
    "Public GitHub repo URL for source-code scoring (Optional)",
    "",
    placeholder="https://github.com/owner/repo",
)
st.caption("Note: Source-code scanning currently supports repositories built with Next.js, React, and Tailwind CSS.")
st.session_state.setdefault("live_logs", [])
st.session_state.setdefault("live_scan_running", False)
st.session_state.setdefault("scan_stop_requested", False)
st.session_state.setdefault("scan_process", None)
st.session_state.setdefault("scan_output_queue", None)
st.session_state.setdefault("scan_reader_thread", None)
st.session_state.setdefault("scan_output_path", None)
st.session_state.setdefault("scan_report_data", None)
st.session_state.setdefault("scan_report_path", None)
st.session_state.setdefault("latest_run_summary", None)
st.session_state.setdefault("scan_mode", "invalid")
st.session_state.setdefault("source_logs", [])

_render_scan_controls()

download_placeholder = st.empty()
with st.expander("Show live logs", expanded=False):
    live_log_box = st.empty()
    if st.session_state["live_scan_running"]:
        _drain_scan_output()
    live_logs = _live_site_log_lines(st.session_state.get("scan_report_data") or None, st.session_state.get("scan_mode")) + st.session_state["live_logs"]
    _render_live_logs(live_log_box, live_logs, st.session_state["live_scan_running"])

with st.expander("Show source code scan", expanded=False):
    source_log_box = st.empty()
    source_logs = _source_code_log_lines(st.session_state.get("scan_report_data") or None, st.session_state["live_scan_running"])
    _render_log_window(
        source_log_box,
        "humanonn-source-code-log",
        source_logs,
        "Source-code scan will appear here after the site scan completes.",
        "No source-code scan available yet. Run a scan with a GitHub repo URL to see it here.",
    )


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

if st.session_state["scan_report_data"] and not st.session_state["live_scan_running"]:
    report_data = st.session_state["scan_report_data"]
    latest_run_summary = st.session_state.get("latest_run_summary")
    last_report_path = st.session_state.get("scan_report_path")
else:
    latest_run_summary = st.session_state.get("latest_run_summary")
    last_report_path = st.session_state.get("scan_report_path")
    report_data = None
    if not st.session_state["scan_stop_requested"]:
        latest = load_latest_report()
        if latest and latest.exists() and not st.session_state["live_scan_running"]:
            try:
                report_data = json.loads(latest.read_text(encoding="utf-8"))
            except Exception:
                report_data = None
            last_report_path = latest

if not st.session_state["live_scan_running"] and report_data:
    report_json = json.dumps(report_data, indent=2)
    download_placeholder.download_button("Download report (JSON)", report_json, file_name=(Path(last_report_path).name if last_report_path else "site.json"), mime="application/json")
else:
    download_placeholder.empty()


st.markdown("---")

# Top-level layout: left (flagged issues) and right (metrics + summaries)
left_col, right_col = st.columns([6, 7])

with left_col:
    st.header("Findings")
    if st.session_state["live_scan_running"]:
        st.write("Scan running. Metrics and findings will reappear after the current site finishes.")
    elif report_data:
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
    if st.session_state["live_scan_running"]:
        st.write("Metrics hidden until the new scan completes.")
    elif report_data:
        score_block = report_data.get("score") if isinstance(report_data.get("score"), dict) else {}
        vibe = report_data.get("vibe_score") or score_block.get("vibe_score") or report_data.get("final_score")
        rendered_vibe = score_block.get("rendered_vibe_score")
        source_code_score = score_block.get("source_code_score", 0)
        category = score_block.get("category") or report_data.get("category") or "Human Built"
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
        st.markdown(f"**Merged Vibe Score:** {vibe if vibe is not None else '—'}/100")
        st.markdown(f"### {category}")
        if score_mode == "source_only":
            st.markdown("**Live Site Score:** —/100")
            st.markdown("**Source-only mode:** live site scraping is disabled; score is driven by source-code findings.")
            st.markdown(f"**Source Code Score:** +{source_code_score}")
        elif rendered_vibe is not None or source_code_score:
            st.markdown(f"**Live Site Score:** {rendered_vibe if rendered_vibe is not None else vibe}/100")
            st.markdown(f"**Source Code Score:** +{source_code_score}")
        st.markdown(f"**Humanness Score:** {humanness if humanness is not None else '—'}/100")
        st.markdown(f"**Base Score:** {base if base is not None else '—'}")
        st.markdown(f"**Cluster Bonus:** {cluster if cluster is not None else '—'}")
        st.markdown(f"**Score Mode:** {score_mode}")
        st.markdown(f"**Flagged Signals:** {flagged_count}/{total_findings}")
        scan_metadata = report_data.get("scan_metadata") if isinstance(report_data.get("scan_metadata"), dict) else {}
        if scan_metadata:
            st.markdown(f"**Verified Components:** {scan_metadata.get('verified_components', 0)}")
            st.markdown(f"**Style-Verified Components:** {scan_metadata.get('style_verified_components', 0)}")
            st.markdown(f"**Unverified Components:** {scan_metadata.get('unverified_components', 0)}")
            st.markdown(f"**Interaction Coverage:** {scan_metadata.get('interaction_coverage_ratio', 'â€”')}")

        tier_counts = score_block.get("tier_counts")
        if isinstance(tier_counts, dict) and tier_counts:
            st.markdown("**Tier Counts:**")
            for tier in sorted(tier_counts, key=lambda x: str(x)):
                st.markdown(f"- Tier {tier}: {tier_counts[tier]}")

        screenshot_path = report_data.get("screenshot_path")
        screenshot_file = _resolve_screenshot_path(screenshot_path)
        st.markdown("**Screenshot:**")
        if screenshot_file:
            st.image(str(screenshot_file), width='stretch')
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

        source_code = report_data.get("source_code") if isinstance(report_data.get("source_code"), dict) else {}
        with st.expander("Source Code Findings", expanded=bool(source_code)):
            if source_code:
                st.markdown(f"**Repo:** {source_code.get('repo_url', '—')}")
                st.markdown(f"**Files Scanned:** {source_code.get('files_scanned', 0)}")
                st.markdown(f"**Source Code Score:** +{source_code.get('source_code_score', 0)}")
                source_findings = source_code.get("findings") or []
                flagged_source_findings = [item for item in source_findings if isinstance(item, dict) and item.get("flagged")]
                if flagged_source_findings:
                    for item in flagged_source_findings:
                        st.markdown(
                            f"- T{item.get('tier', '?')} {item.get('bucket', '?')} +{item.get('points', 0)} "
                            f"**{item.get('name', item.get('id', 'Source finding'))}**: {item.get('reason', '')}"
                        )
                elif source_code.get("error"):
                    st.write(f"Source scan failed: {source_code.get('error')}")
                else:
                    st.write("No source-code checks were flagged.")
            else:
                st.write("No GitHub repo was provided for this scan.")

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

if st.session_state["live_scan_running"]:
    time.sleep(1)
    st.rerun()
