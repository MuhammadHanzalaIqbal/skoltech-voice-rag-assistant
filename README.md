# Skoltech Voice RAG Assistant

A voice-enabled conversational AI over the **Skoltech PhD Handbook**, built with retrieval-augmented generation (RAG), FAISS vector search, Groq LLM inference, and a browser-based speech interface. Built as a Skoltech Short Term Project under Prof. Andrey Somov, Department of IoT and Wireless Communication.

## Demo

Full voice-chat walkthrough on Google Drive:
**https://drive.google.com/file/d/1Q31O33xJwpGgMvn2uOP_6tk7PKSMIdWY/view?usp=sharing**

## What it does

A user speaks a question about Skoltech PhD policy. The system transcribes the audio, retrieves the most relevant chunks from a FAISS index built over the PhD Handbook, generates a grounded answer with a Groq-hosted LLM, refines and re-ranks with Cohere, then speaks the answer back. Every conversation is persisted to SQLite for later review and analysis.

## Architecture

```
User microphone
   |
   v
Browser (Web Audio API capture)
   |
   v
Speech-to-text (Web Speech API / server-side STT)
   |
   v
Query enhancement
   |
   v
FAISS retrieval over PhD Handbook index
   |
   v
Groq LLM (answer generation)
   |
   v
Cohere reranking + refinement
   |
   v
Text-to-speech (pyttsx3)
   |
   v
SQLite conversation log
```

## Stack

- **Backend**: Python, FastAPI, asyncio
- **Frontend**: HTML, JavaScript, Web Audio API
- **Speech-to-text**: Web Speech API (client side)
- **Text-to-speech**: pyttsx3
- **Embeddings**: HuggingFace sentence-transformers
- **Vector store**: FAISS (Facebook AI Similarity Search)
- **LLM inference**: Groq API (fast hosted Llama / Mixtral)
- **Reranking and refinement**: Cohere API
- **Persistence**: SQLite for conversation history
- **Document processing**: semantic chunking, hierarchical chunking, 10 to 20 percent chunk overlap, special handling for lists, tables, and FAQ blocks to keep retrieval grounded

## Repository layout

```
skoltech-voice-rag-assistant/
├── main.py                  # FastAPI backend, lifespan, RAG pipeline
├── requirements.txt         # Python dependencies
├── phd_handbook_index/      # Pre-built FAISS index over the PhD Handbook
│   ├── index.faiss
│   └── index.pkl
└── Front_end/
    ├── index.html           # Voice chat UI
    ├── script.js            # Mic capture, STT, sending to backend
    ├── styles.css           # UI styling
    └── images/              # UI assets
```

## Setup

1. Clone the repo

```bash
git clone https://github.com/MuhammadHanzalaIqbal/skoltech-voice-rag-assistant.git
cd skoltech-voice-rag-assistant
```

2. Create a virtual environment and install dependencies

```bash
python -m venv .venv
source .venv/bin/activate   # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

3. Set environment variables for API keys

Create a `.env` file in the project root with:

```
GROQ_API_KEY=your_groq_api_key_here
COHERE_API_KEY=your_cohere_api_key_here
```

Get free keys at:
- Groq: https://console.groq.com/keys
- Cohere: https://dashboard.cohere.com/api-keys

4. Start the backend

```bash
uvicorn main:app --reload --port 8000
```

5. Open the frontend

Open `Front_end/index.html` in a modern browser (Chrome recommended for the Web Speech API). Allow microphone access when prompted.

## Document processing

The PhD Handbook is preprocessed into the FAISS index using:

- Semantic chunking with 10 to 20 percent overlap between chunks
- Hierarchical chunking so headings, list items, and tables stay together
- Special handling for FAQ sections and tabular policies to preserve question-answer pairs

This grounding strategy is what keeps the assistant from making up policy text. If the retrieved chunks do not cover the user's question, the LLM is prompted to say so rather than guess.

## My contributions

This was a small collaborative team project. My specific contributions were:

- Speech recognition and speech synthesis pipeline: browser microphone capture, STT integration, pyttsx3 TTS playback
- Anti-hallucination work: tuning the chunking and prompting so responses stay grounded in actual handbook content
- Conversation persistence layer in SQLite for review and dataset building

## Supervisor and context

- **Supervisor**: Prof. Andrey Somov (Skoltech IoT Lab)
- **Course**: Short Term Project (STP), Department of IoT and Wireless Communication
- **Institution**: Skolkovo Institute of Science and Technology (Skoltech), Moscow

## Notes

- API keys are read from environment variables. Never hardcode keys in `main.py`. Use the `.env` file or your shell environment.
- The pre-built FAISS index in `phd_handbook_index/` is small (under 100 KB) because the embeddings are stored compactly. To rebuild from a different source document, regenerate `index.faiss` and `index.pkl` with sentence-transformers + FAISS.

## License

MIT

