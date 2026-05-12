"""FastAPI RAG server for querying healthcare disruption incident reports.

This module provides a REST API that answers questions about healthcare
crises (cyberattacks, supply shortages, natural disasters, staffing issues)
using a retrieval-augmented generation (RAG) pipeline backed by ChromaDB
and Ollama LLM.

Endpoints:
    GET / : Serve the web UI (index.html).
    POST /chat : Ask a question and get an answer with source citations.
    GET /status : Check system readiness and database statistics.

The RAG chain uses HuggingFace embeddings for semantic search and Ollama
for generating answers from retrieved incident reports.
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


CHROMA_DIR = str(Path(__file__).parent.parent / "chroma_db")
COLLECTION = "agentic_data"
EMBED_MODEL = "all-MiniLM-L6-v2"
OLLAMA_MODEL = "llama3.2"  # "deepseek-r1:8b"
TOP_K = 6
# NOTE: may need to tweak TOP_K in the future


app = FastAPI(title="Healthcare GTP RAG")
_chain = None
_retriever = None


def get_chain():
    """Initialize and return the RAG chain and retriever (singleton pattern).

    Lazily initializes the embedding model, ChromaDB connection, and LLM
    on first call, then caches them as module globals to avoid re-creation.
    Subsequent calls return the cached instances.

    Returns:
        tuple: (chain, retriever) where chain is a LangChain runnable that
            takes a question and returns an answer, and retriever is a
            LangChain retriever that fetches the top-K similar documents.

    Raises:
        RuntimeError: If ChromaDB has not been initialized (run ingest.py first).
    """

    # set up a singleton to avoid re-creating the chain and retriever every time
    global _chain, _retriever
    if _chain:
        return _chain, _retriever

    # check for the vector store
    if not Path(CHROMA_DIR).exists():
        raise RuntimeError(
            "Vector store not found. Run: python src/ingest.py --file <path_to_json_file>.json"
        )

    # use the same embedding as ingest.py
    print("Loading embedding model…")
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBED_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )

    # opens a connection to the vector store
    print("Connecting to ChromaDB…")
    db = Chroma(
        persist_directory=CHROMA_DIR,
        embedding_function=embeddings,
        collection_name=COLLECTION,
    )

    # connecting to the LLM
    print(f"Connecting to LLM ({OLLAMA_MODEL})…")
    llm = OllamaLLM(model=OLLAMA_MODEL, temperature=0.1)
    # NOTE: temperature needs to be tweaked in the future

    # NOTE: swap out eventually
    prompt = PromptTemplate(
        input_variables=["context", "question"],
        template="""You are a healthcare intelligence analyst. Answer questions about healthcare \
            disruptions — including cyberattacks, medical device shortages, natural disasters, \
            and staffing crises — using ONLY the incident reports below.

            Rules:
            - Always cite the source name and subsector of the incident.
            - Include key figures (people affected, beds offline, ransom demanded, etc.) when stated.
            - If the records do not contain enough information, say so — do NOT invent facts.

            --- INCIDENT REPORTS ---
            {context}
            --- END OF REPORTS ---

            Question: {question}

            Answer:""",
    )

    # wraps a ChromaDB connection into LangChain retrievable object that returns K objects
    _retriever = db.as_retriever(search_kwargs={"k": TOP_K})

    # makes on the _retriever objects into strings to be used in the prompt
    def format_docs(docs):
        return "\n\n---\n\n".join(doc.page_content for doc in docs)

    # full RAG assembled into a single chain
    _chain = (
        {"context": _retriever | format_docs, "question": RunnablePassthrough()}
        | prompt
        | llm
        | StrOutputParser()
    )

    print("RAG chain ready.\n")
    return _chain, _retriever


# req/res models for the chat endpoint
class ChatRequest(BaseModel):
    """Request model for the /chat endpoint.

    Attributes:
        question (str): The user's question about healthcare disruptions.
    """
    question: str


class SourceDoc(BaseModel):
    """Metadata for a source document retrieved by the RAG pipeline.

    Attributes:
        id (str): Unique identifier for the document.
        title (str): Article title.
        source_name (str): Source news outlet or publication.
        direct_link (str): URL to the original article.
    """
    id: str
    title: str
    source_name: str
    direct_link: str


class ChatResponse(BaseModel):
    """Response model for the /chat endpoint.

    Attributes:
        answer (str): The LLM's generated answer to the question.
        sources (list[SourceDoc]): Metadata for retrieved source documents.
        model (str): Name of the LLM used (e.g., "llama3.2").
        chunks_retrieved (int): Number of document chunks returned by the retriever.
    """
    answer: str
    sources: list[SourceDoc]
    model: str
    chunks_retrieved: int


# API endpoints
@app.get("/", response_class=HTMLResponse)
def serve_ui():
    """Serve the web UI (index.html) at the root path.

    Returns:
        HTMLResponse: The contents of index.html.

    Raises:
        HTTPException: 404 if index.html is not found.
    """
    html_path = Path(__file__).parent / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return HTMLResponse(content=html_path.read_text())


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    """Retrieve relevant incident reports and generate an answer via RAG.

    Uses the RAG chain to retrieve the top-K most relevant documents from
    ChromaDB and feed them to the LLM with a specialized healthcare
    disruption prompt. Returns the LLM's answer and source metadata.

    Args:
        req (ChatRequest): Request containing a `question` string.

    Returns:
        ChatResponse: The answer, retrieved sources, model name, and chunk count.

    Raises:
        HTTPException: 400 if question is empty; 503 if ChromaDB not initialized;
            500 for other RAG errors.
    """

    # check the question is not empty
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    try:
        chain, retriever = get_chain()  # grabs singletons form above
        source_docs = retriever.invoke(question)  # returns raw retrieved documents
        answer = chain.invoke(question)  # returns final answer string

    # handles when the vector db doesnt exist
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    # handles everything else
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"RAG error: {str(e)}")

    sources = []
    for doc in source_docs:
        m = doc.metadata
        sources.append(
            SourceDoc(
                id=m.get("id", "—"),
                title=m.get("title", "—"),
                source_name=m.get("source_name", "—"),
                direct_link=m.get("direct_link", "—"),
            )
        )

    # returns the answer and metadata. This will be used later in the debugging phase
    return ChatResponse(
        answer=answer,
        sources=sources,
        model=OLLAMA_MODEL,
        chunks_retrieved=len(source_docs),
    )


@app.get("/status")
def status():
    """Return system readiness status and database statistics.

    Checks if ChromaDB has been initialized and counts the number of
    indexed documents. Returns configuration and availability information.

    Returns:
        JSONResponse: A JSON object with keys:
            - status (str): "ok" if DB is ready, "no_db" otherwise.
            - db_ready (bool): Whether ChromaDB exists.
            - records_indexed (int): Number of documents in the collection.
            - llm_model (str): Name of the LLM in use.
            - embed_model (str): Name of the embedding model in use.
            - message (str): Human-readable status message.
    """
    db_ready = Path(CHROMA_DIR).exists()
    count = 0
    if db_ready:
        try:
            emb = HuggingFaceEmbeddings(
                model_name=EMBED_MODEL,
                model_kwargs={"device": "cpu"},
                encode_kwargs={"normalize_embeddings": True},
            )
            db = Chroma(
                persist_directory=CHROMA_DIR,
                embedding_function=emb,
                collection_name=COLLECTION,
            )
            count = db._collection.count()
        except Exception:
            pass

    return JSONResponse(
        {
            "status": "ok" if db_ready else "no_db",
            "db_ready": db_ready,
            "records_indexed": count,
            "llm_model": OLLAMA_MODEL,
            "embed_model": EMBED_MODEL,
            "message": (
                "Ready to answer questions."
                if db_ready
                else "Run ingest.py first to build the vector store."
            ),
        }
    )
