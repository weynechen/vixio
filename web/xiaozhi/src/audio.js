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
        
        // If we want to abruptly stop currently playing audio, we might need reference to current source
        // For simple BufferSource implementation, we can't easily "stop all", but closing/reopening context is drastic.
        // A better way is to track the last source or suspend context.
        // Since we schedule ahead, we can just cancel scheduled events or close context.
        // For this demo, let's just close and re-init context to "stop" immediately or suspend.
        if (this.context) {
             this.context.suspend().then(() => {
                 // IMPORTANT: Resume immediately to allow new recording/playback
                 this.context.resume();
                 this.nextStartTime = this.context.currentTime;
             });
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
