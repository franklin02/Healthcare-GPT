# Developer Workflow

Use this page as the current contributor path for the code that is active in
this repository.

## Setup

Create a virtual environment from the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
```

Install documentation and formatting tools:

```bash
python -m pip install -r requirements-dev.txt
```

Install runtime dependencies based on what you are changing:

- GDELT-only work: `python -m pip install requests pandas beautifulsoup4 lxml`
- Full RAG, ingestion, scraper, or API work: `python -m pip install -r requirements.txt`

Ollama is required for LLM validation and extraction paths:

```bash
ollama list
```

Keep the Ollama app or `ollama serve` running before commands that validate or
extract article fields. Pull only the model needed for the path you are
testing: current shared validation uses `src.shared_utils.AI_MODEL`
(`llama3.2`), while the RAG app and Gemma experiment use `gemma4:e4b`.
Confirm the needed model appears in `ollama list` before relying on LLM-backed
validation results.

## Local Checks

Format Python files before opening a pull request:

```bash
ruff format .
```

Check formatting without changing files:

```bash
python -m ruff format --check .
```

Run lint checks:

```bash
python -m ruff check .
```

Run the test suite:

```bash
python -m pytest -q
```

Build the Sphinx documentation locally:

```bash
sphinx-build -b html -E docs docs/_build/html
```

For a quick no-network smoke test of the orchestrator and HTML scraper wiring:

```bash
python -m src.orchestrator --skip-gdelt --html-start-page 1 --html-page-cap 0 --verbose
```

Expected result: each configured HTML site reaches `page cap (0)`, and the
summary reports zero processed records and zero errors.

For an optional LLM-backed smoke test of field extraction, keep Ollama running
and confirm the configured model appears in `ollama list` first:

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

Expected result: `sector_data` contains only shared extraction fields, and
`subsector_data` contains only `cyber_attack` fields. Missing values should be
`None`/`null`, not boilerplate examples. Extraction uses subsector-specific
field guidance and only fills values directly supported by the article text; if
the local LLM request fails or times out, the fallback result is null-filled
field dictionaries.

GitHub Actions runs pytest, Ruff lint, and Ruff formatting checks on pull
requests to `main`. The same docs build also runs on pull requests. Pushes to
`main` publish the built HTML to GitHub Pages.

## Pull Request Checklist

- Keep changes focused on one pipeline, scraper, app, or documentation concern.
- Update Sphinx docs when workflow commands, output paths, or public modules
  change.
- Keep generated data, ChromaDB files, and sensitive inputs out of git.
- Prefer small GDELT runs such as `--num-files 2 --limit 3` for smoke tests.
- Preserve source URLs and enough provenance to trace every generated record.
