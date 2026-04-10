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
OLLAMA_MODEL = "llama3.2"
TOP_K = 6 
#NOTE: may need to tweak TOP_K in the future


app = FastAPI(title="Healthcare GTP RAG")
_chain = None
_retriever = None


def get_chain():

    #set up a singleton to avoid re-creating the chain and retriever every time
    global _chain, _retriever
    if _chain:
        return _chain, _retriever

    #check for the vector store
    if not Path(CHROMA_DIR).exists():
        raise RuntimeError(
            "Vector store not found. Run: python src/ingest.py --file <path_to_json_file>.json"
        )

    #use the same embedding as ingest.py
    print("Loading embedding model…")
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBED_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )

    #opens a connection to the vector store
    print("Connecting to ChromaDB…")
    db = Chroma(
        persist_directory=CHROMA_DIR,
        embedding_function=embeddings,
        collection_name=COLLECTION,
    )

    #connecting to the LLM
    print(f"Connecting to LLM ({OLLAMA_MODEL})…")
    llm = OllamaLLM(model=OLLAMA_MODEL, temperature=0.1) 
    #NOTE: temperature needs to be tweaked in the future

    # NOTE: swap out eventually 
    prompt = PromptTemplate(
        input_variables=["context", "question"],
        template=
            """You are a healthcare intelligence analyst. Answer questions about healthcare \
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

            Answer:"""
    )

    #wraps a ChromaDB connection into LangChain retrievable object that returns K objects
    _retriever = db.as_retriever(search_kwargs={"k": TOP_K})

    #makes on the _retriever objects into strings to be used in the prompt
    def format_docs(docs):
        return "\n\n---\n\n".join(doc.page_content for doc in docs)

    #full RAG assembled into a single chain
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
    question: str

class SourceDoc(BaseModel):
    id: str
    title: str
    source_name: str
    direct_link: str

class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceDoc]
    model: str
    chunks_retrieved: int


# API endpoints
@app.get("/", response_class=HTMLResponse)
def serve_ui():
    html_path = Path(__file__).parent / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return HTMLResponse(content=html_path.read_text())

'''
Posts the question to the RAG chain and returns the answer and sources
'''
@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):

    #check the question is not empty
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    try:
        chain, retriever = get_chain() #grabs singletons form above
        source_docs = retriever.invoke(question) # returns raw retrieved documents
        answer = chain.invoke(question) # returns final answer string
        
    #handles when the vector db doesnt exist
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
        
    #handles everything else
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"RAG error: {str(e)}")

    sources = []
    for doc in source_docs:
        m = doc.metadata
        sources.append(SourceDoc(
            id = m.get("id", "—"),
            title = m.get("title", "—"),
            source_name = m.get("source_name", "—"),
            direct_link = m.get("direct_link", "—"),
        ))

    # returns the answer and metadata. This will be used later in the debugging phase
    return ChatResponse(
        answer = answer,
        sources = sources,
        model = OLLAMA_MODEL,
        chunks_retrieved = len(source_docs),
    )

'''
This a JSON object with the current state of the system. 
'''
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
    })