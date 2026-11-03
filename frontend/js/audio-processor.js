/**
 * audio-processor.js - AudioWorklet resampler for GConnect.
 *
 * Runs on the high-priority AudioWorklet rendering thread.
 * Downsamples hardware microphone input (44.1kHz / 48kHz) to exactly 16kHz
 * 16-bit signed mono PCM using fractional linear interpolation.
 * Packages and dispatches 512-sample (1024-byte, 32ms) binary chunks to the main
 * thread via zero-copy ArrayBuffer transfer.
 */

class AudioResamplerProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.targetSampleRate = (options && options.processorOptions && options.processorOptions.targetSampleRate) || 16000;
    this.chunkSize = (options && options.processorOptions && options.processorOptions.chunkSize) || 512;
    // sampleRate is provided by AudioWorkletGlobalScope
    this.ratio = sampleRate / this.targetSampleRate;
    this.inputBuffer = [];
    this.outputBuffer = new Int16Array(this.chunkSize);
    this.outputIndex = 0;
    this.fractionalIndex = 0;
    this.isMuted = false;

    this.port.onmessage = (event) => {
      if (event.data && event.data.type === "set_muted") {
        this.isMuted = !!event.data.muted;
        if (this.isMuted) {
          this.inputBuffer = [];
          this.fractionalIndex = 0;
          this.outputIndex = 0;
        }
      }
    };
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0] || input[0].length === 0) {
      return true;
    }
    if (this.isMuted) {
      return true;
    }

    const channelData = input[0];
    for (let i = 0; i < channelData.length; i++) {
      this.inputBuffer.push(channelData[i]);
    }

    while (this.fractionalIndex + 1 < this.inputBuffer.length) {
      const idx = Math.floor(this.fractionalIndex);
      const frac = this.fractionalIndex - idx;
      const s0 = this.inputBuffer[idx];
      const s1 = this.inputBuffer[idx + 1] !== undefined ? this.inputBuffer[idx + 1] : s0;
      const sample = s0 + frac * (s1 - s0);

      // Quantize Float32 [-1.0, 1.0] to 16-bit signed Int16 [-32768, 32767]
      const clamped = Math.max(-1.0, Math.min(1.0, sample));
      this.outputBuffer[this.outputIndex++] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7FFF;

      if (this.outputIndex >= this.chunkSize) {
        // Zero-copy transfer of ArrayBuffer to main thread
        const chunkToSend = new Int16Array(this.outputBuffer);
        this.port.postMessage(
          { type: "pcm_chunk", buffer: chunkToSend.buffer },
          [chunkToSend.buffer]
        );
        this.outputIndex = 0;
      }

      this.fractionalIndex += this.ratio;
    }

    // Prune consumed input samples
    const consumed = Math.min(Math.floor(this.fractionalIndex), this.inputBuffer.length);
    if (consumed > 0) {
      this.inputBuffer.splice(0, consumed);
      this.fractionalIndex -= consumed;
    }

    return true;
  }
}

registerProcessor("audio-resampler-processor", AudioResamplerProcessor);
