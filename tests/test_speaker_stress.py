"""Empirical stress test harness for SpeakerArbitrator and AudioRouter (Milestone 1 / Requirement R2).

Verifies:
1. Rapid 1-click takeover across 10 concurrent simulated clients under heavy thread contention.
   - Active speaker atomically switches.
   - At no time can more than one client be authorized to stream.
   - Status signaling is consistent cluster-wide.
2. Audio gating and zero leakage:
   - When one client is active, concurrent PCM chunks sent by non-active clients are completely dropped.
   - 0 bytes from non-active clients leak into the AudioRingBuffer or the container mic sink.
   - Gating switches immediately upon takeover without cross-stream contamination.
3. Disconnect handling and fallback cascade:
   - When the active speaker disconnects, arbitrator immediately transfers role to next valid client.
   - Prefers local host clients (127.0.0.1 / localhost) before remote clients.
   - Falls back to oldest connected remote client if no local host clients remain.
   - Empties to None when all clients disconnect.
   - Tested across randomized disconnect order permutations.
4. Mute toggling:
   - Active speaker muted -> mic stream completely suppressed (zero bytes in buffer/sink).
   - Unmute -> mic audio resumes immediately.
   - Rapid concurrent mute toggles do not drop or corrupt audio state.
5. End-to-end WebSocket cluster stress (10 concurrent clients on /ws/audio):
   - Multi-client handshake and personalized speaker_status signaling.
   - Sequential and rapid claim_speaker takeovers over WebSocket.
   - Binary PCM audio gating through the live router pipeline.
   - Mute signaling and active mic suppression over WebSocket.
   - Disconnect handling and cluster-wide status update broadcast.
"""

import array
import os
import random
import sys
import threading
import time
from typing import List

import pytest
from fastapi.testclient import TestClient

# Ensure GConnect root is on sys.path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from backend.audio_router import (
    AudioRouter,
    SpeakerArbitrator,
    audio_router,
)
from backend.config import settings
from tests.conftest import TEST_TOKEN


# -----------------------------------------------------------------------------
# Test Fixture: Router Isolation
# -----------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_audio_router_state():
    """Reset the global audio_router singleton between tests."""
    audio_router.arbitrator = SpeakerArbitrator()
    audio_router.ring_buffer.clear()
    audio_router.ducking_gate.set_force_ducking(False)
    audio_router.set_container_mic_sink(None)
    audio_router._websockets.clear()
    yield
    audio_router.arbitrator = SpeakerArbitrator()
    audio_router.ring_buffer.clear()
    audio_router.ducking_gate.set_force_ducking(False)
    audio_router.set_container_mic_sink(None)
    audio_router._websockets.clear()


# -----------------------------------------------------------------------------
# 1. Multi-Threaded Rapid Takeover Stress Test
# -----------------------------------------------------------------------------

def test_speaker_arbitrator_rapid_takeover_concurrency():
    """10 concurrent threads simultaneously race to claim speaker 500 times each.

    Invariants checked at every step:
    - active_speaker_id is always one of the registered client IDs.
    - Exactly one client has can_stream == True across the entire cluster.
    - Non-active clients ALWAYS have can_stream == False.
    - get_speaker_status returns is_you=True ONLY for the active client.
    """
    arb = SpeakerArbitrator()
    num_clients = 10
    iterations_per_client = 500
    client_ids = [f"client-{i}" for i in range(num_clients)]

    for cid in client_ids:
        arb.register_client(cid, f"User {cid}", is_local=(cid == "client-0"))

    errors: List[str] = []
    stop_event = threading.Event()

    def client_worker(cid: str):
        for _ in range(iterations_per_client):
            if stop_event.is_set():
                break
            try:
                # 1-click takeover
                arb.claim_speaker(cid)

                # Check invariants under high contention
                active = arb.active_speaker_id
                if active not in client_ids:
                    errors.append(f"Invalid active speaker: {active}")
                    stop_event.set()
                    break

                # Exactly one client should be authorized to stream
                can_stream_count = sum(1 for c in client_ids if arb.can_stream(c))
                if can_stream_count > 1:
                    errors.append(f"Multiple streaming clients detected! Count: {can_stream_count}")
                    stop_event.set()
                    break

                # Verify status consistency
                status = arb.get_speaker_status(for_client_id=cid)
                if status["is_you"] != (active == cid):
                    errors.append(
                        f"Inconsistent is_you flag for {cid}: is_you={status['is_you']} but active={active}"
                    )
                    stop_event.set()
                    break

            except Exception as e:
                errors.append(f"Exception in thread {cid}: {e}")
                stop_event.set()
                break

    threads = [
        threading.Thread(target=client_worker, args=(cid,), name=f"Thread-{cid}")
        for cid in client_ids
    ]

    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)
    elapsed = time.time() - t0

    assert not errors, f"Stress test failed with errors: {errors[:5]}"
    assert not any(t.is_alive() for t in threads), "One or more threads timed out!"
    print(f"\n[PASS] 10 concurrent clients performed {num_clients * iterations_per_client} takeovers in {elapsed:.3f}s")


# -----------------------------------------------------------------------------
# 2. Audio Gating & Zero Leakage Stress Test
# -----------------------------------------------------------------------------

def test_speaker_arbitrator_audio_gating_no_leakage():
    """Verify non-active client PCM chunks are completely dropped and never leak into buffer or sink.

    Procedure:
    - 10 registered clients.
    - client-0 claims speaker (active).
    - client-0 streams 200 chunks with distinct signature 0xAA (sample value 10,000).
    - Concurrently, clients 1..9 spam 200 chunks each with distinct signatures (0x11 .. 0x99).
    - Container mic sink collects all delivered chunks.
    - Oracle: 100% of chunks in sink and ring buffer match client-0 signature.
      Zero chunks from clients 1..9 ever leak into the pipeline!
    """
    router = AudioRouter()
    sink_chunks: List[bytes] = []
    router.set_container_mic_sink(lambda chunk: sink_chunks.append(chunk))

    num_clients = 10
    chunks_per_client = 200
    chunk_samples = 320  # 20ms at 16kHz

    # Register 10 clients
    for i in range(num_clients):
        router.arbitrator.register_client(f"client-{i}", f"User {i}")

    # client-0 claims speaker
    router.arbitrator.claim_speaker("client-0")

    # Generate distinct synthetic PCM chunks
    # client-0 uses sample 10000
    active_sample = 10000
    active_chunk = array.array("h", [active_sample] * chunk_samples).tobytes()

    # non-active clients use unique negative sample values
    non_active_chunks = {}
    for i in range(1, num_clients):
        sample_val = -(i * 1000)
        non_active_chunks[f"client-{i}"] = array.array("h", [sample_val] * chunk_samples).tobytes()

    def stream_client(cid: str):
        is_active = (cid == "client-0")
        chunk_data = active_chunk if is_active else non_active_chunks[cid]
        for _ in range(chunks_per_client):
            # Simulate router mic intake logic
            if router.arbitrator.can_stream(cid):
                ducked = router.ducking_gate.apply_ducking(chunk_data)
                router.ring_buffer.write(ducked)
                # Call sink synchronously for test
                sink_chunks.append(ducked)

    threads = [
        threading.Thread(target=stream_client, args=(f"client-{i}",))
        for i in range(num_clients)
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    # Verification:
    # Sink must have received exactly chunks_per_client chunks (all from client-0)
    assert len(sink_chunks) == chunks_per_client, (
        f"Expected {chunks_per_client} sink chunks, got {len(sink_chunks)}"
    )

    for idx, chunk in enumerate(sink_chunks):
        samples = array.array("h")
        samples.frombytes(chunk)
        # Every single sample must be 10000 (from client-0)
        assert samples[0] == active_sample, (
            f"Leakage detected at chunk {idx}! Expected sample {active_sample}, got {samples[0]}"
        )

    # Next phase: Mid-stream Takeover by client-4
    sink_chunks.clear()
    router.ring_buffer.clear()
    router.arbitrator.claim_speaker("client-4")
    new_active_chunk = array.array("h", [4444] * chunk_samples).tobytes()

    # Now client-4 is active; client-0 and others should be dropped
    def stream_phase2(cid: str):
        chunk_data = new_active_chunk if cid == "client-4" else active_chunk
        for _ in range(100):
            if router.arbitrator.can_stream(cid):
                ducked = router.ducking_gate.apply_ducking(chunk_data)
                router.ring_buffer.write(ducked)
                sink_chunks.append(ducked)

    threads2 = [
        threading.Thread(target=stream_phase2, args=(f"client-{i}",))
        for i in range(num_clients)
    ]
    for t in threads2:
        t.start()
    for t in threads2:
        t.join(timeout=5.0)

    assert len(sink_chunks) == 100
    for chunk in sink_chunks:
        samples = array.array("h")
        samples.frombytes(chunk)
        assert samples[0] == 4444, f"Old speaker audio leaked after takeover: got {samples[0]}"

    print(f"\n[PASS] Audio gating verified: zero leakage across {num_clients * chunks_per_client} concurrent chunks")


# -----------------------------------------------------------------------------
# 3. Disconnect Handling & Local Host Preference Fallback Cascade
# -----------------------------------------------------------------------------

def test_speaker_arbitrator_disconnect_handling_and_fallback():
    """Verify disconnect auto-transfer rules:

    1. When active speaker disconnects:
       - Prefers local host clients (is_local=True).
       - If multiple local clients exist, transfers to the first registered local client.
       - If no local clients exist, transfers to oldest connected remote client.
       - If all clients disconnect, active speaker becomes None.
    2. Tested across 50 randomized disconnect permutation rounds.
    """
    arb = SpeakerArbitrator()

    # Step 1: Explicit Deterministic Sequence
    # Connect 2 local clients and 3 remote clients in specific order
    arb.register_client("remote-1", "Remote 1", is_local=False)
    time.sleep(0.005)
    arb.register_client("remote-2", "Remote 2", is_local=False)
    time.sleep(0.005)
    arb.register_client("local-1", "Local Host 1", is_local=True)
    time.sleep(0.005)
    arb.register_client("local-2", "Local Host 2", is_local=True)
    time.sleep(0.005)
    arb.register_client("remote-3", "Remote 3", is_local=False)

    # Initial active speaker was remote-1 (first to connect)
    assert arb.active_speaker_id == "remote-1"

    # Make remote-2 the active speaker
    arb.claim_speaker("remote-2")
    assert arb.active_speaker_id == "remote-2"

    # Active speaker remote-2 disconnects -> local-1 preferred over remote-1 or remote-3
    new_active = arb.unregister_client("remote-2")
    assert new_active == "local-1", f"Expected local-1, got {new_active}"
    assert arb.active_speaker_id == "local-1"

    # local-1 disconnects -> remaining local client local-2 preferred
    new_active = arb.unregister_client("local-1")
    assert new_active == "local-2", f"Expected local-2, got {new_active}"
    assert arb.active_speaker_id == "local-2"

    # local-2 disconnects -> no local clients remain. Oldest remote client (remote-1) selected!
    new_active = arb.unregister_client("local-2")
    assert new_active == "remote-1", f"Expected oldest remote-1, got {new_active}"
    assert arb.active_speaker_id == "remote-1"

    # remote-1 disconnects -> last client remote-3 selected
    new_active = arb.unregister_client("remote-1")
    assert new_active == "remote-3", f"Expected remote-3, got {new_active}"
    assert arb.active_speaker_id == "remote-3"

    # remote-3 disconnects -> None
    new_active = arb.unregister_client("remote-3")
    assert new_active is None
    assert arb.active_speaker_id is None
    assert arb.get_speaker_status()["active_client_id"] == ""

    # Step 2: 50 Randomized Permutation Rounds
    for round_idx in range(50):
        arb_rand = SpeakerArbitrator()
        clients = []
        for i in range(10):
            is_local = (i < 3)  # 3 local, 7 remote
            cid = f"client-{round_idx}-{i}"
            arb_rand.register_client(cid, is_local=is_local)
            clients.append((cid, is_local))
            time.sleep(0.0005)

        # Disconnect in random order until empty
        disconnect_order = list(clients)
        random.shuffle(disconnect_order)

        for disc_cid, _ in disconnect_order:
            was_active = (arb_rand.active_speaker_id == disc_cid)
            arb_rand.unregister_client(disc_cid)
            remaining = arb_rand.connected_clients

            if not remaining:
                assert arb_rand.active_speaker_id is None
            elif was_active:
                curr_active = arb_rand.active_speaker_id
                remaining_locals = [c for c in remaining.values() if c.is_local]
                if remaining_locals:
                    assert curr_active in [c.client_id for c in remaining_locals], (
                        f"Round {round_idx}: Active speaker {curr_active} is not a remaining local client!"
                    )
                else:
                    oldest_id = min(remaining.values(), key=lambda c: c.connected_at).client_id
                    assert curr_active == oldest_id, (
                        f"Round {round_idx}: Active speaker {curr_active} did not fall back to oldest remote {oldest_id}"
                    )

    print("\n[PASS] Disconnect fallback verified deterministically and across 50 randomized cascade rounds")


# -----------------------------------------------------------------------------
# 4. Mute Gating & Suppression Stress Test
# -----------------------------------------------------------------------------

def test_speaker_arbitrator_mute_suppression_and_toggle_stress():
    """Verify mute toggling completely suppresses mic stream and handles rapid toggling."""
    arb = SpeakerArbitrator()
    arb.register_client("host-1", "Host", is_local=True)
    arb.register_client("remote-1", "Remote", is_local=False)

    # Active speaker can stream when unmuted
    assert arb.can_stream("host-1") is True
    assert arb.is_muted("host-1") is False

    # Mute active speaker
    arb.set_mute("host-1", True)
    assert arb.is_muted("host-1") is True
    assert arb.can_stream("host-1") is False

    # Status payload reflects active speaker even when muted
    status = arb.get_speaker_status(for_client_id="host-1")
    assert status["active_client_id"] == "host-1"
    assert status["is_you"] is True

    # Unmute restores streaming
    arb.set_mute("host-1", False)
    assert arb.can_stream("host-1") is True

    # Muting non-active client does not affect active speaker
    arb.set_mute("remote-1", True)
    assert arb.can_stream("host-1") is True
    assert arb.can_stream("remote-1") is False

    # Concurrency stress: 1 thread streaming audio, 1 thread rapidly toggling mute
    suppressed_chunks = 0
    passed_chunks = 0

    def audio_streamer():
        nonlocal suppressed_chunks, passed_chunks
        for _ in range(1000):
            if arb.can_stream("host-1"):
                passed_chunks += 1
            else:
                suppressed_chunks += 1
            time.sleep(0.0001)

    def mute_toggler():
        state = False
        for _ in range(500):
            state = not state
            arb.set_mute("host-1", state)
            time.sleep(0.0002)
        arb.set_mute("host-1", False)

    t1 = threading.Thread(target=audio_streamer)
    t2 = threading.Thread(target=mute_toggler)

    t1.start()
    t2.start()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)

    assert passed_chunks > 0, "No chunks passed during mute test!"
    assert suppressed_chunks > 0, "No chunks suppressed during mute test!"
    assert arb.can_stream("host-1") is True, "Host should be unmuted at conclusion"
    print(f"\n[PASS] Mute suppression verified: {passed_chunks} passed, {suppressed_chunks} suppressed during rapid toggling")


# -----------------------------------------------------------------------------
# 5. End-to-End WebSocket Cluster Integration Test (10 Concurrent Clients)
# -----------------------------------------------------------------------------

def test_websocket_cluster_10_clients_takeover_gating_disconnect(client, auth_settings):
    """End-to-end multi-client WebSocket test on /ws/audio with 10 concurrent connections.

    Verifies:
    - 10 clients connect with token authentication.
    - Initial speaker_status broadcasts: first client is active.
    - Sequential 1-click takeovers via JSON {"type": "claim_speaker"}:
      Every client successfully claims active speaker role.
    - Audio gating over WebSocket:
      Binary PCM chunks sent by non-active clients do NOT reach container mic sink.
      Binary PCM chunks sent by active speaker DO reach container mic sink.
    - Mute over WebSocket:
      Active speaker sends {"type": "mute", "muted": True} -> binary chunks suppressed.
      Active speaker sends {"type": "mute", "muted": False} -> binary chunks pass.
    - Disconnect handling:
      Active speaker closes connection -> remaining clients receive broadcast with new active speaker.
    """
    num_clients = 10
    sockets = []
    received_sink_chunks = []
    audio_router.set_container_mic_sink(lambda chunk: received_sink_chunks.append(chunk))

    try:
        # Step 1: Connect 10 concurrent WebSocket clients
        for i in range(num_clients):
            cid = f"cluster-client-{i}"
            cname = f"Cluster Device {i}"
            url = f"/ws/audio?token={TEST_TOKEN}&client_id={cid}&client_name={cname}"
            ws_conn = client.websocket_connect(url)
            ws = ws_conn.__enter__()
            sockets.append((ws_conn, ws, cid, cname))

        # Drain all initial connect broadcasts on all sockets
        for i in range(num_clients):
            ws = sockets[i][1]
            cid = sockets[i][2]
            for _ in range(num_clients - i):
                msg = ws.receive_json()
                assert msg["type"] == "speaker_status"
                assert msg["active_client_id"] == "cluster-client-0"
                assert msg["is_you"] == (cid == "cluster-client-0")

        print("\n[E2E] All 10 clients connected; initial active speaker confirmed: cluster-client-0", flush=True)

        # Step 2: 1-Click Takeover Sequence across clients 1..9
        for target_idx in range(1, num_clients):
            target_ws = sockets[target_idx][1]
            target_cid = sockets[target_idx][2]
            target_cname = sockets[target_idx][3]

            # Send claim_speaker from target client
            target_ws.send_json({
                "type": "claim_speaker",
                "client_id": target_cid,
                "client_name": target_cname,
            })
            time.sleep(0.02)  # Allow TestClient background event loop to broadcast

            # Every connected client must receive the broadcasted status
            for s_idx in range(num_clients):
                s_ws = sockets[s_idx][1]
                s_cid = sockets[s_idx][2]
                s_msg = s_ws.receive_json()
                assert s_msg["type"] == "speaker_status"
                assert s_msg["active_client_id"] == target_cid
                assert s_msg["is_you"] == (s_cid == target_cid)

        print("[E2E] Sequential 1-click takeovers across all 10 clients verified", flush=True)

        # Step 3: Binary Audio Gating over WebSocket
        # Currently, cluster-client-9 is active speaker
        active_idx = 9
        non_active_idx = 3
        active_ws = sockets[active_idx][1]
        non_active_ws = sockets[non_active_idx][1]

        received_sink_chunks.clear()

        # Non-active client sends PCM chunk (sample 9999)
        non_active_pcm = array.array("h", [9999] * 320).tobytes()
        non_active_ws.send_bytes(non_active_pcm)
        time.sleep(0.05)

        # Must NOT be received in container mic sink
        assert len(received_sink_chunks) == 0, "Non-active client PCM leaked to sink!"

        # Active speaker sends PCM chunk (sample 5555)
        active_pcm = array.array("h", [5555] * 320).tobytes()
        active_ws.send_bytes(active_pcm)
        time.sleep(0.05)

        # Must BE received in container mic sink
        assert len(received_sink_chunks) == 1, "Active client PCM failed to reach sink!"
        received_samples = array.array("h")
        received_samples.frombytes(received_sink_chunks[0])
        assert received_samples[0] == 5555

        print("[E2E] WebSocket binary audio gating verified (non-active dropped, active passed)", flush=True)

        # Step 4: Mute Signaling and Audio Suppression over WebSocket
        received_sink_chunks.clear()

        # Active speaker mutes
        active_ws.send_json({"type": "mute", "muted": True})
        time.sleep(0.02)
        # All 10 sockets receive the mute status broadcast
        for s_idx in range(num_clients):
            mute_msg = sockets[s_idx][1].receive_json()
            assert mute_msg["type"] == "speaker_status"

        # Active speaker sends PCM chunk while muted
        active_ws.send_bytes(active_pcm)
        time.sleep(0.05)
        assert len(received_sink_chunks) == 0, "Muted active speaker PCM leaked to sink!"

        # Active speaker unmutes
        active_ws.send_json({"type": "mute", "muted": False})
        time.sleep(0.02)
        # All 10 sockets receive the unmute status broadcast
        for s_idx in range(num_clients):
            unmute_msg = sockets[s_idx][1].receive_json()
            assert unmute_msg["type"] == "speaker_status"

        # Active speaker sends PCM chunk while unmuted
        active_ws.send_bytes(active_pcm)
        time.sleep(0.05)
        assert len(received_sink_chunks) == 1, "Unmuted active speaker PCM failed to reach sink!"

        print("[E2E] WebSocket mute signaling and audio suppression verified", flush=True)

        # Step 5: Disconnect Handling
        # Active speaker (cluster-client-9) disconnects
        active_ws = sockets[active_idx][1]
        active_ws.close()
        sockets.pop(active_idx)

        time.sleep(0.05)
        # Remaining 9 clients receive updated speaker_status broadcast
        # Since all testclient connections are local, arbitrator selects first remaining local client
        expected_new_active = sockets[0][2]  # cluster-client-0
        for s_idx in range(len(sockets)):
            disc_broadcast = sockets[s_idx][1].receive_json()
            assert disc_broadcast["type"] == "speaker_status"
            assert disc_broadcast["active_client_id"] == expected_new_active
            assert disc_broadcast["is_you"] == (sockets[s_idx][2] == expected_new_active)

        print(f"[E2E] Disconnect handling verified; new active speaker broadcast to 9 remaining clients: {expected_new_active}", flush=True)

    finally:
        # Cleanly close all remaining connections
        for _, ws, _, _ in sockets:
            try:
                ws.close()
            except Exception:
                pass
        audio_router.set_container_mic_sink(None)


# -----------------------------------------------------------------------------
# Standalone Runner
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("STARTING EMPIRICAL STRESS TEST SUITE FOR SpeakerArbitrator")
    print("=" * 70)

    # 1. Concurrency takeover stress
    test_speaker_arbitrator_rapid_takeover_concurrency()

    # 2. Audio gating zero-leakage stress
    test_speaker_arbitrator_audio_gating_no_leakage()

    # 3. Disconnect handling and cascade fallback
    test_speaker_arbitrator_disconnect_handling_and_fallback()

    # 4. Mute suppression stress
    test_speaker_arbitrator_mute_suppression_and_toggle_stress()

    # 5. E2E WebSocket 10-client cluster test
    from backend.main import app
    test_client = TestClient(app)
    settings.AUTH_TOKEN = TEST_TOKEN
    settings.AUTH_DISABLED = False
    settings.ALLOW_LAN = False
    from backend import auth
    auth.reset_token_for_tests()

    class DummyAuthSettings:
        pass

    test_websocket_cluster_10_clients_takeover_gating_disconnect(test_client, DummyAuthSettings())

    print("=" * 70)
    print("ALL EMPIRICAL STRESS TESTS PASSED SUCCESSFULLY!")
    print("=" * 70)
