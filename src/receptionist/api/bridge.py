import base64
import logging

from receptionist.api.ws_manager import ConnectionManager
from receptionist.core.bus import EventBus
from receptionist.core.events import (
    AudioReady,
    HandlerFailed,
    PartialResponse,
    ResponseReady,
    SpeechFinished,
    TurnCancelled,
)

logger = logging.getLogger(__name__)


class Bridge:
    def __init__(self, bus: EventBus, manager: ConnectionManager) -> None:
        self._bus = bus
        self._manager = manager
        self._cancelled_turns: dict[str, set[str]] = {}

    def register(self) -> None:
        self._bus.subscribe(PartialResponse, self.on_partial)
        self._bus.subscribe(ResponseReady, self.on_response)
        self._bus.subscribe(AudioReady, self.on_audio)
        self._bus.subscribe(SpeechFinished, self.on_speech_finished)
        self._bus.subscribe(TurnCancelled, self.on_turn_cancelled)
        self._bus.subscribe(HandlerFailed, self.on_handler_failed)

    def _is_stale(self, event) -> bool:
        cancelled = self._cancelled_turns.get(event.session_id, set())
        if event.turn_id in cancelled:
            logger.debug(
                "dropping stale %s for turn %s", type(event).__name__, event.turn_id[:8]
            )
            return True
        return False

    async def on_partial(self, event: PartialResponse) -> None:
        if self._is_stale(event):
            return
        self._manager.send(
            event.session_id,
            {
                "type": "response_partial",
                "turn_id": event.turn_id,
                "text": event.text,
                "index": event.index,
                "done": event.done,
            },
        )

    async def on_response(self, event: ResponseReady) -> None:
        if self._is_stale(event):
            return
        self._manager.send(
            event.session_id,
            {
                "type": "response",
                "turn_id": event.turn_id,
                "text": event.text,
            },
        )

    async def on_audio(self, event: AudioReady) -> None:
        if self._is_stale(event):
            return
        self._manager.send(
            event.session_id,
            {
                "type": "audio",
                "turn_id": event.turn_id,
                "seq": event.sequence,
                "sample_rate": event.sample_rate,
                "pcm": base64.b64encode(event.pcm).decode(),
            },
        )

    async def on_speech_finished(self, event: SpeechFinished) -> None:
        if self._is_stale(event):
            self._cancelled_turns.pop(event.session_id, None)
            return
        self._cancelled_turns.pop(event.session_id, None)
        self._manager.send(
            event.session_id,
            {"type": "turn_complete", "turn_id": event.turn_id},
        )

    async def on_turn_cancelled(self, event: TurnCancelled) -> None:
        if event.turn_id:
            self._cancelled_turns.setdefault(event.session_id, set()).add(event.turn_id)
        self._manager.send(
            event.session_id,
            {"type": "turn_cancelled", "turn_id": event.turn_id},
        )

    async def on_handler_failed(self, event: HandlerFailed) -> None:
        logger.error(
            "handler %s failed on %s (turn %s): %s",
            event.handler,
            event.failed_event_type,
            event.turn_id,
            event.error,
        )
        self._manager.send(
            event.session_id,
            {
                "type": "error",
                "turn_id": event.turn_id,
                "message": "internal error",
            },
        )
