import asyncio
import logging

from receptionist.core.bus import EventBus
from receptionist.core.events import (
    AudioReady,
    SentenceReady,
    SpeechFinished,
    TurnCancelled,
)
from receptionist.config import DEFAULT_TTS_VOICE

logger = logging.getLogger(__name__)


class FakeEngine:
    def create(self, text: str, voice: str) -> tuple[bytes, int]:
        return f"<audio:{text}>".encode(), 24000


_TEXT_FIXES = {
    "\u2019": "'",
    "\u2018": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2014": ", ",
    "\u2013": ", ",
    "\u2026": "...",
    "\xa0": " ",
}


def clean_text(text: str) -> str:
    for source, replacement in _TEXT_FIXES.items():
        text = text.replace(source, replacement)
    return text.strip()


class KokoroEngine:
    SILENCE_S = 0.07

    def __init__(self, model_path: str, voices_path: str, speed: float = 1.0) -> None:
        import onnxruntime as ort

        if "CUDAExecutionProvider" in ort.get_available_providers() and hasattr(
            ort, "preload_dlls"
        ):
            ort.preload_dlls()

        from kokoro_onnx import Kokoro

        logger.info("loading kokoro model from %s", model_path)
        self._kokoro = Kokoro(model_path=model_path, voices_path=voices_path)
        self._speed = speed

    def create(self, text: str, voice: str) -> tuple[bytes, int]:
        import numpy as np

        samples, sample_rate = self._kokoro.create(
            clean_text(text), voice=voice, speed=self._speed, lang="en-us"
        )
        pcm = np.clip(samples, -1.0, 1.0)
        pcm = (pcm * 32767).astype(np.int16)
        pause = np.zeros(
            int(sample_rate * self.SILENCE_S), dtype=np.int16
        )
        pcm = np.concatenate([pcm, pause])
        return pcm.tobytes(), int(sample_rate)


class TTSHandler:
    def __init__(self, bus: EventBus, engine, voice: str = DEFAULT_TTS_VOICE) -> None:
        self._bus = bus
        self._engine = engine
        self._voice = voice
        self._semaphore = asyncio.Semaphore(1)
        self._pending: dict[str, int] = {}
        self._done: set[str] = set()
        self._cancelled: set[str] = set()
        self._sequence: dict[str, int] = {}

    def register(self) -> None:
        self._bus.subscribe(SentenceReady, self.on_sentence_ready)
        self._bus.subscribe(TurnCancelled, self.on_turn_cancelled)

    async def on_sentence_ready(self, event: SentenceReady) -> None:
        turn_id = event.turn_id
        if turn_id in self._cancelled:
            logger.debug("stale SentenceReady for cancelled turn %s", turn_id[:8])
            return
        if event.text:
            self._pending[turn_id] = self._pending.get(turn_id, 0) + 1
            self._bus.spawn(
                self._synthesize(event),
                name=f"tts-synth:{turn_id[:8]}",
            )
        if event.done:
            self._done.add(turn_id)
            self._maybe_finish(event)

    async def _synthesize(self, event: SentenceReady) -> None:
        turn_id = event.turn_id
        sequence = None
        try:
            async with self._semaphore:
                pcm, sample_rate = await asyncio.to_thread(
                    self._engine.create, event.text, self._voice
                )
            if turn_id in self._cancelled:
                logger.debug("discarding synth for cancelled turn %s", turn_id[:8])
                return
            sequence = self._sequence.get(turn_id, 0)
            self._sequence[turn_id] = sequence + 1
            self._bus.emit(
                AudioReady(
                    pcm=pcm,
                    sample_rate=sample_rate,
                    sequence=sequence,
                    session_id=event.session_id,
                    turn_id=turn_id,
                    causation_id=event.event_id,
                )
            )
        except Exception:
            logger.exception("tts synthesis failed for turn %s", turn_id[:8])
        finally:
            self._release(event)

    def _release(self, event: SentenceReady) -> None:
        turn_id = event.turn_id
        remaining = self._pending.get(turn_id)
        if remaining is None:
            return
        remaining -= 1
        if remaining <= 0:
            self._pending.pop(turn_id, None)
            self._maybe_finish(event)
        else:
            self._pending[turn_id] = remaining

    def _maybe_finish(self, event: SentenceReady) -> None:
        turn_id = event.turn_id
        if (
            turn_id not in self._pending
            and turn_id in self._done
            and turn_id not in self._cancelled
        ):
            self._done.discard(turn_id)
            self._sequence.pop(turn_id, None)
            self._bus.spawn(
                self._emit_finished(event),
                name=f"tts-finish:{turn_id[:8]}",
            )

    async def _emit_finished(self, event: SentenceReady) -> None:
        self._bus.emit(
            SpeechFinished(
                session_id=event.session_id,
                turn_id=event.turn_id,
                causation_id=event.event_id,
            )
        )

    async def on_turn_cancelled(self, event: TurnCancelled) -> None:
        turn_id = event.turn_id
        self._cancelled.add(turn_id)
        self._pending.pop(turn_id, None)
        self._done.discard(turn_id)
        self._sequence.pop(turn_id, None)
        if len(self._cancelled) > 128:
            oldest = sorted(self._cancelled)[:64]
            self._cancelled -= set(oldest)
