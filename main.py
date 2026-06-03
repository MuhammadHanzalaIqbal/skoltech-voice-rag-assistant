from fastapi import FastAPI, HTTPException, BackgroundTasks, File, UploadFile
from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Any
from uuid import UUID, uuid4
import uvicorn
import asyncio
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import logging
from contextlib import asynccontextmanager
from langchain.embeddings import HuggingFaceEmbeddings
from langchain.vectorstores import FAISS
import cohere
import os
from json import dumps, loads
from groq import Groq  # New import for Groq
import pyttsx3
import speech_recognition as sr
import tempfile
import io
import base64
import sqlite3
import datetime
import pathlib

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Models
class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    max_sources: Optional[int] = Field(default=5, ge=1, le=30)
    chat_id: Optional[str] = None
    is_initialization: Optional[bool] = False
    is_sync: Optional[bool] = False

class SourceScore(BaseModel):
    cohere: float
    similarity: float
    combined: float

class Source(BaseModel):
    id: UUID
    source: str
    content: str
    scores: SourceScore

class QueryResponse(BaseModel):
    query: str
    response: str
    query_id: UUID
    sources: List[Source]

class SourceRequest(BaseModel):
    query_id: UUID
    source_id: UUID

class TextRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=1000)
    chat_id: Optional[str] = None
    unique_id: Optional[str] = None

class QueryRefinementResponse(BaseModel):
    original_query: str
    refined_query: str

class SyncMessage(BaseModel):
    chat_id: str
    message_type: str
    content: str
    mime_type: Optional[str] = None
    timestamp: str

class SyncRequest(BaseModel):
    chat_id: str
    messages: List[SyncMessage]

# Database Models
class MessageDB(BaseModel):
    id: str
    chat_id: str
    message_type: str  # 'text_input', 'text_output', 'voice_input', 'voice_output', 'transcription', 'refined_query'
    content: str  # Text content or base64 encoded audio
    mime_type: Optional[str] = None  # For audio files
    endpoint: str  # Which API endpoint was called
    timestamp: str

# Database setup function
def setup_database():
    db_path = 'sq_db.sqlite'
    db_exists = os.path.exists(db_path)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Check if the messages table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
    messages_table_exists = cursor.fetchone() is not None
    
    # Check if the chats table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chats'")
    chats_table_exists = cursor.fetchone() is not None
    
    if not db_exists or not messages_table_exists or not chats_table_exists:
        logger.info("Creating new SQLite database tables")
        
        # Drop the tables if they exist but are incomplete
        if messages_table_exists:
            cursor.execute("DROP TABLE messages")
        if chats_table_exists:
            cursor.execute("DROP TABLE chats")
        
        # Create messages table
        cursor.execute('''
        CREATE TABLE messages (
            id TEXT PRIMARY KEY,
            chat_id TEXT NOT NULL,
            message_type TEXT NOT NULL,
            content TEXT NOT NULL,
            mime_type TEXT,
            endpoint TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
        ''')
        
        # Create chats table
        cursor.execute('''
        CREATE TABLE chats (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        ''')
        
        conn.commit()
        logger.info("Database tables created successfully")
    else:
        logger.info("Using existing SQLite database with messages and chats tables")
    
    conn.close()
    return db_path

# Database operations
class DatabaseManager:
    def __init__(self, db_path):
        self.db_path = db_path
        # Ensure the database directory exists
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    
    def get_connection(self):
        try:
            return sqlite3.connect(self.db_path)
        except sqlite3.Error as e:
            logger.error(f"Error connecting to database: {e}")
            # Try to recreate the database
            setup_database()
            return sqlite3.connect(self.db_path)
    
    async def save_message(self, message: MessageDB) -> bool:
        try:
            logger.info(f"Attempting to save message: id={message.id}, chat_id={message.chat_id}, type={message.message_type}")
            
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Check if the messages table exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
            if cursor.fetchone() is None:
                logger.info("Messages table not found, creating it now")
                cursor.execute('''
                CREATE TABLE messages (
                    id TEXT PRIMARY KEY,
                    chat_id TEXT,
                    message_type TEXT,
                    content TEXT,
                    mime_type TEXT,
                    endpoint TEXT,
                    timestamp TEXT
                )
                ''')
                conn.commit()
            
            # Insert the message
            cursor.execute(
                '''
                INSERT INTO messages (id, chat_id, message_type, content, mime_type, endpoint, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    message.id,
                    message.chat_id,
                    message.message_type,
                    message.content,
                    message.mime_type,
                    message.endpoint,
                    message.timestamp
                )
            )
            
            conn.commit()
            conn.close()
            
            logger.info(f"Successfully saved message {message.id} for chat {message.chat_id}")
            
            # Verify the message was saved by retrieving it
            verification_conn = self.get_connection()
            verification_cursor = verification_conn.cursor()
            verification_cursor.execute(
                "SELECT id FROM messages WHERE id = ?",
                (message.id,)
            )
            result = verification_cursor.fetchone()
            verification_conn.close()
            
            if result:
                logger.info(f"Verified message {message.id} was saved successfully")
                return True
            else:
                logger.warning(f"Message {message.id} was not found after saving")
                return False
            
        except Exception as e:
            logger.error(f"Database error saving message: {e}", exc_info=True)
            return False
    
    async def get_chat_messages(self, chat_id: str):
        try:
            logger.info(f"Getting messages for chat {chat_id}")
            
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Check if the messages table exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
            if cursor.fetchone() is None:
                logger.warning("Messages table not found when trying to get chat messages")
                return []
            
            # Log the total number of messages in the database
            cursor.execute("SELECT COUNT(*) FROM messages")
            total_count = cursor.fetchone()[0]
            logger.info(f"Total messages in database: {total_count}")
            
            # Log the number of messages for this chat
            cursor.execute("SELECT COUNT(*) FROM messages WHERE chat_id = ?", (chat_id,))
            chat_count = cursor.fetchone()[0]
            logger.info(f"Messages for chat {chat_id} in database: {chat_count}")
            
            # Specifically check for voice_input messages
            cursor.execute(
                "SELECT COUNT(*) FROM messages WHERE chat_id = ? AND message_type = 'voice_input'", 
                (chat_id,)
            )
            voice_input_count = cursor.fetchone()[0]
            logger.info(f"Chat {chat_id} has {voice_input_count} voice_input messages")
            
            if voice_input_count > 0:
                # Get details of voice_input messages
                cursor.execute(
                    "SELECT id, timestamp FROM messages WHERE chat_id = ? AND message_type = 'voice_input'",
                    (chat_id,)
                )
                voice_inputs = cursor.fetchall()
                for vi in voice_inputs:
                    logger.info(f"Voice input message found: id={vi[0]}, timestamp={vi[1]}")
            
            # Log message types in this chat for debugging
            cursor.execute(
                '''
                SELECT message_type, COUNT(*) 
                FROM messages 
                WHERE chat_id = ? 
                GROUP BY message_type
                ''',
                (chat_id,)
            )
            type_counts = cursor.fetchall()
            for msg_type, count in type_counts:
                logger.info(f"Chat {chat_id} has {count} messages of type '{msg_type}'")
            
            # Get all messages for this chat
            cursor.execute(
                '''
                SELECT id, chat_id, message_type, content, mime_type, endpoint, timestamp
                FROM messages
                WHERE chat_id = ?
                ORDER BY timestamp ASC
                ''',
                (chat_id,)
            )
            
            rows = cursor.fetchall()
            
            # Double-check if voice_input messages are in the result
            voice_input_in_result = False
            for row in rows:
                if row[2] == 'voice_input':
                    voice_input_in_result = True
                    logger.info(f"Voice input message included in result: id={row[0]}")
            
            if voice_input_count > 0 and not voice_input_in_result:
                logger.error(f"Voice input messages exist but were not included in the result!")
                
                # Try a direct query for voice_input messages
                cursor.execute(
                    '''
                    SELECT id, chat_id, message_type, content, mime_type, endpoint, timestamp
                    FROM messages
                    WHERE chat_id = ? AND message_type = 'voice_input'
                    ''',
                    (chat_id,)
                )
                voice_input_rows = cursor.fetchall()
                logger.info(f"Direct query found {len(voice_input_rows)} voice_input messages")
                
                # Add these to the result if they're not already there
                if voice_input_rows:
                    # Check if these IDs are already in rows
                    existing_ids = set(row[0] for row in rows)
                    for vi_row in voice_input_rows:
                        if vi_row[0] not in existing_ids:
                            logger.info(f"Adding missing voice_input message: {vi_row[0]}")
                            rows.append(vi_row)
            
            conn.close()
            
            logger.info(f"Retrieved {len(rows)} messages for chat {chat_id}")
            
            messages = []
            for row in rows:
                message = MessageDB(
                    id=row[0],
                    chat_id=row[1],
                    message_type=row[2],
                    content=row[3],
                    mime_type=row[4],
                    endpoint=row[5],
                    timestamp=row[6]
                )
                messages.append(message)
                logger.info(f"Retrieved message: id={message.id}, type={message.message_type}, content_preview={message.content[:50] if message.content else 'None'}")
            
            # Final check of message types being returned
            message_types = [msg.message_type for msg in messages]
            logger.info(f"Final message types being returned: {message_types}")
            
            return messages
        except Exception as e:
            logger.error(f"Database error getting chat messages: {e}", exc_info=True)
            return []
    
    async def save_chat(self, chat_id: str, title: str) -> bool:
        try:
            logger.info(f"Saving chat: id={chat_id}, title={title}")
            
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Check if the chats table exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chats'")
            if cursor.fetchone() is None:
                logger.info("Chats table not found, creating it now")
                cursor.execute('''
                CREATE TABLE chats (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                ''')
                conn.commit()
            
            # Check if the chat already exists
            cursor.execute("SELECT id FROM chats WHERE id = ?", (chat_id,))
            chat_exists = cursor.fetchone() is not None
            
            now = datetime.datetime.now().isoformat()
            
            if chat_exists:
                # Update the existing chat
                cursor.execute(
                    '''
                    UPDATE chats 
                    SET title = ?, updated_at = ?
                    WHERE id = ?
                    ''',
                    (title, now, chat_id)
                )
            else:
                # Insert a new chat
                cursor.execute(
                    '''
                    INSERT INTO chats (id, title, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    ''',
                    (chat_id, title, now, now)
                )
            
            conn.commit()
            conn.close()
            
            logger.info(f"Successfully saved chat {chat_id}")
            return True
            
        except Exception as e:
            logger.error(f"Database error saving chat: {e}", exc_info=True)
            return False
    
    async def get_all_chats(self):
        try:
            logger.info("Getting all chats")
            
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Check if the chats table exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chats'")
            if cursor.fetchone() is None:
                logger.warning("Chats table not found")
                return []
            
            cursor.execute(
                '''
                SELECT id, title, created_at, updated_at
                FROM chats
                ORDER BY updated_at DESC
                '''
            )
            
            rows = cursor.fetchall()
            conn.close()
            
            logger.info(f"Retrieved {len(rows)} chats")
            
            chats = []
            for row in rows:
                chat = {
                    "id": row[0],
                    "title": row[1],
                    "created_at": row[2],
                    "updated_at": row[3]
                }
                chats.append(chat)
            
            return chats
        except Exception as e:
            logger.error(f"Database error getting chats: {e}", exc_info=True)
            return []
    
    async def delete_chat(self, chat_id: str) -> bool:
        try:
            logger.info(f"Deleting chat: id={chat_id}")
            
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Delete all messages associated with the chat
            cursor.execute(
                '''
                DELETE FROM messages
                WHERE chat_id = ?
                ''',
                (chat_id,)
            )
            
            # Delete the chat entry
            cursor.execute(
                '''
                DELETE FROM chats
                WHERE id = ?
                ''',
                (chat_id,)
            )
            
            conn.commit()
            conn.close()
            
            logger.info(f"Successfully deleted chat {chat_id} and its messages")
            return True
            
        except Exception as e:
            logger.error(f"Database error deleting chat: {e}", exc_info=True)
            return False

# Global resources manager
class RAGResources:
    def __init__(self):
        self.groq_client = None
        self.groq_client_refine = None
        self.groq_client_speech = None
        self.vectordb = None
        self.embeddings = None
        self.cohere_client = None
        self.tts_engine = pyttsx3.init()
        self.recognizer = sr.Recognizer()
        self.db_path = setup_database()
        self.db_manager = DatabaseManager(self.db_path)

# Lifespan manager for resources
@asynccontextmanager
async def lifespan(app: FastAPI):
    resources = RAGResources()
    try:
        logger.info("Loading resources...")
        resources.groq_client = await load_groq_client(os.environ.get("GROQ_API_KEY", ""))
        resources.groq_client_refine = await load_groq_client(os.environ.get("GROQ_API_KEY", ""))  # Replace with your second API key
        resources.groq_client_speech = await load_groq_client(os.environ.get("GROQ_API_KEY", ""))  # Replace with your third API key
        resources.embeddings = await load_embeddings()
        resources.vectordb = await load_vectordb(resources.embeddings)
        resources.cohere_client = cohere.Client(os.environ.get("COHERE_API_KEY", ""))  # Replace with your API key
        app.state.resources = resources
        yield
    finally:
        pass

app = FastAPI(lifespan=lifespan)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*","http://192.168.212.92:5500"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Resource loading functions
async def load_groq_client(api_key):
    try:
        logger.info(f"Initializing Groq client with key ending in ...{api_key[-5:]}")
        return Groq(api_key=api_key)
    except Exception as e:
        logger.error(f"Groq client initialization error: {e}")
        raise RuntimeError("Failed to initialize Groq client")

async def load_embeddings():
    try:
        logger.info("Loading embeddings...")
        return HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
    except Exception as e:
        logger.error(f"Embeddings loading error: {e}")
        raise RuntimeError("Failed to load embeddings")

async def load_vectordb(embeddings):
    try:
        logger.info("Loading vector database...")
        persist_directory = 'Chakwali_DB'
        if os.path.exists(persist_directory) and os.listdir(persist_directory):
            vectordb = FAISS.load_local(
                persist_directory,
                embeddings,
                allow_dangerous_deserialization=True
            )
            logger.info(f"Loaded existing FAISS vector store from: {persist_directory}")
        else:
            raise RuntimeError("Vector store not found")
        return vectordb
    except Exception as e:
        logger.error(f"VectorDB loading error: {e}")
        raise RuntimeError("Failed to load vector database")

# Query processing function remains the same
async def process_query(query: str, resources: RAGResources, max_sources: int) -> List[Dict]:
    try:
        # Get relevant documents with scores
        docs_and_scores = resources.vectordb.similarity_search_with_score(
            query,
            k=max_sources * 2  # Get more docs initially for better reranking
        )
        
        # Prepare documents for reranking
        documents_for_rerank = []
        doc_scores = []
        
        for doc, similarity_score in docs_and_scores:
            # Convert similarity score to cosine similarity
            similarity = 1 / (1 + similarity_score)
            doc_scores.append((doc, similarity))
            documents_for_rerank.append(doc.page_content)

        try:
            # Perform Cohere reranking
            reranked_results = resources.cohere_client.rerank(
                query=query,
                documents=documents_for_rerank,
                model='rerank-multilingual-v3.0',
                top_n=len(documents_for_rerank)
            )

            # Create mapping of document content to Cohere score
            cohere_scores = {}
            for result in reranked_results.results:
                doc_index = result.index
                score = result.relevance_score
                if doc_index < len(documents_for_rerank):
                    original_text = documents_for_rerank[doc_index]
                    cohere_scores[original_text] = float(score)

        except Exception as e:
            logger.error(f"Cohere reranking error: {e}")
            cohere_scores = {doc.page_content: 0.0 for doc, _ in doc_scores}

        # Combine results
        combined_results = []
        for doc, similarity in doc_scores:
            cohere_score = cohere_scores.get(doc.page_content, 0.0)
            combined_score = cohere_score
            
            combined_results.append({
                'content': doc.page_content,
                'source': doc.metadata.get("source", "Unknown source"),
                'cohere_score': cohere_score,
                'similarity': similarity,
                'combined_score': combined_score,
                'formatted_text': (
                    f"[From PDF: {doc.metadata.get('source', 'Unknown source')}]\n"
                    f"(Cohere: {cohere_score:.3f}, "
                    f"Similarity: {similarity:.3f}, "
                    f"Combined: {combined_score:.3f})\n"
                    f"{doc.page_content}"
                )
            })

        # Sort by Cohere score and limit to max_sources
        sorted_results = sorted(combined_results, key=lambda x: x['cohere_score'], reverse=True)
        return sorted_results[:max_sources]

    except Exception as e:
        logger.error(f"Query processing error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error processing query: {str(e)}"
        )

async def generate_response(
    query: str,
    ranked_documents: List[Dict],
    resources: RAGResources
) -> str:
    try:
        # Create context from ranked documents
        context_parts = []
        for doc in ranked_documents:
            context_parts.append(
                f"[From PDF: {doc['source']}] \n"
                f"(Cohere Ranking: {doc.get('cohere_score', 0):.3f}, "
                f"Similarity: {doc['similarity']:.3f}, "
                f"Combined: {doc.get('combined_score', 0):.3f})\n"
                f"{doc['content']}"
            )
        context = "\n\n".join(context_parts)

        prompt = f"""You are a Voice assistant tasked with answering questions based SOLELY on the provided context.
        The provided context is all about PhD Student Handbook of SKOLTECH University by Hanzala
Do not use any external knowledge or information not present in the given context.
If the question is of any other field and irrelevant to the context provided, respond just with "I can't tell you this, ask something from the provided context." DO NOT INCLUDE YOUR OWN OPINION.

INSTRUCTIONS:
1) Your answer should be well structured and meaningful..
2) Your answer should be purely factual and accuracy should be the first priority.
3) As accuracy is the first priority, Do not create facts on your own.
5) Your answer should elaborate every tiny detail mentioned in the context.
6) Be precise and specific like you are a voice assistant.
7) Your answer should be in the same language as the question.
Be concise and to the point and give the answer in 4-5 sentences of the following query:
Question: {query}

Context:
{context}

Answer:"""

        # Generate response using Groq API
        completion = resources.groq_client.chat.completions.create(
            messages=[{
                "role": "user",
                "content": prompt
            }],
            model="llama-3.3-70b-versatile",  # or your preferred Groq model
            temperature=0.1,
            max_tokens=256,
            top_p=0.95
        )

        if not completion.choices:
            return "Error: No response generated"

        generated_text = completion.choices[0].message.content.strip()
        if not generated_text:
            return "Error: Empty response generated"
            
        return generated_text

    except Exception as e:
        logger.error(f"Response generation error: {e}")
        raise

# Main QA endpoint and get_source endpoint remain the same
@app.post("/api/qa/", response_model=QueryResponse)
async def qa_endpoint(
    query_request: QueryRequest,
    background_tasks: BackgroundTasks
):
    try:
        resources = app.state.resources
        query_id = uuid4()
        
        # Extract chat_id from the request
        chat_id = query_request.chat_id if hasattr(query_request, 'chat_id') else str(uuid4())
        logger.info(f"Processing query for chat {chat_id}")
        
        # Check if this is just an initialization request
        is_initialization = getattr(query_request, 'is_initialization', False)
        is_sync = getattr(query_request, 'is_sync', False)
        
        if is_initialization:
            # Save chat metadata
            chat_title = "New Chat"
            background_tasks.add_task(
                resources.db_manager.save_chat,
                chat_id,
                chat_title
            )
            
            # Just save a welcome message to the database
            welcome_message = MessageDB(
                id=str(uuid4()),
                chat_id=chat_id,
                message_type="text_output",
                content="Hello! I'm your AI assistant. How can I help you today?",
                endpoint="/api/qa/",
                timestamp=datetime.datetime.now().isoformat()
            )
            
            await resources.db_manager.save_message(welcome_message)
            logger.info(f"Initialized chat {chat_id} with welcome message")
            
            # Return a minimal response
            return QueryResponse(
                query="",
                response="Chat initialized",
                query_id=uuid4(),
                sources=[]
            )
        
        # Save the text input to database
        text_input_message = MessageDB(
            id=str(uuid4()),
            chat_id=chat_id,
            message_type="text_input",
            content=query_request.query,
            endpoint="/api/qa/",
            timestamp=datetime.datetime.now().isoformat()
        )
        
        background_tasks.add_task(
            resources.db_manager.save_message,
            text_input_message
        )
        
        # Update chat title based on first query
        if query_request.query and len(query_request.query) > 0:
            # Create a title from the query
            title = query_request.query[:30]
            if len(query_request.query) > 30:
                title += "..."
                
            background_tasks.add_task(
                resources.db_manager.save_chat,
                chat_id,
                title
            )
        
        # If this is just a sync request, return minimal response
        if is_sync:
            logger.info(f"Synced user message for chat {chat_id}")
            return QueryResponse(
                query=query_request.query,
                response="Message synced",
                query_id=uuid4(),
                sources=[]
            )
        
        # Process query and get response
        ranked_documents = await process_query(
            query_request.query,
            resources,
            max_sources=query_request.max_sources
        )
        
        response = await generate_response(
            query_request.query,
            ranked_documents,
            resources
        )
        
        # Save the text output to database
        text_output_message = MessageDB(
            id=str(uuid4()),
            chat_id=chat_id,
            message_type="text_output",
            content=response,
            endpoint="/api/qa/",
            timestamp=datetime.datetime.now().isoformat()
        )
        
        background_tasks.add_task(
            resources.db_manager.save_message,
            text_output_message
        )

        # Prepare sources
        sources = [
            Source(
                id=uuid4(),
                source=doc['source'],
                content=doc['content'],
                scores=SourceScore(
                    cohere=doc.get('cohere_score', 0.0),
                    similarity=doc['similarity'],
                    combined=doc.get('combined_score', 0.0)
                )
            )
            for doc in ranked_documents
        ]

        # Prepare response
        response_data = QueryResponse(
            query=query_request.query,
            response=response,
            query_id=query_id,
            sources=sources
        )

        return response_data

    except Exception as e:
        logger.error(f"Error processing query: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error processing query: {str(e)}"
        )

@app.post("/api/sources/", response_model=Source)
async def get_source(source_request: SourceRequest):
    try:
        resources = app.state.resources
        
        # Find the specific source in the cached sources
        for source in cached_data['sources']:
            if str(source['id']) == str(source_request.source_id):
                return Source(
                    id=source['id'],
                    source=source['source'],
                    content=source['content'],
                    scores=SourceScore(**source['scores'])
                )
        
        raise HTTPException(
            status_code=404,
            detail="Source not found for the given query_id and source_id"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving source: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving source: {str(e)}"
        )

@app.post("/api/speech-to-text/", response_model=dict)
async def speech_to_text(
    background_tasks: BackgroundTasks, 
    audio_file: UploadFile = File(...), 
    chat_id: Optional[str] = None
):
    """Endpoint to convert speech to text using Whisper via Groq"""
    try:
        resources = app.state.resources
        
        # Read the audio file content
        audio_content = await audio_file.read()
        
        # Clean up the filename - remove any codec information
        clean_filename = audio_file.filename.split(';')[0]
        content_type = audio_file.content_type.split(';')[0]
        
        # Log file information
        logger.info(f"Received audio file: {clean_filename}, size: {len(audio_content)} bytes, content type: {content_type}")
        logger.info(f"Received chat_id from request: {chat_id}")
        
        # CRITICAL: Ensure we have a valid chat_id
        if not chat_id:
            logger.error("No chat_id provided to speech-to-text endpoint!")
            raise HTTPException(
                status_code=400,
                detail="chat_id is required for speech-to-text processing"
            )
        
        logger.info(f"Using chat_id for voice input: {chat_id}")
        
        # Extract the file extension properly
        file_extension = clean_filename.split('.')[-1] if '.' in clean_filename else 'webm'
        
        # Save the audio to a temporary file with clean extension
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_extension}") as temp_file:
            temp_file.write(audio_content)
            temp_file_path = temp_file.name
        
        logger.info(f"Saved audio to temporary file: {temp_file_path}")
        
        # Generate a unique ID for this message
        message_id = str(uuid4())
        
        # Save the voice input to the database
        voice_input_base64 = base64.b64encode(audio_content).decode('utf-8')
        
        # Create the voice input message with the provided chat_id
        voice_input_message = MessageDB(
            id=message_id,
            chat_id=chat_id,  # Use exactly the chat_id provided by the frontend
            message_type="voice_input",
            content=voice_input_base64,
            mime_type=content_type,
            endpoint="/api/speech-to-text/",
            timestamp=datetime.datetime.now().isoformat()
        )
        
        # Save voice input message directly to the database
        try:
            conn = resources.db_manager.get_connection()
            cursor = conn.cursor()
            
            # Insert the message directly
            cursor.execute(
                '''
                INSERT INTO messages (id, chat_id, message_type, content, mime_type, endpoint, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    voice_input_message.id,
                    voice_input_message.chat_id,
                    voice_input_message.message_type,
                    voice_input_message.content,
                    voice_input_message.mime_type,
                    voice_input_message.endpoint,
                    voice_input_message.timestamp
                )
            )
            
            conn.commit()
            
            # Verify the message was saved
            cursor.execute("SELECT id, message_type, chat_id FROM messages WHERE id = ?", (message_id,))
            result = cursor.fetchone()
            
            if result:
                logger.info(f"Voice input message {message_id} saved directly to database with type: {result[1]} and chat_id: {result[2]}")
            else:
                logger.error(f"Failed to save voice input message {message_id} directly")
            
            # Check how many voice_input messages exist for this chat
            cursor.execute(
                "SELECT COUNT(*) FROM messages WHERE chat_id = ? AND message_type = 'voice_input'", 
                (chat_id,)
            )
            voice_input_count = cursor.fetchone()[0]
            logger.info(f"Chat {chat_id} now has {voice_input_count} voice_input messages")
            
            conn.close()
        except Exception as e:
            logger.error(f"Error saving voice input directly: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Error saving voice input: {str(e)}"
            )
        
        try:
            # Use Groq's dedicated audio transcription API
            with open(temp_file_path, "rb") as file:
                # Create a transcription of the audio file
                transcription = resources.groq_client_speech.audio.transcriptions.create(
                    file=(temp_file_path, file.read()),  # Required audio file
                    model="whisper-large-v3-turbo",  # Required model to use for transcription
                    response_format="json",  # Optional
                    language="en",  # Optional
                    temperature=0.0  # Optional
                )
            
            transcribed_text = transcription.text.strip()
            logger.info(f"Transcription result: {transcribed_text}")
            
            # Save transcription to database with the same chat_id
            transcription_message = MessageDB(
                id=str(uuid4()),
                chat_id=chat_id,  # Use exactly the same chat_id
                message_type="transcription",
                content=transcribed_text,
                endpoint="/api/speech-to-text/",
                timestamp=datetime.datetime.now().isoformat()
            )
            
            # Save transcription directly too
            try:
                conn = resources.db_manager.get_connection()
                cursor = conn.cursor()
                
                cursor.execute(
                    '''
                    INSERT INTO messages (id, chat_id, message_type, content, mime_type, endpoint, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        transcription_message.id,
                        transcription_message.chat_id,
                        transcription_message.message_type,
                        transcription_message.content,
                        transcription_message.mime_type,
                        transcription_message.endpoint,
                        transcription_message.timestamp
                    )
                )
                
                conn.commit()
                conn.close()
                logger.info(f"Transcription message saved directly to database with chat_id: {chat_id}")
            except Exception as e:
                logger.error(f"Error saving transcription directly: {e}", exc_info=True)
            
            # Clean up the temporary file
            os.unlink(temp_file_path)
            
            # Return the original chat_id to ensure consistency
            return {"transcribed_text": transcribed_text, "message_id": message_id, "chat_id": chat_id}
        
        except Exception as e:
            # Clean up the temporary file in case of error
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            logger.error(f"Transcription error: {str(e)}", exc_info=True)
            raise e

    except Exception as e:
        logger.error(f"Error processing audio: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error processing audio: {str(e)}"
        )

@app.post("/api/refine-query/", response_model=QueryRefinementResponse)
async def refine_query(text_request: TextRequest, background_tasks: BackgroundTasks):
    """Endpoint to refine a query"""
    try:
        resources = app.state.resources
        
        logger.info(f"Refining query: {text_request.text}")
        
        # Extract chat_id from request or generate a new one
        chat_id = text_request.chat_id
        if not chat_id:
            chat_id = str(uuid4())
            logger.warning(f"No chat_id provided for refine-query, generated new one: {chat_id}")
        else:
            logger.info(f"Using provided chat_id for refine-query: {chat_id}")
        
        system_prompt = """You are a query optimization expert. Convert the given query into a clear, 
        precise version using minimum words while maintaining the core intent. Your query should assign 
        the llm a role and include all necessary prompt engineering elements."""
        
        response = resources.groq_client_refine.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Original query: {text_request.text}\nProvide only the refined query without any explanations."}
            ],
            model="llama-3.3-70b-versatile",
            temperature=0.1,
            max_tokens=100
        )
        
        refined_query = response.choices[0].message.content.strip()
        logger.info(f"Refined query: {refined_query}")
        
        # Save the refined query to the database
        refined_query_message = MessageDB(
            id=str(uuid4()),
            chat_id=chat_id,
            message_type="refined_query",
            content=refined_query,
            endpoint="/api/refine-query/",
            timestamp=datetime.datetime.now().isoformat()
        )
        
        # Save refined query immediately instead of in background
        success = await resources.db_manager.save_message(refined_query_message)
        logger.info(f"Refined query message saved successfully: {success}")
        
        return QueryRefinementResponse(
            original_query=text_request.text,
            refined_query=refined_query
        )

    except Exception as e:
        logger.error(f"Error refining query: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error refining query: {str(e)}"
        )

@app.post("/api/text-to-speech/")
async def text_to_speech(text_request: TextRequest, background_tasks: BackgroundTasks):
    """Endpoint to convert text to speech"""
    try:
        resources = app.state.resources
        
        # Extract chat_id from request or generate a new one
        chat_id = getattr(text_request, 'chat_id', str(uuid4()))
        
        # Use BytesIO to store audio in memory
        audio_buffer = io.BytesIO()
        
        # Get the TTS engine from resources
        engine = resources.tts_engine
        
        # Configure the engine
        engine.setProperty('rate', 150)    # Speed of speech
        engine.setProperty('volume', 0.9)  # Volume (0.0 to 1.0)
        
        # Generate a temporary file path for the audio
        temp_filename = f"speech_{uuid4()}.webm"
        temp_filepath = os.path.join(tempfile.gettempdir(), temp_filename)
        
        # Save to the temporary file
        engine.save_to_file(text_request.text, temp_filepath)
        engine.runAndWait()
        
        # Make sure the engine is done before accessing the file
        await asyncio.sleep(0.5)
        
        # Read the file
        with open(temp_filepath, "rb") as audio_file:
            audio_data = audio_file.read()
        
        # Convert to base64 for storage
        audio_base64 = base64.b64encode(audio_data).decode('utf-8')
        
        # Save the voice output to the database
        voice_output_message = MessageDB(
            id=str(uuid4()),
            chat_id=chat_id,
            message_type="voice_output",
            content=audio_base64,
            mime_type="audio/webm",
            endpoint="/api/text-to-speech/",
            timestamp=datetime.datetime.now().isoformat()
        )
        
        background_tasks.add_task(
            resources.db_manager.save_message,
            voice_output_message
        )
        
        # Clean up the temporary file
        try:
            os.unlink(temp_filepath)
        except Exception as e:
            logger.warning(f"Could not delete temporary audio file: {e}")
            # Schedule deletion for later
            background_tasks.add_task(lambda: os.unlink(temp_filepath) if os.path.exists(temp_filepath) else None)
        
        # Return the audio data as a streaming response
        return StreamingResponse(
            io.BytesIO(audio_data),
            media_type="audio/webm",
            headers={
                "Content-Disposition": "attachment; filename=speech.webm"
            }
        )

    except Exception as e:
        logger.error(f"Error converting text to speech: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error converting text to speech: {str(e)}"
        )

# New endpoint to get chat messages from database
@app.get("/api/chat-messages/{chat_id}")
async def get_chat_messages(chat_id: str):
    """Endpoint to retrieve all messages for a specific chat"""
    try:
        resources = app.state.resources
        logger.info(f"Received request for chat messages with ID: {chat_id}")
        
        messages = await resources.db_manager.get_chat_messages(chat_id)
        
        # Convert to dict for JSON response
        messages_dict = [message.dict() for message in messages]
        
        logger.info(f"Returning {len(messages_dict)} messages for chat {chat_id}")
        if len(messages_dict) > 0:
            logger.info(f"Message types: {[msg.get('message_type') for msg in messages_dict]}")
        else:
            logger.warning(f"No messages found for chat {chat_id}")
        
        return {"chat_id": chat_id, "messages": messages_dict}
        
    except Exception as e:
        logger.error(f"Error retrieving chat messages: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving chat messages: {str(e)}"
        )

@app.post("/api/sync-messages/")
async def sync_messages(sync_request: SyncRequest, background_tasks: BackgroundTasks):
    """Endpoint to sync messages from frontend to backend"""
    try:
        resources = app.state.resources
        logger.info(f"Received sync request for chat {sync_request.chat_id} with {len(sync_request.messages)} messages")
        
        # Log database connection status
        try:
            conn = resources.db_manager.get_connection()
            cursor = conn.cursor()
            cursor.execute("PRAGMA integrity_check")
            integrity = cursor.fetchone()[0]
            logger.info(f"Database integrity check: {integrity}")
            conn.close()
        except Exception as e:
            logger.error(f"Database connection check failed: {e}")
        
        # Process each message
        for i, msg in enumerate(sync_request.messages):
            logger.info(f"Syncing message {i+1}/{len(sync_request.messages)}: type={msg.message_type}, content_preview={msg.content[:50] if msg.content else 'None'}")
            
            # Create a MessageDB object
            message_db = MessageDB(
                id=str(uuid4()),
                chat_id=msg.chat_id,
                message_type=msg.message_type,
                content=msg.content,
                mime_type=msg.mime_type,
                endpoint="/api/sync-messages/",
                timestamp=msg.timestamp
            )
            
            # Save to database - don't use background_tasks here to ensure immediate saving
            success = await resources.db_manager.save_message(message_db)
            logger.info(f"Message sync result for message {i+1}: {'Success' if success else 'Failed'}")
        
        # Verify the messages were saved
        messages = await resources.db_manager.get_chat_messages(sync_request.chat_id)
        logger.info(f"After sync, chat {sync_request.chat_id} now has {len(messages)} messages in database")
        
        # Return detailed information
        return {
            "status": "success", 
            "message_count": len(sync_request.messages),
            "total_messages_in_chat": len(messages),
            "message_types": [msg.message_type for msg in messages]
        }
        
    except Exception as e:
        logger.error(f"Error syncing messages: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error syncing messages: {str(e)}"
        )

# Add new endpoints for chat history
@app.get("/api/chat-history/")
async def get_chat_history():
    """Endpoint to retrieve all available chats"""
    try:
        resources = app.state.resources
        logger.info("Received request for chat history")
        
        chats = await resources.db_manager.get_all_chats()
        
        logger.info(f"Returning {len(chats)} chats")
        
        return {"chats": chats}
        
    except Exception as e:
        logger.error(f"Error retrieving chat history: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving chat history: {str(e)}"
        )

# Add delete chat endpoint
@app.delete("/api/chat/{chat_id}")
async def delete_chat(chat_id: str):
    """Endpoint to delete a chat and all its messages"""
    try:
        resources = app.state.resources
        logger.info(f"Received request to delete chat {chat_id}")
        
        # Delete the chat and its messages
        success = await resources.db_manager.delete_chat(chat_id)
        
        if success:
            logger.info(f"Successfully deleted chat {chat_id}")
            return {"status": "success", "message": f"Chat {chat_id} deleted successfully"}
        else:
            logger.error(f"Failed to delete chat {chat_id}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to delete chat {chat_id}"
            )
        
    except Exception as e:
        logger.error(f"Error deleting chat: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error deleting chat: {str(e)}"
        )

@app.get("/api/debug-params/")
async def debug_params(chat_id: Optional[str] = None):
    """Debug endpoint to check if parameters are being received correctly"""
    logger.info(f"Debug endpoint received chat_id: {chat_id}")
    return {
        "received_chat_id": chat_id,
        "timestamp": datetime.datetime.now().isoformat()
    }

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        workers=4
    )
