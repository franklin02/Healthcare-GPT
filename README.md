# FDA Drug Shortage Intelligence — RAG Prototype

> **What this is:** A fully local, offline AI system that lets you query the FDA drug shortage database using plain English. You type a question like _"which oncology drugs are currently unavailable?"_ and the system searches through 1,695 FDA records and gives you a grounded, cited answer — no hallucinations, no cloud, no cost per query.

---

## Table of Contents

1. [How It Works](#how-it-works)
2. [Prerequisites](#prerequisites)
3. [Step-by-Step Setup](#step-by-step-setup)
4. [Running the App](#running-the-app)
5. [Using the Chat Interface](#using-the-chat-interface)
6. [Troubleshooting](#troubleshooting)
7. [Project File Reference](#project-file-reference)
8. [Libraries & Technologies Used](#libraries--technologies-used)
9. [Why RAG vs. Plain ChatGPT](#why-rag-vs-plain-chatgpt)
10. [Roadmap](#roadmap)

---

## How It Works

This system uses a technique called **RAG (Retrieval-Augmented Generation)**. Here's the flow:

```
FDA JSON File (1,695 records)
         │
         ▼
┌─────────────────────┐
│   1. INGESTION      │  ingest.py reads every record and converts
│   (run once)        │  it into readable text chunks
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│   2. EMBEDDING      │  Each chunk is converted into a vector
│                     │  (a list of numbers capturing its meaning)
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│   3. VECTOR STORE   │  All vectors are saved locally in ChromaDB
│   (chroma_db/)      │  — a searchable database of meaning
└────────┬────────────┘
         │
   [ At query time ]
         │
         ▼
┌─────────────────────┐
│   4. RETRIEVAL      │  User question → embed → find top 6
│                     │  most semantically similar records
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│   5. GENERATION     │  The 6 records + question are sent to
│                     │  Llama 3 (running locally via Ollama)
└────────┬────────────┘
         │
         ▼
    Answer in browser
    (with source records shown)
```

**Key insight:** The LLM never "guesses" — it can only answer from the actual records retrieved. Every answer is traceable to real FDA data.

---

## Prerequisites

You need four things installed on your Mac before starting. Do these in order.

### 1. Python 3.10 or higher

Check if you have it:

```bash
python3 --version
```

If the version shown is below 3.10, download the latest from **https://www.python.org/downloads/**

### 2. Git

Check if you have it:

```bash
git --version
```

If not, macOS will prompt you to install it automatically when you run that command. Follow the prompt.

### 3. Ollama (local LLM runner)

Go to **https://ollama.com** and click **Download for Mac**. Install it like any normal macOS app (drag to Applications). Once installed, it runs silently in your menu bar.

### 4. The Llama 3 language model

After installing Ollama, open Terminal and run:

```bash
ollama pull llama3.2
```

> ⚠️ This downloads ~2 GB. Make sure you're on WiFi, not a hotspot. It only happens once — it's cached permanently after that.

---

## Step-by-Step Setup

### Step 1 — Get the project on your machine

```bash
git clone https://github.com/edgar-damian/rag_fda_prototype
cd rag_fda_prototype
```

Or if you downloaded a ZIP: unzip it, then open Terminal and `cd` into the folder.

> 💡 **Tip:** In Finder, drag the project folder directly onto the Terminal icon in your Dock — it will `cd` into it automatically.

### Step 2 — Create a virtual environment

A virtual environment (venv) is an isolated Python sandbox just for this project. It prevents package conflicts with anything else on your Mac.

```bash
python3 -m venv .venv
```

This creates a hidden `.venv/` folder in your project directory.

### Step 3 — Activate the virtual environment

```bash
source .venv/bin/activate
```

Your terminal prompt will now show `(.venv)` at the start, like this:

```
(.venv) yourusername@YourMac rag_fda_prototype %
```

> ⚠️ **You must run this activation command every time you open a new Terminal window** to work on this project. If you ever see `ModuleNotFoundError`, this is almost always why — just reactivate and try again.

To deactivate when you're done working: type `deactivate`.

### Step 4 — Install Python dependencies

With your venv active, run both commands:

```bash
pip install -r requirements.txt
pip install -U langchain-core langchain langchain-huggingface langchain-ollama
```

> This takes 2–5 minutes on first run. You'll see a lot of output — that's normal.
> If you see "dependency conflict" warnings mentioning `langchain-community` — **ignore them**. That package is not used in this project.

### Step 5 — Add the FDA JSON file to the project folder

Place the FDA drug shortage JSON file in the root of the project folder (same level as `main.py`). The expected filename is:

```
drug-shortages-0001-of-0001.json
```

If your file has a different name, just note it — you'll use it in the next step.

### Step 6 — Run the ingestion script

This one-time step reads the JSON and builds the local vector database:

```bash
python ingest.py --file drug-shortages-0001-of-0001.json
```

Replace the filename if yours is different. You should see output like this:

```
[1/4] Loading JSON...         → 1,695 records loaded
[2/4] Converting records...   → 1,695 documents created
[3/4] Loading embedding model...
[4/4] Building vector store → ./chroma_db
      Embedding records 1–500 of 1695…
      Embedding records 501–1000 of 1695…
      Embedding records 1001–1500 of 1695…
      Embedding records 1501–1695 of 1695…

✅ Ingestion complete! 1,695 records indexed in ./chroma_db
```

> A `chroma_db/` folder will appear in your project directory. This is your local vector database. **Do not delete it.**

> `Failed to send telemetry event` messages are harmless — ChromaDB's analytics trying and failing to phone home. They don't affect anything.

> **Re-ingestion:** If the FDA JSON file is ever updated with new data, just re-run this command. It will rebuild the index automatically.

---

## Running the App

Every time you want to use the app, you need the FastAPI server running. Ollama runs automatically in the background after install.

### Check Ollama is running

```bash
curl http://localhost:11434
```

You should see: `Ollama is running`

If you see an error, open the Ollama app from your Applications folder and wait a few seconds.

> ⚠️ Do **NOT** run `ollama serve` manually — the macOS app already does this. Running it again will cause an "address already in use" error, which is harmless but confusing.

### Start the server

In your terminal (with `.venv` activated):

```bash
uvicorn main:app --reload
```

You should see:

```
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Application startup complete.
```

### Open the chat UI

Open your browser and go to:

```
http://localhost:8000
```

> The **first question** you ask will take 10–20 seconds while Llama 3 loads into RAM. Every question after that will be faster (5–10 seconds).

To stop the server: press `Ctrl + C` in the terminal.

---

## Using the Chat Interface

The UI shows:

- A chat window where you type your question
- The AI's answer, grounded in real FDA records
- A **Retrieved Records** table showing exactly which FDA entries were used to generate the answer

**Example questions to try:**

- `Which drugs are currently in shortage?`
- `What are the most common reasons for drug shortages?`
- `Are there any oncology drugs affected by shortages?`
- `Which manufacturers have the most shortage entries?`
- `What is the status of Lorazepam?`
- `Show me neurology drugs that are unavailable`
- `Which drugs are being discontinued?`
- `Are there any psychiatric medications with limited availability?`

---

## Troubleshooting

| Symptom                                       | Cause                                     | Fix                                                  |
| --------------------------------------------- | ----------------------------------------- | ---------------------------------------------------- |
| `ModuleNotFoundError`                         | venv not activated                        | Run `source .venv/bin/activate`                      |
| `Vector store not found`                      | ingest.py not run yet                     | Run `python ingest.py --file <yourfile>.json`        |
| `model 'llama3' not found (404)`              | Model not downloaded                      | Run `ollama pull llama3`                             |
| `address already in use` on port 11434        | Ollama app already running                | This is fine — do NOT run `ollama serve`             |
| Can't reach `localhost:8000`                  | uvicorn not running                       | Run `uvicorn main:app --reload`                      |
| First query is very slow / times out          | Llama 3 loading into RAM                  | Wait 20–30 sec and try again                         |
| `dependency conflict` warnings from pip       | Old `langchain-community` package present | Safe to ignore — it is not used                      |
| `Failed to send telemetry event`              | ChromaDB analytics                        | Harmless — ignore completely                         |
| Answers seem slow                             | Large model on CPU                        | Normal — Llama 3 on CPU takes 5–15s per query        |
| Answer says "I don't have enough information" | Question doesn't match any records well   | Try rephrasing with a specific drug name or category |

---

## Project File Reference

```
rag_fda_prototype/
│
├── main.py                          ← FastAPI server + RAG chain
│                                      Endpoints:
│                                        GET  /        → serves the chat UI
│                                        POST /chat    → accepts question, returns answer
│                                        GET  /status  → health check + record count
│
├── ingest.py                        ← One-time ingestion pipeline
│                                      Reads JSON → converts to text → embeds → saves to ChromaDB
│
├── index.html                       ← Browser chat interface
│                                      Dark clinical UI, served by FastAPI at localhost:8000
│                                      Shows answers + source record table
│
├── requirements.txt                 ← Python package list for pip install
│
├── README.md                        ← This file
│
├── drug-shortages-0001-of-0001.json ← FDA source data (you provide this file)
│
└── chroma_db/                       ← Auto-generated local vector database
                                       Created by ingest.py — do not edit or delete manually
```

---

## Libraries & Technologies Used

### AI / Language Model

| Tool                      | Purpose                                                                                                                                                                                                                    | Link                              |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------- |
| **Ollama**                | Runs Llama 3 locally on your Mac. No API key, no cloud, no cost per query. Manages model downloads and serves them via a local API on port 11434.                                                                          | https://ollama.com                |
| **Llama 3 (8B)**          | The language model by Meta AI that reads the retrieved records and generates natural language answers. 8 billion parameters, runs on CPU with ~8 GB RAM.                                                                   | https://ollama.com/library/llama3 |
| **sentence-transformers** | Converts text into semantic embedding vectors using the `all-MiniLM-L6-v2` model. This is what makes similarity search possible — it turns words into numbers that capture meaning. ~90 MB download, runs entirely on CPU. | https://www.sbert.net             |

### RAG Orchestration

| Library                   | Purpose                                                                                                                                                                                                                   | Link                                                  |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- |
| **LangChain**             | The orchestration framework that wires everything together. Defines the chain: retriever → prompt → LLM → output parser. Think of it as the "glue" between all the components.                                            | https://python.langchain.com                          |
| **langchain-core**        | Core building blocks used in the chain: `PromptTemplate` (structures the question + records into a prompt), `StrOutputParser` (parses the LLM's response), `RunnablePassthrough` (passes the question through the chain). | https://python.langchain.com/docs/concepts            |
| **langchain-huggingface** | LangChain's modern integration for HuggingFace embedding models. Wraps `sentence-transformers` so it works natively inside LangChain chains.                                                                              | https://github.com/langchain-ai/langchain-huggingface |
| **langchain-ollama**      | LangChain's modern integration for Ollama. Provides `OllamaLLM`, which connects LangChain to the locally running Llama 3 model.                                                                                           | https://github.com/langchain-ai/langchain-ollama      |
| **langchain-chroma**      | LangChain's integration for ChromaDB. Allows ChromaDB to be used as a LangChain retriever — takes a question, returns the most relevant document chunks.                                                                  | https://github.com/langchain-ai/langchain-chroma      |

### Vector Database

| Library      | Purpose                                                                                                                                                                                                             | Link                      |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------- |
| **ChromaDB** | Local, file-based vector database. Stores all 1,695 FDA record embeddings on disk in the `chroma_db/` folder. At query time, performs fast similarity search to find the most relevant records. No server required. | https://www.trychroma.com |

### Web Backend & API

| Library      | Purpose                                                                                                                                | Link                         |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------- |
| **FastAPI**  | Python web framework. Serves the HTML chat interface and exposes the `/chat` REST endpoint that the UI calls when you send a question. | https://fastapi.tiangolo.com |
| **Uvicorn**  | ASGI web server that runs the FastAPI application. Started with `uvicorn main:app --reload`.                                           | https://www.uvicorn.org      |
| **Pydantic** | Data validation library (comes with FastAPI). Validates and structures the incoming chat requests and outgoing responses.              | https://docs.pydantic.dev    |

### Data Source

| Source                             | Description                                                                                                                                                                                                                           | Link                                      |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------- |
| **FDA openFDA Drug Shortages API** | Public FDA database of active and historical drug shortages in the United States. Updated by the FDA regularly. Contains drug name, manufacturer, shortage reason, availability status, and timeline. 1,695 records as of March 2026. | https://open.fda.gov/apis/drug/shortages/ |

---

## Why RAG vs. Plain ChatGPT

This prototype exists to demonstrate a critical limitation of general-purpose AI for healthcare intelligence work:

| Capability                       | ChatGPT / Generic LLM               | This RAG System                 |
| -------------------------------- | ----------------------------------- | ------------------------------- |
| Knows your private/internal data | ❌ Never                            | ✅ Always                       |
| Uses current FDA shortage data   | ❌ Frozen at training cutoff        | ✅ As fresh as your last ingest |
| Cites exact source records       | ❌ No traceability                  | ✅ Shows every record used      |
| Runs fully offline               | ❌ Requires internet + API key      | ✅ 100% local                   |
| Data leaves your network         | ✅ Sent to OpenAI/Anthropic servers | ❌ Never leaves your machine    |
| Cost per query                   | ~$0.01–0.05 at scale                | $0.00                           |
| Answers can be audited           | ❌ Black box                        | ✅ Full source transparency     |
| Can be updated with new data     | ❌ Requires model retraining        | ✅ Re-run ingest.py             |

A general LLM cannot tell you the current status of a specific drug shortage, which manufacturers are affected this month, or what the recovery timeline looks like — because it simply doesn't have that data. This system does, and it can be updated the moment the FDA publishes new records.

---

## Roadmap

This prototype demonstrates Phase 1 of a larger health sector intelligence platform:

- ✅ **Phase 1 — FDA Drug Shortage Query** ← _You are here_
  Local RAG over FDA JSON with natural language browser interface

- 🔲 **Phase 2 — Automated Data Pipeline**
  Scheduled scraper to pull fresh FDA shortage data daily/weekly and re-ingest automatically — removing the manual download step

- 🔲 **Phase 3 — Multi-Source Expansion**
  Add additional health sector data sources: FDA device recalls, MedWatch adverse event reports, hospital cybersecurity incident feeds, and pharmaceutical supply chain disruption data

- 🔲 **Phase 4 — Risk Scoring Engine**
  Cross-reference shortage data with drug criticality scores, available therapeutic alternatives, and patient impact metrics to surface the highest-risk situations automatically

- 🔲 **Phase 5 — Analyst Dashboard**
  Visual dashboard with shortage trends over time, manufacturer risk profiles, therapeutic category heatmaps, and automated alerts when new critical shortages are posted

---

_Built with LangChain · ChromaDB · Ollama · Llama 3 · FastAPI · FDA openFDA API_
