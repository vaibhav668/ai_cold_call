// Global states
let activeVoiceId = null;
let voices = [];
let industries = [];
let sessionId = null;
let websocket = null;
let connectionState = "disconnected"; // disconnected, connecting, connected

// Call Duration Timer
let callTimerInterval = null;
let callDurationSeconds = 0;

// Web Audio API
let audioContext = null;
let mediaStream = null;
let audioInputNode = null;
let scriptProcessorNode = null;
let activeSources = [];
let nextPlayTime = 0;
let isMuted = false;

// UI Elements
const industrySelect = document.getElementById("industry-select");
const languageSelect = document.getElementById("language-select");
const activeVoiceAvatar = document.getElementById("active-voice-avatar");
const activeVoiceName = document.getElementById("active-voice-name");
const activeVoiceRole = document.getElementById("active-voice-role");
const activeVoiceDesc = document.getElementById("active-voice-desc");
const startBtn = document.getElementById("start-btn");
const endBtn = document.getElementById("end-btn");
const callMetrics = document.getElementById("call-metrics");
const connectionStatusText = document.getElementById("connection-status");
const callTimerText = document.getElementById("call-timer");
const muteBtn = document.getElementById("mute-btn");
const micStatusText = document.getElementById("mic-status");
const aiOrb = document.getElementById("ai-orb");
const orbStatusText = document.getElementById("orb-status-text");
const voiceNodesContainer = document.getElementById("voice-nodes-container");
const transcriptScroll = document.getElementById("transcript-scroll");
const emptyTranscriptMsg = document.getElementById("empty-transcript-msg");
const typingIndicator = document.getElementById("typing-indicator");
const summaryScroll = document.getElementById("summary-scroll");

const tabTranscriptBtn = document.getElementById("tab-transcript-btn");
const tabSummaryBtn = document.getElementById("tab-summary-btn");
const transcriptTabContent = document.getElementById("transcript-tab-content");
const summaryTabContent = document.getElementById("summary-tab-content");

const floatingWidget = document.getElementById("floating-widget");
const widgetOrbClick = document.getElementById("widget-orb-click");

// ─────────────────────────────────────────────────────────────────────────────
// Initialization
// ─────────────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
    loadVoices();
    loadIndustries();
    setupEventListeners();
});

// Setup Event Listeners
function setupEventListeners() {
    startBtn.addEventListener("click", startConversation);
    endBtn.addEventListener("click", stopConversation);
    muteBtn.addEventListener("click", toggleMute);
    
    // Tab switching
    tabTranscriptBtn.addEventListener("click", () => switchTab("transcript"));
    tabSummaryBtn.addEventListener("click", () => switchTab("summary"));

    // Floating Widget click handler (smooth scroll to bottom and highlight orb)
    widgetOrbClick.addEventListener("click", () => {
        const arena = document.getElementById("circular-arena");
        arena.scrollIntoView({ behavior: 'smooth' });
        aiOrb.classList.add("btn-glow");
        setTimeout(() => aiOrb.classList.remove("btn-glow"), 2000);
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// API Loading
// ─────────────────────────────────────────────────────────────────────────────

// Load Voice Profiles from Backend
async function loadVoices() {
    try {
        const response = await fetch("/api/v1/voice-demo/voices");
        voices = await response.json();
        renderVoiceCircularSelector();
        
        // Select first voice by default
        if (voices.length > 0) {
            selectVoice(voices[0].id);
        }
    } catch (error) {
        console.error("Failed to load voices:", error);
    }
}

// Load Industry configuration
async function loadIndustries() {
    try {
        const response = await fetch("/api/v1/voice-demo/industries");
        industries = await response.json();
        
        industrySelect.innerHTML = industries.map(ind => 
            `<option value="${ind.id}">${ind.name}</option>`
        ).join("");
    } catch (error) {
        console.error("Failed to load industries:", error);
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Voice Circular Arrangement & Selection
// ─────────────────────────────────────────────────────────────────────────────

function getSvgAvatar(name, gender) {
    const initials = name.charAt(0);
    const colorStart = gender === "Female" ? "#f43f5e" : "#6366f1";
    const colorEnd = gender === "Female" ? "#ec4899" : "#3b82f6";
    const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">
        <defs>
            <linearGradient id="grad_${name}" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%" style="stop-color:${colorStart};stop-opacity:1" />
                <stop offset="100%" style="stop-color:${colorEnd};stop-opacity:1" />
            </linearGradient>
        </defs>
        <circle cx="50" cy="50" r="50" fill="url(#grad_${name})" />
        <text x="50%" y="54%" dominant-baseline="middle" text-anchor="middle" font-family="'Outfit', sans-serif" font-weight="700" font-size="38" fill="#ffffff">${initials}</text>
    </svg>
    `;
    return 'data:image/svg+xml;utf8,' + encodeURIComponent(svg);
}

function renderVoiceCircularSelector() {
    voiceNodesContainer.innerHTML = "";
    const total = voices.length;
    const radius = 170; // Position radius around central orb

    voices.forEach((voice, index) => {
        const node = document.createElement("div");
        node.className = "voice-node";
        node.setAttribute("data-voice-id", voice.id);
        node.title = `${voice.name} - ${voice.description}`;
        
        const fallback = getSvgAvatar(voice.name, voice.gender);
        node.innerHTML = `<img src="${voice.avatar}" onerror="this.src='${fallback}';">`;

        // Calculate trigonometric coordinate offsets
        const angle = (index / total) * 2 * Math.PI - Math.PI / 2; // Start from top
        const x = Math.cos(angle) * radius;
        const y = Math.sin(angle) * radius;

        node.style.transform = `translate(${x}px, ${y}px)`;
        node.addEventListener("click", () => selectVoice(voice.id));

        voiceNodesContainer.appendChild(node);
    });
}

function selectVoice(voiceId) {
    activeVoiceId = voiceId;
    
    // Highlight active node
    document.querySelectorAll(".voice-node").forEach(node => {
        if (node.getAttribute("data-voice-id") === voiceId) {
            node.classList.add("selected");
        } else {
            node.classList.remove("selected");
        }
    });

    const voice = voices.find(v => v.id === voiceId);
    if (voice) {
        const fallback = getSvgAvatar(voice.name, voice.gender);
        activeVoiceAvatar.src = voice.avatar;
        activeVoiceAvatar.onerror = function() { this.src = fallback; };
        activeVoiceName.textContent = voice.name;
        activeVoiceRole.textContent = voice.description;
        activeVoiceDesc.textContent = `Gender: ${voice.gender} | Supports: ${voice.supported_languages}`;
        orbStatusText.textContent = `Ready with ${voice.name}`;
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Audio Streaming (Browser Microphone Input)
// ─────────────────────────────────────────────────────────────────────────────

async function startConversation() {
    if (connectionState !== "disconnected") return;

    connectionState = "connecting";
    startBtn.disabled = true;
    startBtn.classList.add("hidden");
    endBtn.classList.remove("hidden");
    callMetrics.classList.remove("hidden");
    connectionStatusText.textContent = "Setting up session...";
    connectionStatusText.className = "metric-value connection-status text-primary";
    updateOrbVisual(CallState.CONNECTED, "Setting up...");

    // Clear transcript
    transcriptScroll.innerHTML = "";
    emptyTranscriptMsg.classList.add("hidden");
    tabSummaryBtn.disabled = true;
    switchTab("transcript");

    const industry = industrySelect.value;
    const language = languageSelect.value;

    try {
        // 1. Initialize session on backend
        const sessionRes = await fetch("/api/v1/voice-demo/sessions", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                voice_profile_id: activeVoiceId,
                industry: industry,
                language: language
            })
        });

        if (!sessionRes.ok) {
            throw new Error(`Failed to create session: ${sessionRes.statusText}`);
        }

        const sessionData = await sessionRes.json();
        sessionId = sessionData.session_id;

        // Auto Voice Switched Notification if adaptive selection occurred
        if (sessionData.voice_profile.id !== activeVoiceId) {
            selectVoice(sessionData.voice_profile.id);
            appendLogBubble("System", `Adapted voice profile to compatible native speaker: ${sessionData.voice_profile.name}`);
        }

        // 2. Request Microphone Access
        mediaStream = await navigator.mediaDevices.getUserMedia({
            audio: {
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true,
                channelCount: 1,
                sampleRate: 16000
            }
        });

        // 3. Establish WebSocket connection
        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const wsUrl = `${protocol}//${window.location.host}/api/v1/voice-demo/stream/${sessionId}`;
        websocket = new WebSocket(wsUrl);

        websocket.onopen = () => {
            connectionState = "connected";
            connectionStatusText.textContent = "Connected";
            connectionStatusText.className = "metric-value connection-status text-success";
            startTimer();
            setupAudioRecording();
        };

        websocket.onmessage = async (event) => {
            // Check for control messages
            if (typeof event.data === "string") {
                try {
                    const msg = JSON.parse(event.data);
                    handleControlMessage(msg);
                } catch (e) {
                    console.error("Error parsing WS control message:", e);
                }
            } else if (event.data instanceof Blob) {
                // Audio packets (G.711 mu-law bytes)
                const arrayBuffer = await event.data.arrayBuffer();
                const u8Array = new Uint8Array(arrayBuffer);
                const float32Data = ulawToFloat32(u8Array);
                playQueuedAudio(float32Data);
            }
        };

        websocket.onerror = (e) => {
            console.error("WS error:", e);
            updateOrbVisual(CallState.ERROR, "Network Error");
        };

        websocket.onclose = () => {
            stopConversation();
        };

    } catch (error) {
        console.error("Error starting conversation:", error);
        alert(`Mic Permission or Connection Error: ${error.message}`);
        resetUIAfterCall();
    }
}

function setupAudioRecording() {
    audioContext = new (window.AudioContext || window.webkitAudioContext)();
    const sourceNode = audioContext.createMediaStreamSource(mediaStream);
    
    // ScriptProcessorNode handles chunk-based capture and downsampling
    scriptProcessorNode = audioContext.createScriptProcessor(4096, 1, 1);
    
    scriptProcessorNode.onaudioprocess = (event) => {
        if (!websocket || websocket.readyState !== WebSocket.OPEN || isMuted) return;
        
        const float32Samples = event.inputBuffer.getChannelData(0);
        // Downsample input rate (typically 44.1k or 48k) to 8000Hz
        const downsampled = downsampleBuffer(float32Samples, audioContext.sampleRate, 8000);
        const int16Buffer = float32ToInt16(downsampled);
        
        // Stream raw Int16 binary chunk
        websocket.send(int16Buffer);
    };

    sourceNode.connect(scriptProcessorNode);
    scriptProcessorNode.connect(audioContext.destination);
    
    audioInputNode = sourceNode;
}

// Downsampler
function downsampleBuffer(buffer, inputSampleRate, outputSampleRate) {
    if (inputSampleRate === outputSampleRate) {
        return buffer;
    }
    const sampleRateRatio = inputSampleRate / outputSampleRate;
    const newLength = Math.round(buffer.length / sampleRateRatio);
    const result = new Float32Array(newLength);
    let offsetResult = 0;
    let offsetBuffer = 0;
    while (offsetResult < result.length) {
        const nextOffsetBuffer = Math.round((offsetResult + 1) * sampleRateRatio);
        let accum = 0, count = 0;
        for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i++) {
            accum += buffer[i];
            count++;
        }
        result[offsetResult] = count > 0 ? accum / count : 0;
        offsetResult++;
        offsetBuffer = nextOffsetBuffer;
    }
    return result;
}

// Convert Float32Array samples to raw Int16 PCM array buffer
function float32ToInt16(buffer) {
    const l = buffer.length;
    const buf = new Int16Array(l);
    for (let i = 0; i < l; i++) {
        const s = Math.max(-1, Math.min(1, buffer[i]));
        buf[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
    }
    return buf.buffer;
}

// ─────────────────────────────────────────────────────────────────────────────
// Audio Playback (Sequential Queue Scheduling)
// ─────────────────────────────────────────────────────────────────────────────

// Decode G.711 mu-law sample to Float32
function decodeUlaw(ulawByte) {
    const u_val = ~ulawByte & 0xFF;
    const sign = (u_val & 0x80);
    const exponent = (u_val >> 4) & 0x07;
    const mantissa = u_val & 0x0F;
    let sample = (mantissa << 3) + 132;
    sample <<= exponent;
    sample -= 132;
    return (sign ? -sample : sample) / 32768.0;
}

function ulawToFloat32(ulawBuffer) {
    const float32 = new Float32Array(ulawBuffer.length);
    for (let i = 0; i < ulawBuffer.length; i++) {
        float32[i] = decodeUlaw(ulawBuffer[i]);
    }
    return float32;
}

function playQueuedAudio(float32Samples) {
    if (!audioContext || audioContext.state === "suspended") {
        audioContext.resume();
    }

    // Create 8kHz 1-channel buffer
    const audioBuffer = audioContext.createBuffer(1, float32Samples.length, 8000);
    audioBuffer.getChannelData(0).set(float32Samples);

    const sourceNode = audioContext.createBufferSource();
    sourceNode.buffer = audioBuffer;
    sourceNode.connect(audioContext.destination);

    const currentTime = audioContext.currentTime;
    if (nextPlayTime < currentTime) {
        nextPlayTime = currentTime + 0.02; // Small padding to avoid gap clicking
    }

    sourceNode.start(nextPlayTime);
    nextPlayTime += audioBuffer.duration;

    activeSources.push(sourceNode);
    sourceNode.onended = () => {
        const idx = activeSources.indexOf(sourceNode);
        if (idx !== -1) {
            activeSources.splice(idx, 1);
        }
    };
}

// Stop all playing audio instantly (Barge-in / Interruption)
function stopAllActiveAudio() {
    activeSources.forEach(source => {
        try {
            source.stop();
        } catch (e) {}
    });
    activeSources = [];
    nextPlayTime = 0;
}

// ─────────────────────────────────────────────────────────────────────────────
// WebSocket Message Routing & State Manager
// ─────────────────────────────────────────────────────────────────────────────

const CallState = {
    CONNECTED: "CONNECTED",
    AI_SPEAKING: "AI_SPEAKING",
    WAITING_FOR_CUSTOMER: "WAITING_FOR_CUSTOMER",
    CUSTOMER_SPEAKING: "CUSTOMER_SPEAKING",
    TRANSCRIBING: "TRANSCRIBING",
    THINKING: "THINKING",
    GENERATING_RESPONSE: "GENERATING_RESPONSE",
    CALL_COMPLETED: "CALL_COMPLETED",
    ERROR: "ERROR"
};

function handleControlMessage(msg) {
    const event = msg.event;
    
    if (event === "state_change") {
        updateOrbVisual(msg.state);
    } 
    else if (event === "clear_audio") {
        stopAllActiveAudio();
    } 
    else if (event === "transcript") {
        appendTranscriptBubble(msg.sender, msg.text, msg.timestamp);
    }
}

// Update Central Orb animations based on CallState
function updateOrbVisual(state, customLabel = null) {
    aiOrb.className = "orb-pulsar"; // Reset
    
    let text = customLabel || "Active";
    
    switch (state) {
        case CallState.CONNECTED:
            aiOrb.classList.add("state-idle");
            text = customLabel || "Connected";
            break;
        case CallState.WAITING_FOR_CUSTOMER:
            aiOrb.classList.add("state-idle");
            text = "Listening";
            break;
        case CallState.CUSTOMER_SPEAKING:
            aiOrb.classList.add("state-listening");
            text = "You Speaking";
            break;
        case CallState.TRANSCRIBING:
            aiOrb.classList.add("state-thinking");
            text = "Transcribing";
            break;
        case CallState.THINKING:
            aiOrb.classList.add("state-thinking");
            text = "AI Thinking";
            break;
        case CallState.GENERATING_RESPONSE:
            aiOrb.classList.add("state-thinking");
            text = "Synthesizing";
            break;
        case CallState.AI_SPEAKING:
            aiOrb.classList.add("state-speaking");
            text = "AI Speaking";
            break;
        case CallState.CALL_COMPLETED:
            aiOrb.classList.add("state-disconnected");
            text = "Call Ended";
            break;
        case CallState.ERROR:
            aiOrb.classList.add("state-error");
            text = "Error";
            break;
    }
    
    orbStatusText.textContent = text;

    // Show/hide thinking typing indicators
    if (state === CallState.THINKING || state === CallState.TRANSCRIBING) {
        typingIndicator.classList.remove("hidden");
    } else {
        typingIndicator.classList.add("hidden");
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Session Termination & Summary Gathering
// ─────────────────────────────────────────────────────────────────────────────

async function stopConversation() {
    if (connectionState === "disconnected") return;
    
    connectionState = "disconnected";
    updateOrbVisual(CallState.CALL_COMPLETED);
    stopAllActiveAudio();
    stopTimer();

    // Close sockets
    if (websocket) {
        try {
            websocket.send(JSON.stringify({ event: "stop" }));
        } catch (e) {}
        websocket.close();
        websocket = null;
    }

    // Stop mic stream
    if (mediaStream) {
        mediaStream.getTracks().forEach(track => track.stop());
        mediaStream = null;
    }

    // Disconnect nodes
    if (scriptProcessorNode) {
        scriptProcessorNode.disconnect();
        scriptProcessorNode = null;
    }
    if (audioInputNode) {
        audioInputNode.disconnect();
        audioInputNode = null;
    }
    if (audioContext) {
        audioContext.close();
        audioContext = null;
    }

    connectionStatusText.textContent = "Call Ended";
    connectionStatusText.className = "metric-value text-secondary";
    
    // Enable summary tab and trigger summary fetch
    tabSummaryBtn.disabled = false;
    await fetchCallSummary();

    resetUIAfterCall();
}

function resetUIAfterCall() {
    startBtn.disabled = false;
    startBtn.classList.remove("hidden");
    endBtn.classList.add("hidden");
    isMuted = false;
    muteBtn.className = "btn-icon";
    micStatusText.textContent = "Active";
}

// Fetch Call Summary Analysis
async function fetchCallSummary() {
    if (!sessionId) return;
    
    switchTab("summary");
    summaryScroll.innerHTML = `<div class="empty-transcript-message"><i class="fa-solid fa-spinner fa-spin"></i><p>Analyzing conversation and compiling intents...</p></div>`;

    try {
        const res = await fetch(`/api/v1/voice-demo/summary/${sessionId}`, {
            method: "POST"
        });
        
        if (res.ok) {
            const data = await res.json();
            renderCallSummary(data);
        } else {
            summaryScroll.innerHTML = `<div class="empty-transcript-message"><i class="fa-solid fa-triangle-exclamation"></i><p>Failed to generate summary analysis.</p></div>`;
        }
    } catch (e) {
        console.error("Summary error:", e);
        summaryScroll.innerHTML = `<div class="empty-transcript-message"><i class="fa-solid fa-triangle-exclamation"></i><p>Error retrieving call metrics.</p></div>`;
    }
}

function renderCallSummary(data) {
    let extractedInfoHtml = "";
    if (data.extracted_information && Object.keys(data.extracted_information).length > 0) {
        extractedInfoHtml = Object.entries(data.extracted_information)
            .filter(([_, v]) => v)
            .map(([k, v]) => `
                <div class="info-item">
                    <label>${k.replace(/_/g, " ").toUpperCase()}</label>
                    <span>${v}</span>
                </div>
            `).join("");
    } else {
        extractedInfoHtml = `<div class="info-item"><span style="color:var(--text-secondary)">No variables captured.</span></div>`;
    }

    let knowledgeHtml = "";
    if (data.knowledge_retrieved && data.knowledge_retrieved.length > 0) {
        knowledgeHtml = `<ul class="summary-list">` + 
            data.knowledge_retrieved.map(item => `<li>${item}</li>`).join("") + 
            `</ul>`;
    } else {
        knowledgeHtml = `<p style="font-size:0.85rem;color:var(--text-secondary);">No RAG facts retrieved.</p>`;
    }

    summaryScroll.innerHTML = `
        <div class="metric-grid">
            <div class="metric-card">
                <label><i class="fa-solid fa-bullseye"></i> Intent</label>
                <span>${data.intent}</span>
            </div>
            <div class="metric-card">
                <label><i class="fa-solid fa-face-smile"></i> Sentiment</label>
                <span>${data.sentiment}</span>
            </div>
            <div class="metric-card">
                <label><i class="fa-solid fa-clock"></i> Duration</label>
                <span>${formatTime(data.duration_seconds)}</span>
            </div>
            <div class="metric-card">
                <label><i class="fa-solid fa-id-card"></i> Lead Rating</label>
                <span>${data.lead_qualification}</span>
            </div>
            <div class="metric-card wide">
                <label><i class="fa-solid fa-calendar-check"></i> Appointment Status</label>
                <span>${data.appointment_status}</span>
            </div>
        </div>

        <div class="summary-card">
            <h4>Conversation Summary</h4>
            <p>${data.summary}</p>
        </div>

        <div class="summary-card">
            <h4>Extracted Information</h4>
            <div class="info-list">
                ${extractedInfoHtml}
            </div>
        </div>

        <div class="summary-card">
            <h4>RAG Knowledge Retrieved</h4>
            ${knowledgeHtml}
        </div>

        <div class="summary-card">
            <h4>Recommended Next Action</h4>
            <p>${data.recommended_next_action}</p>
        </div>
    `;
}

// ─────────────────────────────────────────────────────────────────────────────
// Live Transcript Renderers
// ─────────────────────────────────────────────────────────────────────────────

function appendTranscriptBubble(sender, text, timestamp) {
    emptyTranscriptMsg.classList.add("hidden");
    
    const div = document.createElement("div");
    const isUser = sender === "user";
    div.className = `dialog-msg ${isUser ? 'user' : 'agent'}`;

    const date = timestamp ? new Date(timestamp) : new Date();
    const timeStr = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

    div.innerHTML = `
        <span class="msg-sender">${isUser ? 'Customer' : 'AI Voice Agent'}</span>
        <div class="msg-bubble">${escapeHtml(text)}</div>
        <span class="msg-time">${timeStr}</span>
    `;

    transcriptScroll.appendChild(div);
    transcriptScroll.scrollTop = transcriptScroll.scrollHeight;
}

function appendLogBubble(type, text) {
    const div = document.createElement("div");
    div.className = "dialog-msg agent";
    div.style.alignSelf = "center";
    div.style.alignItems = "center";
    div.innerHTML = `
        <div class="msg-bubble" style="background:rgba(255,255,255,0.02);border: 1px dashed var(--primary);font-size:0.8rem;text-align:center;padding:6px 12px;border-radius:8px;">
            <i class="fa-solid fa-circle-info"></i> ${text}
        </div>
    `;
    transcriptScroll.appendChild(div);
    transcriptScroll.scrollTop = transcriptScroll.scrollHeight;
}

// ─────────────────────────────────────────────────────────────────────────────
// Helper Utilities
// ─────────────────────────────────────────────────────────────────────────────

function toggleMute() {
    isMuted = !isMuted;
    if (isMuted) {
        muteBtn.classList.add("muted");
        muteBtn.innerHTML = `<i class="fa-solid fa-microphone-slash"></i>`;
        micStatusText.textContent = "Muted";
    } else {
        muteBtn.classList.remove("muted");
        muteBtn.innerHTML = `<i class="fa-solid fa-microphone"></i>`;
        micStatusText.textContent = "Active";
    }
}

function startTimer() {
    stopTimer();
    callDurationSeconds = 0;
    callTimerText.textContent = "00:00";
    callTimerInterval = setInterval(() => {
        callDurationSeconds++;
        callTimerText.textContent = formatTime(callDurationSeconds);
    }, 1000);
}

function stopTimer() {
    if (callTimerInterval) {
        clearInterval(callTimerInterval);
        callTimerInterval = null;
    }
}

function formatTime(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
}

function switchTab(tab) {
    if (tab === "transcript") {
        tabTranscriptBtn.classList.add("active");
        tabSummaryBtn.classList.remove("active");
        transcriptTabContent.classList.remove("hidden");
        summaryTabContent.classList.add("hidden");
    } else {
        tabTranscriptBtn.classList.remove("active");
        tabSummaryBtn.classList.add("active");
        transcriptTabContent.classList.add("hidden");
        summaryTabContent.classList.remove("hidden");
    }
}

function escapeHtml(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return text.replace(/[&<>"']/g, function(m) { return map[m]; });
}
