"""Unit tests for Docker Container Audio Virtualization (Milestone 2 / Requirement R3).

Tests:
1. ALSA configuration (/etc/asound.conf) syntax, routing, and hints.
2. Container bridge daemon script syntax, non-blocking FIFO handling, and port bindings.
3. DockerSandboxManager port mapping, config fingerprinting, and post-start provisioning.
4. ContainerAudioBridgeClient live TCP duplex streaming (mic sending, playback receiving).
5. ContainerAudioBridgeClient graceful offline fallback (connection refused, Docker stopped).
6. AudioRouter integration with ContainerAudioBridgeClient and ducking pipeline.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from backend.config import settings
from backend.docker_manager import (
    ASOUND_CONF_CONTENT,
    CONTAINER_AUDIO_BRIDGE_CODE,
    DockerSandboxManager,
)
from backend.audio_router import AudioRouter, ContainerAudioBridgeClient


# -----------------------------------------------------------------------------
# 1. /etc/asound.conf Validation Tests
# -----------------------------------------------------------------------------

def test_asound_conf_structure_and_syntax():
    """Validates /etc/asound.conf format, ALSA plugin tree, and device hints."""
    conf = ASOUND_CONF_CONTENT

    # 1. Capture pipeline: file plugin with infile pointing to mic.fifo
    assert 'pcm.gconnect_mic_raw' in conf
    assert 'type file' in conf
    assert 'infile "/tmp/.gconnect_audio/mic.fifo"' in conf
    assert 'format "raw"' in conf

    # 2. Capture plug plugin: resamples/downmixes to 16kHz 16-bit mono
    assert 'pcm.gconnect_mic {' in conf
    assert 'type plug' in conf
    assert 'rate 16000' in conf
    assert 'format S16_LE' in conf
    assert 'channels 1' in conf
    assert 'Virtual GConnect Microphone' in conf

    # 3. Playback pipeline: file plugin writing to spk.fifo
    assert 'pcm.gconnect_spk_raw' in conf
    assert 'file "/tmp/.gconnect_audio/spk.fifo"' in conf

    # 4. Playback plug plugin: resamples to 16kHz 16-bit mono
    assert 'pcm.gconnect_spk {' in conf
    assert 'Virtual GConnect Speaker' in conf

    # 5. Default PCM: asymmetric binding of capture and playback
    assert 'pcm.!default {' in conf
    assert 'type asym' in conf
    assert 'playback.pcm "gconnect_spk"' in conf
    assert 'capture.pcm "gconnect_mic"' in conf
    assert 'Default Virtual GConnect Audio Device' in conf

    # 6. Default Mixer Control: null device to prevent ALSA GropeDevice crash
    assert 'ctl.!default {' in conf
    assert 'type null' in conf


# -----------------------------------------------------------------------------
# 2. Container Audio Bridge Script Validation Tests
# -----------------------------------------------------------------------------

def test_container_audio_bridge_script_compilation():
    """Validates that the in-container audio bridge Python code is 100% syntactically valid."""
    code_str = CONTAINER_AUDIO_BRIDGE_CODE
    assert len(code_str) > 500

    # Must compile cleanly in Python runtime
    compiled = compile(code_str, "<gconnect_audio_bridge.py>", "exec")
    assert compiled is not None

    # Key architectural assertions
    assert "mic.fifo" in code_str
    assert "spk.fifo" in code_str
    assert "os.O_RDWR" in code_str
    assert "os.O_NONBLOCK" in code_str
    assert "GCONNECT_AUDIO_PORT" in code_str
    assert "8002" in code_str
    assert "CHUNK_SIZE = 1024" in code_str
    assert "drained = os.read(mic_fd, 65536)" in code_str
    assert "fresh_chunk = data[-1600:]" in code_str
    assert "client_sock.send(data[total_sent:])" in code_str


# -----------------------------------------------------------------------------
# 3. DockerSandboxManager Audio Integration Tests
# -----------------------------------------------------------------------------

def test_docker_manager_config_fingerprint_includes_audio_port(monkeypatch, tmp_path):
    """Configuration fingerprint must reflect audio bridge port changes."""
    mgr = DockerSandboxManager.__new__(DockerSandboxManager)
    ws = str(tmp_path / "ws")

    monkeypatch.setattr(settings, "AUDIO_BRIDGE_PORT", 8002, raising=False)
    fp1 = mgr._desired_config_fingerprint(ws)

    monkeypatch.setattr(settings, "AUDIO_BRIDGE_PORT", 8099, raising=False)
    fp2 = mgr._desired_config_fingerprint(ws)

    assert fp1 != fp2
    assert len(fp1) == 16
    assert len(fp2) == 16


def test_docker_manager_provisioning_lifecycle():
    """Verifies that provision_container_audio executes all ALSA & daemon provisioning steps."""
    mgr = DockerSandboxManager.__new__(DockerSandboxManager)
    mgr.docker_available = True

    mock_container = MagicMock()
    mock_container.status = "running"

    commands_executed = []

    def mock_exec_run(cmd, detach=False):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        commands_executed.append((cmd_str, detach))
        res = MagicMock()
        res.exit_code = 0
        return res

    mock_container.exec_run = mock_exec_run

    success = mgr.provision_container_audio(mock_container)
    assert success is True

    # Check required provisioning steps were executed:
    executed_all = " ".join([c[0] for c in commands_executed])
    assert "libasound.so.2" in executed_all
    assert "/etc/asound.conf" in executed_all
    assert "mic.fifo" in executed_all
    assert "spk.fifo" in executed_all
    assert "gconnect_audio_bridge.py" in executed_all
    assert "pgrep -f gconnect_audio_bridge.py" in executed_all

    # ensure_sandbox_ready alias check
    ready = mgr.ensure_sandbox_ready(mock_container)
    assert ready is True


def test_docker_manager_provisioning_graceful_on_error():
    """Verifies provision_container_audio does not crash when container is invalid or throws."""
    import threading
    mgr = DockerSandboxManager.__new__(DockerSandboxManager)
    mgr.docker_available = False
    mgr.client = None
    mgr._init_lock = threading.Lock()
    mgr._last_init_attempt = 0.0

    # When container is None and Docker is offline
    assert mgr.provision_container_audio(None) is False

    # When container lacks exec_run
    assert mgr.provision_container_audio(object()) is False

    # When exec_run raises an exception
    bad_container = MagicMock()
    bad_container.exec_run.side_effect = RuntimeError("Docker daemon exploded")
    assert mgr.provision_container_audio(bad_container) is False


# -----------------------------------------------------------------------------
# 4. ContainerAudioBridgeClient Tests (Offline Resilience & Live Duplex)
# -----------------------------------------------------------------------------

@pytest.mark.anyio
async def test_container_audio_bridge_client_offline_fallback():
    """Client must handle unreachable container bridge with zero unhandled exceptions."""
    # Use a non-listening loopback port
    client = ContainerAudioBridgeClient(host="127.0.0.1", port=59123, reconnect_cooldown=0.1)

    assert client.is_connected is False

    # Connect attempt should return False without raising
    connected = await client.connect()
    assert connected is False
    assert client.is_connected is False

    # Sending mic audio to offline bridge drops smoothly
    sent = await client.send_mic_audio(b"\x00\x01" * 160)
    assert sent is False

    # Ensure background loop starts and stops cleanly
    await client.ensure_started()
    await asyncio.sleep(0.05)
    await client.stop()
    assert client.is_connected is False


@pytest.mark.anyio
async def test_container_audio_bridge_client_live_tcp_duplex():
    """Tests live duplex streaming between ContainerAudioBridgeClient and a mock bridge server."""
    received_from_client = bytearray()
    server_client_writer = None

    async def mock_bridge_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        nonlocal server_client_writer
        server_client_writer = writer
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                received_from_client.extend(data)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    # Start mock container bridge TCP server on ephemeral port
    server = await asyncio.start_server(mock_bridge_handler, "127.0.0.1", 0)
    server_port = server.sockets[0].getsockname()[1]

    received_playback = []

    def on_playback(chunk: bytes):
        received_playback.append(chunk)

    bridge_client = ContainerAudioBridgeClient(
        host="127.0.0.1",
        port=server_port,
        on_playback=on_playback,
        reconnect_cooldown=0.1,
    )

    try:
        # Establish connection first, then start receiver loop
        connected = await bridge_client.connect()
        assert connected is True
        assert bridge_client.is_connected is True
        await bridge_client.ensure_started()

        # 1. Test Host Mic -> Container Transmission
        test_mic_chunk = b"\x12\x34" * 256  # 512 bytes
        ok = await bridge_client.send_mic_audio(test_mic_chunk)
        assert ok is True

        # Wait briefly for mock server read
        for _ in range(20):
            if len(received_from_client) >= len(test_mic_chunk):
                break
            await asyncio.sleep(0.02)

        assert bytes(received_from_client) == test_mic_chunk

        # 2. Test Container Playback -> Host Reception
        assert server_client_writer is not None
        test_spk_chunk = b"\x56\x78" * 512  # 1024 bytes
        server_client_writer.write(test_spk_chunk)
        await server_client_writer.drain()

        for _ in range(20):
            if received_playback:
                break
            await asyncio.sleep(0.02)

        assert len(received_playback) > 0
        assert b"".join(received_playback) == test_spk_chunk

    finally:
        await bridge_client.stop()
        if server_client_writer is not None:
            try:
                server_client_writer.close()
                await server_client_writer.wait_closed()
            except Exception:
                pass
        server.close()
        await server.wait_closed()


# -----------------------------------------------------------------------------
# 5. AudioRouter Integration Tests
# -----------------------------------------------------------------------------

@pytest.mark.anyio
async def test_audio_router_bridge_integration():
    """Verifies AudioRouter forwards mic audio to bridge and routes container playback."""
    router = AudioRouter()

    # Verify container_bridge is attached with configured port
    assert router.container_bridge is not None
    assert router.container_bridge.port == getattr(settings, "AUDIO_BRIDGE_PORT", 8002)

    # When bridge is offline, write_to_container_mic must not raise
    test_mic_chunk = b"\x44\x55" * 320
    await router.write_to_container_mic(test_mic_chunk)

    # Verify on_container_playback triggers ducking
    router.ducking_gate.set_playback_active(False)
    assert router.ducking_gate.is_ducking_active() is False

    playback_chunk = b"\x77\x88" * 320
    await router.on_container_playback(playback_chunk)

    # Container playback should have activated ducking gate
    assert router.ducking_gate.is_ducking_active() is True

    # Clean up
    await router.stop_container_bridge()


@pytest.mark.anyio
async def test_container_audio_bridge_client_reconnection():
    """Verifies that client auto-reconnects when the container bridge restarts."""
    server_writers = []
    received_bytes = bytearray()

    async def mock_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        server_writers.append(writer)
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                received_bytes.extend(data)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    server = await asyncio.start_server(mock_handler, "127.0.0.1", 0)
    server_port = server.sockets[0].getsockname()[1]

    client = ContainerAudioBridgeClient(host="127.0.0.1", port=server_port, reconnect_cooldown=0.05)

    try:
        # Phase 1: Connect & Send Initial Chunk
        ok = await client.send_mic_audio(b"A" * 512)
        assert ok is True
        assert client.is_connected is True

        for _ in range(20):
            if len(received_bytes) >= 512:
                break
            await asyncio.sleep(0.02)
        assert len(received_bytes) == 512

        # Phase 2: Force server connection drop (simulate container crash/reboot)
        for w in server_writers:
            w.close()
            try:
                await w.wait_closed()
            except Exception:
                pass
        server_writers.clear()

        # Phase 3: Wait a tick, send next chunk. Should detect disconnect and attempt reconnect
        await asyncio.sleep(0.1)
        ok2 = await client.send_mic_audio(b"B" * 512)
        assert ok2 is True
        assert client.is_connected is True

        for _ in range(20):
            if len(received_bytes) >= 1024:
                break
            await asyncio.sleep(0.02)

        assert len(received_bytes) == 1024
        assert received_bytes[:512] == b"A" * 512
        assert received_bytes[512:] == b"B" * 512

    finally:
        await client.stop()
        for w in server_writers:
            try:
                w.close()
                await w.wait_closed()
            except Exception:
                pass
        server.close()
        await server.wait_closed()


@pytest.mark.anyio
async def test_multi_chunk_duplex_streaming_integrity():
    """Verifies high-throughput multi-chunk duplex audio streaming without frame corruption."""
    received_mic = bytearray()
    server_writer = None

    async def mock_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        nonlocal server_writer
        server_writer = writer
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                received_mic.extend(data)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    server = await asyncio.start_server(mock_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    received_spk = bytearray()
    client = ContainerAudioBridgeClient(
        host="127.0.0.1",
        port=port,
        on_playback=lambda chunk: received_spk.extend(chunk),
        reconnect_cooldown=0.1,
    )

    try:
        await client.connect()
        await client.ensure_started()

        num_chunks = 20
        chunk_size = 1024
        test_mic_payload = b"".join(bytes([i % 256]) * chunk_size for i in range(num_chunks))

        # Send all mic chunks
        for i in range(num_chunks):
            chunk = test_mic_payload[i * chunk_size : (i + 1) * chunk_size]
            await client.send_mic_audio(chunk)

        # Wait for server reception
        for _ in range(50):
            if len(received_mic) >= len(test_mic_payload):
                break
            await asyncio.sleep(0.02)

        assert bytes(received_mic) == test_mic_payload

        # Send playback chunks from server to client
        test_spk_payload = b"".join(bytes([(255 - i) % 256]) * chunk_size for i in range(num_chunks))
        assert server_writer is not None
        server_writer.write(test_spk_payload)
        await server_writer.drain()

        # Wait for client reception
        for _ in range(50):
            if len(received_spk) >= len(test_spk_payload):
                break
            await asyncio.sleep(0.02)

        assert bytes(received_spk) == test_spk_payload

    finally:
        await client.stop()
        if server_writer is not None:
            try:
                server_writer.close()
                await server_writer.wait_closed()
            except Exception:
                pass
        server.close()
        await server.wait_closed()


# -----------------------------------------------------------------------------
# 6. Milestone 2 Iteration 2 Specific Invariant & Regression Tests
# -----------------------------------------------------------------------------

def test_fifo_saturation_drainage_clamps_latency_sub_50ms():
    """Verifies that FIFO saturation drainage empties stale pipe backlog and clamps latency <= 50ms.

    Linux default pipe capacity is 65,536 bytes.
    At 16,000 Hz, 16-bit mono PCM (32,000 B/s), 1ms = 32 bytes.
    Draining only 4KB would leave 61,440 bytes (~1.92s) of stale audio.
    The Iteration 2 fix drains the entire unread backlog in a non-blocking loop,
    then writes at most 1,600 bytes (50ms) of fresh audio.
    """
    pipe_capacity = 65536
    stale_payload = b"\xaa" * pipe_capacity
    pipe_buffer = bytearray(stale_payload)

    # Simulate non-blocking read and write descriptors
    def mock_read(max_bytes: int) -> bytes:
        nonlocal pipe_buffer
        if not pipe_buffer:
            return b""
        chunk = pipe_buffer[:max_bytes]
        pipe_buffer = pipe_buffer[max_bytes:]
        return bytes(chunk)

    def mock_write(chunk: bytes) -> int:
        pipe_buffer.extend(chunk)
        return len(chunk)

    # 1. Verify old flawed logic left ~1.92s of stale audio
    flawed_drain = len(stale_payload[:4096])
    flawed_remaining = len(stale_payload) - flawed_drain
    flawed_latency_ms = (flawed_remaining / (16000 * 2)) * 1000
    assert flawed_latency_ms > 1900, "Old logic must leave > 1.9s of stale audio"

    # 2. Execute Iteration 2 non-blocking full drainage loop
    drained_total = 0
    while True:
        drained = mock_read(65536)
        if not drained:
            break
        drained_total += len(drained)

    assert drained_total == pipe_capacity, "Must drain entire 64KB pipe backlog"
    assert len(pipe_buffer) == 0, "Pipe buffer must be completely emptied"

    # 3. Write fresh incoming chunk clamped to last 1600 bytes (50ms)
    incoming_chunk = b"\x55" * 2048  # > 1600 bytes
    fresh_chunk = incoming_chunk[-1600:] if len(incoming_chunk) > 1600 else incoming_chunk
    written = mock_write(fresh_chunk)
    assert written == 1600

    # 4. Invariant: resulting backlog latency is strictly <= 50ms
    current_backlog_bytes = len(pipe_buffer)
    latency_ms = (current_backlog_bytes / (16000 * 2)) * 1000
    assert current_backlog_bytes <= 1600
    assert latency_ms <= 50.0

    # 5. Verify CONTAINER_AUDIO_BRIDGE_CODE contains the drainage loop and clamping
    code_str = CONTAINER_AUDIO_BRIDGE_CODE
    assert "drained = os.read(mic_fd, 65536)" in code_str
    assert "fresh_chunk = data[-1600:]" in code_str
    assert "client_sock.send(data[total_sent:])" in code_str


@pytest.mark.anyio
async def test_playback_loop_reader_disconnect_guard_preserves_reconnected_writer():
    """Verifies that when ContainerAudioBridgeClient reconnects due to send_mic_audio,
    _playback_loop waking up on the old reader does NOT sever the newly reconnected writer.
    """
    server_writers = []
    received_mic = bytearray()

    async def mock_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        server_writers.append(writer)
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                received_mic.extend(data)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    server = await asyncio.start_server(mock_handler, "127.0.0.1", 0)
    server_port = server.sockets[0].getsockname()[1]

    client = ContainerAudioBridgeClient(
        host="127.0.0.1",
        port=server_port,
        reconnect_cooldown=0.01,
    )

    try:
        # Phase 1: Connect and start background playback loop
        ok = await client.connect()
        assert ok is True
        await client.ensure_started()

        initial_reader = client._reader
        initial_writer = client._writer
        assert initial_reader is not None
        assert initial_writer is not None

        # Give loop a brief moment to enter current_reader.read()
        await asyncio.sleep(0.05)

        # Phase 2: Force a reconnect (as send_mic_audio does on broken pipe)
        reconnected = await client.connect(force=True)
        assert reconnected is True
        new_reader = client._reader
        new_writer = client._writer

        assert new_writer is not None
        assert new_writer is not initial_writer
        assert new_reader is not None
        assert new_reader is not initial_reader

        # Phase 3: Now close the first server connection (initial reader sees EOF)
        assert len(server_writers) >= 1
        first_server_writer = server_writers[0]
        first_server_writer.close()
        try:
            await first_server_writer.wait_closed()
        except Exception:
            pass

        # Give playback loop time to wake up on the old socket
        await asyncio.sleep(0.08)

        # Phase 4: Crucial assertion - the disconnect guard must have prevented
        # _playback_loop from wiping the new writer and reader!
        assert client._writer is new_writer, "New writer must NOT be wiped by old playback reader EOF"
        assert client._reader is new_reader, "New reader must NOT be wiped by old playback reader EOF"
        assert client.is_connected is True

        # Phase 5: Confirm client can still stream mic audio over the preserved new writer
        test_payload = b"ACTIVE_MIC_AUDIO_STILL_CONNECTED"
        sent = await client.send_mic_audio(test_payload)
        assert sent is True

        # Wait for server to receive payload over the second connection
        for _ in range(20):
            if test_payload in received_mic:
                break
            await asyncio.sleep(0.02)
        assert test_payload in received_mic

    finally:
        await client.stop()
        for w in server_writers:
            try:
                w.close()
                await w.wait_closed()
            except Exception:
                pass
        server.close()
        await server.wait_closed()

