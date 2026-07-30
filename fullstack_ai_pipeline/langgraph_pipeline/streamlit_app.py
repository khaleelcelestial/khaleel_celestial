"""
Streamlit UI for the LangGraph multi-capability pipeline.

Wraps the same entry points the CLI (main.py) uses - dispatch_new_request,
run_pipeline(update=True), and the output/ project registry - so this is a
second interface onto the same pipeline, not a separate implementation.
"""

import contextlib
from pathlib import Path

import streamlit as st

import main
from skills.project_registry import list_projects, load_project_snapshot, OUTPUT_BASE

st.set_page_config(page_title="Fullstack AI Pipeline", page_icon="🛠️", layout="wide")

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


class _LiveLogCapture:
    """
    Redirect stdout into a Streamlit placeholder, updated as the pipeline
    prints - this is what makes the terminal's exact output (every node
    start/complete, tool call, model attempt/fallback, stage transition)
    show up live in the browser, since core/logger.py's PipelineLogger
    writes everything through plain print() (looked up fresh from sys.stdout
    on every call, so redirect_stdout catches all of it - nothing here uses
    the `logging` module or a cached stream reference that would bypass it).

    The LIVE view during the run is capped for rendering performance (a long
    pipeline run's console output can run to hundreds of KB and re-rendering
    the whole thing on every single print() call would make the page
    sluggish) - but full_log() below returns the COMPLETE, uncapped text,
    which the caller is responsible for persisting (st.session_state) and
    rendering permanently after the run - the cap only ever applies to the
    transient live view, never to what's kept.
    """

    LIVE_VIEW_CHAR_CAP = 20000
    LIVE_VIEW_HEIGHT = 400

    def __init__(self, placeholder):
        self.placeholder = placeholder
        self.lines = []

    def write(self, text):
        if text:
            self.lines.append(text)
            self.placeholder.code(
                "".join(self.lines)[-self.LIVE_VIEW_CHAR_CAP:],
                language="text",
                height=self.LIVE_VIEW_HEIGHT,
            )

    def flush(self):
        pass

    def full_log(self) -> str:
        return "".join(self.lines)


def run_with_live_log(fn, *args, **kwargs):
    """Run a blocking pipeline call while streaming its console output into the UI."""
    placeholder = st.empty()
    capture = _LiveLogCapture(placeholder)
    result = None
    with contextlib.redirect_stdout(capture):
        try:
            result = fn(*args, **kwargs)
        except SystemExit:
            result = None
        except Exception as e:
            print(f"\n❌ Unexpected error: {e}")
    return result, capture.full_log()


def render_full_log(log: str, key: str):
    """
    Permanent, terminal-equivalent log view - shown every rerun, not just
    while the pipeline is actively running. This is the exact same text the
    CLI prints to the real terminal (see _LiveLogCapture's docstring), kept
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


def render_pipeline_result(result):
    """Render a full ProjectState (build or update run)."""
    if not result:
        st.error("The run did not complete - see the log above. If files were generated, "
                 "they were still salvaged to output/ and are available under Update/Explore.")
        return

    runtime = result.get("runtime", {})

    col1, col2, col3 = st.columns(3)
    col1.metric("Quality", "PASSED" if runtime.get("quality_passed") else "FAILED")
    col2.metric("Completed Nodes", len(runtime.get("completed_nodes", [])))
    col3.metric("Retries", runtime.get("retry_count", 0))

    if runtime.get("final_project_path"):
        st.success(f"Project available at: `{runtime['final_project_path']}`")
    else:
        st.warning("Pipeline did not reach release_eng - check the log above.")

    st.write("**Execution Plan:**", runtime.get("execution_plan", {}))

    issues = runtime.get("review_issues", [])
    if issues:
        with st.expander(f"⚠️ {len(issues)} outstanding issue(s)"):
            for issue in issues:
                st.markdown(f"- **[{issue['severity']}]** `{issue['file']}` — {issue['description']}")

    logs = runtime.get("logs", [])
    if logs:
        with st.expander("Execution logs (most recent)"):
            for log in logs[-15:]:
                st.text(log)


def render_simple_file_result(result):
    if not result:
        st.error("Could not generate the requested file(s) - see the log above.")
        return

    st.success(f"Generated {len(result['files'])} file(s)")
    st.write(f"**Project ID:** `{result['project_id']}`")
    st.write(f"**Path:** `{result['project_path']}`")
    for fname, content in result["files"].items():
        with st.expander(f"📄 {fname}"):
            st.code(content, language=lang_for(fname))


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


if "projects" not in st.session_state:
    refresh_projects()

st.title("🛠️ Fullstack AI Pipeline")
st.caption(f"LangGraph multi-capability pipeline — projects are read from `{OUTPUT_BASE.resolve()}`")

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
    if st.button("🚀 Build", type="primary"):
        combined_request = combine_request(new_request, new_request_file)
        if not combined_request:
            st.warning("Enter a request or attach a requirements file first.")
        else:
            with st.status("Running pipeline...", expanded=True) as status:
                result, log = run_with_live_log(main.dispatch_new_request, combined_request)
                status.update(label="Done", state="complete")
            refresh_projects()
            # Stash the result AND the full log, and force a full rerun so the
            # sidebar (rendered earlier in script order) picks up the new
            # project on this same interaction, instead of only on the next
            # unrelated one. The log must be stashed too - without this, the
            # live view above only exists for the duration of the run itself;
            # the moment this rerun happens, that placeholder is gone and
            # there'd be nothing left showing the terminal-equivalent output.
            st.session_state["new_build_result"] = result
            st.session_state["new_build_log"] = log
            st.rerun()

    if "new_build_result" in st.session_state:
        result = st.session_state["new_build_result"]
        if result is not None and "runtime" not in result:
            render_simple_file_result(result)  # run_simple_file_task's return shape
        else:
            render_pipeline_result(result)
        render_full_log(st.session_state.get("new_build_log", ""), key="new_build")

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

        if st.button("🔧 Apply Update", type="primary"):
            combined_change_request = combine_request(change_request, change_request_file)
            if not combined_change_request:
                st.warning("Describe the change or attach a file first.")
            else:
                project_id = options[choice]
                with st.status(f"Updating {project_id}...", expanded=True) as status:
                    result, log = run_with_live_log(
                        main.run_pipeline, combined_change_request, project_id=project_id, update=True
                    )
                    status.update(label="Done", state="complete")
                refresh_projects()
                st.session_state["update_result"] = result
                st.session_state["update_log"] = log
                st.rerun()

    if "update_result" in st.session_state:
        render_pipeline_result(st.session_state["update_result"])
        render_full_log(st.session_state.get("update_log", ""), key="update")

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
