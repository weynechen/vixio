export class AudioController {
    constructor() {
        this.context = null;
        this.processor = null;
        this.input = null;
        this.ws = null;
        this.isRecording = false;
        
        // Queue for audio playback
        this.nextStartTime = 0;
        this.audioQueue = [];
        this.isPlaying = false;
        
        // Track active audio sources for interrupt support
        this.activeSources = [];
    }

    init(ws) {
        this.ws = ws;
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        this.context = new AudioContext({ sampleRate: 16000 });
        this.nextStartTime = this.context.currentTime;
    }

    async startRecording() {
        if (this.isRecording) return;
        
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            this.input = this.context.createMediaStreamSource(stream);
            
            // Use ScriptProcessor for resampling/conversion (Simple for demo)
            // bufferSize 4096 = ~250ms at 16k, or ~85ms at 48k. 
            // Xiaozhi uses 60ms frames. 
            // At 16000Hz, 60ms = 960 samples.
            // We want to send chunks of ~960 samples if possible, or just stream what we get.
            
            this.processor = this.context.createScriptProcessor(4096, 1, 1);
            
            this.processor.onaudioprocess = (e) => {
                if (!this.isRecording) return;
                
                const inputData = e.inputBuffer.getChannelData(0); // Float32
                // Downsample if needed (Browser usually handles it if context is init with 16000, but checking is safe)
                // Since we set AudioContext({ sampleRate: 16000 }), the browser input stream should be resampled automatically by the graph if the HW is different.
                
                // Convert Float32 to Int16
                const int16Data = this.floatTo16BitPCM(inputData);
                
                // Send via WebSocket
                if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                    this.ws.send(int16Data);
                }
            };

            this.input.connect(this.processor);
            this.processor.connect(this.context.destination); // Needed for processing to happen in Chrome
            
            this.isRecording = true;
        } catch (err) {
            console.error('Error starting recording:', err);
        }
    }

    stopRecording() {
        this.isRecording = false;
        if (this.processor) {
            this.processor.disconnect();
            this.processor = null;
        }
        if (this.input) {
            this.input.disconnect();
            this.input = null;
        }
    }

    stopPlayback() {
        // Clear queue
        this.audioQueue = [];
        this.isPlaying = false;
        
        // Stop all active audio sources immediately
        if (this.activeSources.length > 0) {
            console.log(`Stopping ${this.activeSources.length} active audio sources`);
            for (const source of this.activeSources) {
                try {
                    // Stop the source if it hasn't already stopped
                    source.stop();
                } catch (e) {
                    // Source may have already stopped naturally, ignore error
                }
            }
            // Clear the active sources array
            this.activeSources = [];
        }
        
        // Reset playback timing
        if (this.context) {
            this.nextStartTime = this.context.currentTime;
        }
    }

    playAudio(arrayBuffer) {
        if (!this.context) return;

        // Convert Int16 ArrayBuffer to Float32
        const int16Array = new Int16Array(arrayBuffer);
        const float32Array = new Float32Array(int16Array.length);
        
        for (let i = 0; i < int16Array.length; i++) {
            // Convert Int16 (-32768 to 32767) to Float32 (-1.0 to 1.0)
            float32Array[i] = int16Array[i] / 32768.0;
        }

        const buffer = this.context.createBuffer(1, float32Array.length, 16000);
        buffer.getChannelData(0).set(float32Array);

        const source = this.context.createBufferSource();
        source.buffer = buffer;
        source.connect(this.context.destination);

        // Schedule playback
        // If nextStartTime is in the past, reset it to now
        if (this.nextStartTime < this.context.currentTime) {
            this.nextStartTime = this.context.currentTime;
        }
        
        // Track this source for interrupt support
        this.activeSources.push(source);
        
        // Remove from active sources when playback ends
        source.onended = () => {
            const index = this.activeSources.indexOf(source);
            if (index > -1) {
                this.activeSources.splice(index, 1);
            }
        };
        
        source.start(this.nextStartTime);
        this.nextStartTime += buffer.duration;
    }
    
    // Helper: Float32 to Int16
    floatTo16BitPCM(input) {
        const output = new Int16Array(input.length);
        for (let i = 0; i < input.length; i++) {
            const s = Math.max(-1, Math.min(1, input[i]));
            output[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        return output.buffer;
    }

    close() {
        this.stopRecording();
        if (this.context && this.context.state !== 'closed') {
            this.context.close().catch(e => console.error("Error closing context:", e));
        }
    }
}
