/**
 * audio-capture-worklet.js
 *
 * AudioWorklet processor that runs in the dedicated audio rendering thread.
 * Captures raw Float32 PCM frames and posts them to the main thread via MessagePort.
 *
 * Why AudioWorklet over ScriptProcessorNode:
 *  - Runs off the main thread (dedicated audio rendering thread)
 *  - Never drops frames due to main-thread jank (React renders, garbage collection, etc.)
 *  - Processes every 128-sample render quantum with no gaps
 *  - Lower, more consistent latency
 */

class AudioCaptureProcessor extends AudioWorkletProcessor {
    constructor(options) {
        super(options);

        // Accumulate samples until we have a full 4096-sample frame (matching old ScriptProcessor size)
        // This gives ~93ms frames at 44100Hz, adequate for real-time VAD
        this._buffer = new Float32Array(4096);
        this._writePos = 0;
        this._active = true;

        this.port.onmessage = (evt) => {
            if (evt.data && evt.data.type === "stop") {
                this._active = false;
            }
        };
    }

    process(inputs, outputs, parameters) {
        if (!this._active) return false;

        const input = inputs[0];
        if (!input || !input[0]) return true;

        const channel = input[0]; // mono channel, 128 samples per render quantum

        for (let i = 0; i < channel.length; i++) {
            this._buffer[this._writePos++] = channel[i];

            if (this._writePos >= this._buffer.length) {
                // Send a copy — don't send the SharedArrayBuffer reference
                const frame = this._buffer.slice(0);
                this.port.postMessage({ type: "frame", data: frame }, [frame.buffer]);
                this._writePos = 0;
            }
        }

        return true; // Keep processor alive
    }
}

registerProcessor("audio-capture-processor", AudioCaptureProcessor);
