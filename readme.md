# MAX.sys — Multi-Agent Pipeline System

> **Architect → Build → Test → Document.** A fully automated, human-in-the-loop AI pipeline that takes a project idea from concept to documented codebase using specialized LLM agents.

---

## Overview

MAX.sys is a multi-agent development pipeline powered by [Groq](https://groq.com/) , Meta's Llama 4 Scout and groq/compound. You describe a project idea in plain English, and four specialized AI agents handle the rest — each one passing its output to the next, with user approval gates at the critical stages.

```
Your Idea
   │
   ▼
┌──────────────┐     approve / improve
│  ARCHITECT   │ ──────────────────────► revise
│  designs doc │
└──────┬───────┘
       │ approved
       ▼
┌──────────────┐     approve / improve
│   BUILDER    │ ──────────────────────► revise
│  writes code │
└──────┬───────┘
       │ approved
       ▼
┌──────────────┐
│    TESTER    │  (auto — no gate)
│  finds bugs  │
└──────┬───────┘
       │
       ▼
┌──────────────┐
│    WRITER    │  (auto — no gate)
│  writes docs │
└──────┬───────┘
       │
       ▼
    Done ✓
```

---

## Features

- **4 specialized agents** — each with a focused system prompt and a single job
- **User approval gates** after Architect and Builder — approve to advance or type feedback to iterate
- **Automatic hand-off** — once the build is approved, Tester and Writer run back-to-back without interruption
- **Revision loop** — request improvements at any gate; the agent revises and re-presents
- **Intent detection** — the backend classifies your message as APPROVE or IMPROVE using the LLM (with heuristic fallback), so you never have to click special buttons
- **Rich frontend** — pipeline progress tracker, markdown rendering, syntax-highlighted code blocks with copy buttons, thinking indicators
- **Stateful pipeline** — full stage machine on the server; reloading the page reconnects to the current state
- **One-click reset** — wipe the session and start a new project instantly

---

## Agents

| Agent | Role | User Gate |
|---|---|---|
| **ARCHITECT** | Produces a complete architecture document — tech stack, folder structure, module breakdown, API design, implementation notes | ✅ Yes |
| **BUILDER** | Implements the full codebase from the architecture document — production-ready, no placeholders | ✅ Yes |
| **TESTER** | Static analysis, bug report, fixed files for critical issues, unit/integration test cases, quality score | ❌ Auto |
| **WRITER** | Generates a professional `README.md` from the architecture, code, and test report | ❌ Auto |

---

## Tech Stack

| Layer | Technology |
|---|---|
| LLM API | [Groq](https://console.groq.com/) |
| Models | `meta-llama/llama-4-scout-17b-16e-instruct` and `groq/compound`|
| Backend | Python · Flask · Flask-CORS |
| Frontend | Vanilla HTML/CSS/JS (single file) |
| Markdown | [marked.js](https://marked.js.org/) |
| Syntax highlighting | [highlight.js](https://highlightjs.org/) |
| Config | python-dotenv |

---

## Prerequisites

- Python **3.9+**
- A [Groq API key](https://console.groq.com/keys) (free tier works)
- `pip` (comes with Python)

---

## Installation & Setup

**1. Clone the repository**

```bash
git clone https://github.com/yourusername/max-sys.git
cd max-sys
```

**2. Install dependencies**

```bash
pip install flask flask-cors groq python-dotenv
```

**3. Create your `.env` file**

```bash
cp .env.example .env
```

Then open `.env` and add your key:

```env
GROQ_API_KEY=your_groq_api_key_here
```

**4. Run the server**

```bash
python app.py
```

**5. Open the UI**

Navigate to [http://localhost:5000](http://localhost:5000) in your browser.

---

## Usage

### Starting a project

Type your project idea into the input field and press **Enter**.

```
Build a REST API for a task management app with user auth, 
CRUD for tasks, and PostgreSQL as the database.
```

The **ARCHITECT** agent will generate a full architecture document.

### At an approval gate

After the Architect or Builder responds, an approval bar appears:

- Click **✓ Approve** to advance to the next stage
- Click **✏ Improve** (or just type) to request revisions

```
# Example improvement requests:
"Add WebSocket support for real-time updates"
"Use SQLite instead of PostgreSQL"
"Add rate limiting to the auth endpoints"
```

### Watching the pipeline run

The header tracker shows live progress:

```
[1 ARCHITECT ✓] → [2 BUILDER ✓] → [3 TESTER ⚙] → [4 WRITER]
```

Once you approve the build, the Tester and Writer run automatically. The final response contains the full test report and a ready-to-use `README.md`.

### Starting a new project

Click **↺ reset** in the top right at any time to wipe the session and start fresh.

---

## Project Structure

```
max-sys/
├── app.py          # Flask backend — pipeline state machine, agent runners, routes
├── index.html      # Frontend UI — single file, no build step required
├── .env            # Your Groq API key (never commit this)
├── .env.example    # Template for the env file
└── README.md       # This file
```

---

## API Reference

### `POST /chat`

Send a user message. The backend determines the current stage and routes accordingly.

**Request body:**
```json
{ "message": "your message here" }
```

**Response:**
```json
{
  "stage": "await_arch_approval",
  "agent": "ARCHITECT",
  "message": "... agent output ...",
  "waiting_for": "approval",
  "pipeline_status": {
    "arch": true,
    "build": false,
    "test": false,
    "write": false
  }
}
```

### `POST /reset`

Wipes all pipeline state and returns to `idle`.

### `GET /state`

Returns the current pipeline stage, full conversation history, and status flags.

---

## Pipeline Stages

| Stage | Description |
|---|---|
| `idle` | Waiting for the initial project idea |
| `await_arch_approval` | Architecture generated — waiting for user approval or feedback |
| `builder` | Builder is generating the codebase |
| `await_build_approval` | Build complete — waiting for user approval or feedback |
| `tester` | Tester running automatically |
| `writer` | Writer running automatically |
| `done` | Pipeline complete — type a new idea to restart |

---

## Environment Variables

| Variable | Description | Required |
|---|---|---|
| `GROQ_API_KEY` | Your Groq API key from [console.groq.com](https://console.groq.com/keys) | ✅ Yes |

---

## Known Limitations

- **No persistent storage** — pipeline state is in-memory; restarting the server resets it
- **Single session** — designed for one active pipeline at a time
- **Token limits** — very large projects may hit Groq's `max_tokens` cap of 4096 per call; complex builds may be truncated
- **No streaming** — responses arrive all at once after the model finishes

---
## Author

**Samin Saikia**

Python Developer focused on backend systems, AI agents, and practical software tools.

- GitHub: https://github.com/Samin-Saikia
- LinkedIn: https://www.linkedin.com/in/samin-saikia-b7660b3a1/

Built as an experimental research project exploring multi-agent software development pipelines.

---

## Contributing

Contributions are welcome! To get started:

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Commit your changes: `git commit -m "add: your feature description"`
4. Push to the branch: `git push origin feature/your-feature`
5. Open a Pull Request

Please keep PRs focused — one feature or fix per PR.

---

## License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.

---

<div align="center">
  Built with MAX.sys · Powered by Groq + Llama 4
</div>