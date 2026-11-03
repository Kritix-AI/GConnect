/**
 * audio.js - Frontend Audio Manager for GConnect (Requirement R1).
 *
 * Implements real-time bidirectional audio streaming between the client and
 * the host container sandbox:
 * - Device Enumeration: Lists and selects microphones and speakers/headphones.
 * - Hardware Audio Processing: getUserMedia with hardware AEC, AGC, and noise suppression.
 * - Resampling Pipeline: Uses AudioWorklet (audio-processor.js) for 16kHz Int16 mono PCM.
 * - Playback Queue: Low-latency scheduling of container audio targeting user's sinkId.
 * - Cluster Arbitration: UI for exclusive active speaker status and one-click takeover.
 * - Google Meet-style Audio Settings Modal: Minimal, responsive dark-themed controls.
 */

class AudioManager {
  constructor() {
    this.initialized = false;
    this.isRecording = false;
    this.mediaStream = null;
    this.audioContext = null;
    this.processorNode = null;
    this.sourceNode = null;
    this.analyserNode = null;

    this.playbackAudioContext = null;
    this.speakerGainNode = null;
    this.speakerDestinationNode = null;
    this.sinkAudioElement = null;
    this.nextPlaybackTime = 0;

    this.ws = null;
    this.wsReconnectTimer = null;
    this.wsPingTimer = null;
    this.reconnectAttempts = 0;

    // Persistence defaults
    this.selectedInputDeviceId = localStorage.getItem("gconnect_audio_input_device_id") || "default";
    this.selectedOutputDeviceId = localStorage.getItem("gconnect_audio_output_device_id") || "default";
    this.isMicMuted = localStorage.getItem("gconnect_audio_mic_muted") === "true";
    this.isSpeakerMuted = localStorage.getItem("gconnect_audio_speaker_muted") === "true";

    // Cluster state
    this.activeSpeakerId = "";
    this.activeSpeakerName = "";
    this.isYouActiveSpeaker = false;
    this.isDucking = false;

    // VU meter animation
    this.vuAnimationId = null;
    this.vuDataArray = null;

    // DOM references
    this.dom = {};
  }

  /**
   * Initializes DOM references, event handlers, device enumeration, and WebSocket connection.
   */
  init() {
    if (this.initialized) return;
    this.initialized = true;

    this.cacheDom();
    this.bindEvents();
    this.checkSecureContext();
    this.refreshDevices();

    // Listen for device connection / disconnection
    if (navigator.mediaDevices && typeof navigator.mediaDevices.addEventListener === "function") {
      navigator.mediaDevices.addEventListener("devicechange", () => {
        this.refreshDevices();
      });
    }

    // Connect audio WebSocket hub
    this.connectWebSocket();

    // Update initial button and mute UI states
    this.updateMicUi();
    this.updateSpeakerUi();
    this.updateStatusDot();
  }

  cacheDom() {
    this.dom = {
      // Header button
      btnAudio: document.getElementById("btnAudio"),
      audioBtnIcon: document.getElementById("audioBtnIcon"),
      audioBtnLabel: document.getElementById("audioBtnLabel"),
      audioStatusDot: document.getElementById("audioStatusDot"),

      // Modal
      modalOverlay: document.getElementById("audioStreamModalOverlay"),
      btnCloseModal: document.getElementById("btnCloseAudioStreamModal"),
      btnDoneModal: document.getElementById("btnDoneAudioStreamModal"),
      insecureBanner: document.getElementById("insecureAudioBanner"),

      // Microphone
      audioInputSelect: document.getElementById("audioInputSelect"),
      btnMicMute: document.getElementById("btnMicMute"),
      micMuteIcon: document.getElementById("micMuteIcon"),
      micMuteLabel: document.getElementById("micMuteLabel"),
      micVuMeter: document.getElementById("micVuMeter"),
      micVuFill: document.getElementById("micVuFill"),

      // Speaker
      audioOutputSelect: document.getElementById("audioOutputSelect"),
      btnTestAudioOutput: document.getElementById("btnTestAudioOutput"),
      btnSpeakerMute: document.getElementById("btnSpeakerMute"),
      speakerMuteIcon: document.getElementById("speakerMuteIcon"),
      speakerMuteLabel: document.getElementById("speakerMuteLabel"),

      // Cluster & Status
      clusterActiveSpeakerLabel: document.getElementById("clusterActiveSpeakerLabel"),
      btnClaimSpeaker: document.getElementById("btnClaimSpeaker"),
      audioDuckingBadge: document.getElementById("audioDuckingBadge"),
      audioConnectionBadge: document.getElementById("audioConnectionBadge"),
    };
  }

  bindEvents() {
    if (this.dom.btnAudio) {
      this.dom.btnAudio.addEventListener("click", () => {
        this.toggleModal();
      });
    }

    if (this.dom.btnCloseModal) {
      this.dom.btnCloseModal.addEventListener("click", () => {
        this.showModal(false);
      });
    }

    if (this.dom.btnDoneModal) {
      this.dom.btnDoneModal.addEventListener("click", () => {
        this.showModal(false);
      });
    }

    // Close on overlay background click
    if (this.dom.modalOverlay) {
      this.dom.modalOverlay.addEventListener("click", (e) => {
        if (e.target === this.dom.modalOverlay) {
          this.showModal(false);
        }
      });
    }

    // Close on Escape key
    window.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && this.dom.modalOverlay && !this.dom.modalOverlay.classList.contains("hidden")) {
        this.showModal(false);
      }
    });

    // Mute toggles
    if (this.dom.btnMicMute) {
      this.dom.btnMicMute.addEventListener("click", () => {
        this.toggleMicMute();
      });
    }

    if (this.dom.btnSpeakerMute) {
      this.dom.btnSpeakerMute.addEventListener("click", () => {
        this.toggleSpeakerMute();
      });
    }

    // Output chime test
    if (this.dom.btnTestAudioOutput) {
      this.dom.btnTestAudioOutput.addEventListener("click", () => {
        this.playTestChime();
      });
    }

    // Cluster microphone claim
    if (this.dom.btnClaimSpeaker) {
      this.dom.btnClaimSpeaker.addEventListener("click", () => {
        this.claimSpeaker();
      });
    }

    // Device selector changes
    if (this.dom.audioInputSelect) {
      this.dom.audioInputSelect.addEventListener("change", (e) => {
        this.switchInputDevice(e.target.value);
      });
    }

    if (this.dom.audioOutputSelect) {
      this.dom.audioOutputSelect.addEventListener("change", (e) => {
        this.switchOutputDevice(e.target.value);
      });
    }
  }

  checkSecureContext() {
    const isSecure = window.isSecureContext || location.hostname === "localhost" || location.hostname === "127.0.0.1";
    if (!isSecure && this.dom.insecureBanner) {
      this.dom.insecureBanner.classList.remove("hidden");
    }
  }

  /**
   * Toggles the audio settings modal open or closed.
   */
  toggleModal() {
    if (!this.dom.modalOverlay) return;
    const isHidden = this.dom.modalOverlay.classList.contains("hidden");
    this.showModal(isHidden);
  }

  showModal(show) {
    if (!this.dom.modalOverlay) return;
    if (show) {
      this.dom.modalOverlay.classList.remove("hidden");
      this.ensurePlaybackContext();
      if (!this.isRecording) {
        this.startMicrophone().catch((err) => {
          console.warn("[AudioManager] Automatic mic startup on modal open:", err);
        });
      }
      this.refreshDevices();
      this.startVuMeter();
      if (window.lucide) {
        window.lucide.createIcons();
      }
    } else {
      this.dom.modalOverlay.classList.add("hidden");
      this.stopVuMeter();
    }
  }

  /**
   * Refreshes the list of available input and output audio devices.
   */
  async refreshDevices() {
    if (!navigator.mediaDevices || typeof navigator.mediaDevices.enumerateDevices !== "function") {
      return;
    }

    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      const inputDevices = devices.filter((d) => d.kind === "audioinput");
      const outputDevices = devices.filter((d) => d.kind === "audiooutput");

      // Update Input Select
      if (this.dom.audioInputSelect) {
        this.dom.audioInputSelect.innerHTML = "";
        if (inputDevices.length === 0) {
          const opt = document.createElement("option");
          opt.value = "";
          opt.textContent = "Default Microphone";
          this.dom.audioInputSelect.appendChild(opt);
        } else {
          inputDevices.forEach((dev, idx) => {
            const opt = document.createElement("option");
            opt.value = dev.deviceId || `mic-${idx}`;
            opt.textContent = dev.label || `Microphone ${idx + 1}`;
            if (dev.deviceId === this.selectedInputDeviceId) {
              opt.selected = true;
            }
            this.dom.audioInputSelect.appendChild(opt);
          });
        }
      }

      // Update Output Select
      if (this.dom.audioOutputSelect) {
        this.dom.audioOutputSelect.innerHTML = "";
        if (outputDevices.length === 0) {
          const opt = document.createElement("option");
          opt.value = "";
          opt.textContent = "Default System Speakers";
          this.dom.audioOutputSelect.appendChild(opt);
        } else {
          outputDevices.forEach((dev, idx) => {
            const opt = document.createElement("option");
            opt.value = dev.deviceId || `speaker-${idx}`;
            opt.textContent = dev.label || `Speaker/Headphones ${idx + 1}`;
            if (dev.deviceId === this.selectedOutputDeviceId) {
              opt.selected = true;
            }
            this.dom.audioOutputSelect.appendChild(opt);
          });
        }
      }
    } catch (err) {
      console.warn("[AudioManager] Error enumerating devices:", err);
    }
  }

  /**
   * Captures microphone with AEC and auto-gain, then sets up AudioWorklet resampling.
   */
  async startMicrophone() {
    if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== "function") {
      console.warn("[AudioManager] getUserMedia not supported in this context.");
      return;
    }

    // Stop any existing stream
    this.stopMicrophone();

    const constraints = {
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: 1,
      },
      video: false,
    };

    if (this.selectedInputDeviceId && this.selectedInputDeviceId !== "default") {
      constraints.audio.deviceId = { exact: this.selectedInputDeviceId };
    }

    try {
      this.mediaStream = await navigator.mediaDevices.getUserMedia(constraints);
    } catch (err) {
      // Fallback without exact deviceId constraint
      console.warn("[AudioManager] Failed with exact deviceId, retrying with default audio:", err);
      constraints.audio.deviceId = undefined;
      this.mediaStream = await navigator.mediaDevices.getUserMedia(constraints);
    }

    // Refresh devices now that permission is granted (to unlock friendly device labels)
    this.refreshDevices();

    // Initialize Web Audio Context
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) {
      console.warn("[AudioManager] Web Audio API not supported.");
      return;
    }

    if (!this.audioContext || this.audioContext.state === "closed") {
      this.audioContext = new AudioCtx({ latencyHint: "interactive" });
    }

    if (this.audioContext.state === "suspended") {
      await this.audioContext.resume();
    }

    // Load AudioWorklet module
    try {
      await this.audioContext.audioWorklet.addModule("js/audio-processor.js");
    } catch (workletErr) {
      console.error("[AudioManager] Failed to load AudioWorklet module:", workletErr);
      return;
    }

    // Create source node & worklet processor node
    this.sourceNode = this.audioContext.createMediaStreamSource(this.mediaStream);
    this.processorNode = new AudioWorkletNode(this.audioContext, "audio-resampler-processor", {
      processorOptions: {
        targetSampleRate: 16000,
        chunkSize: 512,
      },
    });

    // Analyser node for live VU meter
    this.analyserNode = this.audioContext.createAnalyser();
    this.analyserNode.fftSize = 256;
    this.sourceNode.connect(this.analyserNode);

    // AudioWorklet chunk message handler
    this.processorNode.port.onmessage = (event) => {
      if (event.data && event.data.type === "pcm_chunk" && event.data.buffer) {
        this.sendAudioChunk(event.data.buffer);
      }
    };

    // Apply mute state to worklet
    this.processorNode.port.postMessage({
      type: "set_muted",
      muted: this.isMicMuted,
    });

    // Connect source to worklet (do NOT connect to audioContext.destination to avoid local mic echo)
    this.sourceNode.connect(this.processorNode);
    this.isRecording = true;
    this.updateStatusDot();
  }

  stopMicrophone() {
    this.isRecording = false;
    if (this.mediaStream) {
      this.mediaStream.getTracks().forEach((track) => track.stop());
      this.mediaStream = null;
    }
    if (this.sourceNode) {
      try {
        this.sourceNode.disconnect();
      } catch (_) {}
      this.sourceNode = null;
    }
    if (this.processorNode) {
      try {
        this.processorNode.disconnect();
      } catch (_) {}
      this.processorNode = null;
    }
    if (this.micVuFill) {
      this.micVuFill.style.width = "0%";
    }
    this.updateStatusDot();
  }

  async switchInputDevice(deviceId) {
    this.selectedInputDeviceId = deviceId;
    localStorage.setItem("gconnect_audio_input_device_id", deviceId);
    if (this.isRecording) {
      await this.startMicrophone();
    }
  }

  /**
   * Initializes playback AudioContext, gain node, and sink routing.
   */
  ensurePlaybackContext() {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;

    if (!this.playbackAudioContext || this.playbackAudioContext.state === "closed") {
      this.playbackAudioContext = new AudioCtx({ latencyHint: "interactive" });
    }

    if (this.playbackAudioContext.state === "suspended") {
      this.playbackAudioContext.resume().catch(() => {});
    }

    if (!this.speakerGainNode) {
      this.speakerGainNode = this.playbackAudioContext.createGain();
      this.speakerGainNode.gain.value = this.isSpeakerMuted ? 0 : 1;
      this.applyOutputSink(this.selectedOutputDeviceId);
    }
  }

  async applyOutputSink(deviceId) {
    this.ensurePlaybackContext();
    if (!this.playbackAudioContext || !this.speakerGainNode) return;

    // Modern Chrome/Edge/WebView2 direct setSinkId on AudioContext
    if (typeof this.playbackAudioContext.setSinkId === "function") {
      try {
        if (deviceId && deviceId !== "default") {
          await this.playbackAudioContext.setSinkId(deviceId);
        } else {
          await this.playbackAudioContext.setSinkId("");
        }
        this.speakerGainNode.disconnect();
        this.speakerGainNode.connect(this.playbackAudioContext.destination);
        return;
      } catch (err) {
        console.warn("[AudioManager] AudioContext.setSinkId failed, trying audio element fallback:", err);
      }
    }

    // Fallback via MediaStreamAudioDestinationNode + HTMLAudioElement
    this.ensureSinkAudioElement();
    if (this.sinkAudioElement) {
      try {
        this.speakerGainNode.disconnect();
        if (!this.speakerDestinationNode) {
          this.speakerDestinationNode = this.playbackAudioContext.createMediaStreamDestination();
        }
        this.speakerGainNode.connect(this.speakerDestinationNode);
        this.sinkAudioElement.srcObject = this.speakerDestinationNode.stream;
        this.sinkAudioElement.play().catch(() => {});

        if (typeof this.sinkAudioElement.setSinkId === "function" && deviceId && deviceId !== "default") {
          await this.sinkAudioElement.setSinkId(deviceId);
        }
      } catch (err) {
        console.warn("[AudioManager] Fallback audio sink setup failed:", err);
        // Fallback to standard destination
        this.speakerGainNode.disconnect();
        this.speakerGainNode.connect(this.playbackAudioContext.destination);
      }
    } else {
      this.speakerGainNode.disconnect();
      this.speakerGainNode.connect(this.playbackAudioContext.destination);
    }
  }

  ensureSinkAudioElement() {
    if (!this.sinkAudioElement) {
      let el = document.getElementById("audioOutputSinkElement");
      if (!el) {
        el = document.createElement("audio");
        el.id = "audioOutputSinkElement";
        el.autoplay = true;
        el.style.display = "none";
        document.body.appendChild(el);
      }
      this.sinkAudioElement = el;
    }
  }

  async switchOutputDevice(deviceId) {
    this.selectedOutputDeviceId = deviceId;
    localStorage.setItem("gconnect_audio_output_device_id", deviceId);
    await this.applyOutputSink(deviceId);
  }

  /**
   * Plays incoming 16kHz 16-bit mono PCM binary chunk from the container sandbox.
   */
  playIncomingChunk(arrayBuffer) {
    if (this.isSpeakerMuted) return;
    this.ensurePlaybackContext();
    if (!this.playbackAudioContext || !this.speakerGainNode) return;

    if (this.playbackAudioContext.state === "suspended") {
      this.playbackAudioContext.resume().catch(() => {});
    }

    const int16 = new Int16Array(arrayBuffer);
    if (int16.length === 0) return;

    // Convert Int16 to Float32 [-1.0, 1.0]
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) {
      const s = int16[i];
      float32[i] = s < 0 ? s / 0x8000 : s / 0x7FFF;
    }

    // Schedule seamlessly
    const audioBuffer = this.playbackAudioContext.createBuffer(1, float32.length, 16000);
    audioBuffer.getChannelData(0).set(float32);

    const source = this.playbackAudioContext.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(this.speakerGainNode);

    const now = this.playbackAudioContext.currentTime;
    // Keep 20ms of lead buffer to smooth network packet jitter; clamp upper-bound against bursts or tab throttling
    if (this.nextPlaybackTime < now || this.nextPlaybackTime > now + 0.20) {
      this.nextPlaybackTime = now + 0.02;
    }

    source.start(this.nextPlaybackTime);
    this.nextPlaybackTime += audioBuffer.duration;
  }

  /**
   * Connects to the /ws/audio WebSocket hub on the host daemon.
   */
  connectWebSocket() {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    clearTimeout(this.wsReconnectTimer);
    clearInterval(this.wsPingTimer);

    let url = "";
    if (window.authManager && typeof window.authManager.wsUrl === "function") {
      url = window.authManager.wsUrl("/ws/audio");
    } else {
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      url = `${proto}//${location.host}/ws/audio`;
    }

    try {
      this.ws = new WebSocket(url);
      this.ws.binaryType = "arraybuffer";

      this.ws.onopen = () => {
        this.reconnectAttempts = 0;
        this.updateStatusDot();
        if (this.dom.audioConnectionBadge) {
          this.dom.audioConnectionBadge.textContent = "Connected";
          this.dom.audioConnectionBadge.className = "audio-badge connected";
        }

        // Start keepalive ping every 15 seconds
        this.wsPingTimer = setInterval(() => {
          this.sendJson({ type: "ping", timestamp: Date.now() });
        }, 15000);

        // Sync initial mute status
        this.sendJson({ type: "mute", muted: this.isMicMuted });
      };

      this.ws.onmessage = (event) => {
        if (event.data instanceof ArrayBuffer) {
          // Binary frame: Container audio playback
          this.playIncomingChunk(event.data);
        } else if (typeof event.data === "string") {
          // Text frame: JSON signaling
          try {
            const data = JSON.parse(event.data);
            this.handleSignaling(data);
          } catch (jsonErr) {
            console.warn("[AudioManager] Invalid JSON signaling received:", jsonErr);
          }
        }
      };

      this.ws.onclose = () => {
        clearInterval(this.wsPingTimer);
        this.updateStatusDot();
        if (this.dom.audioConnectionBadge) {
          this.dom.audioConnectionBadge.textContent = "Disconnected";
          this.dom.audioConnectionBadge.className = "audio-badge disconnected";
        }
        this.scheduleReconnect();
      };

      this.ws.onerror = (err) => {
        console.debug("[AudioManager] WebSocket error:", err);
      };
    } catch (err) {
      console.warn("[AudioManager] WebSocket creation failed:", err);
      this.scheduleReconnect();
    }
  }

  scheduleReconnect() {
    clearTimeout(this.wsReconnectTimer);
    const delay = Math.min(10000, 1500 * Math.pow(1.5, this.reconnectAttempts++));
    this.wsReconnectTimer = setTimeout(() => {
      this.connectWebSocket();
    }, delay);
  }

  sendJson(payload) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      try {
        this.ws.send(JSON.stringify(payload));
      } catch (err) {
        console.warn("[AudioManager] Failed to send JSON payload:", err);
      }
    }
  }

  sendAudioChunk(arrayBuffer) {
    if (this.isMicMuted) return;
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      try {
        this.ws.send(arrayBuffer);
      } catch (err) {
        console.warn("[AudioManager] Failed to send audio chunk:", err);
      }
    }
  }

  handleSignaling(data) {
    if (!data || !data.type) return;

    if (data.type === "speaker_status") {
      this.activeSpeakerId = data.active_client_id || "";
      this.activeSpeakerName = data.active_client_name || "None";
      this.isYouActiveSpeaker = !!data.is_you;

      if (this.dom.clusterActiveSpeakerLabel) {
        if (this.isYouActiveSpeaker) {
          this.dom.clusterActiveSpeakerLabel.textContent = `Active: This PC (${this.activeSpeakerName}) - Broadcasting`;
        } else if (this.activeSpeakerName && this.activeSpeakerName !== "None") {
          this.dom.clusterActiveSpeakerLabel.textContent = `Active: ${this.activeSpeakerName}`;
        } else {
          this.dom.clusterActiveSpeakerLabel.textContent = "No active broadcaster";
        }
      }

      if (this.dom.btnClaimSpeaker) {
        if (this.isYouActiveSpeaker) {
          this.dom.btnClaimSpeaker.classList.add("btn-claimed");
          this.dom.btnClaimSpeaker.innerHTML = '<i data-lucide="check" class="icon-xs"></i> <span>Broadcasting</span>';
        } else {
          this.dom.btnClaimSpeaker.classList.remove("btn-claimed");
          this.dom.btnClaimSpeaker.innerHTML = '<i data-lucide="radio" class="icon-xs"></i> <span>Claim Microphone</span>';
        }
        if (window.lucide) {
          window.lucide.createIcons();
        }
      }

      this.updateStatusDot();
    } else if (data.type === "ducking_status") {
      this.isDucking = !!data.ducking;
      if (this.dom.audioDuckingBadge) {
        if (this.isDucking) {
          this.dom.audioDuckingBadge.classList.remove("hidden");
          this.dom.audioDuckingBadge.textContent = "Ducking (-18dB)";
        } else {
          this.dom.audioDuckingBadge.classList.add("hidden");
        }
      }
    }
  }

  /**
   * Claims active microphone broadcasting in a multi-client cluster.
   */
  claimSpeaker() {
    const myName = (window.authManager && window.authManager.getClientId()) || "Client";
    this.sendJson({
      type: "claim_speaker",
      client_name: myName,
    });
  }

  /**
   * Toggles microphone mute state and transmits update to AudioWorklet and WebSocket.
   */
  toggleMicMute() {
    this.isMicMuted = !this.isMicMuted;
    localStorage.setItem("gconnect_audio_mic_muted", String(this.isMicMuted));

    // Notify AudioWorklet processor
    if (this.processorNode) {
      this.processorNode.port.postMessage({
        type: "set_muted",
        muted: this.isMicMuted,
      });
    }

    // Notify backend router
    this.sendJson({
      type: "mute",
      muted: this.isMicMuted,
    });

    this.updateMicUi();
    this.updateStatusDot();
  }

  updateMicUi() {
    if (this.dom.btnMicMute) {
      if (this.isMicMuted) {
        this.dom.btnMicMute.classList.add("btn-muted");
        this.dom.btnMicMute.innerHTML = '<i data-lucide="mic-off" class="icon-xs"></i> <span>Unmute</span>';
      } else {
        this.dom.btnMicMute.classList.remove("btn-muted");
        this.dom.btnMicMute.innerHTML = '<i data-lucide="mic" class="icon-xs"></i> <span>Mute</span>';
      }
    }

    if (this.dom.audioBtnIcon) {
      if (this.isMicMuted) {
        this.dom.audioBtnIcon.setAttribute("data-lucide", "mic-off");
      } else {
        this.dom.audioBtnIcon.setAttribute("data-lucide", "mic");
      }
    }

    if (window.lucide) {
      window.lucide.createIcons();
    }
  }

  /**
   * Toggles speaker mute state.
   */
  toggleSpeakerMute() {
    this.isSpeakerMuted = !this.isSpeakerMuted;
    localStorage.setItem("gconnect_audio_speaker_muted", String(this.isSpeakerMuted));

    if (this.speakerGainNode) {
      this.speakerGainNode.gain.value = this.isSpeakerMuted ? 0 : 1;
    }

    this.updateSpeakerUi();
  }

  updateSpeakerUi() {
    if (this.dom.btnSpeakerMute) {
      if (this.isSpeakerMuted) {
        this.dom.btnSpeakerMute.classList.add("btn-muted");
        this.dom.btnSpeakerMute.innerHTML = '<i data-lucide="volume-x" class="icon-xs"></i> <span>Unmute</span>';
      } else {
        this.dom.btnSpeakerMute.classList.remove("btn-muted");
        this.dom.btnSpeakerMute.innerHTML = '<i data-lucide="volume-2" class="icon-xs"></i> <span>Mute</span>';
      }
    }

    if (window.lucide) {
      window.lucide.createIcons();
    }
  }

  /**
   * Synthesizes a clean two-tone chime to test the selected speakers/headphones.
   */
  playTestChime() {
    this.ensurePlaybackContext();
    if (!this.playbackAudioContext || !this.speakerGainNode) return;

    if (this.playbackAudioContext.state === "suspended") {
      this.playbackAudioContext.resume().catch(() => {});
    }

    const ctx = this.playbackAudioContext;
    const now = ctx.currentTime;

    const osc = ctx.createOscillator();
    const gain = ctx.createGain();

    osc.type = "sine";
    // Tone 1: 587.33 Hz (D5), Tone 2: 880.00 Hz (A5)
    osc.frequency.setValueAtTime(587.33, now);
    osc.frequency.setValueAtTime(880.0, now + 0.15);

    gain.gain.setValueAtTime(0.001, now);
    gain.gain.exponentialRampToValueAtTime(0.3, now + 0.04);
    gain.gain.setValueAtTime(0.3, now + 0.15);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.4);

    osc.connect(gain);
    gain.connect(this.speakerGainNode);

    osc.start(now);
    osc.stop(now + 0.42);
  }

  /**
   * Updates the visual status dot in the header button.
   */
  updateStatusDot() {
    if (!this.dom.audioStatusDot) return;

    this.dom.audioStatusDot.className = "audio-status-dot";

    const isConnected = this.ws && this.ws.readyState === WebSocket.OPEN;
    if (!isConnected) {
      this.dom.audioStatusDot.classList.add("offline");
      this.dom.audioStatusDot.title = "Audio Stream: Disconnected";
      return;
    }

    if (this.isMicMuted) {
      this.dom.audioStatusDot.classList.add("muted");
      this.dom.audioStatusDot.title = "Audio Stream: Connected (Mic Muted)";
    } else if (this.isYouActiveSpeaker) {
      this.dom.audioStatusDot.classList.add("broadcasting");
      this.dom.audioStatusDot.title = "Audio Stream: Active Broadcaster (Live)";
    } else {
      this.dom.audioStatusDot.classList.add("connected");
      this.dom.audioStatusDot.title = "Audio Stream: Connected (Listening)";
    }
  }

  /**
   * Live VU meter animation loop.
   */
  startVuMeter() {
    if (this.vuAnimationId) return;

    const updateVu = () => {
      if (this.analyserNode && !this.isMicMuted && this.isRecording) {
        if (!this.vuDataArray) {
          this.vuDataArray = new Uint8Array(this.analyserNode.frequencyBinCount);
        }
        this.analyserNode.getByteTimeDomainData(this.vuDataArray);

        let sumSquares = 0;
        for (let i = 0; i < this.vuDataArray.length; i++) {
          const val = (this.vuDataArray[i] - 128) / 128;
          sumSquares += val * val;
        }
        const rms = Math.sqrt(sumSquares / this.vuDataArray.length);
        const level = Math.min(100, Math.round(rms * 280));
        if (this.dom.micVuFill) {
          this.dom.micVuFill.style.width = `${level}%`;
        }
      } else {
        if (this.dom.micVuFill) {
          this.dom.micVuFill.style.width = "0%";
        }
      }

      this.vuAnimationId = requestAnimationFrame(updateVu);
    };

    this.vuAnimationId = requestAnimationFrame(updateVu);
  }

  stopVuMeter() {
    if (this.vuAnimationId) {
      cancelAnimationFrame(this.vuAnimationId);
      this.vuAnimationId = null;
    }
    if (this.dom.micVuFill) {
      this.dom.micVuFill.style.width = "0%";
    }
  }
}

// Global AudioManager instance
window.audioManager = new AudioManager();
