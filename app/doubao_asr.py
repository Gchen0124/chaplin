"""Doubao (Volcengine) streaming ASR over WebSocket.

The browser streams 16 kHz mono PCM frames; this module drives the Volcengine
"bigmodel_async" SAUC endpoint and reports recognised text as it arrives.

Wire format (binary, from the original DoubaoAsr.kt):

    header (4 bytes) : [0x11, (type<<4)|flags, (JSON<<4)|GZIP, 0]
    full client req  : header(1,0) + int32be(len) + gzip(json)
    audio packet     : header(2,1) + int32be(seq) + int32be(len) + gzip(pcm)
    last audio packet: header(2,3) + int32be(-seq) + int32be(len) + gzip(pcm)
    server error     : header(15,..) + int32be(len) + gzip(message)

Server frames are parsed with the same header rules.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import struct
import uuid

from fastapi import WebSocket, WebSocketDisconnect

WS_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
RESOURCE_ID = "volc.seedasr.sauc.duration"

_TYPE_FULL = 0b0001
_TYPE_AUDIO = 0b0010
_TYPE_ERROR = 0b1111
_JSON = 0b0001
_GZIP = 0b0001


class DoubaoError(RuntimeError):
    pass


def _header(kind: int, flags: int = 0) -> bytes:
    return bytes([0x11, ((kind << 4) | flags) & 0xFF, ((_JSON << 4) | _GZIP), 0x00])


def _full_client(reqid: str) -> bytes:
    payload = {
        "user": {"uid": "chaplin"},
        "audio": {"format": "pcm", "codec": "raw", "rate": 16000, "bits": 16, "channel": 1},
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True,
            "enable_punc": True,
            "enable_ddc": True,
            "show_utterances": True,
            "ssd_version": "200",
            "result_type": "single",
            "reqid": reqid,
        },
    }
    body = gzip.compress(json.dumps(payload).encode())
    return _header(_TYPE_FULL, 0) + struct.pack(">i", len(body)) + body


def _audio_packet(pcm: bytes, seq: int, last: bool) -> bytes:
    body = gzip.compress(pcm)
    flags = 0b0011 if last else 0b0001
    number = -seq if last else seq
    return _header(_TYPE_AUDIO, flags) + struct.pack(">i", number) + struct.pack(">i", len(body)) + body


def _parse(frame: bytes) -> dict | None:
    if len(frame) < 8:
        return None
    kind = (frame[1] >> 4) & 0x0F
    flags = frame[1] & 0x0F
    compression = frame[2] & 0x0F
    off = (frame[0] & 0x0F) * 4
    if flags & 0x01:
        off += 4
    if kind == _TYPE_ERROR:
        size = struct.unpack(">i", frame[off:off + 4])[0]
        raw = frame[off + 4:off + 4 + size]
        if compression == _GZIP and raw:
            raw = gzip.decompress(raw)
        return {"error": True, "msg": raw.decode(errors="replace")}
    if len(frame) < off + 4:
        return None
    size = struct.unpack(">i", frame[off:off + 4])[0]
    raw = frame[off + 4:off + 4 + size]
    if compression == _GZIP and raw:
        raw = gzip.decompress(raw)
    try:
        return json.loads(raw.decode())
    except Exception:  # noqa: BLE001
        return None


class DoubaoSession:
    """One Volcengine SAUC stream: connect, feed PCM, receive text."""

    def __init__(self, api_key: str, on_text, on_error=None):
        self.api_key = api_key
        self.on_text = on_text
        self.on_error = on_error
        self._ws = None
        self._recv_task: asyncio.Task | None = None
        self._seq = 2
        self._closed = False

    async def connect(self) -> None:
        try:
            import websockets
        except ImportError as exc:  # pragma: no cover
            raise DoubaoError("websockets package is not installed") from exc

        headers = {
            "X-Api-Key": self.api_key,
            "X-Api-Resource-Id": RESOURCE_ID,
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }
        self._ws = await websockets.connect(WS_URL, additional_headers=headers,
                                            max_size=None, ping_interval=15)
        await self._ws.send(_full_client(str(uuid.uuid4())))
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def _recv_loop(self) -> None:
        try:
            async for frame in self._ws:
                if isinstance(frame, str):
                    continue
                parsed = _parse(frame)
                if parsed is None:
                    continue
                if parsed.get("error"):
                    if self.on_error:
                        self.on_error(str(parsed.get("msg") or "doubao error"))
                    continue
                result = parsed.get("result") or parsed
                text = (result or {}).get("text") or ""
                definite = False
                utterances = (result or {}).get("utterances") or []
                if utterances:
                    last = utterances[-1] or {}
                    definite = bool(last.get("definite"))
                    if not text:
                        text = last.get("text") or ""
                if text.strip():
                    res = self.on_text(text, definite)
                    if asyncio.iscoroutine(res):
                        await res
        except Exception as exc:  # noqa: BLE001
            if not self._closed and self.on_error:
                self.on_error(str(exc))

    async def send_pcm(self, chunk: bytes) -> None:
        if self._ws is None or self._closed:
            return
        await self._ws.send(_audio_packet(chunk, self._seq, last=False))
        self._seq += 1

    async def close(self) -> None:
        if self._ws is None:
            return
        self._closed = True
        try:
            await self._ws.send(_audio_packet(b"", self._seq, last=True))
        except Exception:  # noqa: BLE001
            pass
        try:
            await self._ws.close()
        except Exception:  # noqa: BLE001
            pass
        if self._recv_task:
            self._recv_task.cancel()
        self._ws = None


def attach(app, keys) -> None:
    """Register /ws/asr: browser PCM in, Doubao transcript out."""

    async def _send(ws, payload: dict):
        try:
            await ws.send_text(json.dumps(payload, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            pass

    @app.websocket("/ws/asr")
    async def asr(websocket: WebSocket):
        print('[asr] ws hit', flush=True)
        await websocket.accept()
        session: DoubaoSession | None = None

        async def on_text(text: str, definite: bool) -> None:
            await _send(websocket, {"type": "asr", "text": text, "definite": definite})

        async def on_error(message: str) -> None:
            await _send(websocket, {"type": "error", "detail": message})

        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    if session is not None:
                        await session.send_pcm(message["bytes"])
                    continue
                text = message.get("text")
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except ValueError:
                    continue
                kind = payload.get("type")
                if kind == "start":
                    api_key = keys.get("doubao")
                    if not api_key:
                        await _send(websocket, {"type": "error",
                                                "detail": "No Doubao key (doubao.api.key / DOUBAO_API_KEY)."})
                        continue
                    session = DoubaoSession(api_key, on_text=on_text, on_error=on_error)
                    try:
                        await session.connect()
                        await _send(websocket, {"type": "status", "state": "live"})
                    except Exception as exc:  # noqa: BLE001
                        await _send(websocket, {"type": "error", "detail": str(exc)})
                elif kind == "stop":
                    if session is not None:
                        await session.close()
                        session = None
                    await _send(websocket, {"type": "closed"})
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # noqa: BLE001
            await _send(websocket, {"type": "error", "detail": str(exc)})
        finally:
            if session is not None:
                await session.close()
