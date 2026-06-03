document.addEventListener('DOMContentLoaded', () => {
    const queryInput = document.getElementById('query-input');
    const submitBtn = document.getElementById('submit-btn');
    const chatMessages = document.getElementById('chat-messages');
    const typingIndicator = document.getElementById('typing-indicator');
    const chatHistory = document.getElementById('chat-history');
    const BACKEND_URL = 'http://localhost:8000';

    let currentChatId = null;
    let chatHistoryData = [];
    // Flag to track if we've loaded from history
    let initialHistoryLoaded = false;
    // Flag to prevent multiple chat creations
    let isCreatingChat = false;

    // Initialize if no current chat is set
    // if (!currentChatId) {
    //     createNewChat();
    // }

    const voiceRecordBtn = document.getElementById('voice-record-btn');
    const audioControls = document.querySelector('.audio-controls');
    const recordedAudio = document.getElementById('recorded-audio');
    const showTranscriptionBtn = document.getElementById('show-transcription');
    const showRefinedBtn = document.getElementById('show-refined');
    const recordingIndicator = document.getElementById('recording-indicator');

    let mediaRecorder;
    let audioChunks = [];
    let isRecording = false;
    let transcribedText = '';
    let refinedQuery = '';

    let popupContainer = null;

    const popupHTML = `
    <div id="popup-container" class="popup-container" style="display: none;">
        <div class="popup-content">
            <div class="popup-text"></div>
            <button class="popup-close">Close</button>
        </div>
    </div>
    `;

    document.body.insertAdjacentHTML('beforeend', popupHTML);
    popupContainer = document.getElementById('popup-container');

    function formatMessage(text) {
        // Handle bold text
        text = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');

        // Handle italic text
        text = text.replace(/\*(.*?)\*/g, '<em>$1</em>');

        // Handle numbered lists
        text = text.replace(/^\d+\.\s+(.+)$/gm, '<li>$1</li>');
        text = text.replace(/(<li>.*?<\/li>(\n|$))+/g, '<ol>$&</ol>');

        // Handle bullet points
        text = text.replace(/^[\*\-]\s+(.+)$/gm, '<li>$1</li>');
        text = text.replace(/(<li>.*?<\/li>(\n|$))+/g, '<ul>$&</ul>');

        // Handle headers
        text = text.replace(/^###\s+(.+)$/gm, '<h3>$1</h3>');
        text = text.replace(/^##\s+(.+)$/gm, '<h2>$1</h2>');
        text = text.replace(/^#\s+(.+)$/gm, '<h1>$1</h1>');

        // Handle paragraphs
        text = text.replace(/\n\n/g, '</p><p>');
        text = '<p>' + text + '</p>';

        return text;
    }

    function addMessage(content, isUser, sources = null, timestamp = null, responseId = null) {
        console.log(`Adding message: isUser=${isUser}, responseId=${responseId}`);
        if (typeof content === 'string') {
            console.log(`Message content (first 50 chars): "${content.substring(0, 50)}..."`);
        } else if (content instanceof Blob) {
            console.log(`Message content is Blob, size: ${content.size} bytes`);
        } else if (typeof content === 'object') {
            console.log(`Message content is object with type: ${content.type}`);
        }

        // Create message element
        const messageDiv = document.createElement('div');
        messageDiv.className = isUser ? 'message user-message' : 'message ai-message';

        // Add timestamp if provided, otherwise use current time
        const messageTime = timestamp ? new Date(timestamp) : new Date();
        
        // Format the message content
        let audioElement = null;
        if (typeof content === 'string') {
            if (isUser) {
                // User messages are simple text
                messageDiv.textContent = content;
            } else {
                // AI messages may contain markdown-like formatting
                messageDiv.innerHTML = formatMessage(content);
            }
        } else if (content instanceof Blob || (typeof content === 'object' && content.type === 'audio')) {
            // Handle audio content
            audioElement = document.createElement('audio');
            audioElement.controls = true;
            
            let audioSrc;
            if (content instanceof Blob) {
                audioSrc = URL.createObjectURL(content);
            } else {
                audioSrc = content.data.startsWith('data:') ? content.data : `data:${content.mimeType || 'audio/webm'};base64,${content.data}`;
            }
            
            audioElement.src = audioSrc;
            
            if (content.isAIResponse) {
                const audioLabel = document.createElement('div');
                audioLabel.className = 'audio-label';
                audioLabel.textContent = 'AI voice response:';
                messageDiv.appendChild(audioLabel);
                
                // Add "Show Text" button for AI voice responses
                if (responseId) {
                    const showTextBtn = document.createElement('button');
                    showTextBtn.className = 'show-text-btn';
                    showTextBtn.textContent = 'Show Text';
                    showTextBtn.onclick = () => showAITextResponse(responseId);
                    messageDiv.appendChild(showTextBtn);
                }
            } else {
                const audioLabel = document.createElement('div');
                audioLabel.className = 'audio-label';
                audioLabel.textContent = 'Your voice message:';
                messageDiv.appendChild(audioLabel);
            }
            
            messageDiv.appendChild(audioElement);
        }

        // Add timestamp
        const timestampDiv = document.createElement('div');
        timestampDiv.className = 'timestamp';
        const hours = messageTime.getHours();
        const minutes = messageTime.getMinutes();
        const formattedTime = `${hours}:${minutes < 10 ? '0' + minutes : minutes}`;
        timestampDiv.textContent = formattedTime;
        messageDiv.appendChild(timestampDiv);

        // Add sources if provided
        if (sources && sources.length > 0) {
            const sourcesDiv = document.createElement('div');
            sourcesDiv.className = 'sources';
            sourcesDiv.innerHTML = `<div class="sources-title">Sources:</div><ul>${sources.map(source => `<li>${source}</li>`).join('')}</ul>`;
            messageDiv.appendChild(sourcesDiv);
        }

        // Add to chat display
        chatMessages.appendChild(messageDiv);
        chatMessages.scrollTop = chatMessages.scrollHeight;
        
        return audioElement;
    }

    function updateChatHistory() {
        chatHistory.innerHTML = '';
        console.log('Updating chat history from server...');

        // Fetch chat history from backend
        fetch(`${BACKEND_URL}/api/chat-history/`)
            .then(response => {
                if (!response.ok) {
                    throw new Error(`Failed to fetch chat history: ${response.statusText}`);
                }
                return response.json();
            })
            .then(data => {
                chatHistoryData = data.chats || [];
                console.log(`Loaded ${chatHistoryData.length} chats from server`);
                
                // Mark that we've loaded history
                initialHistoryLoaded = true;
                
                // Display chats in sidebar
                chatHistoryData.forEach(chat => {
                    const historyItem = document.createElement('div');
                    historyItem.className = `history-item ${chat.id === currentChatId ? 'active' : ''}`;
                    historyItem.dataset.chatId = chat.id;
                    
                    // Add a delete button to each history item
                    historyItem.innerHTML = `
                        <span>💬</span>
                        <span class="history-item-title">${chat.title}</span>
                        <button class="delete-chat" title="Delete chat">🗑️</button>
                    `;

                    // Add click event for loading the chat
                    historyItem.addEventListener('click', (e) => {
                        // Don't trigger if clicking the delete button
                        if (e.target.className === 'delete-chat') {
                            return;
                        }
                        console.log(`Clicked on chat: ${chat.id}`);
                        loadChat(chat.id);
                    });
                    
                    // Add click event for delete button
                    const deleteBtn = historyItem.querySelector('.delete-chat');
                    deleteBtn.addEventListener('click', (e) => {
                        e.stopPropagation(); // Prevent triggering the parent click
                        deleteChat(chat.id);
                    });
                    
                    chatHistory.appendChild(historyItem);
                });
                
                // If we have chats available, load the most recent one if no current chat
                if (chatHistoryData.length > 0) {
                    if (!currentChatId || !chatHistoryData.find(chat => chat.id === currentChatId)) {
                        console.log(`Setting current chat to most recent: ${chatHistoryData[0].id}`);
                        loadChat(chatHistoryData[0].id);
                    } else {
                        // If current chat is valid, make sure it's updated in the UI
                        updateActiveChatInSidebar();
                    }
                } else if (chatHistoryData.length === 0 && !isCreatingChat) {
                    // Only create a new chat if there are no chats and we're not already creating one
                    console.log('No chats found in history, creating a new one');
                    createNewChat();
                }
            })
            .catch(error => {
                console.error('Error fetching chat history:', error);
                // If we can't get chat history and we're not already creating a chat, create a new one
                if (!isCreatingChat) {
                    createNewChat();
                }
            });
    }

    // Ensure messages persist on reload by fetching from backend
    window.addEventListener('load', () => {
        console.log('Page loaded, updating chat history');
        // Just update history - it will create a new chat only if needed
        updateChatHistory();
    });

    // Function to delete a chat
    function deleteChat(chatId) {
        console.log(`Deleting chat: ${chatId}`);
        
        // Confirm before deleting
        if (!confirm('Are you sure you want to delete this chat?')) {
            return;
        }
        
        fetch(`${BACKEND_URL}/api/chat/${chatId}`, {
            method: 'DELETE'
        })
        .then(response => {
            if (!response.ok) {
                throw new Error(`Failed to delete chat: ${response.statusText}`);
            }
            return response.json();
        })
        .then(data => {
            console.log(`Successfully deleted chat ${chatId}`);
            
            // If we deleted the current chat, load another one or create a new one
            if (chatId === currentChatId) {
                // Find another chat to load
                const otherChats = chatHistoryData.filter(chat => chat.id !== chatId);
                if (otherChats.length > 0) {
                    loadChat(otherChats[0].id);
                } else {
                    // If no other chats, create a new one
                    createNewChat();
                }
            }
            
            // Update the chat history
            updateChatHistory();
        })
        .catch(error => {
            console.error(`Error deleting chat: ${error}`);
            alert('Failed to delete chat. Please try again.');
        });
    }

    // Update chat history when a new chat is created
    function createNewChat() {
        // Prevent multiple simultaneous chat creations
        if (isCreatingChat) {
            console.log('Already creating a new chat, skipping duplicate request');
            return;
        }
        
        isCreatingChat = true;
        currentChatId = Date.now().toString();
        console.log(`Creating new chat with ID: ${currentChatId}`);
        
        // Clear current chat display
        chatMessages.innerHTML = '';
        
        // Initialize chat with a welcome message
        fetch(`${BACKEND_URL}/api/qa/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                query: "Initialize chat",
                chat_id: currentChatId,
                is_initialization: true  // Flag to indicate this is just initializing the chat
            })
        })
        .then(response => {
            if (!response.ok) {
                console.error(`Failed to initialize chat with backend: ${response.statusText}`);
                return;
            }
            return response.json();
        })
        .then(() => {
            console.log(`Successfully initialized chat ${currentChatId} with backend`);
            // Add initial AI welcome message to the UI
            addMessage("Hello! I'm your AI assistant. How can I help you today?", false);
            // Update the chat history to show the new chat
            updateChatHistory();
            // Update active chat in sidebar
            updateActiveChatInSidebar();
            isCreatingChat = false;
        })
        .catch(error => {
            console.error(`Error initializing chat with backend: ${error}`);
            isCreatingChat = false;
        });
    }

    function loadChat(chatId) {
        console.log(`Loading chat: ${chatId}`);
        
        if (!chatId) {
            console.error(`Chat ID is not set`);
            return;
        }

        // Don't reload the same chat
        if (chatId === currentChatId && chatMessages.children.length > 0) {
            console.log(`Chat ${chatId} is already loaded, skipping`);
            return;
        }

        currentChatId = chatId;
        console.log(`Set current chat ID to: ${currentChatId}`);
        
        // Clear current chat display
        chatMessages.innerHTML = '';
        console.log(`Cleared chat display for chat ${chatId}`);
        
        // Load from backend
        console.log(`Attempting to load chat ${chatId} from backend`);
        loadChatFromBackend(chatId).then(success => {
            console.log(`Backend load for chat ${chatId} ${success ? 'succeeded' : 'failed'}`);
            
            // Update active chat in sidebar
            updateActiveChatInSidebar();
            console.log(`Updated active chat in sidebar to: ${chatId}`);
            console.log(`Chat display now has ${chatMessages.children.length} message elements`);
        });
    }

    submitBtn.addEventListener('click', async () => {
        const query = queryInput.value.trim();

        if (!query) {
            return;
        }

        addMessage(query, true);
        queryInput.value = '';

        typingIndicator.style.display = 'block';

        try {
            const response = await fetch(`${BACKEND_URL}/api/qa/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    query: query,
                    max_sources: 5,
                    chat_id: currentChatId  // Ensure chat_id is included
                })
            });

            if (!response.ok) {
                throw new Error('Network response was not ok');
            }

            const data = await response.json();
            typingIndicator.style.display = 'none';
            
            // Store the text response for later use with the "Show Text" button
            const responseId = Date.now().toString();
            aiTextResponses[responseId] = data.response;
            
            // Only add text-to-speech conversion
            try {
                const speechResponse = await fetch(`${BACKEND_URL}/api/text-to-speech/`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        text: data.response,
                        chat_id: currentChatId,  // Include chat_id here
                        unique_id: responseId
                    })
                });

                if (!speechResponse.ok) {
                    throw new Error(`Text-to-speech conversion failed: ${await speechResponse.text()}`);
                }

                // Get the audio blob from the response
                const audioBlob = await speechResponse.blob();
                
                // Create an object that mimics the audio message format
                const audioMessage = {
                    type: 'audio',
                    isAIResponse: true,
                    data: await blobToBase64(audioBlob),
                    mimeType: audioBlob.type,
                    responseId: responseId // Add the response ID to link with text
                };

                // Add the audio message
                addMessage(audioMessage, false);

            } catch (error) {
                console.error('TTS error:', error);
                // If TTS fails, show a message
                addMessage('I apologize, but I encountered an error generating voice response.', false);
            }

        } catch (error) {
            console.error('Error:', error);
            typingIndicator.style.display = 'none';
            addMessage('I apologize, but I encountered an error. Please try again.', false);
        }
    });

    queryInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            submitBtn.click();
        }
    });


    document.querySelector('.new-chat-btn').addEventListener('click', () => {
        createNewChat();
        queryInput.focus();
    });

    // Improved voice recording functionality
    async function startRecording() {
        try {
            // Make sure we have a valid chat ID before starting recording
            if (!currentChatId) {
                console.error("No current chat ID available!");
                // Create a new chat if needed
                await createNewChat();
                console.log(`Created new chat with ID: ${currentChatId}`);
            }
            
            console.log(`Starting voice recording for chat: ${currentChatId}`);
            
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            mediaRecorder = new MediaRecorder(stream);
            audioChunks = [];

            // Show recording indicator
            recordingIndicator.style.display = 'block';
            
            mediaRecorder.ondataavailable = (event) => {
                audioChunks.push(event.data);
            };

            mediaRecorder.onstop = async () => {
                // Hide recording indicator
                recordingIndicator.style.display = 'none';
                
                // Get the actual MIME type from the recorder, but clean it up
                let mimeType = mediaRecorder.mimeType || 'audio/webm';
                // Remove any codec information from the MIME type
                mimeType = mimeType.split(';')[0];
                
                const audioBlob = new Blob(audioChunks, { type: mimeType });
                
                // Convert blob to base64 for storage
                const base64Audio = await blobToBase64(audioBlob);
                
                // Create an audio object for display and storage
                const audioObj = {
                    type: 'audio',
                    data: base64Audio,
                    mimeType: mimeType,
                    isAIResponse: false
                };
                
                // Add audio message using the new format
                addMessage(audioObj, true);
                
                // Save audio file with appropriate extension (without codec info)
                const extension = mimeType.split('/')[1] || 'webm';
                const fileName = `voice_${Date.now()}.${extension}`;
                const audioFile = new File([audioBlob], fileName, { type: mimeType });
                
                // Double check that we have a valid chat ID
                if (!currentChatId) {
                    console.error("No chat ID available for voice processing!");
                    addMessage("Error: No chat ID available. Please try again.", false);
                    return;
                }
                
                // Function to add formData for audio file submission
                const formData = new FormData();
                formData.append('audio_file', audioFile);
                
                // CRITICAL: Make sure we're using the current chat ID
                console.log(`Using chat_id for voice processing: ${currentChatId}`);
                
                // Add chat_id as a URL parameter instead of in FormData
                const url = `${BACKEND_URL}/api/speech-to-text/?chat_id=${encodeURIComponent(currentChatId)}`;
                console.log(`Sending request to URL: ${url}`);

                try {
                    typingIndicator.style.display = 'block'; // Show AI thinking indicator
                    
                    // Log the audio format being sent
                    console.log(`Sending audio file: ${fileName}, type: ${mimeType}, size: ${audioBlob.size} bytes`);
                    
                    // Speech to text conversion with chat_id as URL parameter
                    const speechResponse = await fetch(url, {
                        method: 'POST',
                        body: formData
                    });
                    
                    if (!speechResponse.ok) {
                        const errorText = await speechResponse.text();
                        console.error('Speech-to-text error:', errorText);
                        throw new Error(`Speech-to-text failed: ${errorText}`);
                    }
                    
                    const speechData = await speechResponse.json();
                    transcribedText = speechData.transcribed_text;
                    
                    // Verify the chat_id in the response matches our current chat_id
                    if (speechData.chat_id !== currentChatId) {
                        console.error(`Chat ID mismatch! Frontend: ${currentChatId}, Backend: ${speechData.chat_id}`);
                        // Use the chat_id from the backend to ensure consistency
                        console.warn(`Updating current chat_id to match backend: ${speechData.chat_id}`);
                        currentChatId = speechData.chat_id;
                    } else {
                        console.log(`Chat ID consistency verified: ${currentChatId}`);
                    }
                    
                    // Display transcription as a system message
                    const transcriptionDiv = document.createElement('div');
                    transcriptionDiv.className = 'message system-message';
                    transcriptionDiv.innerHTML = `<strong>Transcription:</strong> ${transcribedText}`;
                    chatMessages.appendChild(transcriptionDiv);
                    chatMessages.scrollTop = chatMessages.scrollHeight;
                    
                    // Refine the query
                    const refineResponse = await fetch(`${BACKEND_URL}/api/refine-query/`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ 
                            text: transcribedText,
                            chat_id: currentChatId // Make sure to use the current chat ID
                        })
                    });
                    
                    if (!refineResponse.ok) {
                        const errorText = await refineResponse.text();
                        console.error('Query refinement error:', errorText);
                        throw new Error(`Query refinement failed: ${errorText}`);
                    }
                    
                    const refineData = await refineResponse.json();
                    refinedQuery = refineData.refined_query;
                    
                    // Display refined query in chat
                    const refinedDiv = document.createElement('div');
                    refinedDiv.className = 'message system-message';
                    refinedDiv.innerHTML = `<strong>Refined Query:</strong> ${refinedQuery}`;
                    chatMessages.appendChild(refinedDiv);
                    chatMessages.scrollTop = chatMessages.scrollHeight;

                    // Process the refined query with the same chat ID
                    await processQuery(refinedQuery);
                } catch (error) {
                    console.error('Error processing audio:', error);
                    addMessage('Error processing audio: ' + error.message, false);
                } finally {
                    typingIndicator.style.display = 'none'; // Hide AI thinking indicator
                }
            };

            mediaRecorder.start();
            voiceRecordBtn.classList.add('recording');
            isRecording = true;

        } catch (error) {
            console.error('Error accessing microphone:', error);
            addMessage('Error accessing microphone. Please ensure you have granted microphone permissions.', false);
        }
    }

    function stopRecording() {
        if (mediaRecorder && mediaRecorder.state === 'recording') {
            mediaRecorder.stop();
            mediaRecorder.stream.getTracks().forEach(track => track.stop());
            voiceRecordBtn.classList.remove('recording');
            recordingIndicator.style.display = 'none';
            isRecording = false;
        }
    }

    voiceRecordBtn.addEventListener('click', () => {
        if (!isRecording) {
            startRecording();
        } else {
            stopRecording();
        }
    });

    showTranscriptionBtn.addEventListener('click', () => {
        showPopup(`Transcribed Text: ${transcribedText}`);
    });

    showRefinedBtn.addEventListener('click', () => {
        showPopup(`Refined Query: ${refinedQuery}`);
    });

    async function processQuery(query) {
        typingIndicator.style.display = 'block';
        try {
            console.log('Processing query:', query);
            console.log(`Using chat_id for query processing: ${currentChatId}`);
            
            const response = await fetch(`${BACKEND_URL}/api/qa/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    query: query,
                    chat_id: currentChatId  // Make sure this is included
                })
            });

            if (!response.ok) {
                const errorText = await response.text();
                console.error('QA API error:', errorText);
                throw new Error(`QA API failed: ${errorText}`);
            }

            const data = await response.json();
            console.log('QA API response:', data);
            
            // Store the text response for later use with the "Show Text" button
            const responseId = Date.now().toString();
            aiTextResponses[responseId] = data.response;
            
            // Convert response to speech and save as audio message
            try {
                const speechResponse = await fetch(`${BACKEND_URL}/api/text-to-speech/`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        text: data.response,
                        chat_id: currentChatId,  // Send the chat_id to backend
                        unique_id: responseId
                    })
                });

                if (!speechResponse.ok) {
                    throw new Error(`Text-to-speech conversion failed: ${await speechResponse.text()}`);
                }

                // Get the audio blob from the response
                const audioBlob = await speechResponse.blob();
                
                // Create an object that mimics the audio message format
                const audioMessage = {
                    type: 'audio',
                    isAIResponse: true, // Flag to identify AI-generated audio
                    data: await blobToBase64(audioBlob),
                    mimeType: audioBlob.type
                };

                // Add the audio message with the responseId
                const audioElement = addMessage(audioMessage, false, null, new Date().toISOString(), responseId);
                
                // Auto-play the audio when it's first received
                if (audioElement && audioElement.tagName === 'AUDIO') {
                    console.log('Auto-playing AI voice response');
                    audioElement.play().catch(e => {
                        console.warn('Auto-play failed:', e);
                        console.log('Browser may require user interaction before playing audio');
                    });
                }

            } catch (error) {
                console.error('TTS error:', error);
                // Continue even if TTS fails - but we need some response
                addMessage("I'm sorry, I couldn't generate a voice response. Please try again.", false);
            }

        } catch (error) {
            console.error('Error:', error);
            addMessage('I apologize, but I encountered an error. Please try again. Error: ' + error.message, false);
        } finally {
            typingIndicator.style.display = 'none'; // Hide AI thinking indicator
        }
    }

    // Helper function to convert Blob to base64
    function blobToBase64(blob) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onloadend = () => resolve(reader.result);
            reader.onerror = reject;
            reader.readAsDataURL(blob);
        });
    }

    // Function to show popup
    function showPopup(text) {
        const popupText = popupContainer.querySelector('.popup-text');
        popupText.textContent = text;
        popupContainer.style.display = 'flex';
    }

    // Add click event for popup close button
    document.querySelector('.popup-close').addEventListener('click', () => {
        popupContainer.style.display = 'none';
    });

    // Function to load chat from backend database
    async function loadChatFromBackend(chatId) {
        console.log(`Attempting to load chat ${chatId} from backend`);
        try {
            const response = await fetch(`${BACKEND_URL}/api/chat-messages/${chatId}`);
            
            console.log(`Backend response status for chat ${chatId}:`, response.status);
            
            if (!response.ok) {
                const errorText = await response.text();
                console.error(`Error loading chat ${chatId} from backend:`, errorText);
                
                // Only create a new chat if getting a 404 (chat not found) and we don't have any chats
                if (response.status === 404 && chatHistoryData.length === 0 && !isCreatingChat) {
                    console.log("Chat not found and no other chats available, creating new chat");
                    createNewChat();
                } else if (chatHistoryData.length > 0) {
                    // If we have other chats, load the most recent one
                    console.log("Loading most recent chat instead");
                    const mostRecentChat = chatHistoryData[0];
                    if (mostRecentChat.id !== chatId) {
                        loadChat(mostRecentChat.id);
                    }
                }
                return false;
            }
            
            const data = await response.json();
            console.log(`Received data for chat ${chatId} from backend:`, data);
            console.log(`Number of messages received: ${data.messages ? data.messages.length : 0}`);
            
            // Clear current chat display
            chatMessages.innerHTML = '';
            console.log(`Cleared chat display for chat ${chatId}`);
            
            // Process and display messages
            if (data.messages && data.messages.length > 0) {
                console.log(`Processing ${data.messages.length} messages for chat ${chatId}`);
                
                // Check if this is a voice conversation (has voice_input messages)
                const hasVoiceInput = data.messages.some(msg => msg.message_type === 'voice_input');
                
                // Create a map to store text outputs by timestamp
                const textOutputMap = new Map();
                
                // First pass: collect all text outputs
                data.messages.forEach(msg => {
                    if (msg.message_type === 'text_output') {
                        textOutputMap.set(msg.timestamp, msg.content);
                    }
                });
                
                // Display all messages
                console.log(`Starting to display messages for chat ${chatId}`);
                
                for (const msg of data.messages) {
                    console.log(`Processing message of type: ${msg.message_type}`);
                    
                    // Voice input message - always show
                    if (msg.message_type === 'voice_input') {
                        console.log(`Adding voice input message, data length: ${msg.content ? msg.content.length : 0}`);
                        
                        // Create audio object from base64 data
                        const audioObj = {
                            type: 'audio',
                            data: msg.content.startsWith('data:') ? msg.content : `data:${msg.mime_type || 'audio/webm'};base64,${msg.content}`,
                            mimeType: msg.mime_type || 'audio/webm',
                            isAIResponse: false
                        };
                        
                        addMessage(audioObj, true, null, msg.timestamp, null);
                    }
                    // Normal text input - only show if not a voice conversation
                    else if (msg.message_type === 'text_input' && !hasVoiceInput) {
                        console.log(`Adding user text message: "${msg.content.substring(0, 50)}..."`);
                        addMessage(msg.content, true, null, msg.timestamp, null);
                    } 
                    // AI text output - only show if not a voice conversation
                    else if (msg.message_type === 'text_output' && !hasVoiceInput) {
                        console.log(`Adding AI text message: "${msg.content.substring(0, 50)}..."`);
                        addMessage(msg.content, false, null, msg.timestamp, null);
                    }
                    // Transcription - always show
                    else if (msg.message_type === 'transcription') {
                        console.log(`Adding transcription: "${msg.content.substring(0, 50)}..."`);
                        
                        // Create a system message for transcription
                        const transcriptionDiv = document.createElement('div');
                        transcriptionDiv.className = 'message system-message';
                        transcriptionDiv.innerHTML = `<strong>Transcription:</strong> ${msg.content}`;
                        chatMessages.appendChild(transcriptionDiv);
                        
                        // Add timestamp if needed
                        if (msg.timestamp) {
                            const timestampDiv = document.createElement('div');
                            timestampDiv.className = 'timestamp';
                            const messageTime = new Date(msg.timestamp);
                            const hours = messageTime.getHours();
                            const minutes = messageTime.getMinutes();
                            const formattedTime = `${hours}:${minutes < 10 ? '0' + minutes : minutes}`;
                            timestampDiv.textContent = formattedTime;
                            transcriptionDiv.appendChild(timestampDiv);
                        }
                    }
                    // Refined query - show all of them
                    else if (msg.message_type === 'refined_query') {
                        console.log(`Adding refined query: "${msg.content}"`);
                        
                        // Create a system message for refined query
                        const refinedDiv = document.createElement('div');
                        refinedDiv.className = 'message system-message';
                        refinedDiv.innerHTML = `<strong>Refined Query:</strong> ${msg.content}`;
                        chatMessages.appendChild(refinedDiv);
                        
                        // Add timestamp if needed
                        if (msg.timestamp) {
                            const timestampDiv = document.createElement('div');
                            timestampDiv.className = 'timestamp';
                            const messageTime = new Date(msg.timestamp);
                            const hours = messageTime.getHours();
                            const minutes = messageTime.getMinutes();
                            const formattedTime = `${hours}:${minutes < 10 ? '0' + minutes : minutes}`;
                            timestampDiv.textContent = formattedTime;
                            refinedDiv.appendChild(timestampDiv);
                        }
                    }
                    // AI voice output - always show
                    else if (msg.message_type === 'voice_output') {
                        console.log(`Adding voice output message, data length: ${msg.content ? msg.content.length : 0}`);
                        
                        // Create audio object from base64 data
                        const audioObj = {
                            type: 'audio',
                            data: msg.content.startsWith('data:') ? msg.content : `data:${msg.mime_type || 'audio/webm'};base64,${msg.content}`,
                            mimeType: msg.mime_type || 'audio/webm',
                            isAIResponse: true
                        };
                        
                        // Find the corresponding text output that came before this voice output
                        let textContent = '';
                        const msgTime = new Date(msg.timestamp);
                        
                        // Look for the closest text output before this voice output
                        let closestTime = null;
                        for (const [textTime, content] of textOutputMap) {
                            const textDateTime = new Date(textTime);
                            if (textDateTime <= msgTime && (!closestTime || textDateTime > new Date(closestTime))) {
                                closestTime = textTime;
                                textContent = content;
                            }
                        }
                        
                        // Store the text content for this voice response
                        const responseId = msg.timestamp;  // Use timestamp as responseId
                        if (textContent) {
                            aiTextResponses[responseId] = textContent;
                            console.log(`Stored text response for voice message, ID: ${responseId}`);
                        }
                        
                        addMessage(audioObj, false, null, msg.timestamp, responseId);
                    }
                }
                
                console.log(`Finished displaying ${data.messages.length} messages for chat ${chatId}`);
                console.log(`Current chat display has ${chatMessages.children.length} message elements`);
                return true;
            } else {
                console.log(`No messages found for chat ${chatId}, displaying welcome message`);
                // If no messages from backend, display default welcome message
                addMessage("Hello! I'm your AI assistant. How can I help you today?", false, null, new Date().toISOString(), null);
                return true;
            }
        } catch (error) {
            console.error(`Error loading chat ${chatId} from backend:`, error);
            
            // If there's a critical error and we have chats available, try loading a different one
            if (chatHistoryData.length > 0) {
                const mostRecentChat = chatHistoryData[0];
                if (mostRecentChat.id !== chatId) {
                    console.log("Error loading chat, trying most recent one instead");
                    loadChat(mostRecentChat.id);
                }
            } else if (!isCreatingChat) {
                // Only create a new chat if we're not already creating one
                console.log("No chats available after error, creating new chat");
                createNewChat();
            }
            
            return false;
        }
    }

    // Helper function to detect if a string is likely a base64 encoded audio
    function isBase64Audio(str) {
        // Check if it's a data URL
        if (str.startsWith('data:audio/') || str.startsWith('data:application/octet-stream')) {
            return true;
        }
        
        // Check if it's a raw base64 string (common WebM header for audio)
        if (str.startsWith('GkXfo') || str.startsWith('T2dnUw')) {
            return true;
        }
        
        // Check if it's a long string that looks like base64
        if (str.length > 100 && /^[A-Za-z0-9+/=]+$/.test(str)) {
            return true;
        }
        
        return false;
    }

    // Store AI text responses corresponding to voice responses
    let aiTextResponses = {};

    // Function to show AI text response in popup
    function showAITextResponse(responseId) {
        const textResponse = aiTextResponses[responseId];
        if (textResponse) {
            showPopup(textResponse);
        } else {
            showPopup("Text response not available");
        }
    }

    // Add a function to update chat title based on content
    function updateChatTitle(chatId, content) {
        // Try to extract a meaningful title from the content
        let title = '';
        
        // If it's a question, use the first sentence
        if (content.includes('?')) {
            title = content.split('?')[0] + '?';
        } 
        // Otherwise use the first few words
        else {
            const words = content.split(' ');
            title = words.slice(0, 5).join(' ');
        }
        
        // Trim and add ellipsis if needed
        title = title.trim();
        if (title.length > 30) {
            title = title.substring(0, 30) + '...';
        }
        
        console.log(`Updated chat title to: "${title}"`);
    }

    // Add this function to update the active chat in sidebar
    function updateActiveChatInSidebar() {
        const historyItems = document.querySelectorAll('.history-item');
        historyItems.forEach(item => {
            item.classList.remove('active');
            if (item.dataset.chatId === currentChatId) {
                item.classList.add('active');
            }
        });
    }

    // Add this function after startRecording
    async function testChatIdParam() {
        if (!currentChatId) {
            console.error("No current chat ID available!");
            return;
        }
        
        console.log(`Testing chat_id parameter with: ${currentChatId}`);
        
        try {
            const url = `${BACKEND_URL}/api/debug-params/?chat_id=${encodeURIComponent(currentChatId)}`;
            console.log(`Sending test request to: ${url}`);
            
            const response = await fetch(url);
            
            if (!response.ok) {
                console.error(`Test failed: ${response.statusText}`);
                return;
            }
            
            const data = await response.json();
            console.log(`Test result:`, data);
            
            if (data.received_chat_id === currentChatId) {
                console.log(`✅ Success! Server received correct chat_id: ${data.received_chat_id}`);
            } else {
                console.error(`❌ Error! Server received wrong chat_id: ${data.received_chat_id}, expected: ${currentChatId}`);
            }
        } catch (error) {
            console.error(`Test error:`, error);
        }
    }
});