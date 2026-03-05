import os
import json
from flask import Flask, request, jsonify, send_from_directory
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

ARCHITECT_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
BUILD_MODEL     = "groq/compound"
TEST_MODEL      = "meta-llama/llama-4-scout-17b-16e-instruct"
WRITE_MODEL     = "meta-llama/llama-4-scout-17b-16e-instruct"

MAX_TOKENS = 4096

# =========================
# PIPELINE STATE
# =========================
# Flow:
#   idle
#     → architect           (LLM generates architecture doc)
#     → await_arch_approval (user: APPROVE → builder | IMPROVE → re-architect)
#     → builder             (LLM generates full code from arch doc)
#     → await_build_approval(user: APPROVE → tester+writer | IMPROVE → re-build)
#     → tester              (LLM auto-runs, no user gate)
#     → writer              (LLM auto-runs, no user gate)
#     → done

pipeline = {
    "stage":        "idle",
    "idea":         "",
    "arch_doc":     "",
    "build_output": "",
    "test_output":  "",
    "write_output": "",
    "history":      [],
    "iteration":    0,
}

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

Be thorough. The builder uses ONLY this document. No ambiguity.
"""

ARCHITECT_IMPROVE_PROMPT = """
You are MAX Architect.

The user reviewed your architecture and wants revisions.

Input:
- Original architecture document
- User feedback

Produce a fully revised architecture document incorporating all feedback.
Preserve everything not criticised. Output the complete document.
"""

BUILD_PROMPT = """
You are MAX Builder — an expert programmer.

You receive a complete architecture document. Implement it fully.

Rules:
- Write ALL files in the architecture
- Use the exact folder structure
- Production-quality code — no placeholders, no TODOs, no stubs
- Every file must be complete and runnable

Format every file as:
### `path/to/filename.ext`
```language
<complete file contents>
```

End with a **Setup & Run** section with exact install and run commands.
"""

BUILD_IMPROVE_PROMPT = """
You are MAX Builder.

The user reviewed your code and wants changes.

Input:
- Architecture document
- Previous build
- User feedback

Output only the changed files using the same format (### `path/file` code block).
Add a short explanation of what changed and why.
"""

TEST_PROMPT = """
You are MAX Tester — a senior QA engineer.

You receive the architecture and full codebase.

Produce:
1. **Static Analysis** — bugs, logic errors, security issues
2. **Bug Report** — table: File | Issue | Severity (critical/major/minor)
3. **Fixed Files** — full corrected code for every critical/major bug (same format as builder)
4. **Test Cases** — unit/integration tests for key functionality
5. **Quality Summary** — score 1-10, readiness verdict
"""

WRITE_PROMPT = """
You are MAX Writer — a technical documentation expert.

You receive the architecture, final code, and test report.

Produce a professional README.md:
1. Title, badges (placeholders), description
2. Features
3. Tech stack
4. Prerequisites
5. Installation & setup
6. Usage with examples
7. Project structure (directory tree)
8. API reference (if applicable)
9. Testing instructions
10. Contributing guide
11. License

Use proper Markdown. Make it GitHub-ready.
Write all the markdown in code format so user can directly put in github
"""

# =========================
# HELPERS
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


def log(role, stage, content):
    pipeline["history"].append({
        "role":    role,
        "stage":   stage,
        "content": content
    })


def make_response(message, waiting_for=None, agent=None):
    return jsonify({
        "stage":       pipeline["stage"],
        "agent":       agent,
        "message":     message,
        "waiting_for": waiting_for,  # "approval" | "improvement_or_approval" | None
        "pipeline_status": {
            "arch":  bool(pipeline["arch_doc"]),
            "build": bool(pipeline["build_output"]),
            "test":  bool(pipeline["test_output"]),
            "write": bool(pipeline["write_output"]),
        }
    })

# =========================
# INTENT DETECTION
# =========================

def classify_intent(user_input: str) -> str:
    """Returns APPROVE or IMPROVE."""
    prompt = """
Classify the user message as exactly one of:
- APPROVE  (user accepts/confirms: "looks good", "approved", "yes", "proceed", "go ahead", "next", "ok", "ship it", "done", "great", "perfect", "fine")
- IMPROVE  (user wants changes: "change", "fix", "add", "remove", "update", "revise", "but", "instead", "also", "what about", "can you", "make it", "don't", "should")

Return ONLY the single word APPROVE or IMPROVE.
"""
    try:
        r = client.chat.completions.create(
            model=ARCHITECT_MODEL,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user",   "content": user_input}
            ],
            temperature=0.1,
            max_tokens=5
        )
        word = r.choices[0].message.content.strip().upper()
        if "APPROVE" in word:
            return "APPROVE"
    except Exception:
        pass
    # Heuristic fallback
    low = user_input.lower()
    approve_kw = ["approve", "approved", "yes", "looks good", "go ahead", "proceed",
                  "ok", "okay", "next", "ship", "done", "good", "great", "perfect",
                  "fine", "correct", "right", "sure", "yep", "yup", "let's go"]
    if any(k in low for k in approve_kw):
        return "APPROVE"
    return "IMPROVE"

# =========================
# STAGE RUNNERS
# =========================

def do_architect(idea: str) -> str:
    pipeline["idea"] = idea
    pipeline["iteration"] = 0
    out = groq_call(ARCHITECT_MODEL, ARCHITECT_PROMPT,
                    f"Project idea:\n{idea}")
    pipeline["arch_doc"] = out
    return out


def do_architect_improve(feedback: str) -> str:
    pipeline["iteration"] += 1
    out = groq_call(ARCHITECT_MODEL, ARCHITECT_IMPROVE_PROMPT,
                    f"Original architecture:\n{pipeline['arch_doc']}\n\nUser feedback:\n{feedback}")
    pipeline["arch_doc"] = out
    return out


def do_builder() -> str:
    pipeline["iteration"] = 0
    out = groq_call(BUILD_MODEL, BUILD_PROMPT,
                    f"Architecture Document:\n{pipeline['arch_doc']}")
    pipeline["build_output"] = out
    return out


def do_builder_improve(feedback: str) -> str:
    pipeline["iteration"] += 1
    out = groq_call(BUILD_MODEL, BUILD_IMPROVE_PROMPT,
                    f"Architecture:\n{pipeline['arch_doc']}\n\n"
                    f"Previous build:\n{pipeline['build_output']}\n\n"
                    f"User feedback:\n{feedback}")
    pipeline["build_output"] = out
    return out


def do_tester() -> str:
    out = groq_call(TEST_MODEL, TEST_PROMPT,
                    f"Architecture:\n{pipeline['arch_doc']}\n\n"
                    f"Codebase:\n{pipeline['build_output']}")
    pipeline["test_output"] = out
    return out


def do_writer() -> str:
    out = groq_call(WRITE_MODEL, WRITE_PROMPT,
                    f"Architecture:\n{pipeline['arch_doc']}\n\n"
                    f"Codebase:\n{pipeline['build_output']}\n\n"
                    f"Test Report:\n{pipeline['test_output']}")
    pipeline["write_output"] = out
    return out

# =========================
# MAIN CHAT ROUTE
# =========================

@app.route("/chat", methods=["POST"])
def chat():
    data       = request.json or {}
    user_input = (data.get("message") or "").strip()
    if not user_input:
        return jsonify({"error": "Empty message"}), 400

    stage = pipeline["stage"]
    log("user", stage, user_input)

    # ── IDLE → give us your idea ────────────────────────────────
    if stage == "idle":
        pipeline["stage"] = "architect"
        out = do_architect(user_input)
        pipeline["stage"] = "await_arch_approval"
        log("agent", "ARCHITECT", out)
        return make_response(out, waiting_for="approval", agent="ARCHITECT")

    # ── ARCHITECT APPROVAL GATE ──────────────────────────────────
    if stage == "await_arch_approval":
        intent = classify_intent(user_input)
        if intent == "APPROVE":
            # hand off to builder
            pipeline["stage"] = "builder"
            out = do_builder()
            pipeline["stage"] = "await_build_approval"
            log("agent", "BUILDER", out)
            return make_response(out, waiting_for="approval", agent="BUILDER")
        else:
            out = do_architect_improve(user_input)
            log("agent", "ARCHITECT", out)
            return make_response(out, waiting_for="approval", agent="ARCHITECT")

    # ── BUILD APPROVAL GATE ──────────────────────────────────────
    if stage == "await_build_approval":
        intent = classify_intent(user_input)
        if intent == "APPROVE":
            # auto-pipeline: tester → writer
            pipeline["stage"] = "tester"
            test_out = do_tester()
            log("agent", "TESTER", test_out)

            pipeline["stage"] = "writer"
            write_out = do_writer()
            log("agent", "WRITER", write_out)

            pipeline["stage"] = "done"

            final = (
                "---\n## 🔍 Test Report\n\n" + test_out +
                "\n\n---\n## 📄 README.md\n\n" + write_out
            )
            return make_response(final, waiting_for=None, agent="WRITER")
        else:
            out = do_builder_improve(user_input)
            log("agent", "BUILDER", out)
            return make_response(out, waiting_for="approval", agent="BUILDER")

    # ── DONE: start a new project ────────────────────────────────
    if stage == "done":
        _reset()
        pipeline["stage"] = "architect"
        out = do_architect(user_input)
        pipeline["stage"] = "await_arch_approval"
        log("agent", "ARCHITECT", out)
        return make_response(out, waiting_for="approval", agent="ARCHITECT")

    return jsonify({"error": f"Unhandled stage: {stage}"}), 500


@app.route("/reset", methods=["POST"])
def reset():
    _reset()
    return jsonify({"status": "ok", "stage": "idle"})


@app.route("/state", methods=["GET"])
def get_state():
    return jsonify({
        "stage":   pipeline["stage"],
        "history": pipeline["history"],
        "pipeline_status": {
            "arch":  bool(pipeline["arch_doc"]),
            "build": bool(pipeline["build_output"]),
            "test":  bool(pipeline["test_output"]),
            "write": bool(pipeline["write_output"]),
        }
    })


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


def _reset():
    for k in ["stage","idea","arch_doc","build_output","test_output","write_output"]:
        pipeline[k] = "" if k != "stage" else "idle"
    pipeline["history"]   = []
    pipeline["iteration"] = 0


if __name__ == "__main__":
    app.run(debug=True, port=5000)