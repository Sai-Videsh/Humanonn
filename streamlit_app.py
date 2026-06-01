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
import urllib.parse
import urllib.request
import urllib.error

ROOT = Path(__file__).parent
REPORTS_DIR = ROOT / "reports" / "webui"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _worker_base_url() -> str | None:
    raw = (os.getenv("HUMANONN_WORKER_URL") or "").strip()
    if not raw:
        return None
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw.rstrip("/")
    return f"http://{raw.rstrip('/')}"


def _is_production() -> bool:
    return (os.getenv("HUMANONN_PRODUCTION") or "false").strip().lower() == "true"


def _worker_timeout_seconds(default_timeout: int) -> int:
    if _is_production():
        return max(default_timeout, 30)
    return default_timeout


def _worker_request(method: str, path: str, payload: dict | None = None, timeout: int = 10) -> dict:
    base = _worker_base_url()
    if not base:
        raise RuntimeError("HUMANONN_WORKER_URL is not configured.")
    body = None
    headers = {"User-Agent": "Humanonn-UI/1.0"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(f"{base}{path}", data=body, headers=headers, method=method)

    attempts = 3 if _is_production() else 1
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=_worker_timeout_seconds(timeout)) as response:
                text = response.read().decode("utf-8", errors="replace")
            return json.loads(text) if text else {}
        except urllib.error.URLError as exc:
            last_error = exc
            timeout_error = isinstance(getattr(exc, "reason", None), TimeoutError) or "timed out" in str(exc).lower()
            if not _is_production() or not timeout_error or attempt + 1 >= attempts:
                raise
            time.sleep(1.0)

    if last_error:
        raise last_error
    raise RuntimeError("Worker request failed.")


def _inject_css(path: Path):
    if path.exists():
        css = path.read_text(encoding="utf-8")
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def _render_live_logs(placeholder, logs: list[str], running: bool) -> None:
    if logs:
        body = "\n".join(html.escape(str(line)) for line in logs[-300:])
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
        if (!logBox.dataset.bound) {
          logBox.dataset.bound = "true";
          logBox.addEventListener("scroll", () => {
            const isNearBottom = logBox.scrollHeight - logBox.scrollTop - logBox.clientHeight < threshold;
            localStorage.setItem(storageKey, isNearBottom ? "auto" : "paused");
          }, { passive: true });
        }
                // Auto-scroll to bottom unless the user explicitly scrolled up.
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
        body = "\n".join(html.escape(str(line)) for line in logs[-300:])
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


def _render_inline_lines(lines: list[str]) -> str:
    rendered_lines: list[str] = []
    for line in lines:
        text = str(line).strip()
        if not text:
            continue
        lower = text.lower()
        is_error = lower.startswith("error:") or "forbidden" in lower or "rate limit" in lower or "failed" in lower
        color = "#ff3333" if is_error else "#e7e9ee"
        rendered_lines.append(f"<div style='color:{color}; margin-top:6px;'>{html.escape(text)}</div>")
    return "<div style='margin-top:6px;'>" + "".join(rendered_lines) + "</div>"


def _should_hide_github_rate_limit_message(text: str) -> bool:
    lowered = text.lower()
    return "github api request was forbidden or the rate limit was reached" in lowered or (
        "github" in lowered and ("rate limit" in lowered or "forbidden" in lowered)
    )


def _render_audit_summary_block(report_data: dict | None) -> None:
    if not report_data:
        st.write("No audit summary available yet.")
        return

    score_block = report_data.get("score") if isinstance(report_data.get("score"), dict) else {}
    category = score_block.get("category") or report_data.get("category") or "Human Built"
    vibe = report_data.get("vibe_score") or score_block.get("vibe_score") or report_data.get("final_score")
    rendered_vibe = score_block.get("rendered_vibe_score")
    source_code_score = score_block.get("source_code_score", 0)
    humanness = report_data.get("humanness_score") or score_block.get("humanness_score")
    base = report_data.get("base_score") or score_block.get("base_score") or report_data.get("score_base")
    cluster = report_data.get("cluster_bonus") or score_block.get("cluster_bonus") or report_data.get("cluster")
    score_mode = score_block.get("score_mode") or report_data.get("score_mode") or "—"
    report_url = report_data.get("url") or "—"
    report_title = report_data.get("title") or "(untitled)"

    st.markdown("### Humanonn Audit")
    st.markdown(f"**URL:** {report_url}")
    st.markdown(f"**Title:** {report_title}")
    st.markdown(f"**Category:** {category}")
    st.markdown(f"**Merged Vibe Score:** {vibe if vibe is not None else '—'}/100")
    st.markdown(f"**Source Code Score:** {source_code_score}/100")
    st.markdown(f"**Humanness Score:** {humanness if humanness is not None else '—'}/100")
    st.markdown(f"**Base Score:** {base if base is not None else '—'}")
    st.markdown(f"**Cluster Bonus:** {cluster if cluster is not None else '—'}")
    st.markdown(f"**Score Mode:** {score_mode}")
    if score_mode == "source_only":
        st.markdown("**Rendered Vibe Score:** —/100")
        st.markdown("**Source-only mode:** live site scraping is disabled; score is driven by source-code findings.")
    elif rendered_vibe is not None or source_code_score:
        st.markdown(f"**Rendered Vibe Score:** {rendered_vibe if rendered_vibe is not None else vibe}/100")


_SCAN_START_POPUP_MESSAGE = (
    "Your site is looking awesome, with more interactive elements, animated designs, and richer motion. "
    "That takes more time to scan perfectly. So start running, keep working while the scan runs in the background, "
    "and when you come back you will get to know some interesting things about your site. Good luck."
)


def _dismiss_scan_start_popup() -> None:
    st.session_state["scan_start_popup_visible"] = False


if hasattr(st, "dialog"):

    @st.dialog("Scan started")
    def _render_scan_start_popup() -> None:
        st.markdown(_SCAN_START_POPUP_MESSAGE)

else:

    def _render_scan_start_popup() -> None:
        with st.container(border=True):
            st.markdown("### Scan started")
            st.markdown(_SCAN_START_POPUP_MESSAGE)


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

    if _worker_base_url():
        try:
            response = _worker_request(
                "POST",
                "/scan",
                {
                    "url": url.strip(),
                    "repo_url": (repo_url.strip() if repo_url else None),
                },
                timeout=20,
            )
            job_id = str(response.get("job_id") or "").strip()
            if not job_id:
                raise RuntimeError("Worker did not return a job id.")
            st.session_state["scan_remote_job_id"] = job_id
            st.session_state["scan_process"] = None
            st.session_state["scan_output_queue"] = None
            st.session_state["scan_reader_thread"] = None
            st.session_state["scan_output_path"] = out_json
            st.session_state["scan_command"] = ["remote-worker", job_id]
            st.session_state["live_logs"].append(f"Remote worker job started: {job_id}")
            return
        except urllib.error.HTTPError as exc:
            msg = f"Worker request failed with HTTP {exc.code}."
            try:
                msg = json.loads(exc.read().decode("utf-8", errors="replace")).get("detail", msg)
            except Exception:
                pass
            st.session_state["live_scan_running"] = False
            st.session_state["live_logs"].append(f"ERROR: {msg}")
            return
        except Exception as exc:
            st.session_state["live_scan_running"] = False
            st.session_state["live_logs"].append(f"ERROR: Could not start worker scan: {str(exc).splitlines()[0]}")
            return

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


def _open_scan_start_popup() -> None:
    st.session_state["scan_start_popup_visible"] = True


def _stop_scan() -> None:
    remote_job_id = st.session_state.get("scan_remote_job_id")
    if remote_job_id:
        try:
            _worker_request("POST", f"/scan/{remote_job_id}/cancel", timeout=10)
        except Exception:
            pass
        st.session_state["scan_remote_job_id"] = None
        st.session_state["scan_stop_requested"] = True
        st.session_state["live_scan_running"] = False
        return

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
    remote_job_id = st.session_state.get("scan_remote_job_id")
    if remote_job_id:
        try:
            state = _worker_request("GET", f"/scan/{remote_job_id}", timeout=10)
            live_logs = [str(x) for x in state.get("live_logs", []) if x is not None]
            source_logs = [str(x) for x in state.get("source_logs", []) if x is not None]
            st.session_state["live_logs"] = live_logs
            st.session_state["source_logs"] = source_logs

            if state.get("done"):
                st.session_state["live_scan_running"] = False
                st.session_state["scan_remote_job_id"] = None
                report_data = state.get("report") if isinstance(state.get("report"), dict) else None
                st.session_state["scan_report_data"] = report_data
                if report_data:
                    score_block = report_data.get("score") if isinstance(report_data.get("score"), dict) else {}
                    vibe = report_data.get("vibe_score") or score_block.get("vibe_score") or report_data.get("final_score")
                    findings = report_data.get("findings") or report_data.get("flagged_issues") or []
                    st.session_state["latest_run_summary"] = f"{report_data.get('url', '')} — {len(findings)} findings — vibe: {vibe}"
                elif state.get("error"):
                    st.session_state["live_logs"].append(f"ERROR: {state.get('error')}")
                st.session_state["scan_output_queue"] = None
                st.session_state["scan_reader_thread"] = None
                st.session_state["scan_process"] = None
            return
        except Exception as exc:
            st.session_state["live_logs"].append(f"ERROR: Worker polling failed: {str(exc).splitlines()[0]}")
            st.session_state["live_scan_running"] = False
            st.session_state["scan_remote_job_id"] = None
            return

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
            if st.session_state.get("scan_mode") == "source_only" or _is_source_scan_log_line(line):
                st.session_state.setdefault("source_logs", []).append(line)
            else:
                st.session_state["live_logs"].append(line)
        elif kind == "error":
            if st.session_state.get("scan_mode") == "source_only":
                st.session_state.setdefault("source_logs", []).append(f"ERROR: {payload}")
            else:
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
        return []
    if isinstance(report_data.get("scan_live_log"), list) and report_data.get("scan_live_log"):
        return [str(line) for line in report_data.get("scan_live_log", [])]
    score_block = report_data.get("score") if isinstance(report_data.get("score"), dict) else {}
    report_mode = report_data.get("scan_metadata", {}).get("scan_mode") if isinstance(report_data.get("scan_metadata"), dict) else None
    vibe = report_data.get("vibe_score") or score_block.get("vibe_score") or report_data.get("final_score")
    rendered = score_block.get("rendered_vibe_score")
    lines: list[str] = []
    if scan_mode == "source_only" or report_mode == "source_only":
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

    # Normal start/stop buttons (disabled when running or invalid)
    start_clicked = left.button(start_label, disabled=st.session_state["live_scan_running"] or mode == "invalid")
    stop_clicked = right.button("Stop scan", disabled=not st.session_state["live_scan_running"])

    # If a user clicked start, perform repo→site mismatch detection only at this moment
    if start_clicked:
        # If a repo is provided and mode requires it, attempt to detect mismatch.
        repo = github_repo_url.strip() or None
        if repo and mode in ("combined", "source_only"):
            try:
                mismatch = _detect_repo_site_mismatch(url, repo)
            except Exception:
                mismatch = {"status": "unknown", "reason": "error while checking repo metadata"}

            if mismatch:
                # Save pending start parameters and mismatch details, then show confirmation UI.
                st.session_state["pending_start"] = {"url": url, "repo_url": repo}
                st.session_state["repo_site_mismatch"] = mismatch
                st.session_state["repo_mismatch_shown_at"] = int(time.time())
                st.rerun()

        # No mismatch (or no repo) — proceed to start immediately
        _start_scan(url, repo)
        _open_scan_start_popup()
        st.rerun()

    if stop_clicked:
        _stop_scan()
        st.rerun()

    # If a mismatch confirmation is pending, render a clear confirmation UI.
    mismatch = st.session_state.get("repo_site_mismatch")
    pending = st.session_state.get("pending_start")
    if mismatch and pending:
        with st.container():
            # Friendly, explicit warning explaining the mismatch and options
            site_domain = mismatch.get("site_domain") or _extract_domain(pending.get("url"))
            repo_home = mismatch.get("repo_homepage") or "(no homepage declared)"
            repo_desc = mismatch.get("repo_description") or "(no description)"
            if mismatch.get("status") == "mismatch":
                st.warning(
                    "The provided GitHub repository appears to declare a different homepage than the site you entered.\n\n"
                    f"Site: {site_domain}\nRepo homepage: {repo_home}\n\n"
                    "If you proceed, the scan will use the provided repo for source-code findings even though it may belong to a different project."
                )
            else:
                st.info(
                    "Could not match the repository metadata to the live site given.\n\n"
                    "You can retry the check or confirm to proceed anyway."
                )

            cols = st.columns([1, 1, 1])
            with cols[0]:
                confirm = st.button("Confirm and start scan")
            with cols[1]:
                retry = st.button("Retry check")
            with cols[2]:
                cancel = st.button("Cancel")

            if confirm:
                params = st.session_state.pop("pending_start", None) or {"url": url, "repo_url": github_repo_url.strip() or None}
                # clear mismatch info
                st.session_state.pop("repo_site_mismatch", None)
                st.session_state.pop("repo_mismatch_shown_at", None)
                _start_scan(params.get("url"), params.get("repo_url"))
                _open_scan_start_popup()
                st.rerun()

            if retry:
                # remove previous mismatch and re-trigger start flow so detection runs again
                st.session_state.pop("repo_site_mismatch", None)
                st.rerun()

            if cancel:
                st.session_state.pop("repo_site_mismatch", None)
                st.session_state.pop("pending_start", None)
                st.session_state.pop("repo_mismatch_shown_at", None)
                st.rerun()


st.title("Humanonn: Make your site feel more human")
st.write("Run Humanonn scans from the browser. The server must have the repo and dependencies installed.")

_inject_css(REPORTS_DIR / "style.css")

url = st.text_input("URL to scan", "", placeholder="https://example.com")

# Quick client-side site availability check and inline error rendering below the URL input.
def _quick_site_check(u: str) -> str | None:
    if not u:
        return None
    try:
        parsed = urllib.parse.urlparse(u.strip())
        if parsed.scheme not in ("http", "https"):
            return "URL must start with http:// or https://"
        # try HEAD first
        req = urllib.request.Request(u.strip(), headers={"User-Agent": "Humanonn/1.0"}, method="HEAD")
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                code = getattr(resp, "status", None) or getattr(resp, "getcode", lambda: None)()
                if code and 200 <= int(code) < 400:
                    return None
                return f"Site returned HTTP {code}."
        except urllib.error.HTTPError as he:
            # HEAD may not be allowed; try GET for more info
            if he.code in (405,):
                req2 = urllib.request.Request(u.strip(), headers={"User-Agent": "Humanonn/1.0"}, method="GET")
                with urllib.request.urlopen(req2, timeout=3) as resp2:
                    code2 = getattr(resp2, "status", None) or getattr(resp2, "getcode", lambda: None)()
                    if code2 and 200 <= int(code2) < 400:
                        return None
                    return f"Site returned HTTP {code2}."
            if he.code == 404:
                return "Site returned 404 (not found)."
            if he.code == 403:
                return "Access denied (403) when fetching the site."
            return f"Site request failed with HTTP {he.code}."
        except urllib.error.URLError as ue:
            return f"Could not reach site: {str(ue).splitlines()[0]}"
    except Exception as exc:
        return f"Could not verify site: {str(exc).splitlines()[0]}"

# show quick site errors found locally or in-flight logs
site_error = _quick_site_check(url)
site_errors: list[str] = []
if site_error:
    site_errors.append(site_error)
# also include in-flight or finished scan logs related to site errors
report_data = st.session_state.get("scan_report_data")
if report_data:
    if isinstance(report_data.get("scan_live_log"), list):
        site_errors.extend([str(x) for x in report_data.get("scan_live_log", []) if x])
    if isinstance(report_data.get("agent_notes"), list):
        site_errors.extend([str(x) for x in report_data.get("agent_notes", []) if "site" in str(x).lower() or "http" in str(x).lower()])
inline_live = [str(x) for x in st.session_state.get("live_logs", []) or []]
for line in inline_live:
    if any(tok in line.lower() for tok in ("404", "not found", "access denied", "403", "could not reach", "timed out", "connection refused")):
        site_errors.append(line)

if site_errors:
    seen = set()
    compact: list[str] = []
    for v in site_errors:
        s = str(v).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        compact.append(s)
        if len(compact) >= 5:
            break
    st.markdown(_render_inline_lines(compact), unsafe_allow_html=True)
github_repo_url = st.text_input(
    "Public GitHub repo URL for source-code scoring (Optional)",
    "",
    placeholder="https://github.com/owner/repo",
)
st.caption("Note: Source-code scanning currently supports repositories built with Next.js, React, and Tailwind CSS.")
def _validate_github_repo_input(url: str) -> str | None:
    """Return an error string when the input is clearly not a valid public GitHub repo URL."""
    if not url:
        return None
    u = url.strip()
    # Accept forms like: https://github.com/owner/repo or https://github.com/owner/repo.git
    import re

    pattern = re.compile(r"^https://(?:www\.)?github\.com/[^/\s]+/[^/\s]+(?:\.git)?/?$")
    if not pattern.match(u):
        return "Invalid GitHub repo URL — expected: https://github.com/owner/repo"
    return None


def _parse_github_repo(url: str) -> tuple[str, str] | None:
    try:
        parsed = urllib.parse.urlparse(url.strip())
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) < 2:
            return None
        owner, repo = parts[0], parts[1].removesuffix(".git")
        return owner, repo
    except Exception:
        return None


def _extract_domain(u: str | None) -> str | None:
    if not u:
        return None
    try:
        p = urllib.parse.urlparse(u)
        host = (p.netloc or "").lower()
        # strip common www
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return None


def _get_github_repo_metadata(owner: str, repo: str, timeout: int = 6) -> dict | None:
    api_url = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "Humanonn/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                text = resp.read().decode("utf-8", errors="replace")
                return json.loads(text)
    except Exception:
        return None
    return None


def _detect_repo_site_mismatch(site_url: str, repo_url: str) -> dict | None:
    """Return a mismatch dict if the repo metadata suggests a different site domain.
    Non-destructive: returns None when no clear mismatch or metadata unavailable.
    """
    if not site_url or not repo_url:
        return None
    parsed = _parse_github_repo(repo_url)
    if not parsed:
        return None
    owner, repo = parsed
    meta = _get_github_repo_metadata(owner, repo)
    if not meta:
        # Can't verify repo metadata (rate-limit or network) — don't block, but return a caution signal
        return {"status": "unknown", "reason": "could not fetch repo metadata (rate-limited or network)"}

    site_domain = _extract_domain(site_url)
    homepage = (meta.get("homepage") or "") if isinstance(meta.get("homepage"), str) else ""
    description = (meta.get("description") or "") if isinstance(meta.get("description"), str) else ""
    homepage_domain = _extract_domain(homepage)

    # If repo declares a homepage that clearly differs from the provided site, flag mismatch.
    if homepage_domain and site_domain and homepage_domain != site_domain:
        return {
            "status": "mismatch",
            "reason": "repo homepage differs from provided site",
            "repo_homepage": homepage,
            "repo_description": description,
            "site_domain": site_domain,
            "repo_homepage_domain": homepage_domain,
            "owner": owner,
            "repo": repo,
        }

    # If description contains an explicit site domain, check that too.
    if description and site_domain and site_domain not in description.lower():
        # not a strong signal, only return None — prefer homepage check as authoritative
        return None

    return None

# Validate the GitHub repo input and show an inline red error message if invalid.
_github_input_error = _validate_github_repo_input(github_repo_url)
if _github_input_error:
    st.markdown(_render_inline_lines([f"Error: {_github_input_error}" ]), unsafe_allow_html=True)
else:
    # quick existence check so 'repo not found' shows fast
    def _quick_repo_check(url: str) -> str | None:
        if not url:
            return None
        try:
            # Prefer to reuse parsing and error mapping from source_code if available
            try:
                from humanonn.source_code import _parse_github_repo, _github_http_error_message
            except Exception:
                _parse_github_repo = None
                _github_http_error_message = None

            if _parse_github_repo:
                owner, repo = _parse_github_repo(url)
            else:
                parsed = urllib.parse.urlparse(url.strip())
                parts = [p for p in parsed.path.split("/") if p]
                if len(parts) < 2:
                    return "GitHub repo URL must point to the repository root like https://github.com/owner/repo."
                owner, repo = parts[0], parts[1].removesuffix(".git")

            api_url = f"https://api.github.com/repos/{owner}/{repo}"
            req = urllib.request.Request(api_url, headers={"User-Agent": "Humanonn/1.0"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    return None
                return f"GitHub request returned status {resp.status}."
        except urllib.error.HTTPError as exc:
            if _github_http_error_message:
                return _github_http_error_message(api_url, exc)
            if exc.code == 404:
                return "GitHub repository not found or the owner/username is incorrect."
            # if exc.code == 403:
            #     return "GitHub API request was forbidden or rate limited."
            return f"GitHub request failed with HTTP {exc.code}."
        except Exception as exc:
            return f"Could not verify GitHub repo: {str(exc).splitlines()[0]}"

    _repo_check_error = None
    try:
        _repo_check_error = _quick_repo_check(github_repo_url)
    except Exception:
        _repo_check_error = None
    if _repo_check_error and not _should_hide_github_rate_limit_message(_repo_check_error):
        st.markdown(_render_inline_lines([f"Error: {_repo_check_error}" ]), unsafe_allow_html=True)
# Display server-side scan errors (if any) and the merged final score below the GitHub input.
report_data = st.session_state.get("scan_report_data")
server_errors: list[str] = []

# 1) Collect errors already present in the finished report data
if report_data:
    if isinstance(report_data.get("scan_code_log"), list):
        server_errors.extend([str(x) for x in report_data.get("scan_code_log", []) if x and not _should_hide_github_rate_limit_message(str(x))])
    source_code = report_data.get("source_code") if isinstance(report_data.get("source_code"), dict) else {}
    if isinstance(source_code.get("scan_log"), list):
        server_errors.extend([str(x) for x in source_code.get("scan_log", []) if x and not _should_hide_github_rate_limit_message(str(x))])
    if isinstance(report_data.get("agent_notes"), list):
        server_errors.extend([str(x) for x in report_data.get("agent_notes", []) if x])
    for key in ("error", "scan_error", "scan_status"):
        if report_data.get(key):
            val = str(report_data.get(key))
            if _should_hide_github_rate_limit_message(val):
                continue
            server_errors.append(val)

# 2) Also surface any in-flight error-like lines from the session log queues so repo-not-found appears fast
import re
err_pattern = re.compile(r"(?i)(github|repo|repository).*(not found|was not found|forbidden|rate limit|failed|could not be fetched|not supported|error|missing)")
inline_logs = []
inline_logs.extend([str(x) for x in st.session_state.get("source_logs", []) or []])
inline_logs.extend([str(x) for x in st.session_state.get("live_logs", []) or []])
for line in inline_logs:
    if _should_hide_github_rate_limit_message(line):
        continue
    if err_pattern.search(line) or "no supported frontend" in line.lower() or "repository not found" in line.lower():
        server_errors.append(line)

if server_errors:
    # render up to 10 unique errors in red
    seen = set()
    compact: list[str] = []
    for v in server_errors:
        s = str(v).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        compact.append(s)
        if len(compact) >= 10:
            break
    st.markdown(_render_inline_lines(compact), unsafe_allow_html=True)

# Render merged final score (vibe/final_score) in green, plain value only
merged_score = None
if report_data:
    score_block = report_data.get("score") if isinstance(report_data.get("score"), dict) else {}
    merged_score = report_data.get("vibe_score") or score_block.get("vibe_score") or report_data.get("final_score")
if merged_score is not None:
    # Print only the score value in green, no explanation
    st.markdown(f"<div style='color:#00AA33; font-size:1.3em; margin-top:6px;'>{merged_score}</div>", unsafe_allow_html=True)
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
st.session_state.setdefault("scan_start_popup_visible", False)
st.session_state.setdefault("scan_remote_job_id", None)

if st.session_state.get("scan_start_popup_visible"):
    _render_scan_start_popup()
    st.session_state["scan_start_popup_visible"] = False

if st.session_state["live_scan_running"]:
    _drain_scan_output()

_render_scan_controls()

download_placeholder = st.empty()
with st.expander("Show live logs", expanded=False):
    live_log_box = st.empty()
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


def _combined_flagged_findings(report_data: dict) -> list[dict]:
    combined = _flagged_findings(report_data)
    source_code = report_data.get("source_code") if isinstance(report_data.get("source_code"), dict) else {}
    source_findings = source_code.get("findings") if isinstance(source_code.get("findings"), list) else []
    for item in source_findings:
        if not isinstance(item, dict) or not item.get("flagged"):
            continue
        combined.append(item)
    return combined


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
        flagged_findings = _combined_flagged_findings(report_data)
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
            st.markdown(f"**Source Code Score:** {source_code_score}/100")
        elif rendered_vibe is not None or source_code_score:
            st.markdown(f"**Live Site Score:** {rendered_vibe if rendered_vibe is not None else vibe}/100")
            st.markdown(f"**Source Code Score:** {source_code_score}/100")
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
                _render_audit_summary_block(report_data)
                st.markdown("---")
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
                    err_text = str(source_code.get("error"))
                    if not _should_hide_github_rate_limit_message(err_text):
                        st.write(f"Source scan failed: {err_text}")
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
if not st.session_state.get("server_notes_footer_shown"):
    st.caption("Server notes: Ensure `.venv` is activated and dependencies installed. Use `python -m playwright install chromium` if needed.")
    st.session_state["server_notes_footer_shown"] = True

if st.session_state["live_scan_running"]:
    time.sleep(1)
    st.rerun()
