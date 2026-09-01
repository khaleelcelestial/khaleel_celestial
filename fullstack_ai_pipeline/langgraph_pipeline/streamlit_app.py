"""
Streamlit UI for the LangGraph multi-capability pipeline.

Wraps the same entry points the CLI (main.py) uses - dispatch_new_request,
run_pipeline(update=True), and the output/ project registry - so this is a
second interface onto the same pipeline, not a separate implementation.
"""

import json
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import streamlit as st

from skills.project_registry import list_projects, load_project_snapshot, OUTPUT_BASE

st.set_page_config(page_title="Fullstack AI Pipeline", page_icon="🛠️", layout="wide")

# Every run's log is tee'd to disk (not just held in a browser session's
# memory) and to the process's real stdout - so a build/update's output
# both shows up in the terminal that launched `streamlit run` AND survives
# a browser refresh, which otherwise orphans the in-memory placeholder the
# old approach relied on entirely.
LOGS_DIR = Path(__file__).parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)
ACTIVE_RUN_MARKER = LOGS_DIR / "_active_run.json"
STALE_RUN_SECONDS = 6 * 60 * 60  # a marker older than this survived a crash, not just a refresh
MAIN_PY = Path(__file__).parent / "main.py"

LIVE_VIEW_CHAR_CAP = 20000
LIVE_VIEW_HEIGHT = 400


@st.cache_resource
def _process_registry():
    """
    A dict surviving across Streamlit reruns AND across every browser
    session on this server - a plain module-level dict would NOT work here,
    since Streamlit re-executes this whole script top-to-bottom on every
    interaction; st.cache_resource is the documented pattern for exactly
    this (a singleton shared for the server's lifetime), unlike
    st.session_state which is per-browser-session only. Keyed by PID ->
    {"proc": Popen, "reader_thread": Thread}.
    """
    return {}

LANG_BY_EXT = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "tsx", ".jsx": "jsx",
    ".json": "json", ".html": "html", ".css": "css", ".md": "markdown", ".yaml": "yaml",
    ".yml": "yaml", ".sql": "sql", ".sh": "bash", ".txt": "text", ".env": "bash",
}


def lang_for(filename: str) -> str:
    return LANG_BY_EXT.get(Path(filename).suffix, "text")


UPLOAD_TYPES = ["txt", "md", "pdf", "docx"]


def extract_text_from_upload(uploaded_file) -> str:
    """
    Extract plain text from an uploaded requirements document - .txt/.md are
    read as-is, .pdf via pypdf (page by page), .docx via python-docx
    (paragraph by paragraph). Returns "" (with a UI error) rather than
    raising if the file can't be parsed, so it never breaks the request flow.
    """
    suffix = Path(uploaded_file.name).suffix.lower()
    try:
        if suffix in (".txt", ".md"):
            return uploaded_file.read().decode("utf-8", errors="replace")

        if suffix == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(uploaded_file)
            return "\n\n".join(page.extract_text() or "" for page in reader.pages)

        if suffix == ".docx":
            from docx import Document
            document = Document(uploaded_file)
            return "\n".join(p.text for p in document.paragraphs)

        st.warning(f"Unsupported file type '{suffix}' - only .txt, .md, .pdf, .docx are supported.")
        return ""
    except Exception as e:
        st.error(f"Could not read {uploaded_file.name}: {e}")
        return ""


def combine_request(typed_text: str, uploaded_file) -> str:
    """
    Combine the typed request/change-description with an uploaded file's
    extracted text - both are sent together to the pipeline when both are
    present, so a long requirements doc doesn't have to be retyped, but a
    short clarifying note typed alongside it still gets through too.
    """
    parts = []
    if typed_text and typed_text.strip():
        parts.append(typed_text.strip())
    if uploaded_file is not None:
        file_text = extract_text_from_upload(uploaded_file)
        if file_text.strip():
            parts.append(f"--- From uploaded file: {uploaded_file.name} ---\n{file_text.strip()}")
    return "\n\n".join(parts)


def _write_active_run_marker(kind: str, log_path: Path, project_id: str, request: str, pid: int,
                              existing_ids_before: list):
    ACTIVE_RUN_MARKER.write_text(json.dumps({
        "kind": kind,
        "project_id": project_id,
        "request": request[:200],
        "log_file": str(log_path),
        "started_at": time.time(),
        "pid": pid,
        "existing_ids_before": existing_ids_before,
    }))


def _clear_active_run_marker():
    if ACTIVE_RUN_MARKER.exists():
        ACTIVE_RUN_MARKER.unlink()


def _reader_thread(proc: subprocess.Popen, log_path: Path):
    """
    Runs for the lifetime of the pipeline subprocess, independent of any
    Streamlit script rerun - tees every line the subprocess prints to disk
    (so the UI can poll it) and to this process's own real stdout (so it
    still shows up in whatever terminal `streamlit run` was launched from,
    same as a direct CLI run would).
    """
    with open(log_path, "w", encoding="utf-8") as log_file:
        for line in proc.stdout:
            sys.__stdout__.write(line)
            sys.__stdout__.flush()
            log_file.write(line)
            log_file.flush()
    proc.wait()


def launch_pipeline_subprocess(cli_args: list, kind: str, project_id: str, request: str):
    """
    Runs the pipeline as a REAL separate OS process (`python main.py ...`,
    the exact same entry point the CLI uses) instead of an in-process
    function call. This is what makes a genuine Stop button possible:
    Streamlit's own built-in "Stop" only interrupts the script's control
    flow between statements and cannot break out of a single long blocking
    call (an LLM request, `docker compose up`, etc.) sitting inside it -
    confirmed by direct observation earlier this session, where clicking it
    left the pipeline running regardless. A real subprocess can be killed
    outright by PID (see stop_pipeline_subprocess), the same as Ctrl+C would
    in a terminal - actually stronger, since it force-kills the whole
    process tree rather than relying on the child cooperating with SIGINT.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOGS_DIR / f"{timestamp}_{kind}.log"
    proc = subprocess.Popen(
        [sys.executable, "-u", str(MAIN_PY), *cli_args],
        cwd=str(MAIN_PY.parent),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    existing_ids_before = [p["project_id"] for p in list_projects()]
    _process_registry()[proc.pid] = {"proc": proc}
    threading.Thread(target=_reader_thread, args=(proc, log_path), daemon=True).start()
    _write_active_run_marker(kind, log_path, project_id, request, proc.pid, existing_ids_before)


def _pid_alive_and_exit_code(pid: int):
    """Returns (is_alive, exit_code_or_None). Prefers the in-memory Popen handle
    (this server process launched it); falls back to `tasklist` by PID alone so a
    Streamlit rerun that lost the in-memory entry (but not the subprocess itself)
    still detects it correctly."""
    entry = _process_registry().get(pid)
    if entry is not None:
        code = entry["proc"].poll()
        return (code is None), code
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True, timeout=5)
        return (str(pid) in out.stdout), None
    except Exception:
        return False, None


def stop_pipeline_subprocess(pid: int):
    """
    Force-kills the pipeline process AND its whole tree (any docker/npm/pip
    subprocess main.py itself spawned) - `taskkill /T` is what actually
    guarantees this on Windows; a plain Ctrl+C/SIGINT can be swallowed by a
    nested blocking call and leave orphaned children running, which is
    exactly what was observed needing manual process-tree cleanup earlier
    this session.
    """
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=15)
    except Exception:
        pass
    _process_registry().pop(pid, None)


def render_full_log(log: str, key: str):
    """
    Permanent, terminal-equivalent log view - shown every rerun, not just
    while the pipeline is actively running. This is the exact same text the
    CLI prints to the real terminal (see _reader_thread), kept
    in full (no truncation) so it stays available after the run completes,
    after a page rerun, or even after switching tabs and coming back -
    closing the gap where the live view during the run would otherwise be
    the ONLY place this ever appeared, gone the moment Streamlit reran.
    """
    if not log:
        return
    with st.expander(f"📜 Full pipeline log ({len(log.splitlines())} lines)", expanded=False):
        st.code(log, language="text", height=500)
        st.download_button(
            "⬇️ Download full log (.txt)", log, file_name="pipeline_log.txt",
            mime="text/plain", key=f"{key}_download",
        )


def render_finished_run_summary(marker: dict):
    """
    Shown once a launched subprocess has exited. There's no in-memory
    ProjectState to read anymore (the pipeline ran in a separate OS process,
    which is what makes Stop actually work - see launch_pipeline_subprocess)
    - so this reloads whatever the pipeline itself already persisted to
    disk via the project registry, same source Explore Files reads from.
    """
    project_id = marker.get("project_id") or ""
    if not project_id and marker["kind"] == "new":
        # A new build's project_id isn't known until the pipeline derives
        # it from the request text - detect it by diffing the project list
        # against the snapshot taken right before the subprocess launched.
        before = set(marker.get("existing_ids_before", []))
        after = [p["project_id"] for p in list_projects()]
        new_ids = [pid for pid in after if pid not in before]
        if len(new_ids) == 1:
            project_id = new_ids[0]
        elif new_ids:
            st.info(f"Multiple new projects appeared: {', '.join(new_ids)} - open Explore Files to inspect any of them.")

    if not project_id:
        st.warning("Run finished, but no project snapshot was found for it - see the log below, "
                   "and check Explore Files/the sidebar for anything that was generated.")
        return

    snapshot = load_project_snapshot(project_id)
    if not snapshot:
        st.warning(f"Run finished, but `{project_id}` has no saved snapshot yet - see the log below.")
        return

    metadata = snapshot["metadata"]
    st.success(f"Project: `{project_id}`")
    col1, col2 = st.columns(2)
    col1.write(f"**Request:** {metadata.get('user_request', '')[:200]}")
    col2.write(f"**Last updated:** {metadata.get('updated_at', 'n/a')}")
    st.write("**Execution Plan:**", metadata.get("execution_plan", {}))
    st.write("**Stage Status:**", metadata.get("stage_status", {}))


def project_workspace_files(workspace: dict) -> dict:
    """Flatten a saved workspace snapshot into {display_path: content}."""
    files = {}
    for artifact_type in ("backend", "frontend"):
        for path, content in workspace.get(artifact_type, {}).get("files", {}).items():
            files[f"{artifact_type}/{path}"] = content
    for doc_name, content in workspace.get("docs", {}).items():
        files[doc_name] = content
    if workspace.get("database", {}).get("schema"):
        files["schema.sql"] = workspace["database"]["schema"]
    if workspace.get("contract", {}).get("openapi_spec"):
        files["openapi.yaml"] = workspace["contract"]["openapi_spec"]
    return files


def refresh_projects():
    st.session_state["projects"] = list_projects()


def render_active_run_banner():
    """
    Single place that owns the whole lifecycle of a launched pipeline run:
    shows the live log (reloaded from disk, so a browser refresh never
    loses it - even for a session that never saw the run start), a real
    Stop button, and - once the subprocess actually exits - the finished
    summary. Polls via sleep+rerun since Streamlit has no push mechanism
    for background work.
    """
    if not ACTIVE_RUN_MARKER.exists():
        return
    try:
        marker = json.loads(ACTIVE_RUN_MARKER.read_text())
    except Exception:
        return

    age_s = time.time() - marker.get("started_at", 0)
    if age_s > STALE_RUN_SECONDS:
        st.warning("A pipeline run marker from over 6 hours ago is still here - it likely crashed "
                   "without cleaning up rather than still running.")
        if st.button("🗑️ Clear stale run marker"):
            _clear_active_run_marker()
            st.rerun()
        return

    log_path = Path(marker["log_file"])
    pid = marker.get("pid")
    if pid is None:
        # A marker from before the subprocess rewrite (no "pid" field) -
        # can't be tracked or stopped, and its process is long gone by now.
        _clear_active_run_marker()
        return
    is_alive, exit_code = _pid_alive_and_exit_code(pid)

    if not is_alive:
        label = f"{marker['kind']} run" + (f" for `{marker['project_id']}`" if marker.get("project_id") else "")
        if exit_code in (0, None):
            st.success(f"✅ {label} finished.")
        else:
            st.error(f"❌ {label} exited with code {exit_code} - see the log below.")
        render_finished_run_summary(marker)
        if log_path.exists():
            render_full_log(log_path.read_text(encoding="utf-8", errors="replace"), key="finished_run")
        refresh_projects()
        _clear_active_run_marker()
        st.divider()
        return

    started = datetime.fromtimestamp(marker["started_at"]).strftime("%H:%M:%S")
    label = f"project `{marker['project_id']}`" if marker.get("project_id") else "a new project"
    st.info(f"⏳ A {marker['kind']} run for {label} is still in progress (started {started}) - "
            f"live log below, reloaded from disk so it isn't lost on refresh.")

    if log_path.exists():
        text = log_path.read_text(encoding="utf-8", errors="replace")
        st.code(text[-LIVE_VIEW_CHAR_CAP:], language="text", height=LIVE_VIEW_HEIGHT)

    col1, col2, col3 = st.columns([1, 1, 3])
    with col1:
        if st.button("🛑 Stop pipeline", type="primary"):
            stop_pipeline_subprocess(marker["pid"])
            st.warning("Stop requested - force-killing the pipeline process (and any docker/npm/pip "
                      "subprocess it spawned).")
            _clear_active_run_marker()
            st.rerun()
    with col2:
        if st.button("🔄 Refresh now"):
            st.rerun()
    with col3:
        auto_refresh = st.checkbox("Auto-refresh every 3s", value=True, key="active_run_autorefresh")
    st.divider()

    if auto_refresh:
        time.sleep(3)
        st.rerun()


if "projects" not in st.session_state:
    refresh_projects()

st.title("🛠️ Fullstack AI Pipeline")
st.caption(f"LangGraph multi-capability pipeline — projects are read from `{OUTPUT_BASE.resolve()}`")

render_active_run_banner()

with st.sidebar:
    st.header("📁 Projects")
    if st.button("🔄 Refresh"):
        refresh_projects()

    projects = st.session_state["projects"]
    if not projects:
        st.info("No saved projects yet — build one to get started.")
    else:
        for p in projects:
            with st.expander(f"📦 {p['project_id']}"):
                st.write(p.get("user_request", ""))
                plan = p.get("execution_plan", {})
                active = [k for k, v in plan.items() if v]
                st.caption(f"Stages: {', '.join(active) if active else 'none'}")

tab_new, tab_update, tab_explore = st.tabs(["🆕 New Project", "🔧 Update Existing", "📂 Explore Files"])

with tab_new:
    st.subheader("Build something new")
    st.caption("Full apps go through planning → backend/frontend → quality → Docker release. "
              "Simple requests (e.g. \"write a .txt file explaining X\") skip straight to generating the file.")

    new_request = st.text_area(
        "What do you want to build?",
        placeholder="e.g. Build a FastAPI + React todo list app with Postgres",
        height=100,
        key="new_request_input",
    )
    new_request_file = st.file_uploader(
        "...or attach a requirements document (optional)",
        type=UPLOAD_TYPES,
        key="new_request_file",
        help="Attach a .txt, .md, .pdf, or .docx file instead of typing everything out. "
             "If you also type something above, both are sent together.",
    )

    # No `disabled=` here on purpose: st.text_area only commits its value to
    # `new_request` on blur/Ctrl+Enter, not on every keystroke - a disabled
    # check computed from the stale pre-edit value meant clicking Build right
    # after typing (without clicking elsewhere first) silently did nothing.
    # Validating inside the handler instead makes every click actually react.
    if st.button("🚀 Build", type="primary", disabled=ACTIVE_RUN_MARKER.exists()):
        combined_request = combine_request(new_request, new_request_file)
        if not combined_request:
            st.warning("Enter a request or attach a requirements file first.")
        else:
            # Launches `python main.py "<request>"` as a real subprocess and
            # returns immediately - render_active_run_banner (called at the
            # top of every rerun) takes over showing progress and the Stop
            # button from here, instead of blocking this whole script run.
            launch_pipeline_subprocess([combined_request], kind="new", project_id="", request=combined_request)
            st.rerun()
    if ACTIVE_RUN_MARKER.exists():
        st.caption("A pipeline run is already in progress - see below. Only one run at a time is supported.")

with tab_update:
    st.subheader("Update an existing project")

    projects = st.session_state["projects"]
    if not projects:
        st.info("No saved projects to update yet — build one first.")
    else:
        options = {f"{p['project_id']} — {p['user_request'][:60]}": p["project_id"] for p in projects}
        choice = st.selectbox("Project", list(options.keys()), key="update_project_select")
        change_request = st.text_area(
            "What should change?",
            placeholder="e.g. Add a due-date filter to the todo list",
            height=100,
            key="update_request_input",
        )
        change_request_file = st.file_uploader(
            "...or attach a document describing the change (optional)",
            type=UPLOAD_TYPES,
            key="update_request_file",
            help="Attach a .txt, .md, .pdf, or .docx file instead of typing everything out. "
                 "If you also type something above, both are sent together.",
        )

        if st.button("🔧 Apply Update", type="primary", disabled=ACTIVE_RUN_MARKER.exists()):
            combined_change_request = combine_request(change_request, change_request_file)
            if not combined_change_request:
                st.warning("Describe the change or attach a file first.")
            else:
                project_id = options[choice]
                launch_pipeline_subprocess(
                    ["--update", project_id, combined_change_request],
                    kind="update", project_id=project_id, request=combined_change_request,
                )
                st.rerun()
        if ACTIVE_RUN_MARKER.exists():
            st.caption("A pipeline run is already in progress - see below. Only one run at a time is supported.")

with tab_explore:
    st.subheader("Explore a project's generated files")

    projects = st.session_state["projects"]
    if not projects:
        st.info("No saved projects yet.")
    else:
        options = {f"{p['project_id']} — {p['user_request'][:60]}": p["project_id"] for p in projects}
        choice = st.selectbox("Project", list(options.keys()), key="explore_project_select")
        project_id = options[choice]

        snapshot = load_project_snapshot(project_id)
        if not snapshot:
            st.error("Could not load this project's snapshot.")
        else:
            metadata = snapshot["metadata"]
            workspace = snapshot["workspace"]

            col1, col2 = st.columns(2)
            col1.write(f"**Original request:** {metadata.get('user_request', '')}")
            col2.write(f"**Last updated:** {metadata.get('updated_at', 'n/a')}")
            st.write("**Execution Plan:**", metadata.get("execution_plan", {}))

            files = project_workspace_files(workspace)
            if not files:
                st.info("No files generated for this project yet.")
            else:
                file_choice = st.selectbox("File", sorted(files.keys()), key="explore_file_select")
                st.code(files[file_choice], language=lang_for(file_choice))
