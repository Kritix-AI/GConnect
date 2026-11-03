"""Backend Audio Hub & Network Router for GConnect (Requirement R2).

Provides ultra-low-latency bidirectional audio streaming between connected clients
(browser / Tauri desktop) and the host daemon / container sandbox:
- AudioRingBuffer: 50ms circular jitter buffer with pre-buffering, underrun zero-padding,
  and overrun/latency creep clamping.
- DuckingGate: -18dB software audio ducking during container playback using standard
  library array.array('h') (Python 3.14 PEP 594 compliant, zero dependencies).
- SpeakerArbitrator: Exclusive active mic arbitration with 1-click takeover and disconnect auto-transfer.
- AudioRouter: Master WebSocket hub managing /ws/audio streaming, signaling, and container bridge sinks.
"""

import array
import asyncio
import json
import logging
import math
import secrets
import select
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from backend.config import settings

logger = logging.getLogger("connect.audio_router")


# -----------------------------------------------------------------------------
# 1. AudioRingBuffer
# -----------------------------------------------------------------------------

class AudioRingBuffer:
    """Thread-safe circular ring buffer for smoothing audio packet delivery jitter.

    Specifications (16kHz 16-bit signed mono PCM):
    - 1 sample = 2 bytes.
    - 1 ms = 32 bytes.
    - 50 ms pre-buffer (target buffer) = 1,600 bytes (800 samples).
    - 150 ms max latency threshold = 4,800 bytes (2,400 samples).

    Key features:
    - Pre-buffering: Returns silence until target occupancy (50ms) is reached.
    - Underrun recovery: Pushes available audio and zero-pads the rest to requested
      size to prevent audio pipeline breakage, then re-enters pre-buffering.
    - Overrun clamping: When occupancy exceeds 150ms, drops oldest samples to clamp
      latency back to exactly 50ms.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        buffer_ms: int = 50,
        max_latency_ms: int = 150,
    ):
        self.sample_rate = sample_rate
        self.buffer_ms = buffer_ms
        self.max_latency_ms = max_latency_ms
        self.bytes_per_sample = 2  # 16-bit mono
        self.bytes_per_ms = (sample_rate * self.bytes_per_sample) // 1000
        self.target_buffer_bytes = self.buffer_ms * self.bytes_per_ms
        self.max_latency_bytes = self.max_latency_ms * self.bytes_per_ms

        self._buffer = bytearray()
        self._is_prebuffering = True
        self._lock = threading.Lock()

    def write(self, pcm_bytes: bytes) -> int:
        """Appends incoming PCM bytes to the ring buffer.

        Applies overrun clamping if occupancy exceeds max_latency_bytes.
        Returns the current buffer occupancy in bytes.
        """
        if not pcm_bytes:
            with self._lock:
                return len(self._buffer)

        with self._lock:
            self._buffer.extend(pcm_bytes)

            # Exit pre-buffering once target threshold is reached
            if len(self._buffer) >= self.target_buffer_bytes:
                self._is_prebuffering = False

            # Overrun / Latency Creep Clamping:
            # If buffer exceeds max latency (>150ms), drop oldest data to bring
            # occupancy back to target level (50ms).
            if len(self._buffer) > self.max_latency_bytes:
                excess = len(self._buffer) - self.target_buffer_bytes
                # Align to 2-byte sample boundary
                remainder = excess % self.bytes_per_sample
                if remainder != 0:
                    excess += (self.bytes_per_sample - remainder)
                del self._buffer[:excess]

            return len(self._buffer)

    def read(self, num_bytes: int) -> bytes:
        """Extracts FIFO PCM bytes from the ring buffer.

        If in pre-buffering state, returns clean zero-padded silence.
        If an underrun occurs (buffer has fewer than num_bytes), returns available
        bytes padded with zeros and re-enters pre-buffering state.
        """
        if num_bytes <= 0:
            return b""

        with self._lock:
            # Pre-buffering phase
            if self._is_prebuffering:
                if len(self._buffer) >= self.target_buffer_bytes:
                    self._is_prebuffering = False
                else:
                    return b"\x00" * num_bytes

            # Underrun handling
            if len(self._buffer) < num_bytes:
                available = bytes(self._buffer)
                self._buffer.clear()
                self._is_prebuffering = True
                padding = b"\x00" * (num_bytes - len(available))
                return available + padding

            # Normal FIFO extraction
            chunk = bytes(self._buffer[:num_bytes])
            del self._buffer[:num_bytes]
            return chunk

    def clear(self) -> None:
        """Clears all buffered audio and resets to pre-buffering state."""
        with self._lock:
            self._buffer.clear()
            self._is_prebuffering = True

    @property
    def level_bytes(self) -> int:
        """Current buffer occupancy in bytes."""
        with self._lock:
            return len(self._buffer)

    @property
    def level_ms(self) -> float:
        """Current buffer occupancy in milliseconds."""
        with self._lock:
            return len(self._buffer) / self.bytes_per_ms if self.bytes_per_ms else 0.0

    @property
    def is_prebuffering(self) -> bool:
        """Whether the buffer is currently building up its pre-buffer."""
        with self._lock:
            return self._is_prebuffering


# -----------------------------------------------------------------------------
# 2. DuckingGate
# -----------------------------------------------------------------------------

class DuckingGate:
    """Software audio ducking to prevent acoustic feedback loops.

    When container audio playback occurs, attenuates the client microphone stream
    by -18dB (linear factor ~0.12589254) with a configurable hold window (250ms).

    Zero-dependency implementation: uses Python standard library array.array('h')
    for fast in-place 16-bit integer scaling, fully compliant with Python 3.14 (PEP 594).
    """

    def __init__(
        self,
        ducking_db: float = -18.0,
        hold_ms: int = 250,
        rms_threshold: float = 100.0,
    ):
        self.ducking_db = ducking_db
        self.hold_ms = hold_ms
        self.rms_threshold = rms_threshold
        # Linear amplitude multiplier: 10^(dB / 20)
        self.linear_factor = 10.0 ** (ducking_db / 20.0)
        self._last_playback_time: float = 0.0
        self._force_ducking: bool = False
        self._lock = threading.Lock()

    def notify_container_playback(self, pcm_chunk: bytes) -> bool:
        """Checks container playback energy; if non-silent, extends the ducking hold timer."""
        if not pcm_chunk:
            return False
        rms = self.calculate_rms(pcm_chunk)
        if rms >= self.rms_threshold:
            with self._lock:
                self._last_playback_time = time.time()
            return True
        return False

    def is_ducking_active(self) -> bool:
        """Returns True if container playback has occurred within the hold window."""
        with self._lock:
            if self._force_ducking:
                return True
            elapsed = time.time() - self._last_playback_time
            return elapsed < (self.hold_ms / 1000.0)

    def set_force_ducking(self, force: bool) -> None:
        """Forces ducking on or off (useful for unit testing)."""
        with self._lock:
            self._force_ducking = force

    def set_playback_active(self, active: bool) -> None:
        """Sets playback active state directly."""
        with self._lock:
            self._last_playback_time = time.time() if active else 0.0

    @staticmethod
    def calculate_rms(pcm_bytes: bytes) -> float:
        """Calculates root-mean-square amplitude of 16-bit signed PCM bytes."""
        if not pcm_bytes or len(pcm_bytes) < 2:
            return 0.0
        usable_len = len(pcm_bytes) - (len(pcm_bytes) % 2)
        samples = array.array("h")
        samples.frombytes(pcm_bytes[:usable_len])
        if not samples:
            return 0.0
        sum_sq = sum(s * s for s in samples)
        return math.sqrt(sum_sq / len(samples))

    def apply_ducking(self, pcm_bytes: bytes, force: bool = False) -> bytes:
        """Attenuates PCM audio samples by ducking factor if ducking is active.

        If ducking is inactive and force is False, returns input untouched.
        Samples are clamped to signed 16-bit range [-32768, 32767].
        """
        if not pcm_bytes:
            return b""
        if not force and not self.is_ducking_active():
            return pcm_bytes

        usable_len = len(pcm_bytes) - (len(pcm_bytes) % 2)
        remainder = pcm_bytes[usable_len:]
        samples = array.array("h")
        samples.frombytes(pcm_bytes[:usable_len])
        factor = self.linear_factor

        for i in range(len(samples)):
            scaled = int(samples[i] * factor)
            if scaled > 32767:
                samples[i] = 32767
            elif scaled < -32768:
                samples[i] = -32768
            else:
                samples[i] = scaled

        return samples.tobytes() + remainder


# -----------------------------------------------------------------------------
# 3. SpeakerArbitrator
# -----------------------------------------------------------------------------

@dataclass
class ClientAudioSession:
    client_id: str
    client_name: str = ""
    is_local: bool = False
    muted: bool = False
    connected_at: float = field(default_factory=time.time)


class SpeakerArbitrator:
    """Arbitrates active microphone broadcast across cluster clients.

    Enforces exclusive active speaker streaming: only the currently active speaker
    has their microphone passed into the jitter buffer / container virtual mic.
    Supports 1-click atomic takeover and disconnect auto-transfer (preferring local
    host clients, then longest-connected remote clients).
    """

    def __init__(self):
        self._clients: Dict[str, ClientAudioSession] = {}
        self._active_speaker_id: Optional[str] = None
        self._lock = threading.Lock()

    @property
    def active_speaker_id(self) -> Optional[str]:
        with self._lock:
            return self._active_speaker_id

    @property
    def connected_clients(self) -> Dict[str, ClientAudioSession]:
        with self._lock:
            return dict(self._clients)

    def register_client(
        self,
        client_id: str,
        client_name: str = "",
        is_local: bool = False,
    ) -> bool:
        """Registers a newly connected audio client session.

        If no active speaker is assigned yet, assigns this client as the active speaker.
        """
        with self._lock:
            if client_id in self._clients:
                client = self._clients[client_id]
                if client_name:
                    client.client_name = client_name
                client.is_local = is_local
            else:
                self._clients[client_id] = ClientAudioSession(
                    client_id=client_id,
                    client_name=client_name or client_id,
                    is_local=is_local,
                )
                if self._active_speaker_id is None:
                    self._active_speaker_id = client_id
            return True

    def claim_speaker(
        self,
        client_id: str,
        client_name: Optional[str] = None,
    ) -> bool:
        """Atomically switches the active speaker to client_id (1-click takeover)."""
        with self._lock:
            if client_id not in self._clients:
                self._clients[client_id] = ClientAudioSession(
                    client_id=client_id,
                    client_name=client_name or client_id,
                )
            elif client_name:
                self._clients[client_id].client_name = client_name
            self._active_speaker_id = client_id
            return True

    def unregister_client(self, client_id: str) -> Optional[str]:
        """Unregisters a client session.

        If the unregistering client was the active speaker, automatically transfers
        the active speaker role to the next best connected client (preferring local
        host clients, then longest-connected clients).
        """
        with self._lock:
            if client_id in self._clients:
                del self._clients[client_id]

            if self._active_speaker_id == client_id:
                if self._clients:
                    locals_ = [c for c in self._clients.values() if c.is_local]
                    if locals_:
                        self._active_speaker_id = locals_[0].client_id
                    else:
                        oldest = min(self._clients.values(), key=lambda c: c.connected_at)
                        self._active_speaker_id = oldest.client_id
                else:
                    self._active_speaker_id = None

            return self._active_speaker_id

    def set_mute(self, client_id: str, muted: bool) -> None:
        """Sets the mute state for a given client."""
        with self._lock:
            if client_id in self._clients:
                self._clients[client_id].muted = muted

    def is_muted(self, client_id: str) -> bool:
        """Checks if a given client is muted."""
        with self._lock:
            client = self._clients.get(client_id)
            return client.muted if client else False

    def can_stream(self, client_id: str) -> bool:
        """Checks if client is the active speaker and unmuted."""
        with self._lock:
            if self._active_speaker_id != client_id:
                return False
            client = self._clients.get(client_id)
            return bool(client and not client.muted)

    def get_speaker_status(self, for_client_id: Optional[str] = None) -> Dict[str, Any]:
        """Builds a standardized speaker_status JSON signaling payload."""
        with self._lock:
            active_id = self._active_speaker_id or ""
            active_name = ""
            if self._active_speaker_id and self._active_speaker_id in self._clients:
                active_name = self._clients[self._active_speaker_id].client_name
            is_you = bool(for_client_id and for_client_id == self._active_speaker_id)
            return {
                "type": "speaker_status",
                "active_client_id": active_id,
                "active_client_name": active_name,
                "is_you": is_you,
            }


# -----------------------------------------------------------------------------
# 4. AudioRouter (Master Hub)
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# 4. ContainerAudioBridgeClient
# -----------------------------------------------------------------------------

class ContainerAudioBridgeClient:
    """Asynchronous TCP client bridging AudioRouter with container audio daemon.

    Connects to 127.0.0.1:AUDIO_BRIDGE_PORT (default 8002).
    - Sends outgoing microphone chunks to the container virtual microphone FIFO.
    - Receives incoming container playback PCM chunks and routes to AudioRouter.
    - Graceful offline fallback: logs debug on connection failure and drops audio
      cleanly with zero crashes or unhandled exceptions when Docker is stopped.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8002,
        on_playback: Optional[Callable[[bytes], Any]] = None,
        reconnect_cooldown: float = 2.0,
    ):
        self.host = host
        self.port = port
        self.on_playback = on_playback
        self.reconnect_cooldown = reconnect_cooldown

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._last_connect_attempt: float = 0.0
        self._playback_task: Optional[asyncio.Task] = None
        self._running: bool = False
        self._connect_lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        """Returns True if TCP socket connection is actively established."""
        if self._writer is None or self._writer.is_closing():
            return False
        if self._reader is not None and self._reader.at_eof():
            return False
        try:
            sock = self._writer.get_extra_info("socket")
            if sock is not None:
                real_sock = getattr(sock, "_sock", sock)
                r, _, _ = select.select([real_sock], [], [], 0)
                if r:
                    peek = real_sock.recv(1, socket.MSG_PEEK)
                    if not peek:
                        return False
        except Exception:
            return False
        return True

    async def connect(self, force: bool = False) -> bool:
        """Attempts to establish connection to the container audio bridge."""
        if not force and self.is_connected:
            return True

        now = time.time()
        if not force and (now - self._last_connect_attempt < self.reconnect_cooldown):
            return False

        async with self._connect_lock:
            if not force and self.is_connected:
                return True
            self._last_connect_attempt = time.time()

            # Close any previous partial connection
            self._disconnect()

            try:
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port),
                    timeout=1.5,
                )
                logger.info("Connected to container audio bridge at %s:%d", self.host, self.port)
                return True
            except (ConnectionRefusedError, OSError, asyncio.TimeoutError) as e:
                logger.debug("Container audio bridge unreachable at %s:%d (offline): %s", self.host, self.port, e)
                self._disconnect()
                return False
            except Exception as e:
                logger.debug("Unexpected error connecting to container audio bridge: %s", e)
                self._disconnect()
                return False

    def _disconnect(self) -> None:
        """Cleanly releases TCP socket descriptors."""
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:
                pass
        self._writer = None
        self._reader = None

    async def send_mic_audio(self, pcm_chunk: bytes) -> bool:
        """Streams microphone PCM chunk to the container bridge over TCP.

        Returns True on successful transmission, False if offline or dropped.
        """
        if not pcm_chunk:
            return True

        if not self.is_connected:
            self._disconnect()
            connected = await self.connect(force=False)
            if not connected:
                return False

        if self._writer is None:
            return False

        try:
            self._writer.write(pcm_chunk)
            await self._writer.drain()
            return True
        except (ConnectionResetError, BrokenPipeError, OSError) as e:
            logger.debug("Container audio bridge write failed (socket disconnected): %s", e)
            self._disconnect()
            # Attempt reconnect on broken pipe if cooldown permits
            reconnected = await self.connect(force=True)
            if reconnected and self._writer is not None:
                try:
                    self._writer.write(pcm_chunk)
                    await self._writer.drain()
                    return True
                except Exception:
                    self._disconnect()
            return False
        except Exception as e:
            logger.debug("Error streaming mic audio to container bridge: %s", e)
            self._disconnect()
            return False

    async def _playback_loop(self) -> None:
        """Background loop reading container playback PCM and routing to callback."""
        chunk_size = 1024
        while self._running:
            current_reader: Optional[asyncio.StreamReader] = None
            try:
                if not self.is_connected:
                    connected = await self.connect()
                    if not connected:
                        await asyncio.sleep(self.reconnect_cooldown)
                        continue

                current_reader = self._reader
                if current_reader is None:
                    await asyncio.sleep(0.5)
                    continue

                data = await current_reader.read(chunk_size)
                if not data:
                    logger.debug("Container audio bridge closed playback stream (EOF).")
                    if self._reader is current_reader:
                        self._disconnect()
                    await asyncio.sleep(self.reconnect_cooldown)
                    continue

                if self.on_playback is not None:
                    try:
                        res = self.on_playback(data)
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception as cb_err:
                        logger.warning("Error in container playback callback: %s", cb_err)

            except asyncio.CancelledError:
                break
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                logger.debug("Container audio bridge playback read disconnected: %s", e)
                if current_reader is not None and self._reader is current_reader:
                    self._disconnect()
                await asyncio.sleep(self.reconnect_cooldown)
            except Exception as e:
                logger.debug("Unexpected error in container audio playback loop: %s", e)
                if current_reader is not None and self._reader is current_reader:
                    self._disconnect()
                await asyncio.sleep(self.reconnect_cooldown)

    async def ensure_started(self) -> None:
        """Starts the background playback receiver loop if not already running."""
        if self._running and self._playback_task is not None and not self._playback_task.done():
            return
        self._running = True
        try:
            loop = asyncio.get_running_loop()
            self._playback_task = loop.create_task(self._playback_loop())
        except RuntimeError:
            pass

    async def stop(self) -> None:
        """Stops the playback receiver loop and closes the TCP connection."""
        self._running = False
        if self._playback_task is not None:
            self._playback_task.cancel()
            try:
                await self._playback_task
            except (asyncio.CancelledError, Exception):
                pass
            self._playback_task = None
        writer = self._writer
        self._disconnect()
        if writer is not None:
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=0.5)
            except Exception:
                pass


# -----------------------------------------------------------------------------
# 5. AudioRouter (Master Hub)
# -----------------------------------------------------------------------------

class AudioRouter:
    """Master network router and audio hub for GConnect.

    Coordinates connected client WebSockets (/ws/audio), audio ring buffering,
    feedback ducking, speaker arbitration, and container audio bridges.
    """

    def __init__(self):
        self.ring_buffer = AudioRingBuffer(
            sample_rate=settings.AUDIO_SAMPLE_RATE,
            buffer_ms=settings.AUDIO_BUFFER_MS,
            max_latency_ms=150,
        )
        self.ducking_gate = DuckingGate(
            ducking_db=settings.AUDIO_DUCKING_DB,
            hold_ms=250,
        )
        self.arbitrator = SpeakerArbitrator()
        self._websockets: Dict[str, WebSocket] = {}
        self._container_mic_sink: Optional[Callable[[bytes], Any]] = None
        self._container_playback_source: Optional[Callable[[], Any]] = None
        self.container_bridge: ContainerAudioBridgeClient = ContainerAudioBridgeClient(
            host="127.0.0.1",
            port=getattr(settings, "AUDIO_BRIDGE_PORT", 8002),
            on_playback=self.on_container_playback,
        )

    def set_container_mic_sink(self, sink: Optional[Callable[[bytes], Any]]) -> None:
        """Registers a sink callback to receive microphone PCM chunks intended for the container."""
        self._container_mic_sink = sink

    def set_container_playback_source(self, source: Optional[Callable[[], Any]]) -> None:
        """Registers a source callback providing container playback PCM chunks."""
        self._container_playback_source = source

    async def start_container_bridge(self) -> None:
        """Explicitly starts the container audio bridge client."""
        if self.container_bridge is not None:
            await self.container_bridge.ensure_started()

    async def stop_container_bridge(self) -> None:
        """Explicitly stops the container audio bridge client."""
        if self.container_bridge is not None:
            await self.container_bridge.stop()

    async def write_to_container_mic(self, pcm_chunk: bytes) -> None:
        """Sends processed, ducked microphone PCM chunk to registered sinks and the container bridge."""
        if self._container_mic_sink is not None:
            try:
                res = self._container_mic_sink(pcm_chunk)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.warning("Error writing to container mic sink: %s", e)

        if self.container_bridge is not None:
            try:
                await self.container_bridge.ensure_started()
                await self.container_bridge.send_mic_audio(pcm_chunk)
            except Exception as e:
                logger.debug("Error writing to container audio bridge: %s", e)

    async def on_container_playback(self, pcm_chunk: bytes) -> None:
        """Interface method called when the container outputs audio playback."""
        await self.broadcast_container_playback(pcm_chunk)

    async def broadcast_container_playback(self, pcm_chunk: bytes) -> None:
        """Broadcasts container audio playback to all connected clients and triggers ducking."""
        if not pcm_chunk:
            return
        self.ducking_gate.notify_container_playback(pcm_chunk)
        disconnected = []
        for cid, ws in list(self._websockets.items()):
            try:
                await ws.send_bytes(pcm_chunk)
            except Exception:
                disconnected.append(cid)
        for cid in disconnected:
            self._disconnect_client(cid)

    async def broadcast_speaker_status(self) -> None:
        """Broadcasts personalized speaker_status signaling payloads to all connected clients."""
        disconnected = []
        for cid, ws in list(self._websockets.items()):
            try:
                status = self.arbitrator.get_speaker_status(for_client_id=cid)
                await ws.send_json(status)
            except Exception:
                disconnected.append(cid)
        for cid in disconnected:
            self._disconnect_client(cid)

    async def broadcast_ducking_status(self, ducking: bool) -> None:
        """Broadcasts ducking_status signaling payload to all connected clients."""
        payload = {
            "type": "ducking_status",
            "ducking": ducking,
            "attenuation_db": settings.AUDIO_DUCKING_DB,
        }
        disconnected = []
        for cid, ws in list(self._websockets.items()):
            try:
                await ws.send_json(payload)
            except Exception:
                disconnected.append(cid)
        for cid in disconnected:
            self._disconnect_client(cid)

    def read_from_ring_buffer(self, num_bytes: int = 640) -> bytes:
        """Extracts smoothed microphone PCM from the ring buffer for container consumption."""
        return self.ring_buffer.read(num_bytes)

    def _disconnect_client(self, client_id: str) -> None:
        """Internal helper to clean up a disconnected client."""
        self._websockets.pop(client_id, None)
        self.arbitrator.unregister_client(client_id)

    async def handle_websocket(self, websocket: WebSocket) -> None:
        """Primary handler for /ws/audio WebSocket connections.

        Manages authentication, client session registration, binary streaming,
        and JSON signaling messages.
        """
        if websocket.client_state == WebSocketState.CONNECTING:
            await websocket.accept()

        client_host = websocket.client.host if websocket.client else ""
        is_local = client_host in ("127.0.0.1", "::1", "localhost", "testclient")
        client_id = websocket.query_params.get("client_id") or f"client-{secrets.token_hex(4)}"
        client_name = websocket.query_params.get("client_name")
        if not client_name:
            ua = websocket.headers.get("user-agent", "")
            try:
                from backend.connection_tracker import connection_tracker
                client_name = connection_tracker._parse_device_name(ua, is_local)
            except Exception:
                client_name = "This PC (Host)" if is_local else "Remote Client"

        self.arbitrator.register_client(client_id, client_name, is_local=is_local)
        self._websockets[client_id] = websocket

        if self.container_bridge is not None:
            await self.container_bridge.ensure_started()

        # Broadcast initial speaker status
        await self.broadcast_speaker_status()

        try:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break

                # Binary frame: Client microphone PCM chunk
                if "bytes" in msg and msg["bytes"]:
                    pcm = msg["bytes"]
                    if self.arbitrator.can_stream(client_id):
                        ducked = self.ducking_gate.apply_ducking(pcm)
                        self.ring_buffer.write(ducked)
                        await self.write_to_container_mic(ducked)

                # Text frame: JSON signaling
                elif "text" in msg and msg["text"]:
                    try:
                        data = json.loads(msg["text"])
                    except json.JSONDecodeError:
                        continue

                    msg_type = data.get("type")
                    if msg_type == "claim_speaker":
                        req_name = data.get("client_name") or client_name
                        self.arbitrator.claim_speaker(client_id, req_name)
                        await self.broadcast_speaker_status()
                    elif msg_type == "mute":
                        muted = bool(data.get("muted", False))
                        self.arbitrator.set_mute(client_id, muted)
                        await self.broadcast_speaker_status()
                    elif msg_type == "ping":
                        ts = data.get("timestamp", time.time())
                        try:
                            await websocket.send_json({"type": "pong", "timestamp": ts})
                        except Exception:
                            break
        finally:
            self._disconnect_client(client_id)
            await self.broadcast_speaker_status()


# Global AudioRouter singleton instance
audio_router = AudioRouter()
