#!/usr/bin/env python3
"""voice_test.py - Standalone container audio verification script.

Tests 3-second microphone recording and chime playback inside `connect_dev_sandbox`.
Compatible with standard Python libraries: sounddevice, speech_recognition, pyaudio,
as well as direct ALSA command-line tools (aplay, arecord) and direct FIFO fallback.
"""

import argparse
import math
import os
import shutil
import struct
import subprocess
import sys
import time

SAMPLE_RATE = 16000
CHANNELS = 1
DEFAULT_RECORD_DURATION = 3.0


def generate_chime_pcm(duration: float = 1.0) -> bytes:
    """Generates a pleasant 1.0-second 880Hz / 440Hz dual-tone chime (16kHz 16-bit mono)."""
    num_samples = int(SAMPLE_RATE * duration)
    pcm = bytearray()
    for i in range(num_samples):
        t = i / SAMPLE_RATE
        # Harmonic frequencies (A5 880Hz and A4 440Hz) with exponential decay
        val = (0.5 * math.sin(2 * math.pi * 880 * t) + 0.5 * math.sin(2 * math.pi * 440 * t)) * math.exp(-3.0 * t)
        int_sample = max(-32767, min(32767, int(val * 24000)))
        pcm.extend(struct.pack("<h", int_sample))
    return bytes(pcm)


def test_playback(duration: float = 1.0) -> bool:
    """Tests audio playback using sounddevice, pyaudio, ALSA aplay, or FIFO fallback."""
    print("=" * 60)
    print("TEST 1: Speaker Playback (Synthesizing & Playing Chime)")
    print("=" * 60)
    pcm_data = generate_chime_pcm(duration)

    # 1. Try sounddevice
    try:
        import numpy as np
        import sounddevice as sd

        print("[Playback] Using 'sounddevice' library...")
        samples = np.frombuffer(pcm_data, dtype=np.int16)
        sd.play(samples, samplerate=SAMPLE_RATE, blocking=True)
        print("[Playback] sounddevice.play() completed successfully.")
        return True
    except ImportError:
        print("[Playback] 'sounddevice' not installed, checking pyaudio...")
    except Exception as e:
        print(f"[Playback] sounddevice error: {e}")

    # 2. Try pyaudio
    try:
        import pyaudio

        print("[Playback] Using 'pyaudio' library...")
        p = pyaudio.PyAudio()
        stream = p.open(format=pyaudio.paInt16, channels=CHANNELS, rate=SAMPLE_RATE, output=True)
        stream.write(pcm_data)
        stream.stop_stream()
        stream.close()
        p.terminate()
        print("[Playback] pyaudio playback completed successfully.")
        return True
    except ImportError:
        print("[Playback] 'pyaudio' not installed, checking ALSA CLI / FIFO...")
    except Exception as e:
        print(f"[Playback] pyaudio error: {e}")

    # 3. Direct ALSA CLI (aplay) fallback
    aplay_bin = shutil.which("aplay")
    if aplay_bin:
        print(f"[Playback] Trying ALSA CLI ({aplay_bin})...")
        try:
            subprocess.run(
                [aplay_bin, "-D", "default", "-r", str(SAMPLE_RATE), "-f", "S16_LE", "-c", str(CHANNELS), "-q"],
                input=pcm_data,
                timeout=duration + 3.0,
                check=True,
            )
            print("[Playback] ALSA aplay completed successfully.")
            return True
        except Exception as e:
            print(f"[Playback] ALSA aplay failed: {e}")

    # 4. Direct FIFO fallback
    spk_fifo = os.environ.get("GCONNECT_SPK_FIFO", "/tmp/.gconnect_audio/spk.fifo")
    if os.path.exists(spk_fifo):
        print(f"[Playback] Writing directly to {spk_fifo}...")
        try:
            nonblock_flag = getattr(os, "O_NONBLOCK", 0)
            fd = os.open(spk_fifo, os.O_WRONLY | nonblock_flag)
            total_written = 0
            while total_written < len(pcm_data):
                written = os.write(fd, pcm_data[total_written:])
                if written <= 0:
                    break
                total_written += written
            os.close(fd)
            print(f"[Playback] Direct FIFO wrote {total_written} bytes successfully.")
            return True
        except Exception as e:
            print(f"[Playback] Direct FIFO write failed: {e}")

    print("[Playback] FAILED: Could not play audio through any interface.")
    return False


def test_capture(record_duration: float = DEFAULT_RECORD_DURATION) -> bool:
    """Tests microphone capture using sounddevice, speech_recognition, pyaudio, ALSA arecord, or FIFO."""
    print("\n" + "=" * 60)
    print(f"TEST 2: Microphone Capture (Recording {record_duration:.1f}s of audio)")
    print("=" * 60)
    recorded_bytes = b""

    # 1. Try sounddevice
    try:
        import numpy as np
        import sounddevice as sd

        print(f"[Capture] Recording {record_duration}s via 'sounddevice'...")
        audio_array = sd.rec(int(record_duration * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=np.int16)
        sd.wait()
        recorded_bytes = audio_array.tobytes()
        print(f"[Capture] Captured {len(recorded_bytes)} bytes via sounddevice.")
    except ImportError:
        print("[Capture] 'sounddevice' not installed, checking speech_recognition...")
    except Exception as e:
        print(f"[Capture] sounddevice recording error: {e}")

    # 2. Try speech_recognition
    if not recorded_bytes:
        try:
            import speech_recognition as sr

            print("[Capture] Recording via 'speech_recognition'...")
            r = sr.Recognizer()
            with sr.Microphone(sample_rate=SAMPLE_RATE) as source:
                audio = r.record(source, duration=record_duration)
            recorded_bytes = audio.get_raw_data()
            print(f"[Capture] Captured {len(recorded_bytes)} bytes via speech_recognition.")
        except ImportError:
            print("[Capture] 'speech_recognition' not installed, checking pyaudio...")
        except Exception as e:
            print(f"[Capture] speech_recognition error: {e}")

    # 3. Try pyaudio directly
    if not recorded_bytes:
        try:
            import pyaudio

            print(f"[Capture] Recording {record_duration}s via 'pyaudio'...")
            p = pyaudio.PyAudio()
            stream = p.open(format=pyaudio.paInt16, channels=CHANNELS, rate=SAMPLE_RATE, input=True, frames_per_buffer=1024)
            chunks = []
            num_chunks = int(SAMPLE_RATE * record_duration / 1024)
            for _ in range(num_chunks):
                chunks.append(stream.read(1024, exception_on_overflow=False))
            stream.stop_stream()
            stream.close()
            p.terminate()
            recorded_bytes = b"".join(chunks)
            print(f"[Capture] Captured {len(recorded_bytes)} bytes via pyaudio.")
        except ImportError:
            print("[Capture] 'pyaudio' not installed, checking ALSA CLI / FIFO...")
        except Exception as e:
            print(f"[Capture] pyaudio error: {e}")

    # 4. Direct ALSA CLI (arecord) fallback
    if not recorded_bytes:
        arecord_bin = shutil.which("arecord")
        if arecord_bin:
            print(f"[Capture] Trying ALSA CLI ({arecord_bin})...")
            try:
                proc = subprocess.run(
                    [arecord_bin, "-D", "default", "-r", str(SAMPLE_RATE), "-f", "S16_LE", "-c", str(CHANNELS), "-d", str(int(record_duration)), "-t", "raw", "-q"],
                    capture_output=True,
                    timeout=record_duration + 3.0,
                    check=True,
                )
                recorded_bytes = proc.stdout
                print(f"[Capture] Captured {len(recorded_bytes)} bytes via arecord.")
            except Exception as e:
                print(f"[Capture] ALSA arecord failed: {e}")

    # 5. Direct FIFO fallback
    if not recorded_bytes:
        mic_fifo = os.environ.get("GCONNECT_MIC_FIFO", "/tmp/.gconnect_audio/mic.fifo")
        if os.path.exists(mic_fifo):
            print(f"[Capture] Reading from {mic_fifo}...")
            try:
                nonblock_flag = getattr(os, "O_NONBLOCK", 0)
                fd = os.open(mic_fifo, os.O_RDONLY | nonblock_flag)
                end_time = time.time() + record_duration
                chunks = []
                while time.time() < end_time:
                    try:
                        chunk = os.read(fd, 1024)
                        if chunk:
                            chunks.append(chunk)
                    except (BlockingIOError, OSError):
                        pass
                    time.sleep(0.02)
                os.close(fd)
                recorded_bytes = b"".join(chunks)
                print(f"[Capture] Read {len(recorded_bytes)} bytes from FIFO.")
            except Exception as e:
                print(f"[Capture] Direct FIFO read failed: {e}")

    if not recorded_bytes:
        print("[Capture] FAILED: No audio frames captured.")
        return False

    # Calculate audio statistics (RMS, peak)
    num_samples = len(recorded_bytes) // 2
    if num_samples == 0:
        print("[Capture] FAILED: Zero samples captured.")
        return False

    samples = struct.unpack(f"<{num_samples}h", recorded_bytes[: num_samples * 2])
    rms = math.sqrt(sum(s * s for s in samples) / num_samples)
    peak = max(abs(s) for s in samples)

    print(f"[Capture] Audio Analysis: {num_samples} samples ({num_samples / SAMPLE_RATE:.2f}s)")
    print(f"[Capture] RMS Energy: {rms:.2f} | Peak Amplitude: {peak}")
    print("[Capture] PASSED: Virtual microphone capture successfully verified.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="GConnect Sandbox Audio Virtualization Verification")
    parser.add_argument("--duration", type=float, default=DEFAULT_RECORD_DURATION, help="Recording duration in seconds")
    parser.add_argument("--playback-only", action="store_true", help="Run only speaker playback test")
    parser.add_argument("--capture-only", action="store_true", help="Run only microphone capture test")
    args = parser.parse_args()

    print("=" * 60)
    print("GConnect Sandbox Audio Virtualization Verification")
    print("=" * 60)

    spk_ok = True
    mic_ok = True

    if not args.capture_only:
        spk_ok = test_playback(duration=1.0)

    if not args.playback_only:
        mic_ok = test_capture(record_duration=args.duration)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    if not args.capture_only:
        print(f"Virtual Speaker Playback : {'PASS' if spk_ok else 'FAIL'}")
    if not args.playback_only:
        print(f"Virtual Microphone Record: {'PASS' if mic_ok else 'FAIL'}")

    if spk_ok and mic_ok:
        print("\nALL AUDIO VIRTUALIZATION TESTS PASSED!")
        return 0
    else:
        print("\nAUDIO VIRTUALIZATION TESTS FAILED.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
