import './style.css'
import { AudioController } from './audio.js';

// State Machine
const STATE = {
  DISCONNECTED: 'disconnected',
  CONNECTING: 'connecting',
  IDLE: 'idle', // Connected, waiting
  LISTENING: 'listening',
  SPEAKING: 'speaking'
};

class XiaozhiClient {
  constructor() {
    this.ws = null;
    this.audio = new AudioController();
    this.state = STATE.DISCONNECTED;
    
    // Elements
    this.chatContainer = document.getElementById('chat-container');
    this.statusBadge = document.getElementById('status-badge');
    this.connectBtn = document.getElementById('connect-btn');
    this.interruptBtn = document.getElementById('interrupt-btn');
    
    this.connectBtn.addEventListener('click', (e) => {
        // Resume AudioContext if it was suspended (autoplay policy)
        if (this.audio.context && this.audio.context.state === 'suspended') {
            this.audio.context.resume();
        }
        this.toggleConnection();
    });
    this.interruptBtn.addEventListener('click', () => this.interruptConversation());
    
    // Init UI
    this.updateState(STATE.DISCONNECTED);
  }

  updateState(newState) {
    this.state = newState;
    this.statusBadge.textContent = newState;
    this.statusBadge.className = `status-indicator ${newState}`;
    
    if (newState === STATE.DISCONNECTED) {
      this.connectBtn.textContent = 'Connect';
      this.connectBtn.disabled = false;
      this.connectBtn.classList.remove('btn-danger');
      this.connectBtn.classList.add('btn-primary');
      this.interruptBtn.disabled = true;
    } else if (newState === STATE.CONNECTING) {
      this.connectBtn.disabled = true;
      this.connectBtn.textContent = 'Connecting...';
      this.interruptBtn.disabled = true;
    } else {
      // Any connected state (IDLE, LISTENING, SPEAKING)
      this.connectBtn.disabled = false;
      this.connectBtn.textContent = 'Disconnect';
      this.connectBtn.classList.remove('btn-primary');
      this.connectBtn.classList.add('btn-danger');
      
      // Enable interrupt button only in SPEAKING state
      this.interruptBtn.disabled = (newState !== STATE.SPEAKING);
    }

    // Audio Control based on State
    if (newState === STATE.LISTENING) {
      this.audio.startRecording();
    } else {
      this.audio.stopRecording();
    }
  }

  toggleConnection() {
    if (this.state === STATE.DISCONNECTED) {
      this.connect();
    } else {
      this.disconnect();
    }
  }

  interruptConversation() {
    if (this.state === STATE.SPEAKING) {
      console.log('Interrupting conversation...');
      // 1. Stop Audio Playback
      this.audio.stopPlayback();
      
      // 2. Send Abort/Interrupt to Server
      this.sendInterrupt();
      
      // 3. Switch to LISTENING
      // Note: stopPlayback might have side effects on audio context timing, 
      // but startRecording should handle re-init of inputs.
      // Force status update
      this.updateState(STATE.LISTENING);
    }
  }

  sendInterrupt() {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
          const msg = {
              type: "abort",
              reason: "interrupt",
          };
          this.ws.send(JSON.stringify(msg));
          
          // Also send a "listen" control message if needed to force server state,
          // though usually "abort" + client sending audio is enough.
          // Let's send a 'listen' state message just in case the server logic expects it
          // Or rely on the fact that we are starting to send audio.
      }
  }

  connect() {
    this.updateState(STATE.CONNECTING);
    
    // Determine WS URL (assume same host port 8000 default or relative)
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    // If running dev server on different port, hardcode or config. 
    // Assuming backend is at localhost:8000 for local dev
    const wsUrl = `${protocol}//${window.location.hostname}:8000/xiaozhi/v1/`;
    
    try {
      this.ws = new WebSocket(wsUrl);
      this.ws.binaryType = 'arraybuffer';
      
      this.ws.onopen = () => {
        console.log('WS Connected');
        this.audio.init(this.ws);
        this.sendHello();
        // Automatically start listening after connection
        // Add a small delay to ensure AudioContext is ready and Hello is sent
        setTimeout(() => {
            console.log('Auto-starting conversation...');
            this.updateState(STATE.LISTENING);
        }, 100);
      };
      
      this.ws.onclose = () => {
        console.log('WS Closed');
        this.disconnect();
      };
      
      this.ws.onerror = (err) => {
        console.error('WS Error', err);
        // disconnect() will be called by onclose usually, but to be safe:
        if (this.state === STATE.CONNECTING) {
             this.updateState(STATE.DISCONNECTED);
        }
      };
      
      this.ws.onmessage = (event) => {
        this.handleMessage(event);
      };
      
    } catch (e) {
      console.error('Connection failed', e);
      this.updateState(STATE.DISCONNECTED);
    }
  }

  disconnect() {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.audio.close();
    this.updateState(STATE.DISCONNECTED);
  }

  sendHello() {
    const hello = {
      type: "hello",
      version: 1,
      transport: "websocket",
      audio_params: {
        format: "pcm",
        sample_rate: 16000,
        channels: 1,
        frame_duration: 60
      }
    };
    this.ws.send(JSON.stringify(hello));
  }

  handleMessage(event) {
    const data = event.data;
    
    if (data instanceof ArrayBuffer) {
      // Audio Data
      if (this.state === STATE.SPEAKING || this.state === STATE.IDLE || this.state === STATE.LISTENING) {
         // Play audio
         this.audio.playAudio(data);
      }
    } else {
      // JSON Message
      try {
        const msg = JSON.parse(data);
        console.log('Received:', msg);
        
        switch (msg.type) {
          case 'hello':
            // Server handshake
            break;
          case 'state':
            this.handleStateMessage(msg);
            break;
          case 'stt':
            this.addMessage('user', msg.text);
            break;
          case 'llm':
             // Sometimes LLM text comes here
             this.updateAgentMessage(msg.text);
             break;
          case 'tts':
             if (msg.state === 'start') {
                 // Bot starts speaking
                 this.updateState(STATE.SPEAKING);
             } else if (msg.state === 'stop') {
                 // Bot stops speaking
                 // Switch back to LISTENING for continuous conversation
                 console.log('TTS Stop received, switching to LISTENING');
                 this.updateState(STATE.LISTENING);
             } else if (msg.text) {
                 this.updateAgentMessage(msg.text);
             }
             break;
        }
      } catch (e) {
        console.error('Parse error', e);
      }
    }
  }
  
  handleStateMessage(msg) {
      if (msg.state === 'listening') {
          this.updateState(STATE.LISTENING);
      } else if (msg.state === 'idle') {
          this.updateState(STATE.IDLE);
          // Re-enable Connect button (Disconnect behavior) handled by toggle
          // But here IDLE means "Connected but waiting"
          // We need to distinguish Disconnected IDLE vs Connected IDLE
          // My simple state machine treats IDLE as Disconnected in toggleConnection.
          // Let's fix that:
          this.statusText.textContent = 'State: Idle (Connected)';
          this.statusBadge.textContent = 'idle';
          this.statusBadge.className = 'status-indicator idle';
          // Keep button as Disconnect
      } else if (msg.state === 'speaking') {
          this.updateState(STATE.SPEAKING);
      }
  }

  addMessage(role, text) {
    const div = document.createElement('div');
    div.className = `message ${role}`;
    
    const avatar = document.createElement('div');
    avatar.className = 'avatar';
    avatar.textContent = role === 'user' ? '👤' : '🤖';
    
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.textContent = text;
    
    div.appendChild(avatar);
    div.appendChild(bubble);
    
    this.chatContainer.appendChild(div);
    this.scrollToBottom();
  }
  
  // Handling streaming text from LLM/TTS
  updateAgentMessage(text) {
      // Find last message. If agent, append/replace. Else create new.
      const lastMsg = this.chatContainer.lastElementChild;
      if (lastMsg && lastMsg.classList.contains('agent')) {
          const bubble = lastMsg.querySelector('.bubble');
          // Start of new sentence vs full text? 
          // Protocol usually sends sentence segments or full text updates.
          // For simplicity, just append if it looks like a delta, or replace if it looks like full text.
          // But usually LLM events are chunks.
          // Let's assume we just append for now, or if it's a new turn create new.
          // Actually, 'llm' messages might be full text or delta.
          // Let's create a new bubble for each event for now to be safe, 
          // or better: check if the text overlaps. 
          
          // Simple approach: Always new message for now to avoid complexity
          this.addMessage('agent', text);
      } else {
          this.addMessage('agent', text);
      }
  }

  scrollToBottom() {
    this.chatContainer.scrollTop = this.chatContainer.scrollHeight;
  }
}

new XiaozhiClient();
