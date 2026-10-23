import asyncio
import json
import logging
import os
import subprocess
import sys
from typing import Any, Optional

from fastapi import WebSocket, WebSocketDisconnect

from backend.config import settings
from backend.docker_manager import docker_manager

logger = logging.getLogger("connect.pty")


class PTYBridge:
    """Bridges WebSocket communication with a Docker container PTY or fallback local shell."""

    async def handle_websocket(self, websocket: WebSocket):
        await websocket.accept()

        exec_res = docker_manager.create_exec_pty()
        if exec_res:
            exec_id, raw_socket = exec_res
            logger.info(f"Connected PTY to Docker container exec {exec_id}.")
            await self._bridge_docker_socket(websocket, exec_id, raw_socket)
        else:
            logger.info("Docker container unavailable. Using local fallback shell.")
            await self._bridge_local_fallback(websocket)

    async def _bridge_docker_socket(self, websocket: WebSocket, exec_id: str, sock: Any):
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        raw_sock = getattr(sock, "_sock", sock)

        def read_from_socket():
            try:
                if hasattr(sock, "read"):
                    return sock.read(4096)
                elif hasattr(raw_sock, "recv"):
                    return raw_sock.recv(4096)
                return b""
            except Exception:
                return b""

        async def socket_to_ws():
            try:
                while not stop_event.is_set():
                    data = await loop.run_in_executor(None, read_from_socket)
                    if not data:
                        break
                    try:
                        text = data.decode("utf-8", errors="replace")
                        await websocket.send_text(text)
                    except Exception:
                        await websocket.send_bytes(data)
            except Exception as e:
                logger.debug(f"Docker to WebSocket loop ended: {e}")
            finally:
                stop_event.set()

        async def ws_to_socket():
            try:
                while not stop_event.is_set():
                    msg = await websocket.receive()
                    if "text" in msg and msg["text"]:
                        text_data = msg["text"]
                        if text_data.startswith("{") and "resize" in text_data:
                            try:
                                payload = json.loads(text_data)
                                if payload.get("type") == "resize":
                                    cols = int(payload.get("cols", 80))
                                    rows = int(payload.get("rows", 24))
                                    docker_manager.resize_exec(exec_id, cols, rows)
                                    continue
                            except json.JSONDecodeError:
                                pass

                        raw_bytes = text_data.encode("utf-8")
                        await loop.run_in_executor(None, raw_sock.sendall, raw_bytes)

                    elif "bytes" in msg and msg["bytes"]:
                        await loop.run_in_executor(None, raw_sock.sendall, msg["bytes"])

            except (WebSocketDisconnect, asyncio.CancelledError):
                pass
            except Exception as e:
                logger.debug(f"WebSocket to Docker loop ended: {e}")
            finally:
                stop_event.set()

        reader_task = asyncio.create_task(socket_to_ws())
        writer_task = asyncio.create_task(ws_to_socket())

        done, pending = await asyncio.wait(
            [reader_task, writer_task],
            return_when=asyncio.FIRST_COMPLETED
        )

        for task in pending:
            task.cancel()

        try:
            if hasattr(raw_sock, "close"):
                raw_sock.close()
        except Exception:
            pass

    async def _bridge_local_fallback(self, websocket: WebSocket):
        """Fallback for environments without active Docker daemon."""
        shell_cmd = "cmd.exe" if sys.platform == "win32" else "/bin/bash"
        workspace_dir = str(settings.WORKSPACE_DIR.resolve())

        intro = (
            f"\r\n\x1b[1;33m[Connect Notice]\x1b[0m Docker sandbox is not running.\r\n"
            f"\x1b[90mRunning fallback local shell in {workspace_dir}\x1b[0m\r\n\r\n"
        )
        await websocket.send_text(intro)

        try:
            proc = await asyncio.create_subprocess_shell(
                shell_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace_dir
            )
        except Exception as e:
            await websocket.send_text(f"\r\n\x1b[1;31mFailed to start shell: {e}\x1b[0m\r\n")
            return

        stop_event = asyncio.Event()

        async def read_stream(stream):
            try:
                while not stop_event.is_set():
                    data = await stream.read(1024)
                    if not data:
                        break
                    await websocket.send_text(data.decode("utf-8", errors="replace"))
            except Exception:
                pass
            finally:
                stop_event.set()

        async def read_ws():
            try:
                while not stop_event.is_set():
                    msg = await websocket.receive()
                    if "text" in msg and msg["text"]:
                        text = msg["text"]
                        if text.startswith("{") and "resize" in text:
                            continue
                        if proc.stdin and not proc.stdin.is_closing():
                            proc.stdin.write(text.encode("utf-8"))
                            await proc.stdin.drain()
            except Exception:
                pass
            finally:
                stop_event.set()

        out_task = asyncio.create_task(read_stream(proc.stdout))
        err_task = asyncio.create_task(read_stream(proc.stderr))
        in_task = asyncio.create_task(read_ws())

        await asyncio.wait(
            [out_task, err_task, in_task],
            return_when=asyncio.FIRST_COMPLETED
        )

        for t in [out_task, err_task, in_task]:
            t.cancel()

        try:
            proc.terminate()
        except Exception:
            pass


pty_bridge = PTYBridge()

