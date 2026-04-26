## Setup for Healthcare GPT

### 1. Prerequisites

- macOS (tested), Linux/WSL (untested but should work) 
- Python 3.12+ (repo uses 3.12.1 — check `.python-version`)
- Git
- Ollama installed

### 2. Clone & Environment Setup

Have ollama downloaded from `ollama.com`
Then run the following commands: 

```bash
git clone https://github.com/franklin02/Healthcare-GPT
cd Healthcare-GPT
```

```bash
python3 -m venv .venv && source .venv/bin/activate
```

WARNING: make sure you do the command below from your .venv environment. 
Check your terminal, should say 
`(.venv) your_name@your_system Healthcare-GPT %` 

```bash
pip install -r requirements.txt  
```

```bash
ollama pull llama3.2
```
If using BERT as a classifier download the model from google drive [here](https://drive.google.com/drive/folders/1rRqZBmgcjEHLE_fc5CecUGVGpzvDgCqm?usp=drive_link) and place it in /src/models

### 3. Environment Variables

- The only place where you need environment variables is when using `src/fda_apis/*.py'
- These may not be needed
- If you want to have this, make a .env file on the root with the following 2 variables
  - `FDA_SHORTAGE_API_KEY`
  - `FDA_SPL_API_KEY`
- These are free from [https://open.fda.gov/apis/drug/drugshortages/](https://open.fda.gov/apis/drug/drugshortages/) (FDA_SHORTAGE_API_KEY) and [https://open.fda.gov/apis/drug/label/](https://open.fda.gov/apis/drug/label/) (FDA_SPL_API_KEY)

### 4. Project Structure (updated file map)

```text
Healthcare-GPT/
├── src/
│   ├── main.py              # FastAPI backend + RAG chain
│   ├── ingest.py            # JSON → embeddings → ChromaDB
│   ├── index.html           # Chat UI
│   ├── scrapers/            # Web scrapers (CNN, AHA, scraper_engine, etc.)
│   ├── fda_apis/            # FDA API clients (SPL, shortage data)
│   ├── GDELT/               # Cybersecurity event analysis
│   ├── data/                # Processed data (Ready_for_RAG/, Noise/, Vulnerabilities/)
│   └── raw_data/            # Raw scraped/downloaded data
├── docs/                    # Project docs, prompts, agent instructions
├── chroma_db/               # Auto-generated vector store (git-ignored)
├── requirements.txt
├── .env                     # API keys (git-ignored)
└── SetUp.md                 # ← This file
```

### 5. Quick Start: Your First Query (end-to-end)

```bash
cd src
python ingest.py --file data/Ready_for_RAG/CyberScoop.json
uvicorn main:app --reload

```

- Open [http://127.0.0.1:8000](http://127.0.0.1:8000)
- Ask: "What cyberattacks have targeted hospitals?" (or any question)

### 6. Ingestion Pipeline

- JSON format SHOULD follow the schema under src/data/schema.json, but as fall back it will ingest { "sources": [ { ... }, ... ] }
- Document the full schema (id, title, source_name, direct_link, 
subsector, content, exec_summary, subsector_data, etc.)
- --file flag (required), --new_db flag (optional, wipes DB)
- Default behavior: additive (deduplicates by id)

### 7. API Referencee (src/main.py)

- GET /              → Chat UI
- POST /chat    → { question: string } → { answer, sources, model, chunks_retrieved }
- GET /status   → System health check
- Updated source doc shape: { id, title, source_name, direct_link }

### Model training:

If training a new classifier version please grab datasets from the google drive [here](https://drive.google.com/drive/folders/16-yWzqTwKiQK7ah7ya4eLd8729sw7iUl?usp=drive_link)