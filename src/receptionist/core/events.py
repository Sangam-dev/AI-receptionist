from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4


@dataclass(frozen=True)
class BaseEvent:
    # Fields every event carries. turn_id groups events into one
    # conversational turn, used for cancellation and stale-event checks.
    event_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    session_id: str = "default"
    turn_id: str = ""
    causation_id: str | None = None


# input


@dataclass(frozen=True)
class UserTextReceived(BaseEvent):
    text: str = ""


@dataclass(frozen=True)
class TranscriptReady(BaseEvent):
    # push-to-talk: one recorded utterance -> one final transcript
    text: str = ""


@dataclass(frozen=True)
class UserInterrupted(BaseEvent):
    # user barge-in or stop button pressed mid-turn
    pass


# llm streaming


@dataclass(frozen=True)
class PartialResponse(BaseEvent):
    text: str = ""
    index: int = 0
    done: bool = False


@dataclass(frozen=True)
class ResponseReady(BaseEvent):
    text: str = ""


# speech output


@dataclass(frozen=True)
class SentenceReady(BaseEvent):
    text: str = ""
    index: int = 0
    done: bool = False  # final marker of a turn (possibly empty text)


@dataclass(frozen=True)
class AudioReady(BaseEvent):
    pcm: bytes = b""
    sample_rate: int = 24000
    sequence: int = 0  # order of chunks within one turn


@dataclass(frozen=True)
class SpeechFinished(BaseEvent):
    pass


@dataclass(frozen=True)
class TurnCancelled(BaseEvent):
    pass


# tools


@dataclass(frozen=True)
class ToolRequested(BaseEvent):
    name: str = ""
    call_id: str = ""


@dataclass(frozen=True)
class ToolCompleted(BaseEvent):
    call_id: str = ""
    result: str = ""
    is_error: bool = False


# ui state


class AssistantState(str, Enum):
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    IDLE = "idle"


@dataclass(frozen=True)
class AssistantStateChanged(BaseEvent):
    state: AssistantState = AssistantState.IDLE


# bus


@dataclass(frozen=True)
class HandlerFailed(BaseEvent):
    # emitted by the EventBus when a handler raises
    handler: str = ""
    failed_event_id: str = ""
    failed_event_type: str = ""
    error: str = ""
