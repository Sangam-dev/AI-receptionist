import logging
import re

from receptionist.core.bus import EventBus
from receptionist.core.events import PartialResponse, ResponseReady, SentenceReady

logger = logging.getLogger(__name__)

CHUNK_TARGET_CHARS = 240

_TERMINATORS = set(".!?")
_ABBREVIATIONS = {
    "a.m",
    "am",
    "approx",
    "appt",
    "dept",
    "dr",
    "e.g",
    "etc",
    "i.e",
    "jr",
    "mr",
    "mrs",
    "ms",
    "no",
    "p.m",
    "pm",
    "prof",
    "sr",
    "st",
    "vs",
}
_TOKEN_RE = re.compile(r"[A-Za-z](?:[A-Za-z.]*)$")


class SentenceChunker:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._buffers: dict[str, str] = {}
        self._batches: dict[str, str] = {}
        self._started: set[str] = set()
        self._next_index: dict[str, int] = {}

    def register(self) -> None:
        self._bus.subscribe(PartialResponse, self.on_partial)
        self._bus.subscribe(ResponseReady, self.on_response_ready)

    async def on_partial(self, event: PartialResponse) -> None:
        if event.done:
            return
        buffer = self._buffers.get(event.turn_id, "") + event.text
        batch = self._batches.get(event.turn_id, "")
        while True:
            cut = self._find_boundary(buffer)
            if cut == -1:
                break
            sentence, buffer = buffer[: cut + 1], buffer[cut + 1 :]
            clean = sentence.strip()
            if not clean:
                continue
            if event.turn_id not in self._started:
                self._started.add(event.turn_id)
                self._emit(event, clean)
            else:
                batch = f"{batch} {clean}".strip()
                if len(batch) >= CHUNK_TARGET_CHARS:
                    self._emit(event, batch)
                    batch = ""
        self._buffers[event.turn_id] = buffer
        self._batches[event.turn_id] = batch

    async def on_response_ready(self, event: ResponseReady) -> None:
        turn_id = event.turn_id
        tail = f"{self._batches.pop(turn_id, '')} {self._buffers.pop(turn_id, '')}".strip()
        self._started.discard(turn_id)
        self._next_index.pop(turn_id, None)
        self._emit(event, tail, done=True)

    @staticmethod
    def _find_boundary(buffer: str) -> int:
        for index, character in enumerate(buffer):
            if character not in _TERMINATORS:
                continue
            if character == "." and SentenceChunker._is_abbreviation(buffer, index):
                continue
            return index
        return -1

    @staticmethod
    def _is_abbreviation(text: str, period_index: int) -> bool:
        token_match = _TOKEN_RE.search(text[:period_index])
        if token_match is None:
            return False
        token = text[token_match.start() :].split(maxsplit=1)[0]
        token = token.rstrip("!?;:,)").lower()
        abbreviation = token.rstrip(".")
        if abbreviation in {"am", "pm"}:
            following = text[period_index + 1 :].lstrip()
            return not following or not following[0].isupper()
        return abbreviation in _ABBREVIATIONS

    def _emit(
        self,
        cause: PartialResponse | ResponseReady,
        text: str,
        done: bool = False,
    ) -> None:
        turn_id = cause.turn_id
        index = self._next_index.get(turn_id, 0)
        self._next_index[turn_id] = index + 1
        self._bus.emit(
            SentenceReady(
                text=text,
                index=index,
                done=done,
                session_id=cause.session_id,
                turn_id=turn_id,
                causation_id=cause.event_id,
            )
        )
        logger.debug("SentenceReady [%s] #%d %d chars", turn_id, index, len(text))
