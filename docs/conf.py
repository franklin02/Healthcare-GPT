"""Sphinx configuration for CORVID documentation."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(SRC_DIR))
sys.path.insert(0, str(SRC_DIR / "GDELT"))

project = "CORVID"
author = "CORVID Contributors"
release = "0.1.0"

extensions = [
    "myst_parser",
    "sphinx_wagtail_theme",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}
master_doc = "index"

exclude_patterns = [
    "_build",
    "Thumbs.db",
    ".DS_Store",
    "expanded-disruption-source-ecosystem.md",
    "meeting-questions.md",
    "prompt-pack-v0/**",
]

html_theme = "sphinx_wagtail_theme"
html_theme_options = {
    "project_name": "CORVID",
}


autodoc_member_order = "bysource"
autodoc_typehints = "description"
suppress_warnings = [
    "myst.header",
    "toc.not_included",
]
autodoc_mock_imports = [
    "bs4",
    "chromadb",
    "fastapi",
    "langchain_chroma",
    "langchain_core",
    "langchain_huggingface",
    "langchain_ollama",
    "langchain_text_splitters",
    "lxml",
    "pandas",
    "pdfplumber",
    "pydantic",
    "requests",
    "sentence_transformers",
    "transformers",
    "tqdm",
]
