# Contributing to Healthcare-GPT

This guide walks you from a fresh machine to a working dev environment. It covers Windows, macOS, and Linux, plus the gotchas we've hit in practice.

> **Status:** Setup steps have been verified on **Windows 11 + Git Bash**. macOS and Linux instructions should work but have not been re-verified end-to-end. If you hit issues on those platforms, update this doc.

---

## 1. Prerequisites

Install these before doing anything else.

| Tool | Version | Notes |
|------|---------|-------|
| Python | 3.12.x | `.python-version` pins 3.12.1. Anything 3.12.x works. |
| Git | any recent | |
| Ollama | latest | https://ollama.com — runs the local LLM |
| VS Code | latest | Recommended editor. Install the **Python** extension by Microsoft. |

### Platform-specific notes

**Windows**
- Install Python from python.org. **Check "Add Python to PATH"** during install.
- Use **Git Bash** as your terminal (comes with Git for Windows). PowerShell works but the commands below assume bash syntax.
- Ollama for Windows installs to `C:\Users\<you>\AppData\Local\Programs\Ollama\`. The installer **does not always add this to PATH** — see Section 6 if `ollama` isn't found.

**macOS**
- `brew install python@3.12 git` then download Ollama from ollama.com.

**Linux**
- Use your distro's package manager for Python 3.12 and Git. For Ollama: `curl -fsSL https://ollama.com/install.sh | sh`.

---

## 2. Clone the repo

```bash
git clone https://github.com/franklin02/Healthcare-GPT
cd Healthcare-GPT
```

---

## 3. Create and activate a virtual environment

A venv is an isolated Python install just for this project — keeps the project's packages out of your system Python.

```bash
python -m venv .venv
```

Activate it:

| Platform / Shell | Command |
|------------------|---------|
| Windows + Git Bash | `source .venv/Scripts/activate` |
| Windows + PowerShell | `.venv\Scripts\Activate.ps1` |
| Windows + cmd.exe | `.venv\Scripts\activate.bat` |
| macOS / Linux | `source .venv/bin/activate` |

After activation your prompt should start with `(.venv)`. If it doesn't, the venv isn't active and pip will install into the wrong Python.

**VS Code:** open the command palette (`Ctrl+Shift+P` / `Cmd+Shift+P`) → "Python: Select Interpreter" → pick the one inside `.venv`. New terminals opened in VS Code will auto-activate the venv after this.

---

## 4. Install Python dependencies

There are two install profiles. **Pick one based on what part of the codebase you'll be working on.**

### 4a. Minimal (GDELT-only contributors)

If you're only working in `src/GDELT/`, you don't need the RAG/vector-DB stack. Install just the four packages GDELT uses:

```bash
pip install requests pandas beautifulsoup4 lxml
```

This is fast and avoids C++ build issues on Windows (see below).

### 4b. Full install (anyone touching ingest, main, scrapers, fda_apis, RAG)

```bash
pip install -r requirements.txt
```

> **Windows gotcha:** `chromadb==0.5.3` pulls in `chroma-hnswlib`, which compiles native C++ code. If you see:
> ```
> error: Microsoft Visual C++ 14.0 or greater is required.
> ```
> install **Microsoft C++ Build Tools** from https://visualstudio.microsoft.com/visual-cpp-build-tools/. During install, check the "Desktop development with C++" workload. Then re-run `pip install -r requirements.txt`. macOS and Linux already have working C compilers and don't hit this.

### Verify packages installed

```bash
python -c "import requests, bs4; print('ok')"
```

(For the full install, also try `import chromadb, langchain, fastapi`.)

---

## 5. Install and start Ollama

After installing Ollama from ollama.com (or the Linux script above), start it:

- **Windows:** Search "Ollama" in the Start menu and launch it. A llama icon appears in the system tray. The service listens on `localhost:11434`.
- **macOS:** Launch the Ollama app from Applications. Same tray icon behavior.
- **Linux:** `ollama serve` (or it runs as a systemd service depending on install).

Pull the model the project uses:

```bash
ollama pull llama3.2
```

This downloads ~2 GB. One-time.

---

## 6. Troubleshooting `ollama: command not found` (Windows)

If `ollama --version` says "command not found" even though the app is installed and running, PATH wasn't updated. Two fixes:

**Quick fix — call the full path:**
```bash
"/c/Users/<you>/AppData/Local/Programs/Ollama/ollama.exe" pull llama3.2
```

**Permanent fix — add to PATH:**
1. Windows Settings → search "Edit environment variables for your account"
2. Edit `Path` → New → add `C:\Users\<you>\AppData\Local\Programs\Ollama`
3. Close and reopen all terminals (and VS Code). `ollama` should now resolve.

---

## 7. Smoke test — confirm everything works end-to-end

With the venv activated and Ollama running, from the repo root:

```bash
cd src/GDELT
python
```

At the `>>>` prompt:

```python
from helpers import get_body, ai_check_validation
b = get_body("https://www.bleepingcomputer.com/news/security/")
print(len(b))                                  # expect a few thousand chars
print(ai_check_validation("test", b[:2000]))   # expect a (bool, str) tuple after ~5–10s
```

If both work, your stack is fully functional. `exit()` to leave.

(Skip URLs from CISA, GovInfo, and other government sites for testing — many block non-browser user-agents and return 403.)

---

## 8. Running the application (full install only)

If you did the full install (§4b), you can run the RAG chat app end-to-end.

### Optional: FDA API keys

Only needed if you'll run scripts in `src/fda_apis/`. The keys are free.

1. Get them:
   - `FDA_SHORTAGE_API_KEY` → https://open.fda.gov/apis/drug/drugshortages/
   - `FDA_SPL_API_KEY` → https://open.fda.gov/apis/drug/label/
2. Create `.env` at the repo root:
   ```
   FDA_SHORTAGE_API_KEY=your_key_here
   FDA_SPL_API_KEY=your_key_here
   ```
   `.env` is git-ignored.

### Quick start

```bash
cd src
python ingest.py --file data/Ready_for_RAG/CyberScoop.json
uvicorn main:app --reload
```

Open http://127.0.0.1:8000 and ask a question.

### Ingestion pipeline (`ingest.py`)

- Input JSON should follow `src/data/schema.json`. Fallback: any object with a top-level `sources: [...]` list.
- Flags:
  - `--file <path>` (required) — JSON file to ingest.
  - `--new_db` (optional) — wipes the existing ChromaDB and starts fresh.
- Default behavior is additive — re-ingesting the same file deduplicates by `id`.

### API reference (`src/main.py`)

| Method | Path | Purpose |
|--------|------|---------|
| `GET`  | `/`        | Chat UI (serves `index.html`) |
| `POST` | `/chat`    | Body: `{ "question": "..." }` → `{ answer, sources, model, chunks_retrieved }` |
| `GET`  | `/status`  | Health check — DB readiness + record count |

Source documents in `/chat` responses follow the shape `{ id, title, source_name, direct_link }`.

---

## 9. Workflow conventions

### Branches
- Branch off `main`. Use descriptive names: `gdelt-runner`, `expand-themes`, etc.

### Commits
- Conventional Commits style: `feat:`, `fix:`, `refactor:`, `chore:`, `docs:`, `test:`.
- Scope where useful: `feat(gdelt): add end-to-end runner`.

### Issues
- Title in the same `feat: ...` style as commits.
- Apply a label for the area of the codebase: `GDELT`, `RAG`, `scrapers`, etc.

### Pull requests
- Reference the issue: "Closes #12".
- Keep PRs focused — one concern per PR.

---

## 10. Repo structure (what's actually live)

```
Healthcare-GPT/
├── src/
│   ├── main.py              # FastAPI backend + RAG chain
│   ├── ingest.py            # JSON → embeddings → ChromaDB
│   ├── index.html           # Chat UI
│   ├── GDELT/               # GDELT pipeline (cyber_security.py, helpers.py, ollama_filter.py)
│   ├── scrapers/            # Per-source scrapers (CNN, AHA, FDA congress reports, etc.)
│   ├── fda_apis/            # FDA API clients (require .env keys — see §8)
│   ├── data/                # Processed JSON for ingestion (Ready_for_RAG/, Noise/, Vulnerabilities/)
│   └── raw_data/            # Raw scraped/downloaded data
├── docs/                    # Project docs, prompts, agent specs
├── chroma_db/               # Auto-generated vector store (git-ignored)
├── requirements.txt
└── CONTRIBUTING.md          # This file
```

> The `README.md` describes a templates/KPI/dashboard-JSON deliverable that isn't reflected in the current code. Treat `CONTRIBUTING.md` as authoritative for setup; treat the live code under `src/` as authoritative for what the project actually does today.

---

## 11. Common errors (quick reference)

| Symptom | Cause | Fix |
|---------|-------|-----|
| `error: Microsoft Visual C++ 14.0 or greater is required` | `chroma-hnswlib` needs a C++ compiler | Install MS C++ Build Tools, or use the minimal install (§4a) if you don't need ChromaDB |
| `ollama: command not found` | Ollama installed but not in PATH | §6 |
| `Warning: could not connect to a running Ollama instance` | Ollama installed but service not started | Launch the Ollama app (Windows/macOS) or run `ollama serve` (Linux) |
| `python -c "..."` silently does nothing in Git Bash | Quoting / MSYS path conversion mangling | Use `python` interactive REPL or save the snippet to a `.py` file |
| `[ERROR] Status is unexpected: 403` from `get_body` | Target site blocks non-browser user-agents | Try a different test URL (e.g. bleepingcomputer.com, healthcaredive.com) |
| Prompt doesn't show `(.venv)` after activation | Venv command path wrong for your shell, or venv wasn't created | Re-check §3 table; recreate with `python -m venv .venv` |
