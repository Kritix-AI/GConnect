"""Automated unit tests for GConnect Backend Audio Hub & Network Router (Requirement R2 / M1).

Covers:
- Configuration defaults (AUDIO_SAMPLE_RATE, AUDIO_BUFFER_MS, AUDIO_DUCKING_DB)
- AudioRingBuffer (pre-buffering, FIFO extraction, underrun recovery, overrun clamping)
- DuckingGate (-18dB attenuation, RMS playback detection, hold timer, sample clamping)
- SpeakerArbitrator (1-click takeover, streaming gating, mute, disconnect auto-transfer)
- /ws/audio WebSocket endpoint (token auth, signaling, binary mic streaming, playback broadcast)
"""

import array
import ast
import math
import os
import struct
import time
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from starlette.websockets import WebSocketDisconnect

from backend.audio_router import (
    AudioRingBuffer,
    DuckingGate,
    SpeakerArbitrator,
    audio_router,
)
from backend.config import settings
from tests.conftest import TEST_TOKEN


# -----------------------------------------------------------------------------
# Configuration Tests
# -----------------------------------------------------------------------------

def test_audio_config_defaults():
    """Verify audio settings defaults in config.py."""
    assert settings.AUDIO_SAMPLE_RATE == 16000
    assert settings.AUDIO_BUFFER_MS == 50
    assert settings.AUDIO_DUCKING_DB == -18.0


# -----------------------------------------------------------------------------
# AudioRingBuffer Tests
# -----------------------------------------------------------------------------

def test_ring_buffer_prebuffering_and_fifo():
    """Verify 50ms pre-buffering and FIFO extraction."""
    # 16kHz 16-bit mono: 32 bytes per ms. 50ms = 1600 bytes.
    buf = AudioRingBuffer(sample_rate=16000, buffer_ms=50, max_latency_ms=150)
    assert buf.is_prebuffering is True
    assert buf.level_bytes == 0

    # Reading while prebuffering returns silence (zeros)
    silence = buf.read(640)
    assert silence == b"\x00" * 640
    assert buf.is_prebuffering is True

    # Write 640 bytes (20ms) - still under 1600 bytes threshold
    data1 = b"\x01\x00" * 320
    buf.write(data1)
    assert buf.level_bytes == 640
    assert buf.is_prebuffering is True

    # Read again: still in prebuffering, returns silence, data remains buffered
    assert buf.read(640) == b"\x00" * 640
    assert buf.level_bytes == 640

    # Write more bytes to cross 1600 bytes: 640 + 1000 = 1640 bytes
    data2 = b"\x02\x00" * 500
    buf.write(data2)
    assert buf.level_bytes == 1640
    assert buf.is_prebuffering is False

    # Now read should extract real audio in FIFO order
    chunk1 = buf.read(640)
    assert chunk1 == data1
    assert buf.level_bytes == 1000


def test_ring_buffer_underrun_recovery():
    """Verify underrun returns available audio zero-padded and resets prebuffering."""
    buf = AudioRingBuffer(sample_rate=16000, buffer_ms=50, max_latency_ms=150)

    # Fill past pre-buffer
    buf.write(b"\x05\x00" * 800)  # 1600 bytes
    assert buf.is_prebuffering is False

    # Read 1200 bytes (leaves 400 bytes)
    c1 = buf.read(1200)
    assert len(c1) == 1200
    assert buf.level_bytes == 400
    assert buf.is_prebuffering is False

    # Request 640 bytes when only 400 bytes exist (underrun)
    c2 = buf.read(640)
    assert len(c2) == 640
    # First 400 bytes are real audio, trailing 240 bytes are silence
    assert c2[:400] == b"\x05\x00" * 200
    assert c2[400:] == b"\x00" * 240
    assert buf.level_bytes == 0
    # Must re-enter prebuffering
    assert buf.is_prebuffering is True

    # Next read should return pure silence
    assert buf.read(640) == b"\x00" * 640


def test_ring_buffer_overrun_clamping():
    """Verify that buffer occupancy >150ms drops oldest data to clamp latency to 50ms."""
    # 16000 Hz, 32 bytes/ms. 50ms = 1600 bytes, 150ms = 4800 bytes.
    buf = AudioRingBuffer(sample_rate=16000, buffer_ms=50, max_latency_ms=150)

    # Write burst of 6000 bytes (187.5ms)
    # Put unique markers: first 4400 bytes has marker b"\x11\x11", last 1600 bytes has b"\x22\x22"
    old_data = b"\x11\x11" * 2200  # 4400 bytes
    new_data = b"\x22\x22" * 800   # 1600 bytes
    buf.write(old_data + new_data)

    # Clamping must drop oldest data and keep exactly 1600 bytes (50ms)
    assert buf.level_bytes == 1600
    assert buf.level_ms == 50.0
    assert buf.is_prebuffering is False

    # The remaining 1600 bytes should be the newest data
    remaining = buf.read(1600)
    assert remaining == new_data


def test_ring_buffer_clear():
    """Verify clear empties buffer and resets prebuffering."""
    buf = AudioRingBuffer()
    buf.write(b"\x01\x02" * 1000)
    assert buf.level_bytes > 0
    buf.clear()
    assert buf.level_bytes == 0
    assert buf.is_prebuffering is True


# -----------------------------------------------------------------------------
# DuckingGate Tests
# -----------------------------------------------------------------------------

def test_ducking_gate_attenuation_accuracy():
    """Verify -18dB attenuation scales samples within 0.1 dB accuracy."""
    gate = DuckingGate(ducking_db=-18.0)

    # Target linear multiplier: 10^(-18/20) ~ 0.12589254
    # Test with synthetic 10,000 amplitude PCM
    samples_in = array.array("h", [10000, -10000, 20000, -20000, 0])
    raw_bytes = samples_in.tobytes()

    # Inactive ducking leaves audio untouched
    assert gate.apply_ducking(raw_bytes) == raw_bytes

    # Force ducking active
    ducked_bytes = gate.apply_ducking(raw_bytes, force=True)
    samples_out = array.array("h")
    samples_out.frombytes(ducked_bytes)

    # 10000 * 0.12589254 = 1258.92 -> 1258
    assert abs(samples_out[0] - 1258) <= 2
    assert abs(samples_out[1] - (-1258)) <= 2
    assert abs(samples_out[2] - 2517) <= 3
    assert abs(samples_out[3] - (-2517)) <= 3
    assert samples_out[4] == 0

    # Verify dB attenuation
    measured_db = 20 * math.log10(samples_out[0] / 10000.0)
    assert abs(measured_db - (-18.0)) < 0.1


def test_ducking_gate_rms_and_hold():
    """Verify RMS playback detection and 250ms hold timer."""
    gate = DuckingGate(ducking_db=-18.0, hold_ms=100, rms_threshold=100.0)

    # Silence (all zeros) has RMS = 0 -> does not trigger ducking
    silence = b"\x00" * 640
    assert gate.notify_container_playback(silence) is False
    assert gate.is_ducking_active() is False

    # Active playback chunk (amplitude 5000)
    loud_samples = array.array("h", [5000] * 320)
    loud_bytes = loud_samples.tobytes()
    assert gate.notify_container_playback(loud_bytes) is True
    assert gate.is_ducking_active() is True

    # Ducking stays active during hold window
    time.sleep(0.04)
    assert gate.is_ducking_active() is True

    # After hold expires (> 100ms)
    time.sleep(0.08)
    assert gate.is_ducking_active() is False


def test_ducking_gate_clamping():
    """Verify samples are clamped to int16 boundaries [-32768, 32767]."""
    # Ducking with positive gain (+6dB) to test clipping
    gate = DuckingGate(ducking_db=6.0)
    extreme_samples = array.array("h", [30000, -30000])
    ducked = gate.apply_ducking(extreme_samples.tobytes(), force=True)
    out = array.array("h")
    out.frombytes(ducked)
    assert out[0] == 32767
    assert out[1] == -32768


# -----------------------------------------------------------------------------
# SpeakerArbitrator Tests
# -----------------------------------------------------------------------------

def test_speaker_arbitrator_registration_and_gating():
    """Verify client registration, active speaker assignment, and mic gating."""
    arb = SpeakerArbitrator()

    # First client becomes active speaker automatically
    arb.register_client("client-1", "Client One", is_local=True)
    assert arb.active_speaker_id == "client-1"
    assert arb.can_stream("client-1") is True

    # Second client connects - cannot stream while client-1 is active
    arb.register_client("client-2", "Client Two", is_local=False)
    assert arb.active_speaker_id == "client-1"
    assert arb.can_stream("client-2") is False

    # 1-click takeover: client-2 claims speaker
    arb.claim_speaker("client-2")
    assert arb.active_speaker_id == "client-2"
    assert arb.can_stream("client-2") is True
    assert arb.can_stream("client-1") is False


def test_speaker_arbitrator_mute_and_status():
    """Verify mute toggles and status payload structure."""
    arb = SpeakerArbitrator()
    arb.register_client("client-1", "Host PC", is_local=True)

    status_self = arb.get_speaker_status(for_client_id="client-1")
    assert status_self["type"] == "speaker_status"
    assert status_self["active_client_id"] == "client-1"
    assert status_self["active_client_name"] == "Host PC"
    assert status_self["is_you"] is True

    status_other = arb.get_speaker_status(for_client_id="client-2")
    assert status_other["is_you"] is False

    # Muting prevents streaming even for active speaker
    arb.set_mute("client-1", True)
    assert arb.is_muted("client-1") is True
    assert arb.can_stream("client-1") is False

    arb.set_mute("client-1", False)
    assert arb.can_stream("client-1") is True


def test_speaker_arbitrator_disconnect_auto_transfer():
    """Verify active speaker transfer upon disconnect (prefers local host)."""
    arb = SpeakerArbitrator()

    # Register remote client first (active), then local host
    arb.register_client("remote-1", "Remote Laptop", is_local=False)
    time.sleep(0.01)
    arb.register_client("local-host", "This PC", is_local=True)
    time.sleep(0.01)
    arb.register_client("remote-2", "Remote Phone", is_local=False)

    assert arb.active_speaker_id == "remote-1"

    # Active speaker disconnects -> transfers to local host
    new_active = arb.unregister_client("remote-1")
    assert new_active == "local-host"
    assert arb.active_speaker_id == "local-host"

    # Local host disconnects -> transfers to remaining client (remote-2)
    new_active = arb.unregister_client("local-host")
    assert new_active == "remote-2"
    assert arb.active_speaker_id == "remote-2"

    # Last client disconnects -> active is None
    new_active = arb.unregister_client("remote-2")
    assert new_active is None
    assert arb.active_speaker_id is None


# -----------------------------------------------------------------------------
# /ws/audio WebSocket Integration Tests
# -----------------------------------------------------------------------------

def test_audio_websocket_unauthenticated_rejected(client, auth_settings):
    """Unauthenticated /ws/audio connects are closed with 4401."""
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/audio"):
            pass


def test_audio_websocket_handshake_and_signaling(client, auth_settings):
    """Authenticated websocket performs handshake, ping/pong, and speaker claim."""
    url = f"/ws/audio?token={TEST_TOKEN}&client_id=dev-1&client_name=Developer"
    with client.websocket_connect(url) as ws:
        # Initial speaker status
        msg = ws.receive_json()
        assert msg["type"] == "speaker_status"
        assert msg["active_client_id"] == "dev-1"
        assert msg["is_you"] is True

        # Keepalive ping -> pong
        ws.send_json({"type": "ping", "timestamp": 12345})
        pong = ws.receive_json()
        assert pong["type"] == "pong"
        assert pong["timestamp"] == 12345

        # Mute signaling
        ws.send_json({"type": "mute", "muted": True})
        mute_status = ws.receive_json()
        assert mute_status["type"] == "speaker_status"

        # Claim speaker
        ws.send_json({"type": "claim_speaker", "client_id": "dev-1", "client_name": "Dev Host"})
        claim_status = ws.receive_json()
        assert claim_status["type"] == "speaker_status"
        assert claim_status["active_client_id"] == "dev-1"


def test_audio_websocket_binary_streaming_and_ducking(client, auth_settings):
    """Binary PCM chunks are processed through ducking and written to router."""
    url = f"/ws/audio?token={TEST_TOKEN}&client_id=mic-tester&client_name=Tester"
    captured_chunks = []
    audio_router.set_container_mic_sink(lambda chunk: captured_chunks.append(chunk))

    with client.websocket_connect(url) as ws:
        _ = ws.receive_json()  # Consume initial speaker_status

        # Send mic PCM chunk (10,000 amplitude)
        test_samples = array.array("h", [10000] * 320)
        ws.send_bytes(test_samples.tobytes())

        # Give small tick for event loop processing
        time.sleep(0.05)
        assert len(captured_chunks) > 0
        received = captured_chunks[-1]

        # Inactive ducking -> unchanged
        assert received == test_samples.tobytes()

        # Trigger ducking via container playback
        audio_router.ducking_gate.set_force_ducking(True)
        ws.send_bytes(test_samples.tobytes())
        time.sleep(0.05)

        ducked_received = captured_chunks[-1]
        out_samples = array.array("h")
        out_samples.frombytes(ducked_received)
        # Attenuated by -18dB -> ~1258
        assert abs(out_samples[0] - 1258) <= 2

    # Clean up
    audio_router.ducking_gate.set_force_ducking(False)
    audio_router.set_container_mic_sink(None)


def test_audio_websocket_container_playback_broadcast(client, auth_settings):
    """Container audio playback is broadcast to connected client websockets."""
    url = f"/ws/audio?token={TEST_TOKEN}&client_id=listener-1"
    with client.websocket_connect(url) as ws:
        _ = ws.receive_json()  # initial status

        # Broadcast playback chunk from container
        playback_data = b"\x33\x44" * 320
        if hasattr(ws, "_portal"):
            ws._portal.call(audio_router.broadcast_container_playback, playback_data)
        else:
            import asyncio
            asyncio.run(audio_router.broadcast_container_playback(playback_data))

        received_bytes = ws.receive_bytes()
        assert received_bytes == playback_data
        # Ducking must be activated on playback
        assert audio_router.ducking_gate.is_ducking_active() is True


# -----------------------------------------------------------------------------
# Additional Requirement R4 & Security / Arbitration Tests
# -----------------------------------------------------------------------------

def test_audio_websocket_invalid_token_rejected(client, auth_settings):
    """Connecting to /ws/audio with an invalid token is rejected with 4401."""
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/ws/audio?token=totally-invalid-token"):
            pass
    assert excinfo.value.code == 4401


def test_audio_websocket_cross_origin_rejected(client, auth_settings):
    """Connecting to /ws/audio from an unauthorized cross-origin is rejected with 4403."""
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(
            f"/ws/audio?token={TEST_TOKEN}&client_id=bad-origin",
            headers={"origin": "https://malicious-attacker.com"},
        ):
            pass
    assert excinfo.value.code == 4403


def test_audio_websocket_multi_client_arbitration_and_gating(client, auth_settings):
    """Verifies exclusive speaker arbitration, audio gating, and disconnect auto-transfer in /ws/audio."""
    audio_router.ducking_gate.set_playback_active(False)
    audio_router.ducking_gate.set_force_ducking(False)

    url1 = f"/ws/audio?token={TEST_TOKEN}&client_id=client-alpha&client_name=Alpha"
    url2 = f"/ws/audio?token={TEST_TOKEN}&client_id=client-beta&client_name=Beta"

    captured_chunks = []
    audio_router.set_container_mic_sink(lambda chunk: captured_chunks.append(chunk))

    try:
        with client.websocket_connect(url1) as ws1:
            msg1 = ws1.receive_json()
            assert msg1["type"] == "speaker_status"
            assert msg1["active_client_id"] == "client-alpha"
            assert msg1["is_you"] is True

            with client.websocket_connect(url2) as ws2:
                msg2 = ws2.receive_json()
                assert msg2["type"] == "speaker_status"
                assert msg2["active_client_id"] == "client-alpha"
                assert msg2["is_you"] is False

                # ws1 receives the broadcast generated by ws2 connecting
                ws1_join_broadcast = ws1.receive_json()
                assert ws1_join_broadcast["type"] == "speaker_status"
                assert ws1_join_broadcast["active_client_id"] == "client-alpha"
                assert ws1_join_broadcast["is_you"] is True

                # 1. Gating test: client-beta (non-active) sends mic PCM -> should be dropped
                beta_pcm = array.array("h", [1111] * 320).tobytes()
                ws2.send_bytes(beta_pcm)
                time.sleep(0.05)
                assert len(captured_chunks) == 0, "Non-active client audio should NOT reach sink!"

                # 2. Gating test: client-alpha (active) sends mic PCM -> reaches sink
                alpha_pcm = array.array("h", [2222] * 320).tobytes()
                ws1.send_bytes(alpha_pcm)
                time.sleep(0.05)
                assert len(captured_chunks) == 1, "Active client audio must reach sink!"
                assert captured_chunks[-1] == alpha_pcm

                # 3. Takeover: client-beta claims speaker
                ws2.send_json({"type": "claim_speaker", "client_id": "client-beta", "client_name": "Beta"})
                claim_reply = ws2.receive_json()
                assert claim_reply["type"] == "speaker_status"
                assert claim_reply["active_client_id"] == "client-beta"
                assert claim_reply["is_you"] is True

                # client-alpha receives broadcast update
                alpha_broadcast = ws1.receive_json()
                assert alpha_broadcast["type"] == "speaker_status"
                assert alpha_broadcast["active_client_id"] == "client-beta"
                assert alpha_broadcast["is_you"] is False

                # Now client-beta can stream, client-alpha cannot
                captured_chunks.clear()
                ws1.send_bytes(alpha_pcm)
                time.sleep(0.05)
                assert len(captured_chunks) == 0, "Dethroned client audio should NOT reach sink!"

                ws2.send_bytes(beta_pcm)
                time.sleep(0.05)
                assert len(captured_chunks) == 1
                assert captured_chunks[-1] == beta_pcm

            # 4. Disconnect handling: client-beta disconnected (exited with block)
            # client-alpha receives auto-transfer speaker broadcast
            transfer_msg = ws1.receive_json()
            assert transfer_msg["type"] == "speaker_status"
            assert transfer_msg["active_client_id"] == "client-alpha"
            assert transfer_msg["is_you"] is True

    finally:
        audio_router.set_container_mic_sink(None)


def test_audio_websocket_disconnect_cleanup(client, auth_settings):
    """Verifies socket and arbitrator cleanup upon WebSocket disconnect."""
    url = f"/ws/audio?token={TEST_TOKEN}&client_id=cleanup-user&client_name=CleanupTest"
    with client.websocket_connect(url) as ws:
        _ = ws.receive_json()
        assert "cleanup-user" in audio_router.arbitrator._clients
        assert "cleanup-user" in audio_router._websockets

    # Disconnected
    time.sleep(0.05)
    assert "cleanup-user" not in audio_router.arbitrator._clients
    assert "cleanup-user" not in audio_router._websockets


# -----------------------------------------------------------------------------
# Standalone voice_test.py Verification Tests (Requirement R4)
# -----------------------------------------------------------------------------

def test_voice_test_presence_syntax_and_shebang():
    """Verifies that voice_test.py exists at workspace root, has shebang, and compiles cleanly."""
    repo_root = Path(__file__).resolve().parent.parent
    script_path = repo_root / "voice_test.py"

    assert script_path.exists(), f"voice_test.py missing at {script_path}"
    assert script_path.is_file()

    code = script_path.read_text(encoding="utf-8")
    assert code.startswith("#!/usr/bin/env python3")
    assert len(code) > 1000

    # Compiles cleanly without syntax error
    compiled = compile(code, str(script_path), "exec")
    assert compiled is not None

    # AST validation: required symbols present
    tree = ast.parse(code)
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "generate_chime_pcm" in func_names
    assert "test_playback" in func_names
    assert "test_capture" in func_names
    assert "main" in func_names


def test_voice_test_chime_synthesis():
    """Verifies that voice_test.py synthesizes valid 16kHz 16-bit mono PCM chime with proper energy."""
    import voice_test

    pcm = voice_test.generate_chime_pcm(duration=1.0)
    assert isinstance(pcm, bytes)
    # 1.0s @ 16kHz 16-bit mono = 16000 samples = 32000 bytes
    assert len(pcm) == 32000

    samples = struct.unpack("<16000h", pcm)
    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    peak = max(abs(s) for s in samples)

    # Chime should have audible energy and well-clamped dynamic range
    assert rms > 2000.0, f"Chime RMS too low: {rms}"
    assert peak > 10000, f"Chime peak amplitude too low: {peak}"
    assert peak <= 32767, f"Chime clipped: {peak}"


def test_voice_test_playback_mocked(monkeypatch):
    """Verifies playback fallback logic across sounddevice, pyaudio, and FIFO."""
    import sys
    import voice_test

    # 1. sounddevice path
    mock_sd = MagicMock()
    mock_np = MagicMock()
    mock_np.frombuffer.return_value = MagicMock()
    monkeypatch.setitem(sys.modules, "sounddevice", mock_sd)
    monkeypatch.setitem(sys.modules, "numpy", mock_np)
    assert voice_test.test_playback(duration=0.01) is True
    mock_sd.play.assert_called_once()

    # 2. pyaudio path (sounddevice unavailable)
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    mock_pa = MagicMock()
    mock_p = MagicMock()
    mock_stream = MagicMock()
    mock_p.open.return_value = mock_stream
    mock_pa.PyAudio.return_value = mock_p
    monkeypatch.setitem(sys.modules, "pyaudio", mock_pa)
    assert voice_test.test_playback(duration=0.01) is True
    mock_stream.write.assert_called_once()

    # 3. FIFO fallback (both libraries unavailable)
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    monkeypatch.setitem(sys.modules, "pyaudio", None)
    monkeypatch.setattr(voice_test.shutil, "which", lambda x: None)

    written_chunks = []
    monkeypatch.setattr(voice_test.os.path, "exists", lambda p: True)
    monkeypatch.setattr(voice_test.os, "open", lambda *a, **kw: 999)
    monkeypatch.setattr(voice_test.os, "write", lambda fd, data: written_chunks.append(data) or len(data))
    monkeypatch.setattr(voice_test.os, "close", lambda fd: None)
    assert voice_test.test_playback(duration=0.01) is True
    assert len(written_chunks) > 0


def test_voice_test_capture_mocked(monkeypatch):
    """Verifies capture fallback logic across sounddevice, speech_recognition, pyaudio, and FIFO."""
    import sys
    import voice_test

    # Synthetic 16kHz 16-bit mono 1-second audio (16000 samples = 32000 bytes)
    sample_val = 5000
    fake_audio = struct.pack("<16000h", *([sample_val] * 16000))

    # 1. sounddevice path
    mock_sd = MagicMock()
    mock_array = MagicMock()
    mock_array.tobytes.return_value = fake_audio
    mock_sd.rec.return_value = mock_array
    mock_np = MagicMock()
    monkeypatch.setitem(sys.modules, "sounddevice", mock_sd)
    monkeypatch.setitem(sys.modules, "numpy", mock_np)
    assert voice_test.test_capture(record_duration=1.0) is True
    mock_sd.rec.assert_called_once()

    # 2. speech_recognition path
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    mock_sr = MagicMock()
    mock_recognizer = MagicMock()
    mock_audio_obj = MagicMock()
    mock_audio_obj.get_raw_data.return_value = fake_audio
    mock_recognizer.record.return_value = mock_audio_obj
    mock_sr.Recognizer.return_value = mock_recognizer
    mock_sr.Microphone.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock_sr.Microphone.return_value.__exit__ = MagicMock(return_value=None)
    monkeypatch.setitem(sys.modules, "speech_recognition", mock_sr)
    assert voice_test.test_capture(record_duration=1.0) is True

    # 3. pyaudio path
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    monkeypatch.setitem(sys.modules, "speech_recognition", None)
    mock_pa = MagicMock()
    mock_p = MagicMock()
    mock_stream = MagicMock()
    mock_stream.read.return_value = fake_audio[:2048]
    mock_p.open.return_value = mock_stream
    mock_pa.PyAudio.return_value = mock_p
    monkeypatch.setitem(sys.modules, "pyaudio", mock_pa)
    assert voice_test.test_capture(record_duration=0.5) is True

    # 4. FIFO fallback
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    monkeypatch.setitem(sys.modules, "speech_recognition", None)
    monkeypatch.setitem(sys.modules, "pyaudio", None)
    monkeypatch.setattr(voice_test.shutil, "which", lambda x: None)

    read_calls = [fake_audio[:2048], b""]
    monkeypatch.setattr(voice_test.os.path, "exists", lambda p: True)
    monkeypatch.setattr(voice_test.os, "open", lambda *a, **kw: 998)
    monkeypatch.setattr(voice_test.os, "read", lambda fd, n: read_calls.pop(0) if read_calls else b"")
    monkeypatch.setattr(voice_test.os, "close", lambda fd: None)
    monkeypatch.setattr(voice_test.time, "sleep", lambda s: None)
    # Short duration so loop terminates quickly
    assert voice_test.test_capture(record_duration=0.01) is True


# =============================================================================
# Adversarial Stress Tests (Milestone 4 Verification & Robustness)
# =============================================================================

def test_adversarial_token_auth_and_origin_fuzzing(client, auth_settings):
    """Stress Test 1A: Fuzz /ws/audio endpoint with adversarial tokens and origins to verify clean rejection."""
    import urllib.parse

    # 1. Adversarial tokens
    fuzz_tokens = [
        "",
        "   ",
        "totally-invalid-secret",
        TEST_TOKEN[:-1],             # Off by one (too short)
        TEST_TOKEN + "x",            # Off by one (too long)
        "X" * 4096,                  # Large buffer
        "DROP_TABLE_CONNECT",
        "NULL_BYTE\x00INJECTION",
        "EMOJI_KEY_TOKEN",
    ]
    for bad_token in fuzz_tokens:
        encoded = urllib.parse.quote(bad_token)
        with pytest.raises(WebSocketDisconnect) as excinfo:
            with client.websocket_connect(f"/ws/audio?token={encoded}&client_id=fuzz"):
                pass
        assert excinfo.value.code == 4401, f"Expected 4401 for token {bad_token!r}, got {excinfo.value.code}"

    # Missing token query parameter completely
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/ws/audio"):
            pass
    assert excinfo.value.code == 4401

    # 2. Adversarial origins
    fuzz_origins = [
        "http://malicious-attacker.com",
        "https://evil.org",
        "null",
        "https://tauri.malicious.com",
        "http://localhost:9999",
        "ftp://localhost:8000",
        "invalid://scheme",
    ]
    for bad_origin in fuzz_origins:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            with client.websocket_connect(
                f"/ws/audio?token={TEST_TOKEN}&client_id=fuzz-origin",
                headers={"origin": bad_origin},
            ):
                pass
        assert excinfo.value.code == 4403, f"Expected 4403 for origin {bad_origin!r}, got {excinfo.value.code}"


def test_adversarial_speaker_claim_concurrency_race():
    """Stress Test 1B: Stress test SpeakerArbitrator with concurrent worker threads performing rapid claim/mute/status races."""
    import random
    import threading

    arbitrator = SpeakerArbitrator()
    num_threads = 8
    iterations_per_thread = 150
    exceptions = []

    def worker(worker_id: int):
        client_id = f"worker-{worker_id}"
        client_name = f"Worker {worker_id}"
        is_local = (worker_id == 0)
        try:
            arbitrator.register_client(client_id, client_name, is_local=is_local)
            for _ in range(iterations_per_thread):
                op = random.randint(1, 5)
                if op == 1:
                    arbitrator.claim_speaker(client_id)
                elif op == 2:
                    _ = arbitrator.can_stream(client_id)
                elif op == 3:
                    arbitrator.set_mute(client_id, random.choice([True, False]))
                elif op == 4:
                    status = arbitrator.get_speaker_status(client_id)
                    assert status["type"] == "speaker_status"
                elif op == 5:
                    _ = arbitrator.is_muted(client_id)
        except Exception as e:
            exceptions.append((client_id, e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
        assert not t.is_alive(), "Worker thread deadlocked during claim race!"

    assert len(exceptions) == 0, f"Encountered concurrency exceptions: {exceptions}"
    active_id = arbitrator.active_speaker_id
    assert active_id is not None
    assert active_id in arbitrator.connected_clients
    assert arbitrator.can_stream(active_id) in (True, False)


def test_adversarial_concurrent_websockets_and_disconnect_cascade(client, auth_settings):
    """Stress Test 1C: Stress test concurrent WebSocket connections, mid-stream speaker claim races, and abrupt disconnect cascades."""
    audio_router.arbitrator._clients.clear()
    audio_router._websockets.clear()
    audio_router.arbitrator._active_speaker_id = None

    client_count = 4
    sockets = []

    try:
        # Establish 4 concurrent client sessions
        for i in range(client_count):
            cid = f"cascade-client-{i}"
            cname = f"Client {i}"
            url = f"/ws/audio?token={TEST_TOKEN}&client_id={cid}&client_name={cname}"
            ws_ctx = client.websocket_connect(url)
            ws = ws_ctx.__enter__()
            _ = ws.receive_json()  # Consume initial speaker_status
            sockets.append((cid, ws_ctx, ws))

        assert len(audio_router._websockets) == client_count
        assert len(audio_router.arbitrator._clients) == client_count
        assert audio_router.arbitrator.active_speaker_id == "cascade-client-0"

        # Rapid speaker claims across clients in rapid succession
        sockets[2][2].send_json({"type": "claim_speaker", "client_id": "cascade-client-2"})
        time.sleep(0.02)
        assert audio_router.arbitrator.active_speaker_id == "cascade-client-2"

        sockets[1][2].send_json({"type": "claim_speaker", "client_id": "cascade-client-1"})
        time.sleep(0.02)
        assert audio_router.arbitrator.active_speaker_id == "cascade-client-1"

        sockets[3][2].send_json({"type": "claim_speaker", "client_id": "cascade-client-3"})
        time.sleep(0.02)
        assert audio_router.arbitrator.active_speaker_id == "cascade-client-3"

        # Abrupt disconnect of active speaker (client 3)
        cid3, ws_ctx3, ws3 = sockets[3]
        ws3.close()
        try:
            ws_ctx3.__exit__(None, None, None)
        except Exception:
            pass

        time.sleep(0.05)
        # Verify active speaker auto-transfers to remaining connected client
        remaining_active = audio_router.arbitrator.active_speaker_id
        assert remaining_active in ["cascade-client-0", "cascade-client-1", "cascade-client-2"]
        assert "cascade-client-3" not in audio_router._websockets
        assert "cascade-client-3" not in audio_router.arbitrator._clients

        # Cascade disconnect for all remaining clients
        for cid, ws_ctx, ws in sockets[:3]:
            ws.close()
            try:
                ws_ctx.__exit__(None, None, None)
            except Exception:
                pass

        time.sleep(0.05)

        # Verify zero socket leaks, zero dangling sessions, active speaker is None
        assert len(audio_router._websockets) == 0, f"Socket leak: {audio_router._websockets}"
        assert len(audio_router.arbitrator._clients) == 0, f"Client session leak: {audio_router.arbitrator._clients}"
        assert audio_router.arbitrator.active_speaker_id is None

    finally:
        audio_router._websockets.clear()
        audio_router.arbitrator._clients.clear()
        audio_router.arbitrator._active_speaker_id = None


def test_adversarial_audio_gating_100_packets_zero_leakage(client, auth_settings):
    """Stress Test 2: Stream 100 packets of distinct marker PCM from non-active client simultaneously with active client.
    Verify that EXACTLY 0 bytes of non-active audio enter the container ring buffer or sink.
    """
    audio_router.ring_buffer.clear()
    audio_router.arbitrator._clients.clear()
    audio_router._websockets.clear()
    audio_router.arbitrator._active_speaker_id = None
    audio_router.ducking_gate.set_playback_active(False)
    audio_router.ducking_gate.set_force_ducking(False)

    # 1024 bytes per packet (512 samples)
    # Active client pattern: 0x1111 (sample value 4369)
    marker_active = struct.pack("<512h", *([4369] * 512))
    # Intruder client pattern: 0x55AA (sample value -21846)
    marker_intruder = struct.pack("<512h", *([-21846] * 512))

    captured_sink_packets = []
    audio_router.set_container_mic_sink(lambda chunk: captured_sink_packets.append(chunk))

    url_active = f"/ws/audio?token={TEST_TOKEN}&client_id=client-primary&client_name=Primary"
    url_intruder = f"/ws/audio?token={TEST_TOKEN}&client_id=client-intruder&client_name=Intruder"

    try:
        with client.websocket_connect(url_active) as ws_active:
            _ = ws_active.receive_json()  # initial status
            with client.websocket_connect(url_intruder) as ws_intruder:
                _ = ws_intruder.receive_json()  # intruder status
                _ = ws_active.receive_json()    # broadcast on ws_active

                assert audio_router.arbitrator.active_speaker_id == "client-primary"
                assert audio_router.arbitrator.can_stream("client-primary") is True
                assert audio_router.arbitrator.can_stream("client-intruder") is False

                # Stream 100 packets from intruder and 100 packets from active client
                for _ in range(100):
                    ws_intruder.send_bytes(marker_intruder)
                    ws_active.send_bytes(marker_active)

                time.sleep(0.1)

                # Check sink packets: exactly 100 packets, all containing only active marker
                assert len(captured_sink_packets) == 100, f"Expected 100 sink packets, got {len(captured_sink_packets)}"
                for pkt in captured_sink_packets:
                    assert marker_intruder not in pkt, "LEAK DETECTED: Intruder PCM entered container sink!"
                    assert pkt == marker_active, "Sink packet does not match active audio!"

                # Check container ring buffer: read all available buffered audio
                ring_data = bytearray()
                while audio_router.ring_buffer.level_bytes > 0:
                    chunk = audio_router.ring_buffer.read(1024)
                    if chunk:
                        ring_data.extend(chunk)

                # Overrun clamping clamps buffer to 1600 bytes (50ms target)
                assert len(ring_data) > 0
                assert marker_intruder not in ring_data, "LEAK DETECTED: Intruder PCM entered ring buffer!"
                samples = struct.unpack(f"<{len(ring_data)//2}h", ring_data[: (len(ring_data)//2)*2])
                assert -21846 not in samples, "Intruder sample found inside ring buffer!"

                # Test Active Speaker Muting:
                captured_sink_packets.clear()
                audio_router.ring_buffer.clear()
                ws_active.send_json({"type": "mute", "muted": True})
                time.sleep(0.02)
                assert audio_router.arbitrator.can_stream("client-primary") is False

                # Stream 20 packets while muted
                for _ in range(20):
                    ws_active.send_bytes(marker_active)
                time.sleep(0.05)

                assert len(captured_sink_packets) == 0, "Muted active speaker audio leaked into sink!"
                assert audio_router.ring_buffer.level_bytes == 0, "Muted active audio leaked into ring buffer!"

    finally:
        audio_router.set_container_mic_sink(None)
        audio_router.ring_buffer.clear()
        audio_router._websockets.clear()
        audio_router.arbitrator._clients.clear()
        audio_router.arbitrator._active_speaker_id = None


def test_adversarial_voice_test_abnormal_sample_rates():
    """Stress Test 3A: Verify voice_test.py chime synthesis across abnormal sample rates and edge-case durations."""
    import voice_test

    original_rate = voice_test.SAMPLE_RATE
    try:
        # Standard, telephony, studio, and high-res sample rates
        for test_rate in [8000, 11025, 22050, 44100, 48000, 96000, 192000]:
            voice_test.SAMPLE_RATE = test_rate
            pcm = voice_test.generate_chime_pcm(duration=0.1)
            expected_samples = int(test_rate * 0.1)
            expected_bytes = expected_samples * 2
            assert len(pcm) == expected_bytes, f"Byte length mismatch at rate {test_rate}"

            samples = struct.unpack(f"<{expected_samples}h", pcm)
            peak = max(abs(s) for s in samples)
            assert peak <= 32767, f"Sample clipped at rate {test_rate}: {peak}"
            assert peak > 0, f"Silent chime generated at rate {test_rate}"

        # Zero and negative durations return empty bytes cleanly without crashing
        voice_test.SAMPLE_RATE = 16000
        assert voice_test.generate_chime_pcm(duration=0.0) == b""
        assert voice_test.generate_chime_pcm(duration=-1.0) == b""

    finally:
        voice_test.SAMPLE_RATE = original_rate


def test_adversarial_voice_test_corrupted_fifo_payloads(monkeypatch):
    """Stress Test 3B: Stress test voice_test.test_capture with corrupted FIFO inputs: odd byte counts, zero bytes, and errors."""
    import sys
    import voice_test

    monkeypatch.setitem(sys.modules, "sounddevice", None)
    monkeypatch.setitem(sys.modules, "speech_recognition", None)
    monkeypatch.setitem(sys.modules, "pyaudio", None)
    monkeypatch.setattr(voice_test.shutil, "which", lambda x: None)
    monkeypatch.setattr(voice_test.os.path, "exists", lambda p: True)
    monkeypatch.setattr(voice_test.os, "open", lambda *a, **kw: 990)
    monkeypatch.setattr(voice_test.os, "close", lambda fd: None)
    monkeypatch.setattr(voice_test.time, "sleep", lambda s: None)

    # 1. Odd byte count (2049 bytes = 1024 samples + 1 trailing odd byte)
    odd_payload = struct.pack("<1024h", *([1234] * 1024)) + b"\x55"
    read_queue = [odd_payload, b""]
    monkeypatch.setattr(voice_test.os, "read", lambda fd, n: read_queue.pop(0) if read_queue else b"")
    # Must unpack valid samples cleanly without struct.error, slicing away odd byte
    assert voice_test.test_capture(record_duration=0.01) is True

    # 2. Corrupted FIFO returns 0 bytes (EOF or empty FIFO)
    read_queue = [b"", b""]
    monkeypatch.setattr(voice_test.os, "read", lambda fd, n: read_queue.pop(0) if read_queue else b"")
    assert voice_test.test_capture(record_duration=0.01) is False

    # 3. Corrupted FIFO returns only 1 single byte (less than 1 full 16-bit sample)
    read_queue = [b"\x00", b""]
    monkeypatch.setattr(voice_test.os, "read", lambda fd, n: read_queue.pop(0) if read_queue else b"")
    assert voice_test.test_capture(record_duration=0.01) is False

    # 4. FIFO read throws BlockingIOError / OSError continuously
    def raise_os_error(fd, n):
        raise BlockingIOError("Resource temporarily unavailable")

    monkeypatch.setattr(voice_test.os, "read", raise_os_error)
    assert voice_test.test_capture(record_duration=0.01) is False

    # 5. Extreme audio values: full-scale square wave (+32767 and -32768)
    square_wave = struct.pack("<1024h", *([32767, -32768] * 512))
    read_queue = [square_wave, b""]
    monkeypatch.setattr(voice_test.os, "read", lambda fd, n: read_queue.pop(0) if read_queue else b"")
    assert voice_test.test_capture(record_duration=0.01) is True


def test_adversarial_voice_test_nonexistent_devices_and_hardware_errors(monkeypatch):
    """Stress Test 3C: Verify test_playback and test_capture fail cleanly when hardware devices are missing or raise exceptions."""
    import sys
    import voice_test

    # 1. Total device absence (all libraries None, CLI None, FIFO missing)
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    monkeypatch.setitem(sys.modules, "speech_recognition", None)
    monkeypatch.setitem(sys.modules, "pyaudio", None)
    monkeypatch.setattr(voice_test.shutil, "which", lambda x: None)
    monkeypatch.setattr(voice_test.os.path, "exists", lambda p: False)

    assert voice_test.test_playback(duration=0.01) is False
    assert voice_test.test_capture(record_duration=0.01) is False

    # 2. Hardware exceptions from libraries (e.g. PortAudioError / DeviceUnavailable)
    mock_sd = MagicMock()
    mock_sd.play.side_effect = RuntimeError("PortAudioError: No default output device found")
    mock_sd.rec.side_effect = RuntimeError("PortAudioError: No default input device found")
    mock_np = MagicMock()
    mock_np.frombuffer.return_value = MagicMock()
    monkeypatch.setitem(sys.modules, "sounddevice", mock_sd)
    monkeypatch.setitem(sys.modules, "numpy", mock_np)

    mock_pa = MagicMock()
    mock_pa.PyAudio.side_effect = RuntimeError("PyAudio initialization failed: -9996")
    monkeypatch.setitem(sys.modules, "pyaudio", mock_pa)

    mock_sr = MagicMock()
    mock_sr.Microphone.side_effect = OSError("No default microphone available")
    monkeypatch.setitem(sys.modules, "speech_recognition", mock_sr)

    # Must catch all hardware exceptions gracefully and return False instead of crashing
    assert voice_test.test_playback(duration=0.01) is False
    assert voice_test.test_capture(record_duration=0.01) is False


def test_adversarial_voice_test_exit_code_semantics(monkeypatch):
    """Stress Test 3D: Verify voice_test.main() exit codes: 0 for all pass, 1 for failures, and CLI arg semantics."""
    import sys
    import voice_test

    # 1. Both pass -> exit code 0
    monkeypatch.setattr(voice_test, "test_playback", lambda duration=1.0: True)
    monkeypatch.setattr(voice_test, "test_capture", lambda record_duration=3.0: True)
    monkeypatch.setattr(sys, "argv", ["voice_test.py"])
    assert voice_test.main() == 0

    # 2. Playback fails -> exit code 1
    monkeypatch.setattr(voice_test, "test_playback", lambda duration=1.0: False)
    monkeypatch.setattr(voice_test, "test_capture", lambda record_duration=3.0: True)
    assert voice_test.main() == 1

    # 3. Capture fails -> exit code 1
    monkeypatch.setattr(voice_test, "test_playback", lambda duration=1.0: True)
    monkeypatch.setattr(voice_test, "test_capture", lambda record_duration=3.0: False)
    assert voice_test.main() == 1

    # 4. Both fail -> exit code 1
    monkeypatch.setattr(voice_test, "test_playback", lambda duration=1.0: False)
    monkeypatch.setattr(voice_test, "test_capture", lambda record_duration=3.0: False)
    assert voice_test.main() == 1

    # 5. --playback-only flag
    monkeypatch.setattr(sys, "argv", ["voice_test.py", "--playback-only"])
    monkeypatch.setattr(voice_test, "test_playback", lambda duration=1.0: True)
    monkeypatch.setattr(voice_test, "test_capture", lambda record_duration=3.0: False)  # should not be called
    assert voice_test.main() == 0

    monkeypatch.setattr(voice_test, "test_playback", lambda duration=1.0: False)
    assert voice_test.main() == 1

    # 6. --capture-only flag
    monkeypatch.setattr(sys, "argv", ["voice_test.py", "--capture-only"])
    monkeypatch.setattr(voice_test, "test_playback", lambda duration=1.0: False)  # should not be called
    monkeypatch.setattr(voice_test, "test_capture", lambda record_duration=3.0: True)
    assert voice_test.main() == 0

    monkeypatch.setattr(voice_test, "test_capture", lambda record_duration=3.0: False)
    assert voice_test.main() == 1


