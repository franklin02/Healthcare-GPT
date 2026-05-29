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

There are three install profiles. **Pick based on what part of the codebase
you'll be working on.**

### 4a. Development tools

Install these if you plan to run tests, format code, or build docs:

```bash
python -m pip install -r requirements-dev.txt
```

### 4b. Minimal runtime (GDELT-only contributors)

If you're only working in `src/GDELT/`, you don't need the RAG/vector-DB stack. Install just the packages GDELT uses:

```bash
python -m pip install requests pandas beautifulsoup4 lxml
```

This is fast and avoids C++ build issues on Windows (see below).

### 4c. Full runtime (anyone touching ingest, scrapers, or RAG)

```bash
python -m pip install -r requirements.txt
```

> **Windows gotcha:** `chromadb==0.5.3` pulls in `chroma-hnswlib`, which compiles native C++ code. If you see:
> ```
> error: Microsoft Visual C++ 14.0 or greater is required.
> ```
> install **Microsoft C++ Build Tools** from https://visualstudio.microsoft.com/visual-cpp-build-tools/. During install, check the "Desktop development with C++" workload. Then re-run `python -m pip install -r requirements.txt`. macOS and Linux already have working C compilers and don't hit this.

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

Then confirm the service is visible:

```bash
ollama list
```

Model pulls are path-specific:

- Current validation/extraction helpers use the model in `src/shared_utils.py`
  (`AI_MODEL`, currently `llama3.2`).
- The RAG app and Gemma experiment use `gemma4:e4b`.

You do not need to pull every model for every contribution. If you are running
an LLM-backed path, check `ollama list` first and pull the model for that path
if it is missing. Missing models can produce misleading validation results.
`gemma4:e4b` has been observed around 9.6 GB, so expect a large one-time
download rather than the previous estimate.

---

## 6. Troubleshooting `ollama: command not found` (Windows)

If `ollama --version` says "command not found" even though the app is installed and running, PATH wasn't updated. Two fixes:

**Quick fix — call the full path:**
```bash
"/c/Users/<you>/AppData/Local/Programs/Ollama/ollama.exe" list
```

**Permanent fix — add to PATH:**
1. Windows Settings → search "Edit environment variables for your account"
2. Edit `Path` → New → add `C:\Users\<you>\AppData\Local\Programs\Ollama`
3. Close and reopen all terminals (and VS Code). `ollama` should now resolve.

---

## 7. Smoke tests

Run the quick checks from the repo root with the venv activated.

### No-network sanity checks

These checks should finish quickly and do not call live websites or the LLM:

```bash
python -m pytest tests/test_orchestrator.py tests/test_html_engine.py tests/test_logging_utils.py -q
python -m src.orchestrator --skip-gdelt --html-start-page 1 --html-page-cap 0 --verbose
```

Expected result:

- The focused tests pass.
- The orchestrator scans the configured HTML sites but stops each one at
  `Reached page cap (0)`.
- The run summary shows `Sites scanned: 5`, `Processed: 0`, `Errors: 0`, and
  `Output records: 0`.

### Ollama configuration check

This prints the model name configured for the current shared validation helper
without starting a generation request:

```bash
python -c "from src.shared_utils import AI_MODEL; print(AI_MODEL)"
```

Expected result: it prints the configured model name, currently `llama3.2`.

### Optional extraction-only LLM smoke

Run this when you specifically need to confirm that `extract_fields()` returns
typed `sector_data` and `subsector_data` for a known subsector. Keep Ollama
running first and confirm the configured model appears in `ollama list`.

```bash
python3 -c "
from src.shared_utils import extract_fields

title = 'Ransomware attack hits Ascension Health hospitals, disrupts patient records'
body = '''Ascension Health confirmed a ransomware attack on May 8 affecting 140 hospitals across 19 states.
Electronic health records were taken offline. The attack encrypted systems affecting radiology, lab results,
and pharmacy. Approximately 500,000 patient records may have been exposed. The FBI has been contacted.
Staff diverted ambulances to nearby facilities. Systems restored after 12 days of downtime.'''

sector, subsector = extract_fields('cyber_attack', title, body)
print('\\n=== sector_data ===')
print(sector)
print('\\n=== subsector_data ===')
print(subsector)
"
```

Expected result: `sector_data` contains only the shared fields
(`exec_summary`, `geography_scope`, dates, and mitigation), and
`subsector_data` contains only `cyber_attack` fields such as `attack_type`,
`systems_affected`, `downtime_days`, and related breach metadata. Fields not
explicitly supported by the text should be `None`/`null` rather than copied
boilerplate.

### Optional slower integration smoke

Only run this when you specifically need to test live GDELT downloads and local
LLM validation. Keep Ollama running first. The first run can take several
minutes if the model is not already installed.

```bash
python -m src.orchestrator --skip-html --num-files 1 --limit 1 --verbose
```

If this fails with a missing-model message, pull the model named in the error
and rerun the command. If it returns only negative validation results, confirm
the configured model appears in `ollama list` before trusting the smoke result.

---

## 8. Running the application (full install only)

If you did the full runtime install (§4c), you can run the RAG chat app
end-to-end.

### Generate or choose processed JSON

Use an existing file under `data/processed/`, or generate a new GDELT file with:

```bash
python -m src.GDELT.runner --num-files 2 --limit 3
```

With the orchestrator:

```bash
python -m src.orchestrator --num-files 2 --limit 3
```

The runner writes final records to `data/processed/GDELT.json` by default.

### Quick start

```bash
python src/ingest.py --file data/processed/AHA.json --force
uvicorn src.RAG.server:app --reload
```

Open http://127.0.0.1:8000 and ask a question.

### Ingestion pipeline (`ingest.py`)

- Input JSON should follow the record shape represented by `src/config/schema.json`.
  The loader expects a top-level `sources: [...]` list.
- Flags:
  - `--file <path>` (required) — JSON file to ingest.
  - `--new_db` (optional) — wipes the existing ChromaDB and starts fresh.
  - `--force` (optional) — skips LLM validation and semantic duplicate checks.
  - `--dup_threshold <float>` (optional) — adjusts semantic duplicate matching.
  - `--use-bert` (optional) — runs BERT before ingestion-time LLM validation.
- Default behavior is additive and checks exact IDs plus semantic duplicates.

### API reference (`src/RAG/server.py`)

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
│   ├── ingest.py            # JSON → embeddings → ChromaDB
│   ├── RAG/                 # FastAPI chat server and frontend assets
│   ├── GDELT/               # GDELT seeds, validation, extraction, runner
│   ├── scrapers/            # Shared scraper and LLM helper utilities
│   ├── classes/             # Dataclass models for disruption records
│   └── config/              # JSON schema reference
├── data/processed/          # Processed JSON files for ingestion
├── docs/                    # Sphinx docs plus historical prompt/source docs
├── chroma_db/               # Auto-generated vector store (git-ignored)
├── requirements.txt
└── CONTRIBUTING.md          # This file
```

---

## 11. Common errors (quick reference)

| Symptom | Cause | Fix |
|---------|-------|-----|
| `error: Microsoft Visual C++ 14.0 or greater is required` | `chroma-hnswlib` needs a C++ compiler | Install MS C++ Build Tools, or use the minimal install (§4a) if you don't need ChromaDB |
| `ollama: command not found` | Ollama installed but not in PATH | §6 |
| `Warning: could not connect to a running Ollama instance` | Ollama installed but service not started | Launch the Ollama app (Windows/macOS) or run `ollama serve` (Linux) |
| Ollama returns a missing-model error | Required model is not installed locally | Run `ollama pull <model-name-from-error>` |
| LLM smoke test appears to hang on first run | Ollama may be downloading/loading the model or using CPU-only inference | Confirm `ollama list`, wait for the first model load, or use the no-network smoke checks for basic setup |
| `python -c "..."` silently does nothing in Git Bash | Quoting / MSYS path conversion mangling | Use `python` interactive REPL or save the snippet to a `.py` file |
| `[ERROR] Status is unexpected: 403` from `get_body` | Target site blocks non-browser user-agents | Try a different test URL (e.g. bleepingcomputer.com, healthcaredive.com) |
| Prompt doesn't show `(.venv)` after activation | Venv command path wrong for your shell, or venv wasn't created | Re-check §3 table; recreate with `python -m venv .venv` |
