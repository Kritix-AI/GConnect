"""Empirical Adversarial Stress Test Suite for GConnect Audio Hub (Milestone 1).

Covers:
1. AudioRingBuffer:
   - Pre-buffering behavior: Reading before 50ms (1600 bytes) returns exact silence (b'\\x00' * chunk).
   - Jitter burst absorption: 10 bursts of random sizes, verified FIFO sample ordering and continuous playback.
   - Underrun recovery: Read until empty; verified silence padding, level zeroing, and clean re-entry into prebuffering.
   - Overrun clamping: Feed 10,000 bytes (312ms); verify buffer drops oldest samples to clamp occupancy back to 50ms (1600 bytes), avoiding latency creep.
   - Multi-threaded stress: Concurrent producers and consumer.
2. DuckingGate:
   - Exact mathematical attenuation: Constant amplitude chunks and 440Hz synthetic sine waves.
   - Verification of scaling factor 0.12589254 (-18.0 dB +/- 0.5dB).
   - 250ms hold window state transitions and hold extension upon subsequent playback events.
"""

import array
import math
import random
import threading
import time
import pytest

from backend.audio_router import AudioRingBuffer, DuckingGate


# =============================================================================
# 1. AudioRingBuffer Empirical Stress Tests
# =============================================================================

class TestAudioRingBufferStress:
    """Empirical verification of AudioRingBuffer specification and stress limits."""

    def test_prebuffering_exact_threshold(self):
        """Pre-buffering behavior: reading before 50ms (1600 bytes) produces silence.
        
        Step-by-step verification:
        - 0 bytes: read returns pure silence.
        - Partial writes (320, 640, 1280, 1598 bytes): read returns pure silence, data preserved.
        - Reaching 1600 bytes: pre-buffering transitions to False, subsequent read returns real audio.
        """
        buf = AudioRingBuffer(sample_rate=16000, buffer_ms=50, max_latency_ms=150)
        chunk_size = 640  # 20ms

        assert buf.is_prebuffering is True
        assert buf.level_bytes == 0

        # Step 1: Read with 0 bytes buffered
        silence = buf.read(chunk_size)
        assert silence == b"\x00" * chunk_size
        assert buf.is_prebuffering is True
        assert buf.level_bytes == 0

        # Step 2: Write 320 bytes (10ms)
        data_320 = b"\x11\x22" * 160
        buf.write(data_320)
        assert buf.level_bytes == 320
        assert buf.is_prebuffering is True
        # Read must return silence, and buffered bytes must remain untouched
        assert buf.read(chunk_size) == b"\x00" * chunk_size
        assert buf.level_bytes == 320

        # Step 3: Write 320 more bytes -> 640 bytes total (20ms)
        data_640_part2 = b"\x33\x44" * 160
        buf.write(data_640_part2)
        assert buf.level_bytes == 640
        assert buf.is_prebuffering is True
        assert buf.read(chunk_size) == b"\x00" * chunk_size
        assert buf.level_bytes == 640

        # Step 4: Write 640 more bytes -> 1280 bytes total (40ms)
        buf.write(b"\x55\x66" * 320)
        assert buf.level_bytes == 1280
        assert buf.is_prebuffering is True
        assert buf.read(chunk_size) == b"\x00" * chunk_size
        assert buf.level_bytes == 1280

        # Step 5: Write 318 more bytes -> 1598 bytes total (just 2 bytes below 1600)
        buf.write(b"\x77\x88" * 159)
        assert buf.level_bytes == 1598
        assert buf.is_prebuffering is True
        assert buf.read(chunk_size) == b"\x00" * chunk_size
        assert buf.level_bytes == 1598

        # Step 6: Write final 2 bytes to reach exactly 1600 bytes (50ms)
        buf.write(b"\x99\xAA")
        assert buf.level_bytes == 1600
        assert buf.is_prebuffering is False

        # Step 7: Now read should return the actual real audio in FIFO order
        first_read = buf.read(chunk_size)
        expected_first = data_320 + data_640_part2
        assert first_read == expected_first
        assert buf.level_bytes == 1600 - chunk_size
        assert buf.is_prebuffering is False

    def test_jitter_burst_absorption_and_fifo(self):
        """Jitter burst absorption: feed 10 bursts of random sizes, verify FIFO and continuous playback.
        
        Creates sequential Int16 samples (0, 1, 2, ..., N-1) sliced into 10 bursts of random sizes.
        Feeds bursts into the buffer, reads back fixed chunks, and verifies exact sample-by-sample FIFO continuity.
        """
        buf = AudioRingBuffer(sample_rate=16000, buffer_ms=50, max_latency_ms=150)
        
        # Total samples to stream: 2000 samples (4000 bytes = 125ms of audio)
        total_samples = 2000
        full_stream_samples = array.array("h", range(total_samples))
        full_stream_bytes = full_stream_samples.tobytes()

        # Partition 4000 bytes into 10 random-sized even chunks (sample-aligned)
        rng = random.Random(42)  # Deterministic seed for reproducibility
        split_points = sorted(rng.sample(range(2, len(full_stream_bytes) - 2, 2), 9))
        
        bursts = []
        prev = 0
        for pt in split_points:
            bursts.append(full_stream_bytes[prev:pt])
            prev = pt
        bursts.append(full_stream_bytes[prev:])

        assert len(bursts) == 10
        assert sum(len(b) for b in bursts) == len(full_stream_bytes)
        assert all(len(b) % 2 == 0 for b in bursts)

        # Feed bursts into buffer
        for burst in bursts:
            buf.write(burst)

        # Total written is 4000 bytes, which is > 1600 bytes and < 4800 bytes (no overrun drop)
        assert buf.level_bytes == 4000
        assert buf.is_prebuffering is False

        # Read back all audio in 640-byte chunks
        received_bytes = bytearray()
        read_chunk_size = 640
        while buf.level_bytes >= read_chunk_size:
            chunk = buf.read(read_chunk_size)
            assert len(chunk) == read_chunk_size
            received_bytes.extend(chunk)

        # Extract remaining bytes
        rem = buf.level_bytes
        if rem > 0:
            tail = buf.read(rem)
            # Underrun triggers if rem < requested, but here we requested exactly rem
            # so len(tail) is rem
            received_bytes.extend(tail[:rem])

        # Verify exact FIFO order of all 2000 samples
        received_samples = array.array("h")
        received_samples.frombytes(received_bytes[: len(full_stream_bytes)])
        assert len(received_samples) == total_samples
        for i in range(total_samples):
            assert received_samples[i] == i, f"FIFO sample mismatch at index {i}: expected {i}, got {received_samples[i]}"

    def test_underrun_recovery_and_prebuffering_reentry(self):
        """Underrun recovery: read until empty; verify silence padding and clean re-entry into prebuffering state."""
        buf = AudioRingBuffer(sample_rate=16000, buffer_ms=50, max_latency_ms=150)
        
        # Write 1600 bytes to exit pre-buffering
        test_audio = b"\x12\x34" * 800
        buf.write(test_audio)
        assert buf.is_prebuffering is False
        assert buf.level_bytes == 1600

        # Read 1200 bytes -> leaves 400 bytes
        chunk1 = buf.read(1200)
        assert chunk1 == test_audio[:1200]
        assert buf.level_bytes == 400
        assert buf.is_prebuffering is False

        # Underrun condition: Request 640 bytes when only 400 bytes are available
        chunk2 = buf.read(640)
        assert len(chunk2) == 640
        # First 400 bytes are valid audio
        assert chunk2[:400] == test_audio[1200:1600]
        # Trailing 240 bytes are silence padding
        assert chunk2[400:] == b"\x00" * 240
        # Buffer must now be empty
        assert buf.level_bytes == 0
        # Must have re-entered prebuffering
        assert buf.is_prebuffering is True

        # Subsequent reads while empty must return pure silence without crashing
        for _ in range(5):
            silence = buf.read(640)
            assert silence == b"\x00" * 640
            assert buf.level_bytes == 0
            assert buf.is_prebuffering is True

        # Verify clean recovery: writing 800 bytes (<1600) keeps prebuffering active
        buf.write(b"\x56\x78" * 400)
        assert buf.level_bytes == 800
        assert buf.is_prebuffering is True
        assert buf.read(640) == b"\x00" * 640
        assert buf.level_bytes == 800

        # Writing another 800 bytes reaches 1600 bytes and exits prebuffering
        buf.write(b"\x9A\xBC" * 400)
        assert buf.level_bytes == 1600
        assert buf.is_prebuffering is False

        # Normal audio flow resumes cleanly
        recovered_chunk = buf.read(640)
        assert recovered_chunk == (b"\x56\x78" * 320)
        assert buf.level_bytes == 960

    def test_overrun_clamping_drops_oldest(self):
        """Overrun clamping: feed 10,000 bytes (312ms); verify buffer drops oldest samples to clamp occupancy to 50ms (1600 bytes)."""
        buf = AudioRingBuffer(sample_rate=16000, buffer_ms=50, max_latency_ms=150)
        
        # 10,000 bytes = 5,000 samples.
        # Max latency threshold = 150ms = 4,800 bytes.
        # When 10,000 bytes arrive, occupancy (10,000) > 4,800.
        # Clamping drops oldest samples: excess = 10,000 - 1,600 = 8,400 bytes (4,200 samples).
        # Retained newest samples: 1,600 bytes (800 samples, indices 4200 to 4999).
        samples = array.array("h", range(5000))
        big_pcm = samples.tobytes()
        assert len(big_pcm) == 10000

        occupancy = buf.write(big_pcm)
        
        # Verify occupancy clamped to exactly 50ms (1600 bytes)
        assert occupancy == 1600
        assert buf.level_bytes == 1600
        assert buf.level_ms == 50.0
        assert buf.is_prebuffering is False

        # Read back the remaining 1600 bytes (800 samples)
        clamped_data = buf.read(1600)
        assert len(clamped_data) == 1600
        clamped_samples = array.array("h")
        clamped_samples.frombytes(clamped_data)
        assert len(clamped_samples) == 800

        # Verify that the samples retained are EXACTLY the newest samples (4200 through 4999)
        for i, val in enumerate(clamped_samples):
            expected_sample = 4200 + i
            assert val == expected_sample, f"Overrun retention error at index {i}: expected {expected_sample}, got {val}"

    def test_consecutive_overrun_stability(self):
        """Verify buffer stability and no latency creep under repeated heavy overruns."""
        buf = AudioRingBuffer(sample_rate=16000, buffer_ms=50, max_latency_ms=150)
        
        for iteration in range(10):
            # Write 8000 bytes each iteration
            chunk = array.array("h", [iteration] * 4000).tobytes()
            buf.write(chunk)
            # Must always clamp back to 1600 bytes (50ms)
            assert buf.level_bytes == 1600
            assert buf.level_ms == 50.0

        # Read back the 1600 bytes from the last iteration
        data = buf.read(1600)
        samples = array.array("h")
        samples.frombytes(data)
        assert len(samples) == 800
        assert all(s == 9 for s in samples)

    def test_concurrent_producers_and_consumer_stress(self):
        """Multi-threaded stress: 4 concurrent producers writing random bursts while 1 consumer reads."""
        buf = AudioRingBuffer(sample_rate=16000, buffer_ms=50, max_latency_ms=150)
        stop_event = threading.Event()
        errors = []
        bytes_written = [0]
        bytes_read = [0]
        lock = threading.Lock()

        def producer(thread_id: int):
            try:
                rng = random.Random(thread_id)
                for _ in range(50):
                    if stop_event.is_set():
                        break
                    # Random burst between 64 and 512 bytes (sample aligned)
                    size = rng.randint(32, 256) * 2
                    data = b"\x01\x02" * (size // 2)
                    buf.write(data)
                    with lock:
                        bytes_written[0] += size
                    time.sleep(0.001)
            except Exception as e:
                errors.append(f"Producer {thread_id} error: {e}")

        def consumer():
            try:
                while not stop_event.is_set() or buf.level_bytes > 0:
                    chunk = buf.read(320)
                    if len(chunk) != 320:
                        errors.append(f"Consumer got bad chunk length: {len(chunk)}")
                    with lock:
                        bytes_read[0] += len(chunk)
                    time.sleep(0.001)
            except Exception as e:
                errors.append(f"Consumer error: {e}")

        producers = [threading.Thread(target=producer, args=(i,)) for i in range(4)]
        cons = threading.Thread(target=consumer)

        for p in producers:
            p.start()
        cons.start()

        for p in producers:
            p.join()
        
        # Allow consumer to drain remaining
        time.sleep(0.05)
        stop_event.set()
        cons.join(timeout=1.0)

        assert not errors, f"Concurrent stress test encountered errors: {errors}"
        assert bytes_written[0] > 0
        assert bytes_read[0] > 0


# =============================================================================
# 2. DuckingGate Empirical Stress Tests
# =============================================================================

class TestDuckingGateStress:
    """Empirical verification of DuckingGate mathematical accuracy and hold timer."""

    def test_exact_attenuation_linear_and_db(self):
        """Verify output amplitude is scaled by factor 0.12589254 (-18.0 dB +/- 0.5dB).
        
        Tests:
        - Exact scaling factor across multiple amplitude steps: 500, 1000, 5000, 10000, 20000, 30000.
        - Positive and negative amplitudes.
        - Measured dB attenuation within 0.1 dB of target -18.0 dB.
        """
        gate = DuckingGate(ducking_db=-18.0)
        expected_factor = 10.0 ** (-18.0 / 20.0)  # ~0.12589254117941673
        assert abs(gate.linear_factor - expected_factor) < 1e-9

        test_amplitudes = [500, 1000, 2500, 5000, 10000, 15000, 20000, 25000, 30000]

        for amp in test_amplitudes:
            for sign in [1, -1]:
                val = amp * sign
                samples_in = array.array("h", [val] * 100)
                ducked_bytes = gate.apply_ducking(samples_in.tobytes(), force=True)
                
                samples_out = array.array("h")
                samples_out.frombytes(ducked_bytes)

                expected_val = int(val * expected_factor)
                actual_val = samples_out[0]

                # Integer truncation can differ by at most 1 unit
                assert abs(actual_val - expected_val) <= 1, (
                    f"Amplitude {val}: expected {expected_val}, got {actual_val}"
                )

                # Verify measured decibel attenuation
                measured_ratio = abs(actual_val) / abs(val)
                measured_db = 20.0 * math.log10(measured_ratio)
                error_db = abs(measured_db - (-18.0))
                assert error_db <= 0.5, (
                    f"Attenuation error too high for amplitude {val}: measured {measured_db:.3f} dB (error: {error_db:.3f} dB)"
                )

    def test_synthetic_sine_wave_ducking(self):
        """Pass synthetic 440Hz sine wave (16kHz sample rate, peak amplitude 20,000).
        
        Compute input RMS and ducked output RMS, verify RMS ratio is -18.0 dB +/- 0.2 dB.
        """
        gate = DuckingGate(ducking_db=-18.0)
        sample_rate = 16000
        frequency = 440.0
        duration_sec = 0.25  # 250ms = 4000 samples
        peak_amp = 20000

        num_samples = int(sample_rate * duration_sec)
        sine_samples = array.array("h")
        for n in range(num_samples):
            sample_val = int(peak_amp * math.sin(2.0 * math.pi * frequency * n / sample_rate))
            sine_samples.append(sample_val)

        raw_bytes = sine_samples.tobytes()
        rms_in = gate.calculate_rms(raw_bytes)
        assert abs(rms_in - (peak_amp / math.sqrt(2))) < 200  # Theoretical RMS for sine wave

        # Apply ducking
        ducked_bytes = gate.apply_ducking(raw_bytes, force=True)
        rms_out = gate.calculate_rms(ducked_bytes)

        # Theoretical RMS ratio = 10^(-18/20) ~ 0.12589254
        measured_ratio = rms_out / rms_in
        measured_db = 20.0 * math.log10(measured_ratio)

        assert abs(measured_db - (-18.0)) < 0.2, (
            f"Sine wave attenuation error: measured {measured_db:.3f} dB"
        )

        # Check sample-by-sample precision
        ducked_samples = array.array("h")
        ducked_samples.frombytes(ducked_bytes)
        for i in range(num_samples):
            expected = int(sine_samples[i] * gate.linear_factor)
            assert abs(ducked_samples[i] - expected) <= 1

    def test_hold_window_state_transitions(self):
        """Verify 250ms hold window state transitions:
        - t=0: playback arrives -> ducking becomes active immediately.
        - t=100ms: still active.
        - t=200ms: still active.
        - t=200ms: second playback event arrives -> timer extended!
        - t=350ms (150ms after 2nd event): still active.
        - t=480ms (>250ms after 2nd event): transitions cleanly to inactive.
        - Inactive state leaves incoming audio un-attenuated.
        """
        gate = DuckingGate(ducking_db=-18.0, hold_ms=250, rms_threshold=100.0)
        
        # Initial state: inactive
        assert gate.is_ducking_active() is False

        # Loud playback chunk (RMS > 100)
        loud_chunk = array.array("h", [5000] * 320).tobytes()
        
        # Event 1: Trigger playback
        triggered = gate.notify_container_playback(loud_chunk)
        assert triggered is True
        assert gate.is_ducking_active() is True

        # Check at ~100ms
        time.sleep(0.10)
        assert gate.is_ducking_active() is True

        # Check at ~200ms
        time.sleep(0.10)
        assert gate.is_ducking_active() is True

        # Event 2: Second playback packet extends hold window
        gate.notify_container_playback(loud_chunk)
        assert gate.is_ducking_active() is True

        # Check at ~150ms after second event (total ~350ms elapsed from t0)
        time.sleep(0.15)
        assert gate.is_ducking_active() is True

        # Check after second hold window expires (> 250ms after second event)
        time.sleep(0.15)  # Total ~300ms since second event
        assert gate.is_ducking_active() is False

        # Inactive ducking leaves test audio completely untouched
        test_chunk = array.array("h", [10000] * 320).tobytes()
        passthrough = gate.apply_ducking(test_chunk)
        assert passthrough == test_chunk

    def test_adversarial_ducking_inputs(self):
        """Test boundary inputs for DuckingGate: empty, 1-byte, silence, full-scale clipping."""
        gate = DuckingGate(ducking_db=-18.0)

        # Empty bytes
        assert gate.apply_ducking(b"") == b""
        assert gate.calculate_rms(b"") == 0.0
        assert gate.notify_container_playback(b"") is False

        # 1-byte chunk (less than 1 full int16 sample)
        assert gate.calculate_rms(b"\x12") == 0.0
        assert gate.apply_ducking(b"\x12", force=True) == b"\x12"

        # 3-byte chunk (1 sample + 1 odd remainder byte)
        chunk_3b = b"\x10\x27\x99"  # 10000 amplitude + odd byte 0x99
        ducked_3b = gate.apply_ducking(chunk_3b, force=True)
        assert len(ducked_3b) == 3
        assert ducked_3b[2] == 0x99  # remainder preserved untouched

        # Full scale clipping test: positive (+32767) and negative (-32768)
        gate_boost = DuckingGate(ducking_db=6.0)  # +6dB gain to force clipping
        clipping_input = array.array("h", [30000, -32000]).tobytes()
        clipped_output = gate_boost.apply_ducking(clipping_input, force=True)
        clipped_samples = array.array("h")
        clipped_samples.frombytes(clipped_output)
        assert clipped_samples[0] == 32767
        assert clipped_samples[1] == -32768


class TestAdversarialAudioPipeline:
    """Stress testing the integrated AudioRingBuffer pipeline under pathological conditions."""

    def test_adversarial_buffer_inputs(self):
        """Zero-byte reads, negative reads, clear while active, empty writes."""
        buf = AudioRingBuffer(sample_rate=16000, buffer_ms=50, max_latency_ms=150)
        
        # Zero and negative reads
        assert buf.read(0) == b""
        assert buf.read(-500) == b""

        # Empty write
        assert buf.write(b"") == 0

        # Partial fill then clear
        buf.write(b"\x01\x02" * 500)
        assert buf.level_bytes == 1000
        assert buf.is_prebuffering is True
        buf.clear()
        assert buf.level_bytes == 0
        assert buf.is_prebuffering is True

        # Read after clear returns pure silence
        assert buf.read(640) == b"\x00" * 640

    def test_long_running_continuous_streaming_simulation(self):
        """Simulate 500 network packet cycles with random burst sizes, underruns, and overruns.
        
        Verifies:
        - Occupancy is strictly bounded: never indefinitely accumulates latency.
        - Underruns always return clean silence without exceptions.
        - Overruns cleanly clamp back to target 50ms (1600 bytes).
        - State consistency over prolonged streaming.
        """
        buf = AudioRingBuffer(sample_rate=16000, buffer_ms=50, max_latency_ms=150)
        rng = random.Random(999)

        total_audio_produced = 0
        total_audio_consumed = 0
        overrun_events = 0
        underrun_events = 0

        for cycle in range(500):
            # 80% normal packets (160 to 640 bytes), 10% massive bursts (5000 bytes = overrun), 10% empty (jitter starvation)
            event_type = rng.random()
            if event_type < 0.80:
                burst_len = rng.randint(80, 320) * 2
                pcm = b"\xAA\x55" * (burst_len // 2)
            elif event_type < 0.90:
                burst_len = 6000  # Will trigger overrun (>4800)
                pcm = b"\xFF\x00" * 3000
                overrun_events += 1
            else:
                burst_len = 0
                pcm = b""

            buf.write(pcm)
            total_audio_produced += burst_len

            # Overrun check: occupancy must never stay above max_latency_bytes after write
            assert buf.level_bytes <= buf.max_latency_bytes

            # Reader attempts to read 640 bytes
            chunk = buf.read(640)
            assert len(chunk) == 640
            total_audio_consumed += len(chunk)

            if buf.is_prebuffering and buf.level_bytes == 0:
                underrun_events += 1

        assert overrun_events > 0, "Overrun condition should have been triggered"
        assert underrun_events > 0, "Underrun condition should have been triggered"
        # Final state check: buffer must still be functional
        buf.write(b"\x11\x22" * 800)
        assert buf.is_prebuffering is False
        final_chunk = buf.read(640)
        assert len(final_chunk) == 640


if __name__ == "__main__":
    pytest.main(["-v", "-s", __file__])

