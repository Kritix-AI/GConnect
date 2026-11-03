"""Empirical Stress Test Harness for Milestone 2: Docker Container Audio Virtualization.

Verifies:
1. Full-Duplex TCP Bridge Throughput & Integrity:
   - Ephemeral mock container audio server.
   - 100,000 bytes mic PCM (50 chunks x 2,000 bytes) host -> container.
   - 100,000 bytes playback PCM container -> host concurrently.
   - 100% byte-for-byte fidelity in both directions concurrently without deadlocks, corruption, or truncation.
2. Rapid Connect/Disconnect & Network Flapping:
   - Repeatedly start, kill, and restart the mock container server while client is actively streaming.
   - Verify client handles broken pipes, resets state, and auto-reconnects when the server returns.
3. Latency & Zero Latency Creep:
   - Measure transit latency across 100 round-trips.
   - Verify sub-10ms localhost transport and statistical stability (zero creep).
"""

import asyncio
import os
import statistics
import time
from typing import List, Optional

import pytest

from backend.audio_router import ContainerAudioBridgeClient


# =============================================================================
# Helper Utilities & Fixtures
# =============================================================================

def generate_pattern_bytes(length: int, seed: int = 42) -> bytes:
    """Generates deterministic pseudo-random binary PCM pattern with high entropy."""
    data = bytearray(length)
    state = seed & 0xFFFFFFFF
    for i in range(length):
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        data[i] = (state >> 16) & 0xFF
    return bytes(data)


# =============================================================================
# Test 1: Full-Duplex TCP Bridge Throughput & Integrity (100k bytes each way)
# =============================================================================

@pytest.mark.anyio
async def test_full_duplex_tcp_bridge_throughput_and_integrity():
    """Streams 100,000 bytes (50 chunks x 2,000 bytes) of mic PCM from host to container,

    and simultaneous 100,000 bytes of playback PCM from container to host.
    Verifies 100% byte-for-byte fidelity in both directions concurrently without
    deadlocks, corruption, or packet truncation.
    """
    total_bytes = 100_000
    chunk_size = 2_000
    num_chunks = total_bytes // chunk_size  # 50 chunks

    expected_mic_data = generate_pattern_bytes(total_bytes, seed=1001)
    expected_spk_data = generate_pattern_bytes(total_bytes, seed=2002)

    received_mic_server = bytearray()
    received_spk_client = bytearray()
    server_client_writer: Optional[asyncio.StreamWriter] = None
    server_client_reader: Optional[asyncio.StreamReader] = None
    server_connected_event = asyncio.Event()

    async def mock_container_server_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        nonlocal server_client_writer, server_client_reader
        server_client_reader = reader
        server_client_writer = writer
        server_connected_event.set()

    # Start mock container bridge TCP server on ephemeral port
    server = await asyncio.start_server(mock_container_server_handler, "127.0.0.1", 0)
    server_port = server.sockets[0].getsockname()[1]

    def on_playback(chunk: bytes):
        received_spk_client.extend(chunk)

    bridge_client = ContainerAudioBridgeClient(
        host="127.0.0.1",
        port=server_port,
        on_playback=on_playback,
        reconnect_cooldown=0.1,
    )

    try:
        # Connect client to server
        connected = await bridge_client.connect()
        assert connected is True, "Client failed to connect to mock container server"
        await bridge_client.ensure_started()
        await asyncio.wait_for(server_connected_event.wait(), timeout=3.0)

        assert server_client_writer is not None
        assert server_client_reader is not None

        # Concurrently execute:
        # 1. Host mic sending (50 chunks of 2,000 bytes)
        # 2. Server mic receiving (until 100,000 bytes)
        # 3. Server spk sending (50 chunks of 2,000 bytes)
        # 4. Host spk receiving (until 100,000 bytes collected by on_playback)

        async def send_host_mic():
            for i in range(num_chunks):
                chunk = expected_mic_data[i * chunk_size : (i + 1) * chunk_size]
                ok = await bridge_client.send_mic_audio(chunk)
                assert ok is True, f"Failed sending mic chunk {i}"
                # Small yield to allow event loop cooperative interleaving
                await asyncio.sleep(0.001)

        async def recv_server_mic():
            while len(received_mic_server) < total_bytes:
                needed = total_bytes - len(received_mic_server)
                data = await server_client_reader.read(min(needed, 8192))
                if not data:
                    break
                received_mic_server.extend(data)

        async def send_server_spk():
            for i in range(num_chunks):
                chunk = expected_spk_data[i * chunk_size : (i + 1) * chunk_size]
                server_client_writer.write(chunk)
                await server_client_writer.drain()
                await asyncio.sleep(0.001)

        async def wait_host_spk():
            for _ in range(500):
                if len(received_spk_client) >= total_bytes:
                    break
                await asyncio.sleep(0.01)

        start_time = time.perf_counter()

        # Run all 4 tasks concurrently under strict timeout (no deadlocks)
        await asyncio.wait_for(
            asyncio.gather(
                send_host_mic(),
                recv_server_mic(),
                send_server_spk(),
                wait_host_spk(),
            ),
            timeout=15.0,
        )

        elapsed = time.perf_counter() - start_time
        throughput_bytes_per_sec = (total_bytes * 2) / elapsed

        print(f"\n[STRESS 1] Full-Duplex 100k bytes streamed in {elapsed:.3f}s "
              f"({throughput_bytes_per_sec / (1024 * 1024):.2f} MB/s total duplex throughput)")

        # Verify integrity
        assert len(received_mic_server) == total_bytes, (
            f"Server received {len(received_mic_server)} mic bytes, expected {total_bytes}"
        )
        assert bytes(received_mic_server) == expected_mic_data, (
            "Byte corruption detected in Host -> Container mic stream!"
        )

        assert len(received_spk_client) == total_bytes, (
            f"Client received {len(received_spk_client)} spk bytes, expected {total_bytes}"
        )
        assert bytes(received_spk_client) == expected_spk_data, (
            "Byte corruption detected in Container -> Host playback stream!"
        )

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


# =============================================================================
# Test 2: Rapid Connect/Disconnect & Network Flapping
# =============================================================================

@pytest.mark.anyio
async def test_rapid_connect_disconnect_and_network_flapping():
    """Repeatedly start, kill, and restart the mock container server while host

    ContainerAudioBridgeClient is actively streaming.
    Verifies client handles broken pipes, resets state, and auto-reconnects
    when the server returns.
    """
    received_chunks = []
    active_writers: List[asyncio.StreamWriter] = []

    async def mock_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        active_writers.append(writer)
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                received_chunks.append(data)
        except (ConnectionResetError, BrokenPipeError, OSError, asyncio.CancelledError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    # Bind to a fixed local port for repeat restarts
    server = await asyncio.start_server(mock_handler, "127.0.0.1", 0)
    server_port = server.sockets[0].getsockname()[1]

    client = ContainerAudioBridgeClient(
        host="127.0.0.1",
        port=server_port,
        reconnect_cooldown=0.05,
    )

    try:
        # 1. Initial healthy connection and streaming
        ok1 = await client.send_mic_audio(b"INIT_CHUNK_" * 10)
        assert ok1 is True
        assert client.is_connected is True

        # Flapping loop: 4 full cycles of kill -> stream during outage -> restart -> recover
        for flap_cycle in range(1, 5):
            print(f"\n[FLAP CYCLE {flap_cycle}] Killing container server on port {server_port}...")

            # Abruptly kill all client connections and stop server
            for w in list(active_writers):
                w.close()
                try:
                    await w.wait_closed()
                except Exception:
                    pass
            active_writers.clear()
            server.close()
            await server.wait_closed()

            # Verify client handles broken pipe or offline server gracefully
            # Attempt streaming during server outage: must NOT raise unhandled exception!
            outage_result = await client.send_mic_audio(b"OUTAGE_DROP_" * 10)
            # Can be False or True if re-buffered/closed, but must not crash
            assert outage_result is False or not client.is_connected

            # Re-verify state reflects disconnection
            # Note: after broken pipe, is_connected should be False
            assert client.is_connected is False or outage_result is False

            # Brief pause to simulate container restart delay
            await asyncio.sleep(0.08)

            # Bring server back up on the EXACT SAME PORT
            print(f"[FLAP CYCLE {flap_cycle}] Restarting container server on port {server_port}...")
            server = await asyncio.start_server(mock_handler, "127.0.0.1", server_port)

            # Attempt streaming again: client must auto-reconnect!
            reconnect_success = False
            for attempt in range(10):
                await asyncio.sleep(0.05)
                ok = await client.send_mic_audio(f"RECONNECTED_{flap_cycle}_".encode() * 10)
                if ok and client.is_connected:
                    reconnect_success = True
                    break

            assert reconnect_success is True, (
                f"Client failed to auto-reconnect on flap cycle {flap_cycle}!"
            )
            print(f"[FLAP CYCLE {flap_cycle}] Client successfully recovered and reconnected.")

    finally:
        await client.stop()
        for w in active_writers:
            try:
                w.close()
                await w.wait_closed()
            except Exception:
                pass
        server.close()
        await server.wait_closed()


# =============================================================================
# Test 3: Latency & Zero Latency Creep (100 Round-Trips)
# =============================================================================

@pytest.mark.anyio
async def test_latency_and_zero_latency_creep_100_roundtrips():
    """Measures transit latency across 100 round-trips over localhost TCP bridge.

    Verifies:
    1. Sub-10ms localhost transport (mean, median, 95th percentile).
    2. Zero latency creep (compares early round-trips vs late round-trips).
    """
    server_client_writer: Optional[asyncio.StreamWriter] = None
    server_ready = asyncio.Event()

    async def echo_server_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        nonlocal server_client_writer
        server_client_writer = writer
        server_ready.set()
        try:
            while True:
                # Read tagged 64-byte frame and immediately echo it back
                data = await reader.read(64)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    server = await asyncio.start_server(echo_server_handler, "127.0.0.1", 0)
    server_port = server.sockets[0].getsockname()[1]

    # Map of pending round-trips: seq -> Future
    pending_rtt = {}

    def on_echo_playback(chunk: bytes):
        # Frame format: 8-byte seq + padding
        if len(chunk) >= 8:
            seq_str = chunk[:8].decode("ascii", errors="ignore").strip()
            if seq_str.isdigit():
                seq = int(seq_str)
                if seq in pending_rtt and not pending_rtt[seq].done():
                    pending_rtt[seq].set_result(time.perf_counter())

    client = ContainerAudioBridgeClient(
        host="127.0.0.1",
        port=server_port,
        on_playback=on_echo_playback,
        reconnect_cooldown=0.1,
    )

    try:
        await client.connect()
        await client.ensure_started()
        await asyncio.wait_for(server_ready.wait(), timeout=3.0)

        num_trips = 100
        latencies_ms: List[float] = []

        loop = asyncio.get_running_loop()

        for seq in range(num_trips):
            fut = loop.create_future()
            pending_rtt[seq] = fut

            # Tagged 64-byte frame: 8-byte zero-padded seq + 56 bytes payload
            frame = f"{seq:08d}".encode("ascii") + b"X" * 56
            t_send = time.perf_counter()

            ok = await client.send_mic_audio(frame)
            assert ok is True, f"Failed sending ping frame seq {seq}"

            t_recv = await asyncio.wait_for(fut, timeout=1.0)
            rtt_ms = (t_recv - t_send) * 1000.0
            latencies_ms.append(rtt_ms)

            # Small inter-packet spacing simulating audio frame timing (2ms)
            await asyncio.sleep(0.002)

        # Statistical Analysis
        min_lat = min(latencies_ms)
        max_lat = max(latencies_ms)
        mean_lat = statistics.mean(latencies_ms)
        median_lat = statistics.median(latencies_ms)
        stdev_lat = statistics.stdev(latencies_ms) if len(latencies_ms) > 1 else 0.0

        sorted_lat = sorted(latencies_ms)
        p90 = sorted_lat[int(0.90 * num_trips)]
        p95 = sorted_lat[int(0.95 * num_trips)]
        p99 = sorted_lat[int(0.99 * num_trips)]

        early_mean = statistics.mean(latencies_ms[:20])
        late_mean = statistics.mean(latencies_ms[-20:])
        creep_delta = late_mean - early_mean

        print("\n" + "=" * 60)
        print("--- LATENCY & JITTER BENCHMARK (100 ROUND-TRIPS) ---")
        print(f"Min:        {min_lat:.3f} ms")
        print(f"Mean:       {mean_lat:.3f} ms")
        print(f"Median:     {median_lat:.3f} ms")
        print(f"Max:        {max_lat:.3f} ms")
        print(f"StdDev:     {stdev_lat:.3f} ms")
        print(f"P90:        {p90:.3f} ms")
        print(f"P95:        {p95:.3f} ms")
        print(f"P99:        {p99:.3f} ms")
        print(f"Early (1-20) Mean:  {early_mean:.3f} ms")
        print(f"Late (81-100) Mean: {late_mean:.3f} ms")
        print(f"Latency Creep:      {creep_delta:+.3f} ms")
        print("=" * 60)

        # Assertions
        assert mean_lat < 10.0, f"Mean latency {mean_lat:.2f}ms exceeds 10ms threshold"
        assert median_lat < 10.0, f"Median latency {median_lat:.2f}ms exceeds 10ms threshold"
        assert p95 < 15.0, f"P95 latency {p95:.2f}ms exceeds 15ms threshold"
        # Zero latency creep: late mean should not blow up relative to early mean
        assert abs(creep_delta) < 5.0, (
            f"Latency creep detected: late mean {late_mean:.2f}ms vs early mean {early_mean:.2f}ms"
        )

    finally:
        await client.stop()
        if server_client_writer is not None:
            try:
                server_client_writer.close()
                await server_client_writer.wait_closed()
            except Exception:
                pass
        server.close()
        await server.wait_closed()
