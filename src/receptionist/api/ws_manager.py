import asyncio
import logging

logger = logging.getLogger(__name__)


class Connection:
    def __init__(self, websocket) -> None:
        self.websocket = websocket
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=64)


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, Connection] = {}

    def connect(self, session_id: str, websocket) -> Connection:
        connection = Connection(websocket)
        self._connections[session_id] = connection
        return connection

    def disconnect(self, session_id: str) -> None:
        self._connections.pop(session_id, None)

    def is_connected(self, session_id: str) -> bool:
        return session_id in self._connections

    def count(self) -> int:
        return len(self._connections)

    def send(self, session_id: str, message: dict) -> None:
        connection = self._connections.get(session_id)
        if connection is None:
            return
        try:
            connection.queue.put_nowait(message)
        except asyncio.QueueFull:
            if message.get("type") != "audio":
                logger.warning("outbound queue full for %s", session_id)

    async def writer(self, session_id: str) -> None:
        connection = self._connections.get(session_id)
        if connection is None:
            return
        while True:
            message = await connection.queue.get()
            try:
                await connection.websocket.send_json(message)
            except Exception:
                logger.info("writer for %s disconnected", session_id)
                self.disconnect(session_id)
                return
