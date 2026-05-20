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
ollama pull gemma4:e4b
```

Keep the Ollama app or `ollama serve` running before commands that validate or
extract article fields.

## Local Checks

Format Python files before opening a pull request:

```bash
ruff format .
```

Check formatting without changing files:

```bash
ruff format --check .
```

Build the Sphinx documentation locally:

```bash
sphinx-build -b html -E docs docs/_build/html
```

The same docs build runs in GitHub Actions on pull requests. Pushes to `main`
also publish the built HTML to GitHub Pages.

## Pull Request Checklist

- Keep changes focused on one pipeline, scraper, app, or documentation concern.
- Update Sphinx docs when workflow commands, output paths, or public modules
  change.
- Keep generated data, ChromaDB files, and sensitive inputs out of git.
- Prefer small GDELT runs such as `--num-files 2 --limit 3` for smoke tests.
- Preserve source URLs and enough provenance to trace every generated record.
