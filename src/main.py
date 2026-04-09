"""
main.py — Healthcare Cybersecurity RAG — FastAPI Backend
---------------------------------------------------------
Uses only modern LangChain packages (no langchain_community):
  - langchain_huggingface  → embeddings
  - langchain_ollama       → local LLM
  - langchain_chroma       → vector store
  - langchain_core         → prompt + chain primitives
"""

from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_ollama import OllamaLLM
from langchain_chroma import Chroma
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough

# ── Config ────────────────────────────────────────────────────────────────────
CHROMA_DIR   = str(Path(__file__).parent.parent / "chroma_db")
COLLECTION   = "agentic_data"
EMBED_MODEL  = "all-MiniLM-L6-v2"
OLLAMA_MODEL = "llama3.2"
TOP_K        = 6
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="Healthcare GTP RAG")

_chain     = None
_retriever = None


def get_chain():
    global _chain, _retriever
    if _chain:
        return _chain, _retriever

    if not Path(CHROMA_DIR).exists():
        raise RuntimeError(
            "Vector store not found. Run: python src/ingest.py --file src/mock.json"
        )

    print("Loading embedding model…")
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBED_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )

    print("Connecting to ChromaDB…")
    db = Chroma(
        persist_directory=CHROMA_DIR,
        embedding_function=embeddings,
        collection_name=COLLECTION,
    )

    print(f"Connecting to Ollama ({OLLAMA_MODEL})…")
    llm = OllamaLLM(model=OLLAMA_MODEL, temperature=0.1)

    prompt = PromptTemplate(
        input_variables=["context", "question"],
        template=
"""You are a healthcare cybersecurity analyst. Your job is to answer questions about \
healthcare data breaches, ransomware attacks, and related incidents using ONLY the \
incident reports provided below.

Rules:
- Cite the specific hospital or organization name in every answer.
- Mention the threat actor (ransomware group) when it is known.
- Include the approximate number of individuals affected if stated.
- Note whether patient data (PHI/PII) was confirmed or suspected to be exfiltrated.
- If the provided records do not contain enough information to answer, say so clearly — do NOT invent facts.

--- INCIDENT REPORTS ---
{context}
--- END OF REPORTS ---

Question: {question}

Answer (be specific; cite organization names, threat actors, and data elements from the reports above):"""
    )

    _retriever = db.as_retriever(search_kwargs={"k": TOP_K})

    def format_docs(docs):
        return "\n\n---\n\n".join(doc.page_content for doc in docs)

    _chain = (
        {"context": _retriever | format_docs, "question": RunnablePassthrough()}
        | prompt
        | llm
        | StrOutputParser()
    )

    print("✅ RAG chain ready.\n")
    return _chain, _retriever


# ── Request / Response models ─────────────────────────────────────────────────

class ChatRequest(BaseModel):
    question: str

class SourceDoc(BaseModel):
    record_id: str
    url:       str
    snippet:   str

class ChatResponse(BaseModel):
    answer:           str
    sources:          list[SourceDoc]
    model:            str
    chunks_retrieved: int


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def serve_ui():
    html_path = Path(__file__).parent / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return HTMLResponse(content=html_path.read_text())


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    try:
        chain, retriever = get_chain()

        source_docs = retriever.invoke(question)
        answer      = chain.invoke(question)
        

    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"RAG error: {str(e)}")

    sources = []
    for doc in source_docs:
        m = doc.metadata
        raw_content = m.get("content", doc.page_content)
        sources.append(SourceDoc(
            record_id = m.get("id", "—"),
            url       = m.get("url", "—"),
            snippet   = raw_content[:300] + "…" if len(raw_content) > 300 else raw_content,
        ))

    return ChatResponse(
        answer           = answer,
        sources          = sources,
        model            = OLLAMA_MODEL,
        chunks_retrieved = len(source_docs),
    )


@app.get("/status")
def status():
    db_ready = Path(CHROMA_DIR).exists()
    count = 0
    if db_ready:
        try:
            emb = HuggingFaceEmbeddings(
                model_name=EMBED_MODEL,
                model_kwargs={"device": "cpu"},
                encode_kwargs={"normalize_embeddings": True},
            )
            db = Chroma(persist_directory=CHROMA_DIR,
                           embedding_function=emb,
                           collection_name=COLLECTION)
            count = db._collection.count()
        except Exception:
            pass

    return JSONResponse({
        "status":          "ok" if db_ready else "no_db",
        "db_ready":        db_ready,
        "records_indexed": count,
        "llm_model":       OLLAMA_MODEL,
        "embed_model":     EMBED_MODEL,
        "message": (
            "Ready to answer questions."
            if db_ready
            else "Run ingest.py first to build the vector store."
        ),
    })