import os
import re
import json
import shutil
import zipfile
import subprocess
import threading
import queue
import time
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, send_file, Response, stream_with_context
from flask_cors import CORS
from dotenv import load_dotenv
from groq import Groq

# =========================
# INIT
# =========================

load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

app = Flask(__name__, static_folder=".")
CORS(app)

ARCHITECT_MODEL = "groq/compound"
BUILD_MODEL     = "groq/compound"
TEST_MODEL      = "meta-llama/llama-4-scout-17b-16e-instruct"
WRITE_MODEL     = "meta-llama/llama-4-scout-17b-16e-instruct"

MAX_TOKENS    = 4096
PROJECTS_DIR  = Path("./projects")
PROJECTS_DIR.mkdir(exist_ok=True)

# =========================
# PIPELINE STATE
# =========================

pipeline = {
    "stage":        "idle",
    "idea":         "",
    "arch_doc":     "",
    "build_output": "",
    "test_output":  "",
    "write_output": "",
    "history":      [],
    "iteration":    0,
    "project_name": "",
    "project_path": "",
    "run_port":     None,
    "run_pid":      None,
    "project_type": "",   # "static" | "flask" | "node" | "other"
    "preview_url":  "",   # always served via MAX at /preview/...
}

# Per-project log queue (for SSE streaming)
log_queue: queue.Queue = queue.Queue()
active_process = None

# =========================
# PROMPTS
# =========================

ARCHITECT_PROMPT = """
You are MAX Architect — a senior software architect.

Take the user's idea and produce a COMPLETE architecture document.

Include:
1. **Project Overview** — purpose, audience
2. **Tech Stack** — languages, frameworks, libraries (specific)
3. **Folder Structure** — full directory tree with file descriptions
4. **System Flow** — how data/control moves through the app
5. **Module Breakdown** — each file, its responsibility, key functions/classes
6. **API / Interface Design** — endpoints, function signatures, data models
7. **Dependencies** — all packages (with versions if possible)
8. **Implementation Notes** — patterns, edge cases, gotchas for the builder

Also include a line at the top:
PROJECT_NAME: <slug-name-for-folder>
RUN_COMMAND: <exact command to run the project, e.g. python app.py or npm start>
INSTALL_COMMAND: <exact install command, e.g. pip install -r requirements.txt or npm install>
PORT: <port the app will listen on, e.g. 3000 or 5001>

Be thorough. The builder uses ONLY this document. No ambiguity.
"""

ARCHITECT_IMPROVE_PROMPT = """
You are MAX Architect.
The user reviewed your architecture and wants revisions.
Produce a fully revised architecture document incorporating all feedback.
Preserve everything not criticised. Keep the PROJECT_NAME, RUN_COMMAND, INSTALL_COMMAND, PORT lines at the top.
"""

BUILD_PROMPT = """
You are MAX Builder — an expert programmer.

You receive a complete architecture document. Implement it fully.

Rules:
- Write ALL files in the architecture
- Use the exact folder structure
- Production-quality code — no placeholders, no TODOs, no stubs
- Every file must be complete and runnable
- If it's a web app, make sure it runs on the PORT specified in the architecture

CRITICAL FORMAT — every file must follow this exact pattern:
### `path/to/filename.ext`
```language
<complete file contents>
```

End with a **Setup & Run** section.
"""

BUILD_IMPROVE_PROMPT = """
You are MAX Builder.
The user reviewed your code and wants changes.
Output only the changed files using the exact same format (### `path/file` code block).
Briefly explain what changed.
"""

TEST_PROMPT = """
You are MAX Tester — a senior QA engineer.
Review the architecture and full codebase.

Produce:
1. **Static Analysis** — bugs, logic errors, security issues
2. **Bug Report** — table: File | Issue | Severity
3. **Fixed Files** — full corrected code for critical/major bugs (same ### `path` format)
4. **Test Cases** — unit/integration tests
5. **Quality Summary** — score 1-10, readiness verdict
"""

WRITE_PROMPT = """
You are MAX Writer — a technical documentation expert.
Produce a professional README.md from the architecture, code, and test report.
Use proper Markdown. Make it GitHub-ready.
"""

# =========================
# GROQ CALL
# =========================

def groq_call(model, system, user_content, temperature=0.6):
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user_content},
        ],
        temperature=temperature,
        max_tokens=MAX_TOKENS
    )
    return response.choices[0].message.content.strip()

# =========================
# FILE PARSER
# =========================

def sanitize_path(raw):
    """Make a path safe for writing to disk."""
    p = raw.strip('`\'"').strip()
    p = re.sub(r'^[/\\]+', '', p)
    parts = re.split(r'[/\\]', p)
    parts = [x for x in parts if x and x != '..']
    if not parts:
        return ''
    return '/'.join(parts)


def parse_files_from_build(build_output):
    """
    Robustly parses builder output for file blocks.
    Handles all common LLM formatting variations.
    Strategy:
      1. Find all fenced code blocks (``` or ~~~) and record positions.
      2. Find all header-like lines that look like file paths.
      3. Match each header to the nearest following fence.
      4. Fallback: if no headers found, guess names from surrounding text.
    """

    HEADER_PATTERNS = [
        # ### `path/file.ext`  or  ### **`path/file.ext`**
        re.compile(r'^#{1,4}\s+\*{0,2}`([^`\n]+)`\*{0,2}\s*$', re.MULTILINE),
        # ### path/file.ext  (plain, no backticks)
        re.compile(r'^#{1,4}\s+([\w][\w\-./\\]*\.[\w]+)\s*$', re.MULTILINE),
        # **`path/file.ext`**
        re.compile(r'^\*{1,2}`([^`\n]+\.\w+)`\*{1,2}\s*$', re.MULTILINE),
        # File: path/file.ext  or  Filename: ...
        re.compile(r'^(?:File(?:name)?|PATH|Path)\s*[:\-]\s*`?([^\s`\n]+\.\w+)`?\s*$', re.MULTILINE | re.IGNORECASE),
        # --- path/file.ext ---
        re.compile(r'^-{2,}\s+([\w][\w\-./\\]*\.[\w]+)\s+-{2,}\s*$', re.MULTILINE),
        # // path/file.ext or # path/file.ext  (only if path has a slash)
        re.compile(r'^(?://|#)\s+([\w\-]+/[\w\-./\\]+\.[\w]+)\s*$', re.MULTILINE),
    ]

    FENCE_RE = re.compile(r'(?:```|~~~)(\w*)\r?\n(.*?)(?:```|~~~)', re.DOTALL)

    # Step 1: collect all fences with positions
    fences = []
    for m in FENCE_RE.finditer(build_output):
        lang    = (m.group(1) or '').strip()
        content = (m.group(2) or '')
        fences.append({
            'start':   m.start(),
            'end':     m.end(),
            'lang':    lang,
            'content': content,
        })

    if not fences:
        _log('WARNING Parser: no code fences found in builder output')
        return []

    # Step 2: collect all headers
    raw_headers = []
    for pat in HEADER_PATTERNS:
        for m in pat.finditer(build_output):
            path = m.group(1).strip().strip('`').strip()
            # Must have an extension
            basename = path.replace('\\', '/').split('/')[-1]
            if '.' not in basename:
                continue
            raw_headers.append({'pos': m.start(), 'end': m.end(), 'path': path})

    # Deduplicate by position
    seen = set()
    headers = []
    for h in sorted(raw_headers, key=lambda x: x['pos']):
        if h['pos'] not in seen:
            seen.add(h['pos'])
            headers.append(h)

    _log(f'  Parser: {len(headers)} headers, {len(fences)} fences')

    # Step 3: match each header to nearest following fence
    MAX_GAP   = 600
    files     = []
    used      = set()

    for h in headers:
        best_idx = None
        best_gap = MAX_GAP + 1
        for i, f in enumerate(fences):
            if i in used:
                continue
            gap = f['start'] - h['end']
            if 0 <= gap <= MAX_GAP and gap < best_gap:
                best_idx = i
                best_gap = gap
        if best_idx is not None:
            used.add(best_idx)
            f = fences[best_idx]
            safe = sanitize_path(h['path'])
            if safe:
                files.append({'path': safe, 'lang': f['lang'], 'content': f['content']})
                _log(f'  parsed: {safe}')

    # Step 4: fallback for any unmatched fences with significant content
    if not files:
        _log('WARNING Parser: header matching failed, using fallback')
        EXT_MAP = {
            'python': 'py', 'javascript': 'js', 'typescript': 'ts',
            'jsx': 'jsx', 'tsx': 'tsx', 'html': 'html', 'css': 'css',
            'json': 'json', 'bash': 'sh', 'shell': 'sh', 'sql': 'sql',
            'markdown': 'md', 'yaml': 'yml', 'toml': 'toml',
        }
        for i, f in enumerate(fences):
            if len(f['content'].strip()) < 20:
                continue  # skip trivial fences
            # search 400 chars before fence for a filename
            snippet = build_output[max(0, f['start'] - 400):f['start']]
            found = re.findall(
                r'[\w\-]+\.(?:py|js|ts|jsx|tsx|html|css|json|md|sh|txt|yaml|yml|env|cfg|toml|ini|sql)',
                snippet
            )
            if found:
                path = found[-1]
            else:
                ext = EXT_MAP.get(f['lang'].lower(), f['lang'] or 'txt')
                path = f'file_{i+1}.{ext}'
            files.append({'path': path, 'lang': f['lang'], 'content': f['content']})
            _log(f'  fallback: {path}')

    _log(f'OK Parser: extracted {len(files)} files total')
    return files


def extract_arch_meta(arch_doc: str) -> dict:
    """Extract PROJECT_NAME, RUN_COMMAND, INSTALL_COMMAND, PORT from arch doc."""
    meta = {
        "project_name":    "max-project",
        "run_command":     None,
        "install_command": None,
        "port":            5001,
    }
    for line in arch_doc.splitlines():
        line = line.strip()
        if line.startswith("PROJECT_NAME:"):
            raw = line.split(":", 1)[1].strip()
            # sanitize to valid folder name
            meta["project_name"] = re.sub(r'[^\w\-]', '-', raw).lower()[:40]
        elif line.startswith("RUN_COMMAND:"):
            meta["run_command"] = line.split(":", 1)[1].strip()
        elif line.startswith("INSTALL_COMMAND:"):
            meta["install_command"] = line.split(":", 1)[1].strip()
        elif line.startswith("PORT:"):
            try:
                meta["port"] = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
    return meta

# =========================
# FILE WRITER
# =========================

def write_project_files(project_path: Path, files: list):
    """Write all parsed files to disk. Returns list of written paths."""
    written = []
    for f in files:
        # Security: strip leading slashes and block traversal
        safe = re.sub(r'^[/\\]+', '', f["path"])
        safe = safe.replace("..", "")
        dest = project_path / safe
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f["content"], encoding="utf-8")
        written.append(safe)
        _log(f"  wrote: {safe}")
    return written

# =========================
# SUBPROCESS RUNNER
# =========================

def _log(line: str):
    ts = time.strftime("%H:%M:%S")
    log_queue.put(f"[{ts}] {line}")

def run_project(project_path: Path, install_cmd, run_cmd, port: int):
    global active_process
    _log("─── Starting project runner ───")

    env = os.environ.copy()
    env["PORT"] = str(port)

    # Install deps
    if install_cmd:
        _log(f"$ {install_cmd}")
        try:
            proc = subprocess.Popen(
                install_cmd, shell=True, cwd=str(project_path),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, env=env
            )
            for line in proc.stdout:
                _log(line.rstrip())
            proc.wait()
            if proc.returncode != 0:
                _log(f"⚠ Install exited with code {proc.returncode}")
            else:
                _log("✓ Install complete")
        except Exception as e:
            _log(f"✗ Install error: {e}")

    if not run_cmd:
        _log("⚠ No run command found — files written but not started")
        return

    # Stop previous process
    if active_process and active_process.poll() is None:
        _log("Stopping previous process...")
        active_process.terminate()
        try:
            active_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            active_process.kill()

    _log(f"$ {run_cmd}  (port {port})")
    try:
        active_process = subprocess.Popen(
            run_cmd, shell=True, cwd=str(project_path),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=env
        )
        pipeline["run_pid"] = active_process.pid
        _log(f"✓ Process started (PID {active_process.pid})")

        for line in active_process.stdout:
            _log(line.rstrip())

        active_process.wait()
        _log(f"Process exited (code {active_process.returncode})")
    except Exception as e:
        _log(f"✗ Run error: {e}")

# =========================
# ZIP BUILDER
# =========================

def build_zip(project_path: Path) -> Path:
    zip_path = project_path.parent / f"{project_path.name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in project_path.rglob("*"):
            if file.is_file():
                zf.write(file, file.relative_to(project_path.parent))
    return zip_path

# =========================
# LOG + INTENT HELPERS
# =========================

def log(role, stage, content):
    pipeline["history"].append({"role": role, "stage": stage, "content": content})

def make_response(message, waiting_for=None, agent=None, extras=None):
    d = {
        "stage":       pipeline["stage"],
        "agent":       agent,
        "message":     message,
        "waiting_for": waiting_for,
        "pipeline_status": {
            "arch":  bool(pipeline["arch_doc"]),
            "build": bool(pipeline["build_output"]),
            "test":  bool(pipeline["test_output"]),
            "write": bool(pipeline["write_output"]),
        },
        "project": {
            "name": pipeline["project_name"],
            "port": pipeline["run_port"],
        }
    }
    if extras:
        d.update(extras)
    return jsonify(d)

def classify_intent(user_input: str) -> str:
    prompt = """
Classify the user message as APPROVE or IMPROVE.
APPROVE = user accepts (yes, ok, looks good, approved, proceed, go ahead, next, done, great, perfect, ship it)
IMPROVE = user wants changes (fix, add, remove, change, update, but, instead, also, can you, make it)
Return ONLY the word APPROVE or IMPROVE.
"""
    try:
        r = client.chat.completions.create(
            model=ARCHITECT_MODEL,
            messages=[{"role":"system","content":prompt},{"role":"user","content":user_input}],
            temperature=0.1, max_tokens=5
        )
        w = r.choices[0].message.content.strip().upper()
        if "APPROVE" in w:
            return "APPROVE"
    except Exception:
        pass
    low = user_input.lower()
    approve_kw = ["approve","approved","yes","looks good","go ahead","proceed",
                  "ok","okay","next","ship","done","good","great","perfect",
                  "fine","correct","right","sure","yep","yup","let's go"]
    if any(k in low for k in approve_kw):
        return "APPROVE"
    return "IMPROVE"

# =========================
# STAGE RUNNERS
# =========================

def do_architect(idea):
    pipeline["idea"] = idea
    pipeline["iteration"] = 0
    out = groq_call(ARCHITECT_MODEL, ARCHITECT_PROMPT, f"Project idea:\n{idea}")
    pipeline["arch_doc"] = out
    return out

def do_architect_improve(feedback):
    pipeline["iteration"] += 1
    out = groq_call(ARCHITECT_MODEL, ARCHITECT_IMPROVE_PROMPT,
                    f"Original architecture:\n{pipeline['arch_doc']}\n\nUser feedback:\n{feedback}")
    pipeline["arch_doc"] = out
    return out

def do_builder():
    pipeline["iteration"] = 0
    out = groq_call(BUILD_MODEL, BUILD_PROMPT,
                    f"Architecture Document:\n{pipeline['arch_doc']}")
    pipeline["build_output"] = out
    return out

def do_builder_improve(feedback):
    pipeline["iteration"] += 1
    out = groq_call(BUILD_MODEL, BUILD_IMPROVE_PROMPT,
                    f"Architecture:\n{pipeline['arch_doc']}\n\n"
                    f"Previous build:\n{pipeline['build_output']}\n\n"
                    f"User feedback:\n{feedback}")
    pipeline["build_output"] = out
    return out

def do_tester():
    out = groq_call(TEST_MODEL, TEST_PROMPT,
                    f"Architecture:\n{pipeline['arch_doc']}\n\nCodebase:\n{pipeline['build_output']}")
    pipeline["test_output"] = out
    return out

def do_writer():
    out = groq_call(WRITE_MODEL, WRITE_PROMPT,
                    f"Architecture:\n{pipeline['arch_doc']}\n\n"
                    f"Codebase:\n{pipeline['build_output']}\n\n"
                    f"Test Report:\n{pipeline['test_output']}")
    pipeline["write_output"] = out
    return out

# =========================
# PROJECT TYPE DETECTION
# =========================

def detect_project_type(project_path: Path, files_written: list) -> str:
    """
    Detect what kind of project was built so we know how to serve it.
    Returns: "static" | "flask" | "node" | "other"
    """
    all_files = [f.lower() for f in files_written]
    exts = {Path(f).suffix.lower() for f in files_written}

    # Check for package.json → Node/JS project
    if "package.json" in all_files:
        return "node"

    # Check for Python server files → Flask/FastAPI/etc
    py_files = [f for f in files_written if f.endswith(".py")]
    for py_file in py_files:
        try:
            src = (project_path / py_file).read_text(errors="replace").lower()
            if any(kw in src for kw in ["flask", "fastapi", "django", "uvicorn", "app.run", "socketio"]):
                return "flask"
        except Exception:
            pass

    # Check for index.html or any .html → static
    html_files = [f for f in files_written if f.endswith(".html")]
    if html_files:
        return "static"

    # Only JS/CSS → static
    if exts and exts.issubset({".js", ".css", ".html", ".json", ".svg", ".png", ".ico", ".txt", ".md"}):
        return "static"

    # Has Python but no Flask → maybe a CLI; serve files statically anyway
    if ".py" in exts:
        return "other"

    return "static"


def find_entry_html(project_path: Path) -> str:
    """Find the best HTML entry point to load in the preview iframe."""
    candidates = ["index.html", "app.html", "main.html", "public/index.html", "src/index.html"]
    for c in candidates:
        if (project_path / c).exists():
            return c
    # any html file
    for f in project_path.rglob("*.html"):
        return str(f.relative_to(project_path))
    return ""


def do_write_and_run():
    """Parse build output → write files → detect type → serve or run."""
    meta = extract_arch_meta(pipeline["arch_doc"])
    project_name = meta["project_name"]
    project_path = PROJECTS_DIR / project_name
    project_path.mkdir(parents=True, exist_ok=True)

    pipeline["project_name"] = project_name
    pipeline["project_path"] = str(project_path)
    pipeline["run_port"]     = meta["port"]

    # Write files + README
    files = parse_files_from_build(pipeline["build_output"])
    if pipeline["write_output"]:
        files.append({"path": "README.md", "lang": "markdown", "content": pipeline["write_output"]})

    written = write_project_files(project_path, files)
    _log(f"✓ Wrote {len(written)} files to projects/{project_name}/")

    # Detect project type
    ptype = detect_project_type(project_path, written)
    pipeline["project_type"] = ptype
    _log(f"✓ Project type detected: {ptype}")

    if ptype == "static":
        # Serve directly via MAX — no subprocess needed
        entry = find_entry_html(project_path)
        preview = f"/preview/{project_name}/{entry}" if entry else f"/preview/{project_name}/"
        pipeline["preview_url"] = preview
        _log(f"✓ Static project — serving at {preview}")

    elif ptype in ("flask", "node"):
        # Run in subprocess AND expose via MAX proxy
        pipeline["preview_url"] = f"/proxy/{project_name}/"
        t = threading.Thread(
            target=run_project,
            args=(project_path, meta["install_command"], meta["run_command"], meta["port"]),
            daemon=True
        )
        t.start()
        _log(f"✓ {ptype} app — proxying :{ meta['port']} at /proxy/{project_name}/")

    else:
        # Other (CLI etc) — just write files, no preview
        pipeline["preview_url"] = ""
        t = threading.Thread(
            target=run_project,
            args=(project_path, meta["install_command"], meta["run_command"], meta["port"]),
            daemon=True
        )
        t.start()
        _log(f"⚠ Project type 'other' — files written, running subprocess")

    return project_name, pipeline["preview_url"], written

# =========================
# ROUTES
# =========================

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/chat", methods=["POST"])
def chat():
    data       = request.json or {}
    user_input = (data.get("message") or "").strip()
    if not user_input:
        return jsonify({"error": "Empty message"}), 400

    stage = pipeline["stage"]
    log("user", stage, user_input)

    if stage == "idle":
        pipeline["stage"] = "architect"
        out = do_architect(user_input)
        pipeline["stage"] = "await_arch_approval"
        log("agent", "ARCHITECT", out)
        return make_response(out, waiting_for="approval", agent="ARCHITECT")

    if stage == "await_arch_approval":
        intent = classify_intent(user_input)
        if intent == "APPROVE":
            pipeline["stage"] = "builder"
            out = do_builder()
            pipeline["stage"] = "await_build_approval"
            log("agent", "BUILDER", out)
            return make_response(out, waiting_for="approval", agent="BUILDER")
        else:
            out = do_architect_improve(user_input)
            log("agent", "ARCHITECT", out)
            return make_response(out, waiting_for="approval", agent="ARCHITECT")

    if stage == "await_build_approval":
        intent = classify_intent(user_input)
        if intent == "APPROVE":
            # Tester → Writer
            pipeline["stage"] = "tester"
            test_out = do_tester()
            log("agent", "TESTER", test_out)

            pipeline["stage"] = "writer"
            write_out = do_writer()
            log("agent", "WRITER", write_out)

            pipeline["stage"] = "running"

            # Write files + run
            project_name, preview_url, written = do_write_and_run()

            final = (
                "---\n## 🔍 Test Report\n\n" + test_out +
                "\n\n---\n## 📄 README.md\n\n" + write_out
            )
            log("agent", "WRITER", write_out)
            pipeline["stage"] = "done"

            return make_response(final, waiting_for=None, agent="WRITER", extras={
                "project_running":  True,
                "project_name":     project_name,
                "project_type":     pipeline["project_type"],
                "preview_url":      pipeline["preview_url"],
                "files_written":    written,
            })
        else:
            out = do_builder_improve(user_input)
            log("agent", "BUILDER", out)
            return make_response(out, waiting_for="approval", agent="BUILDER")

    if stage == "done":
        _reset()
        pipeline["stage"] = "architect"
        out = do_architect(user_input)
        pipeline["stage"] = "await_arch_approval"
        log("agent", "ARCHITECT", out)
        return make_response(out, waiting_for="approval", agent="ARCHITECT")

    return jsonify({"error": f"Unhandled stage: {stage}"}), 500


@app.route("/logs")
def logs():
    """SSE endpoint — streams log lines to the browser in real time."""
    def generate():
        while True:
            try:
                line = log_queue.get(timeout=30)
                yield f"data: {json.dumps(line)}\n\n"
            except queue.Empty:
                yield "data: null\n\n"   # heartbeat
    return Response(stream_with_context(generate()),
                    content_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/download")
def download():
    project_path = Path(pipeline.get("project_path", ""))
    if not project_path.exists():
        return jsonify({"error": "No project built yet"}), 404
    zip_path = build_zip(project_path)
    return send_file(str(zip_path), as_attachment=True,
                     download_name=f"{pipeline['project_name']}.zip")


@app.route("/files")
def list_files():
    project_path = Path(pipeline.get("project_path", ""))
    if not project_path.exists():
        return jsonify({"files": []})
    files = []
    for f in sorted(project_path.rglob("*")):
        if f.is_file():
            rel = str(f.relative_to(project_path))
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                content = ""
            files.append({"path": rel, "content": content, "size": f.stat().st_size})
    return jsonify({"files": files, "project": pipeline["project_name"]})


@app.route("/preview/<project_name>/")
@app.route("/preview/<project_name>/<path:filepath>")
def serve_preview(project_name, filepath=""):
    """Serve static project files directly — works for HTML/JS/CSS projects."""
    project_path = PROJECTS_DIR / project_name
    if not project_path.exists():
        return "Project not found", 404

    # Default to index.html
    if not filepath:
        for candidate in ["index.html", "app.html", "main.html"]:
            if (project_path / candidate).exists():
                filepath = candidate
                break
        else:
            # List all html files as directory
            files = sorted([str(f.relative_to(project_path)) for f in project_path.rglob("*") if f.is_file()])
            links = "".join(f'<li><a href="/preview/{project_name}/{f}">{f}</a></li>' for f in files)
            return f'<html><body style="font-family:monospace;background:#0d1117;color:#cdd9e5;padding:20px"><h2>{project_name}</h2><ul>{links}</ul></body></html>'

    return send_from_directory(str(project_path), filepath)


@app.route("/proxy/<project_name>/")
@app.route("/proxy/<project_name>/<path:subpath>")
def proxy_project(project_name, subpath=""):
    """Reverse-proxy requests to the running subprocess on its port."""
    import urllib.request, urllib.error
    port = pipeline.get("run_port") or 5001
    target = f"http://127.0.0.1:{port}/{subpath}"
    if request.query_string:
        target += "?" + request.query_string.decode()
    try:
        req = urllib.request.Request(
            target,
            data=request.get_data() or None,
            headers={k: v for k, v in request.headers if k.lower() not in ("host", "content-length")},
            method=request.method,
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body    = resp.read()
            status  = resp.status
            headers = dict(resp.headers)
            # Rewrite absolute URLs in HTML so relative links stay within proxy
            ctype = headers.get("Content-Type", "")
            if "html" in ctype:
                body = body.replace(
                    f"http://127.0.0.1:{port}".encode(),
                    f"/proxy/{project_name}".encode()
                )
            from flask import make_response as mk
            r = mk(body, status)
            for h in ("Content-Type", "Content-Encoding"):
                if h in headers:
                    r.headers[h] = headers[h]
            return r
    except urllib.error.URLError as e:
        return (f'<html><body style="font-family:monospace;background:#0d1117;color:#f85149;padding:20px">'
                f'<h3>Proxy error — app may still be starting</h3><pre>{e}</pre>'
                f'<p>Try refreshing in a moment.</p></body></html>'), 502


@app.route("/stop", methods=["POST"])
def stop_process():
    global active_process
    if active_process and active_process.poll() is None:
        active_process.terminate()
        _log("Process stopped by user")
        return jsonify({"status": "stopped"})
    return jsonify({"status": "not_running"})



@app.route("/debug/parse", methods=["GET"])
def debug_parse():
    """Returns what the parser sees in the current build output."""
    build = pipeline.get("build_output", "")
    if not build:
        return jsonify({"error": "No build output yet"}), 404
    files = parse_files_from_build(build)
    fence_re = re.compile(r'(?:```|~~~)(\w*)\r?\n(.*?)(?:```|~~~)', re.DOTALL)
    raw_fences = [{"lang": m.group(1), "preview": m.group(2)[:80]} for m in fence_re.finditer(build)]
    return jsonify({
        "build_length":   len(build),
        "files_parsed":   [f["path"] for f in files],
        "fences_found":   len(raw_fences),
        "fence_previews": raw_fences[:5],
        "build_preview":  build[:500],
    })

@app.route("/reset", methods=["POST"])
def reset():
    global active_process
    if active_process and active_process.poll() is None:
        active_process.terminate()
    _reset()
    return jsonify({"status": "ok", "stage": "idle"})


@app.route("/state", methods=["GET"])
def get_state():
    return jsonify({
        "stage":   pipeline["stage"],
        "history": pipeline["history"],
        "project": {"name": pipeline["project_name"], "port": pipeline["run_port"]},
        "pipeline_status": {
            "arch":  bool(pipeline["arch_doc"]),
            "build": bool(pipeline["build_output"]),
            "test":  bool(pipeline["test_output"]),
            "write": bool(pipeline["write_output"]),
        }
    })


def _reset():
    for k in ["stage","idea","arch_doc","build_output","test_output","write_output",
              "project_name","project_path","run_port","run_pid","project_type","preview_url"]:
        pipeline[k] = "" if k not in ("stage","run_port","run_pid") else \
                      ("idle" if k == "stage" else None)
    pipeline["history"]   = []
    pipeline["iteration"] = 0


if __name__ == "__main__":
    app.run(debug=True, port=5000, threaded=True)