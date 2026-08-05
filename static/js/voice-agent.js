/**
 * Voice Agent Demo — Frontend Logic
 *
 * Architecture:
 *   Browser Mic → PCM16 (8kHz) → WebSocket → Backend (VAD/STT/LLM/TTS)
 *   Backend → mu-law frames (8kHz) → WebSocket → Browser playback queue → Speaker
 *
 * Key design decisions:
 *  - Separate AudioContexts: 44.1kHz capture context + 8kHz playback context
 *    (eliminates nextPlayTime drift from sample-rate mismatch)
 *  - AudioWorkletNode for capture (off main thread, no frame drops)
 *  - WebSocket keepalive ping every 15s (prevents Render 30s idle disconnect)
 *  - nonce-based clear_audio ensures barge-in drains the playback queue completely
 */

"use strict";

// ─── Configuration ────────────────────────────────────────────────────────────
const RENDER_BACKEND    = "https://ai-cold-call.onrender.com";
const RENDER_WS_BACKEND = "wss://ai-cold-call.onrender.com";

function getApiBase() {
    const host = window.location.hostname;
    if (host.includes("vercel.app") || (host.includes("onrender.com") === false && host !== "localhost" && host !== "127.0.0.1")) {
        return RENDER_BACKEND;
    }
    return "";
}

function getWsBase() {
    const apiBase = getApiBase();
    if (apiBase) return apiBase.replace(/^http/, "ws");
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${window.location.host}`;
}

const API_BASE = getApiBase();
const WS_BASE  = getWsBase();
console.log(`[Config] API_BASE="${API_BASE}" WS_BASE="${WS_BASE}"`);

// ─── Global State ─────────────────────────────────────────────────────────────
let activeVoiceId  = null;
let activeVoiceObj = null;
let voices         = [];
let industries     = [];
let sessionId      = null;
let websocket      = null;
let wsClosedByUs   = false;
let connectionState = "disconnected";

// Timer
let callTimerInterval    = null;
let callDurationSeconds  = 0;

// Audio — two separate contexts to prevent sample-rate drift
let captureContext   = null;  // 44100 Hz — for microphone capture
let playbackContext  = null;  // 8000 Hz  — for mu-law playback (matches server output exactly)
let mediaStream      = null;
let workletNode      = null;  // AudioWorkletNode (replaces deprecated ScriptProcessorNode)
let activeSources    = [];
let nextPlayTime     = 0;
let isMuted          = false;
let audioContextStarted = false;

// WebSocket keepalive
let pingInterval = null;

// ─── DOM References ───────────────────────────────────────────────────────────
let elIndustry, elLanguage, elStartBtn, elEndBtn, elCallMetrics,
    elStatus, elTimer, elMuteBtn, elMicStatus,
    elOrb, elOrbStatus, elVoiceNodes, elActiveAvatarWrap,
    elActiveVoiceName, elActiveVoiceRole, elActiveVoiceDesc,
    elTranscriptScroll, elEmptyMsg, elTypingIndicator,
    elSummaryScroll, elTabTranscript, elTabSummary,
    elTranscriptContent, elSummaryContent, elWidgetOrb;

// ─── CallState Enum ───────────────────────────────────────────────────────────
const CallState = {
    CONNECTED:           "CONNECTED",
    WAITING_FOR_CUSTOMER:"WAITING_FOR_CUSTOMER",
    CUSTOMER_SPEAKING:   "CUSTOMER_SPEAKING",
    TRANSCRIBING:        "TRANSCRIBING",
    THINKING:            "THINKING",
    GENERATING_RESPONSE: "GENERATING_RESPONSE",
    AI_SPEAKING:         "AI_SPEAKING",
    CALL_COMPLETED:      "CALL_COMPLETED",
    ERROR:               "ERROR"
};

// ─── Init ─────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
    elIndustry         = document.getElementById("industry-select");
    elLanguage         = document.getElementById("language-select");
    elStartBtn         = document.getElementById("start-btn");
    elEndBtn           = document.getElementById("end-btn");
    elCallMetrics      = document.getElementById("call-metrics");
    elStatus           = document.getElementById("connection-status");
    elTimer            = document.getElementById("call-timer");
    elMuteBtn          = document.getElementById("mute-btn");
    elMicStatus        = document.getElementById("mic-status");
    elOrb              = document.getElementById("ai-orb");
    elOrbStatus        = document.getElementById("orb-status-text");
    elVoiceNodes       = document.getElementById("voice-nodes-container");
    elActiveAvatarWrap = document.getElementById("active-voice-avatar-wrap");
    elActiveVoiceName  = document.getElementById("active-voice-name");
    elActiveVoiceRole  = document.getElementById("active-voice-role");
    elActiveVoiceDesc  = document.getElementById("active-voice-desc");
    elTranscriptScroll = document.getElementById("transcript-scroll");
    elEmptyMsg         = document.getElementById("empty-transcript-msg");
    elTypingIndicator  = document.getElementById("typing-indicator");
    elSummaryScroll    = document.getElementById("summary-scroll");
    elTabTranscript    = document.getElementById("tab-transcript-btn");
    elTabSummary       = document.getElementById("tab-summary-btn");
    elTranscriptContent= document.getElementById("transcript-tab-content");
    elSummaryContent   = document.getElementById("summary-tab-content");
    elWidgetOrb        = document.getElementById("widget-orb-click");

    setupListeners();
    loadVoices();
    loadIndustries();
});

function setupListeners() {
    elStartBtn.addEventListener("click", startConversation);
    elEndBtn.addEventListener("click", stopConversation);
    elMuteBtn.addEventListener("click", toggleMute);
    if (elTabTranscript) elTabTranscript.addEventListener("click", () => switchTab("transcript"));
    if (elTabSummary)    elTabSummary.addEventListener("click",    () => switchTab("summary"));
    elWidgetOrb.addEventListener("click", () => {
        document.getElementById("circular-arena").scrollIntoView({ behavior: "smooth" });
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// Avatar Generator
// ─────────────────────────────────────────────────────────────────────────────
function makeSvgAvatar(name, gender) {
    const initial  = (name || "?").charAt(0).toUpperCase();
    const isFemale = gender === "Female";
    const c1 = isFemale ? "#f43f5e" : "#6366f1";
    const c2 = isFemale ? "#ec4899" : "#3b82f6";
    const svgNS = "http://www.w3.org/2000/svg";
    const svg   = document.createElementNS(svgNS, "svg");
    svg.setAttribute("viewBox", "0 0 100 100");
    svg.setAttribute("width", "100"); svg.setAttribute("height", "100");

    const defs = document.createElementNS(svgNS, "defs");
    const grad  = document.createElementNS(svgNS, "linearGradient");
    const gId   = "g_" + name.replace(/\s/g, "_");
    grad.setAttribute("id", gId);
    grad.setAttribute("x1","0%"); grad.setAttribute("y1","0%");
    grad.setAttribute("x2","100%"); grad.setAttribute("y2","100%");
    const s1 = document.createElementNS(svgNS, "stop");
    s1.setAttribute("offset","0%"); s1.setAttribute("stop-color", c1);
    const s2 = document.createElementNS(svgNS, "stop");
    s2.setAttribute("offset","100%"); s2.setAttribute("stop-color", c2);
    grad.appendChild(s1); grad.appendChild(s2); defs.appendChild(grad);
    svg.appendChild(defs);

    const circle = document.createElementNS(svgNS, "circle");
    circle.setAttribute("cx","50"); circle.setAttribute("cy","50");
    circle.setAttribute("r","50"); circle.setAttribute("fill", `url(#${gId})`);
    svg.appendChild(circle);

    const text = document.createElementNS(svgNS, "text");
    text.setAttribute("x","50%"); text.setAttribute("y","54%");
    text.setAttribute("dominant-baseline","middle");
    text.setAttribute("text-anchor","middle");
    text.setAttribute("font-size","40"); text.setAttribute("font-weight","700");
    text.setAttribute("fill","#ffffff");
    text.setAttribute("font-family","Inter, sans-serif");
    text.textContent = initial;
    svg.appendChild(text);

    const svgStr = new XMLSerializer().serializeToString(svg);
    return "data:image/svg+xml;base64," + btoa(unescape(encodeURIComponent(svgStr)));
}

function createAvatarElement(voice, sizePx) {
    const img = document.createElement("img");
    img.width = sizePx; img.height = sizePx;
    img.alt = voice.name;
    img.style.borderRadius = "50%";
    img.style.objectFit   = "cover";
    img.style.display     = "block";

    const fallbackSrc = makeSvgAvatar(voice.name, voice.gender);
    if (voice.avatar && voice.avatar !== "null" && voice.avatar !== "undefined") {
        let avatarUrl = voice.avatar;
        if (avatarUrl.includes("/static/images/avatars/") && avatarUrl.endsWith(".png")) {
            avatarUrl = avatarUrl.replace(".png", ".svg");
        }
        img.src = avatarUrl;
        img.onerror = function() { this.src = fallbackSrc; this.onerror = null; };
    } else {
        img.src = fallbackSrc;
    }
    return img;
}

// ─────────────────────────────────────────────────────────────────────────────
// API Loaders
// ─────────────────────────────────────────────────────────────────────────────
async function loadVoices() {
    try {
        console.log("[Voices] Fetching voice profiles...");
        const res = await fetch(`${API_BASE}/api/v1/voice-demo/voices`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        voices = await res.json();
        console.log(`[Voices] Loaded ${voices.length} profiles:`, voices.map(v => v.name));
        renderVoiceCircle();
        if (voices.length > 0) selectVoice(voices[0].id);
    } catch (err) {
        console.error("[Voices] Failed to load:", err);
        elOrbStatus.textContent = "Could not load voices";
    }
}

async function loadIndustries() {
    try {
        const res = await fetch(`${API_BASE}/api/v1/voice-demo/industries`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        industries = await res.json();
        elIndustry.innerHTML = industries
            .map(i => `<option value="${i.id}">${i.name}</option>`)
            .join("");
        console.log("[Industries] Loaded:", industries.map(i => i.name));
    } catch (err) {
        console.error("[Industries] Failed to load:", err);
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Voice Circle Renderer
// ─────────────────────────────────────────────────────────────────────────────
function renderVoiceCircle() {
    elVoiceNodes.innerHTML = "";
    const total  = voices.length;
    const radius = 170;

    voices.forEach((voice, idx) => {
        const node = document.createElement("div");
        node.className = "voice-node";
        node.dataset.voiceId = voice.id;
        node.title = `${voice.name} — ${voice.description || ""}`;

        const img = createAvatarElement(voice, 52);
        node.appendChild(img);

        const angle = (idx / total) * 2 * Math.PI - Math.PI / 2;
        node.style.transform = `translate(${Math.cos(angle) * radius}px, ${Math.sin(angle) * radius}px)`;
        node.addEventListener("click", () => selectVoice(voice.id));
        elVoiceNodes.appendChild(node);
    });
}

function selectVoice(voiceId) {
    activeVoiceId  = voiceId;
    activeVoiceObj = voices.find(v => v.id === voiceId);

    elVoiceNodes.querySelectorAll(".voice-node").forEach(n => {
        n.classList.toggle("selected", n.dataset.voiceId === voiceId);
    });

    if (!activeVoiceObj) return;

    elActiveAvatarWrap.innerHTML = "";
    const bigImg = createAvatarElement(activeVoiceObj, 56);
    bigImg.className = "active-avatar";
    elActiveAvatarWrap.appendChild(bigImg);

    elActiveVoiceName.textContent = activeVoiceObj.name;
    elActiveVoiceRole.textContent = activeVoiceObj.description || "";
    elActiveVoiceDesc.textContent = `${activeVoiceObj.gender} · ${activeVoiceObj.supported_languages}`;
    elOrbStatus.textContent       = `Ready · ${activeVoiceObj.name}`;
    console.log(`[Voice] Selected: ${activeVoiceObj.name} (id=${voiceId})`);
}

// ─────────────────────────────────────────────────────────────────────────────
// Session Lifecycle
// ─────────────────────────────────────────────────────────────────────────────
async function startConversation() {
    if (connectionState !== "disconnected") {
        console.warn("[Start] Already connecting/connected, ignoring.");
        return;
    }
    if (!activeVoiceId) {
        alert("Please select a voice profile first.");
        return;
    }

    // Unlock AudioContexts on user gesture — required by browsers
    await ensureAudioContexts();

    connectionState = "connecting";
    elStartBtn.disabled = true;
    elStartBtn.classList.add("hidden");
    elEndBtn.classList.remove("hidden");
    elCallMetrics.classList.remove("hidden");
    elStatus.textContent = "Setting up…";
    elStatus.className   = "metric-value";
    setOrbState(CallState.CONNECTED, "Setting up…");

    if (elTranscriptScroll) elTranscriptScroll.innerHTML = "";
    if (elEmptyMsg)   elEmptyMsg.classList.remove("hidden");
    if (elTabSummary) elTabSummary.disabled = true;
    if (elTabTranscript) switchTab("transcript");

    try {
        // 1. Create backend session
        console.log("[Session] Creating session...");
        const sessionRes = await fetch(`${API_BASE}/api/v1/voice-demo/sessions`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                voice_profile_id: activeVoiceId,
                industry: elIndustry.value,
                language: elLanguage.value
            })
        });

        if (!sessionRes.ok) {
            const errText = await sessionRes.text();
            throw new Error(`Session creation failed (${sessionRes.status}): ${errText}`);
        }

        const sessionData = await sessionRes.json();
        sessionId = sessionData.session_id;
        console.log(`[Session] Created: ${sessionId}`);

        if (sessionData.voice_profile && sessionData.voice_profile.id !== activeVoiceId) {
            selectVoice(sessionData.voice_profile.id);
            appendSystemMessage(`Voice auto-adapted to ${sessionData.voice_profile.name} (language compatibility)`);
        }

        // 2. Request microphone
        console.log("[Mic] Requesting microphone access...");
        mediaStream = await navigator.mediaDevices.getUserMedia({
            audio: {
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl:  true,
                channelCount: 1
            }
        });
        console.log("[Mic] Access granted.");

        // 3. Open WebSocket
        wsClosedByUs = false;
        const wsUrl = `${WS_BASE}/api/v1/voice-demo/stream/${sessionId}`;
        console.log(`[WS] Connecting to: ${wsUrl}`);
        websocket = new WebSocket(wsUrl);
        websocket.binaryType = "arraybuffer";

        websocket.onopen = () => {
            console.log("[WS] Connected.");
            connectionState = "connected";
            elStatus.textContent = "Connected";
            elStatus.style.color = "var(--listening-color)";
            startTimer();
            setupAudioCapture();
            startKeepalive();
        };

        websocket.onmessage = handleWsMessage;

        websocket.onerror = (e) => {
            console.error("[WS] Error event:", e);
            setOrbState(CallState.ERROR, "Connection Error");
        };

        websocket.onclose = (evt) => {
            console.log(`[WS] Closed (code=${evt.code}, wasClean=${evt.wasClean}, byUs=${wsClosedByUs})`);
            stopKeepalive();
            if (!wsClosedByUs) {
                let errMsg = "Connection closed unexpectedly.";
                if (evt.code === 1006) {
                    errMsg = "Connection lost (Code 1006) — server may have crashed or idle timeout hit.";
                }
                appendSystemMessage(errMsg);
                setOrbState(CallState.ERROR, "Failed");
                stopAllAudio();
                stopTimer();
                teardownAudioCapture();

                elStatus.textContent = "Error";
                elStatus.style.color = "var(--error-color)";
                if (elTabSummary) {
                    elTabSummary.disabled = false;
                    fetchSummary();
                }
                resetUIAfterCall();
            }
        };

    } catch (err) {
        console.error("[Start] Error:", err);
        setOrbState(CallState.ERROR, "Failed");
        elStatus.textContent = "Error";
        appendSystemMessage(`Error: ${err.message}`);
        resetUIAfterCall();
    }
}

async function stopConversation() {
    if (connectionState === "disconnected") return;
    console.log("[Stop] Stopping conversation...");

    connectionState = "disconnected";
    setOrbState(CallState.CALL_COMPLETED, "Call Ended");
    stopAllAudio();
    stopTimer();
    stopKeepalive();

    if (websocket && websocket.readyState < WebSocket.CLOSING) {
        wsClosedByUs = true;
        try { websocket.send(JSON.stringify({ event: "stop" })); } catch (_) {}
        websocket.close(1000, "User ended session");
    }
    websocket = null;

    teardownAudioCapture();

    elStatus.textContent = "Ended";
    elStatus.style.color = "";

    if (elTabSummary) {
        elTabSummary.disabled = false;
        await fetchSummary();
    }
    resetUIAfterCall();
}

function teardownAudioCapture() {
    if (workletNode) {
        try { workletNode.port.postMessage({ type: "stop" }); } catch (_) {}
        try { workletNode.disconnect(); } catch (_) {}
        workletNode = null;
    }
    if (mediaStream) {
        mediaStream.getTracks().forEach(t => t.stop());
        mediaStream = null;
    }
}

function resetUIAfterCall() {
    elStartBtn.disabled = false;
    elStartBtn.classList.remove("hidden");
    elEndBtn.classList.add("hidden");
    isMuted = false;
    elMuteBtn.innerHTML = `<i class="fa-solid fa-microphone"></i>`;
    elMuteBtn.classList.remove("muted");
    elMicStatus.textContent = "Active";
}

// ─────────────────────────────────────────────────────────────────────────────
// Audio Context & Capture
// ─────────────────────────────────────────────────────────────────────────────
async function ensureAudioContexts() {
    // Capture context — 44.1kHz for mic capture (browser native)
    if (!captureContext) {
        captureContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 44100 });
        console.log(`[Audio] Capture AudioContext created (sampleRate=${captureContext.sampleRate})`);
    }
    if (captureContext.state === "suspended") {
        await captureContext.resume();
        console.log("[Audio] Capture AudioContext resumed.");
    }

    // Playback context — 8000Hz to EXACTLY match server mu-law output
    // This eliminates nextPlayTime drift completely (no implicit resampling of durations)
    if (!playbackContext) {
        playbackContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 8000 });
        console.log(`[Audio] Playback AudioContext created (sampleRate=${playbackContext.sampleRate})`);
    }
    if (playbackContext.state === "suspended") {
        await playbackContext.resume();
        console.log("[Audio] Playback AudioContext resumed.");
    }

    audioContextStarted = true;
    nextPlayTime = 0;
    activeSources = [];
}

async function setupAudioCapture() {
    if (!captureContext || !mediaStream) return;

    // Try AudioWorklet first (non-blocking, runs off main thread)
    try {
        const workletUrl = `${window.location.origin}/static/js/audio-capture-worklet.js`;
        await captureContext.audioWorklet.addModule(workletUrl);

        const sourceNode = captureContext.createMediaStreamSource(mediaStream);
        workletNode = new AudioWorkletNode(captureContext, "audio-capture-processor");

        workletNode.port.onmessage = (evt) => {
            if (!evt.data || evt.data.type !== "frame") return;
            if (!websocket || websocket.readyState !== WebSocket.OPEN || isMuted) return;

            const float32 = evt.data.data;
            const downsampled = downsample(float32, captureContext.sampleRate, 8000);
            const int16 = float32ToInt16PCM(downsampled);
            websocket.send(int16);
        };

        sourceNode.connect(workletNode);
        // workletNode output not connected to destination — capture only, no audio passthrough
        console.log("[Audio] Capture pipeline active (AudioWorkletNode).");

    } catch (err) {
        console.warn(`[Audio] AudioWorklet failed (${err.message}), falling back to ScriptProcessorNode.`);

        // Fallback: ScriptProcessorNode (deprecated but widely supported)
        const sourceNode = captureContext.createMediaStreamSource(mediaStream);
        const scriptProcessor = captureContext.createScriptProcessor(4096, 1, 1);

        scriptProcessor.onaudioprocess = (evt) => {
            if (!websocket || websocket.readyState !== WebSocket.OPEN || isMuted) return;
            const float32 = evt.inputBuffer.getChannelData(0);
            const downsampled = downsample(float32, captureContext.sampleRate, 8000);
            const int16 = float32ToInt16PCM(downsampled);
            websocket.send(int16);
        };

        sourceNode.connect(scriptProcessor);
        scriptProcessor.connect(captureContext.destination);
        console.log("[Audio] Capture pipeline active (ScriptProcessorNode fallback).");
    }
}

function downsample(buf, fromRate, toRate) {
    if (fromRate === toRate) return buf;
    const ratio  = fromRate / toRate;
    const outLen = Math.round(buf.length / ratio);
    const out    = new Float32Array(outLen);
    for (let i = 0; i < outLen; i++) {
        const start = Math.round(i * ratio);
        const end   = Math.round((i + 1) * ratio);
        let sum = 0, count = 0;
        for (let j = start; j < end && j < buf.length; j++) { sum += buf[j]; count++; }
        out[i] = count ? sum / count : 0;
    }
    return out;
}

function float32ToInt16PCM(buf) {
    const out = new Int16Array(buf.length);
    for (let i = 0; i < buf.length; i++) {
        const s = Math.max(-1, Math.min(1, buf[i]));
        out[i]  = s < 0 ? s * 0x8000 : s * 0x7FFF;
    }
    return out.buffer;
}

// ─────────────────────────────────────────────────────────────────────────────
// WebSocket Keepalive
// ─────────────────────────────────────────────────────────────────────────────
function startKeepalive() {
    stopKeepalive();
    pingInterval = setInterval(() => {
        if (websocket && websocket.readyState === WebSocket.OPEN) {
            websocket.send(JSON.stringify({ event: "ping" }));
            console.debug("[WS] Keepalive ping sent.");
        }
    }, 15000); // Every 15 seconds — well within Render's 30s idle timeout
    console.log("[WS] Keepalive started (15s interval).");
}

function stopKeepalive() {
    if (pingInterval) {
        clearInterval(pingInterval);
        pingInterval = null;
        console.log("[WS] Keepalive stopped.");
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Audio Playback — 8kHz mu-law decode → Web Audio API
// ─────────────────────────────────────────────────────────────────────────────
function decodeUlaw(u) {
    u = (~u) & 0xFF;
    const sign = u & 0x80;
    const exp  = (u >> 4) & 0x07;
    const mant = u & 0x0F;
    let s = ((mant << 3) + 132) << exp;
    s -= 132;
    return (sign ? -s : s) / 32768.0;
}

function playMulaw(arrayBuffer) {
    if (!playbackContext || !audioContextStarted) return;
    if (playbackContext.state === "suspended") playbackContext.resume();

    const u8  = new Uint8Array(arrayBuffer);
    const f32 = new Float32Array(u8.length);
    for (let i = 0; i < u8.length; i++) f32[i] = decodeUlaw(u8[i]);

    // AudioBuffer at 8000 Hz — matches playbackContext.sampleRate exactly
    // buf.duration = u8.length / 8000.0 — correct with no resampling needed
    const buf = playbackContext.createBuffer(1, f32.length, 8000);
    buf.getChannelData(0).set(f32);

    const src = playbackContext.createBufferSource();
    src.buffer = buf;
    src.connect(playbackContext.destination);

    const now = playbackContext.currentTime;
    // Maintain a jitter buffer: schedule at least 20ms ahead of now
    if (nextPlayTime < now + 0.02) nextPlayTime = now + 0.02;
    src.start(nextPlayTime);
    nextPlayTime += buf.duration;  // buf.duration is exact at 8kHz — no drift

    activeSources.push(src);
    src.onended = () => {
        const i = activeSources.indexOf(src);
        if (i !== -1) activeSources.splice(i, 1);
    };
}

function stopAllAudio() {
    activeSources.forEach(src => { try { src.stop(); } catch (_) {} });
    activeSources = [];
    nextPlayTime  = 0;  // CRITICAL: reset so next AI response queues from now, not from old future time
    console.log("[Audio] All playback stopped.");
}

// ─────────────────────────────────────────────────────────────────────────────
// WebSocket Message Handler
// ─────────────────────────────────────────────────────────────────────────────
function handleWsMessage(evt) {
    if (evt.data instanceof ArrayBuffer) {
        playMulaw(evt.data);
        return;
    }
    try {
        const msg = JSON.parse(evt.data);
        console.log("[WS] Control:", msg.event, msg);

        switch (msg.event) {
            case "state_change":
                setOrbState(msg.state);
                break;
            case "clear_audio":
                // Server confirmed barge-in — stop ALL audio immediately and reset timeline
                stopAllAudio();
                break;
            case "transcript":
                appendTranscript(msg.sender, msg.text, msg.timestamp);
                break;
            case "pong":
                // Server acknowledged our keepalive ping
                console.debug("[WS] Pong received.");
                break;
            default:
                console.warn("[WS] Unknown event:", msg.event);
        }
    } catch (e) {
        console.error("[WS] Failed to parse control message:", e, evt.data);
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Orb State Visuals
// ─────────────────────────────────────────────────────────────────────────────
function setOrbState(state, customLabel) {
    elOrb.className = "orb-pulsar";
    let label       = customLabel;
    let showThinking = false;

    switch (state) {
        case CallState.CONNECTED:
            elOrb.classList.add("state-idle");
            label = label || "Connected";
            break;
        case CallState.WAITING_FOR_CUSTOMER:
            elOrb.classList.add("state-idle");
            label = "Listening…";
            break;
        case CallState.CUSTOMER_SPEAKING:
            elOrb.classList.add("state-listening");
            label = "You're speaking";
            break;
        case CallState.TRANSCRIBING:
            elOrb.classList.add("state-thinking");
            label = "Transcribing…";
            showThinking = true;
            break;
        case CallState.THINKING:
            elOrb.classList.add("state-thinking");
            label = "AI thinking…";
            showThinking = true;
            break;
        case CallState.GENERATING_RESPONSE:
            elOrb.classList.add("state-thinking");
            label = "Generating…";
            showThinking = true;
            break;
        case CallState.AI_SPEAKING:
            elOrb.classList.add("state-speaking");
            label = "AI speaking";
            break;
        case CallState.CALL_COMPLETED:
            elOrb.classList.add("state-disconnected");
            label = "Call ended";
            break;
        case CallState.ERROR:
            elOrb.classList.add("state-error");
            label = "Error";
            break;
        default:
            elOrb.classList.add("state-idle");
            label = label || state;
    }

    if (elOrbStatus) elOrbStatus.textContent = label;
    if (elTypingIndicator) {
        if (showThinking) {
            elTypingIndicator.classList.remove("hidden");
        } else {
            elTypingIndicator.classList.add("hidden");
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Transcript Rendering
// ─────────────────────────────────────────────────────────────────────────────
function appendTranscript(sender, text, timestamp) {
    if (!elTranscriptScroll) return;
    if (elEmptyMsg) elEmptyMsg.classList.add("hidden");

    const isUser = sender === "user";
    const div    = document.createElement("div");
    div.className = `dialog-msg ${isUser ? "user" : "agent"}`;

    const date    = timestamp ? new Date(timestamp) : new Date();
    const timeStr = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

    const senderEl = document.createElement("span");
    senderEl.className   = "msg-sender";
    senderEl.textContent = isUser ? "You" : "AI Agent";

    const bubble = document.createElement("div");
    bubble.className   = "msg-bubble";
    bubble.textContent = text;

    const timeEl = document.createElement("span");
    timeEl.className   = "msg-time";
    timeEl.textContent = timeStr;

    div.appendChild(senderEl);
    div.appendChild(bubble);
    div.appendChild(timeEl);

    elTranscriptScroll.appendChild(div);
    elTranscriptScroll.scrollTop = elTranscriptScroll.scrollHeight;
}

function appendSystemMessage(text) {
    if (!elTranscriptScroll) { console.info(`[System] ${text}`); return; }
    if (elEmptyMsg) elEmptyMsg.classList.add("hidden");

    const div = document.createElement("div");
    div.className = "system-msg";
    div.innerHTML = `<i class="fa-solid fa-circle-info"></i> <span>${escapeHtml(text)}</span>`;
    elTranscriptScroll.appendChild(div);
    elTranscriptScroll.scrollTop = elTranscriptScroll.scrollHeight;
}

// ─────────────────────────────────────────────────────────────────────────────
// Summary
// ─────────────────────────────────────────────────────────────────────────────
async function fetchSummary() {
    if (!sessionId) return;
    switchTab("summary");

    elSummaryScroll.innerHTML = "";
    const loadingEl = document.createElement("div");
    loadingEl.className = "empty-transcript-message";
    loadingEl.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i><p>Analyzing conversation…</p>`;
    elSummaryScroll.appendChild(loadingEl);

    try {
        console.log(`[Summary] Fetching summary for session ${sessionId}...`);
        const res = await fetch(`${API_BASE}/api/v1/voice-demo/summary/${sessionId}`, { method: "POST" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        console.log("[Summary] Received:", data);
        renderSummary(data);
    } catch (err) {
        console.error("[Summary] Failed:", err);
        elSummaryScroll.innerHTML = "";
        const errEl = document.createElement("div");
        errEl.className = "empty-transcript-message";
        errEl.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i><p>Failed to generate summary: ${escapeHtml(err.message)}</p>`;
        elSummaryScroll.appendChild(errEl);
    }
}

function renderSummary(data) {
    const extracted = data.extracted_information || {};
    const extractedRows = Object.entries(extracted)
        .filter(([, v]) => v)
        .map(([k, v]) => `<div class="info-item"><label>${escapeHtml(k.replace(/_/g, " "))}</label><span>${escapeHtml(String(v))}</span></div>`)
        .join("") || `<div class="info-item"><span style="color:var(--text-secondary)">No entities captured</span></div>`;

    const knowledge = data.knowledge_retrieved || [];
    const knowledgeHtml = knowledge.length
        ? `<ul class="summary-list">${knowledge.map(k => `<li>${escapeHtml(k)}</li>`).join("")}</ul>`
        : `<p style="color:var(--text-secondary);font-size:0.85rem">No RAG facts retrieved</p>`;

    elSummaryScroll.innerHTML = `
        <div class="metric-grid">
            <div class="metric-card">
                <label><i class="fa-solid fa-bullseye"></i> Intent</label>
                <span>${escapeHtml(data.intent || "—")}</span>
            </div>
            <div class="metric-card">
                <label><i class="fa-solid fa-face-smile"></i> Sentiment</label>
                <span>${escapeHtml(data.sentiment || "—")}</span>
            </div>
            <div class="metric-card">
                <label><i class="fa-solid fa-clock"></i> Duration</label>
                <span>${formatTime(data.duration_seconds || 0)}</span>
            </div>
            <div class="metric-card">
                <label><i class="fa-solid fa-id-card"></i> Lead Rating</label>
                <span>${escapeHtml(data.lead_qualification || "—")}</span>
            </div>
            <div class="metric-card wide">
                <label><i class="fa-solid fa-calendar-check"></i> Appointment Status</label>
                <span>${escapeHtml(data.appointment_status || "—")}</span>
            </div>
        </div>
        <div class="summary-card">
            <h4>Conversation Summary</h4>
            <p>${escapeHtml(data.summary || "No summary available.")}</p>
        </div>
        <div class="summary-card">
            <h4>Extracted Information</h4>
            <div class="info-list">${extractedRows}</div>
        </div>
        <div class="summary-card">
            <h4>RAG Knowledge Retrieved</h4>
            ${knowledgeHtml}
        </div>
        <div class="summary-card">
            <h4>Recommended Next Action</h4>
            <p>${escapeHtml(data.recommended_next_action || "No recommendation.")}</p>
        </div>
        ${data.failure_reason ? `
        <div class="summary-card" style="border: 1px solid rgba(239, 68, 68, 0.4); background: rgba(239, 68, 68, 0.05); border-radius: 8px; padding: 12px; margin-top: 12px;">
            <h4 style="color: #ef4444; margin-top: 0; display: flex; align-items: center; gap: 8px; font-size: 0.95rem;">
                <i class="fa-solid fa-triangle-exclamation"></i> Pipeline Failure Diagnostic
            </h4>
            <p style="font-size: 0.85rem; margin: 4px 0; color: var(--text-primary);">
                <strong>Last State:</strong> <span style="font-family: monospace; background: rgba(239, 68, 68, 0.15); padding: 2px 6px; border-radius: 4px; color: #ef4444;">${escapeHtml(data.current_state || "UNKNOWN")}</span>
            </p>
            <p style="font-size: 0.85rem; margin: 4px 0 12px 0; color: var(--text-secondary);">
                <strong>Reason:</strong> ${escapeHtml(data.failure_reason)}
            </p>
            ${data.error_stack ? `
            <details style="font-size: 0.75rem; cursor: pointer; color: var(--text-secondary);">
                <summary style="font-weight: 600; outline: none; margin-bottom: 6px; user-select: none;">Show developer error stack</summary>
                <pre style="background: rgba(0,0,0,0.2); padding: 8px; border-radius: 4px; overflow-x: auto; font-family: monospace; white-space: pre; color: #f87171; border: 1px solid rgba(255,255,255,0.05);">${escapeHtml(data.error_stack)}</pre>
            </details>` : ""}
        </div>` : ""}
    `;
}

// ─────────────────────────────────────────────────────────────────────────────
// UI Helpers
// ─────────────────────────────────────────────────────────────────────────────
function toggleMute() {
    isMuted = !isMuted;
    if (isMuted) {
        elMuteBtn.classList.add("muted");
        elMuteBtn.innerHTML = `<i class="fa-solid fa-microphone-slash"></i>`;
        elMicStatus.textContent = "Muted";
        console.log("[Mic] Muted.");
    } else {
        elMuteBtn.classList.remove("muted");
        elMuteBtn.innerHTML = `<i class="fa-solid fa-microphone"></i>`;
        elMicStatus.textContent = "Active";
        console.log("[Mic] Unmuted.");
    }
}

function startTimer() {
    stopTimer();
    callDurationSeconds = 0;
    elTimer.textContent = "00:00";
    callTimerInterval = setInterval(() => {
        callDurationSeconds++;
        elTimer.textContent = formatTime(callDurationSeconds);
    }, 1000);
}

function stopTimer() {
    if (callTimerInterval) {
        clearInterval(callTimerInterval);
        callTimerInterval = null;
    }
}

function formatTime(secs) {
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function switchTab(tab) {
    if (!elTabTranscript || !elTabSummary) return;
    const isTranscript = tab === "transcript";
    elTabTranscript.classList.toggle("active", isTranscript);
    elTabSummary.classList.toggle("active", !isTranscript);
    if (elTranscriptContent) elTranscriptContent.classList.toggle("hidden", !isTranscript);
    if (elSummaryContent)    elSummaryContent.classList.toggle("hidden", isTranscript);
}

function escapeHtml(str) {
    return String(str)
        .replace(/&/g,  "&amp;")
        .replace(/</g,  "&lt;")
        .replace(/>/g,  "&gt;")
        .replace(/"/g,  "&quot;")
        .replace(/'/g,  "&#039;");
}
