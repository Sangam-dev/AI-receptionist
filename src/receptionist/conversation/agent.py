import asyncio
import logging

from langchain_core.messages import ToolMessage

from receptionist.conversation.session import SessionStore
from receptionist.core.bus import EventBus
from receptionist.core.events import (
    PartialResponse,
    ResponseReady,
    ToolCompleted,
    ToolRequested,
    TranscriptReady,
    TurnCancelled,
    UserInterrupted,
    UserTextReceived,
)

logger = logging.getLogger(__name__)

FALLBACK_REPLY = (
    "Sorry, something went wrong on my end. Let me try again when you are ready."
)
TOOL_LOOP_FALLBACK = (
    "Sorry, I could not complete that request right now. "
    "Let me connect you with a human staff member."
)
MAX_TOOL_ROUNDS = 6

FILLER_PHRASES = [
    "Let me check that for you.",
    "One moment, please.",
    "Give me just a second.",
    "Let me look that up.",
]


class ConversationAgent:
    def __init__(
        self,
        bus: EventBus,
        llm,
        sessions: SessionStore | None = None,
        tools: dict | None = None,
        max_tool_rounds: int = MAX_TOOL_ROUNDS,
    ) -> None:
        self._bus = bus
        self._llm = llm
        self._sessions = sessions or SessionStore()
        self._tools: dict = tools or {}
        self._max_tool_rounds = max_tool_rounds
        self._filler_index = 0
        self._active: dict[str, tuple[str, asyncio.Task]] = {}

    def register(self) -> None:
        self._bus.subscribe(UserTextReceived, self.on_user_input)
        self._bus.subscribe(TranscriptReady, self.on_user_input)
        self._bus.subscribe(UserInterrupted, self.on_user_interrupted)

    async def on_user_input(self, event: UserTextReceived | TranscriptReady) -> None:
        active = self._active.get(event.session_id)
        if active is not None and not active[1].done():
            logger.info(
                "dropping input for session %s: turn %s still active",
                event.session_id,
                active[0][:8],
            )
            return
        task = asyncio.current_task()
        if task is not None:
            self._active[event.session_id] = (event.turn_id, task)
        await self._stream_reply(event)

    async def on_user_interrupted(self, event: UserInterrupted) -> None:
        entry = self._active.get(event.session_id)
        if entry is None:
            logger.debug("interrupt with no active turn for %s", event.session_id)
            return
        turn_id, task = entry
        if not task.done():
            logger.info("barge-in: cancelling turn %s", turn_id[:8])
            task.cancel()

    def cancel_turn(self, turn_id: str) -> bool:
        for _session, (turn, task) in list(self._active.items()):
            if turn == turn_id and not task.done():
                task.cancel()
                return True
        return False

    async def _stream_reply(self, event: UserTextReceived | TranscriptReady) -> None:
        prompt = event.text.strip()
        session_id, turn_id = event.session_id, event.turn_id
        try:
            self._sessions.append(session_id, "user", prompt)
            self._sessions.touch(session_id)
            history = self._sessions.history(session_id)

            reply = await self._run_turn(event, history)

            self._sessions.append(session_id, "assistant", reply)
            await self._bus.emit_and_wait(
                PartialResponse(
                    text=reply,
                    index=0,
                    causation_id=event.event_id,
                    session_id=session_id,
                    turn_id=turn_id,
                )
            )
            await self._bus.emit_and_wait(
                ResponseReady(
                    text=reply,
                    causation_id=event.event_id,
                    session_id=session_id,
                    turn_id=turn_id,
                )
            )
            logger.info("turn %s complete: %d chars", turn_id[:8], len(reply))
        except asyncio.CancelledError:
            self._sessions.pop_last(session_id)
            logger.info("turn %s cancelled (barge-in)", turn_id[:8])
            self._bus.emit(
                TurnCancelled(
                    session_id=session_id,
                    turn_id=turn_id,
                    causation_id=event.event_id,
                )
            )
            raise
        except Exception as exc:
            logger.exception("agent turn %s failed: %s", turn_id[:8], exc)
            await self._emit_reply(event, FALLBACK_REPLY)
        finally:
            self._active.pop(session_id, None)

    async def _run_turn(
        self, event: UserTextReceived | TranscriptReady, history: list[dict]
    ) -> str:
        messages = list(history)
        spoken = False
        for _ in range(self._max_tool_rounds):
            ai = await self._llm.invoke(messages)
            tool_calls = getattr(ai, "tool_calls", None) or []
            if not tool_calls:
                text = str(getattr(ai, "content", "") or "").strip()
                return text or FALLBACK_REPLY
            if not spoken:
                await self._emit_filler(event)
                spoken = True
            messages.append(ai)
            for call in tool_calls:
                name = call.get("name", "")
                call_id = call.get("id", "")
                args = call.get("args") or {}
                logger.info("tool requested: %s %s", name, args)
                await self._bus.emit_and_wait(
                    ToolRequested(
                        name=name,
                        call_id=call_id,
                        causation_id=event.event_id,
                        session_id=event.session_id,
                        turn_id=event.turn_id,
                    )
                )
                result, is_error = await self._run_tool(name, args)
                await self._bus.emit_and_wait(
                    ToolCompleted(
                        call_id=call_id,
                        result=result,
                        is_error=is_error,
                        causation_id=event.event_id,
                        session_id=event.session_id,
                        turn_id=event.turn_id,
                    )
                )
                messages.append(
                    ToolMessage(content=result, tool_call_id=call_id, name=name)
                )
        logger.warning("tool loop exhausted after %d rounds", self._max_tool_rounds)
        return TOOL_LOOP_FALLBACK

    async def _emit_filler(self, event: UserTextReceived | TranscriptReady) -> None:
        phrase = FILLER_PHRASES[self._filler_index % len(FILLER_PHRASES)]
        self._filler_index += 1
        logger.info("speaking filler for turn %s", event.turn_id[:8])
        await self._bus.emit_and_wait(
            PartialResponse(
                text=phrase,
                index=0,
                causation_id=event.event_id,
                session_id=event.session_id,
                turn_id=event.turn_id,
            )
        )

    async def _run_tool(self, name: str, args: dict) -> tuple[str, bool]:
        tool = self._tools.get(name)
        if tool is None:
            logger.warning("unknown tool requested: %s", name)
            return f"Tool {name} does not exist.", True
        try:
            result = await tool.ainvoke(args)
            return str(result), False
        except Exception as exc:
            logger.exception("tool %s failed: %s", name, exc)
            return f"Tool {name} failed: {exc}", True

    async def _emit_reply(
        self, event: UserTextReceived | TranscriptReady, reply: str
    ) -> None:
        await self._bus.emit_and_wait(
            PartialResponse(
                text=reply,
                index=0,
                causation_id=event.event_id,
                session_id=event.session_id,
                turn_id=event.turn_id,
            )
        )
        await self._bus.emit_and_wait(
            ResponseReady(
                text=reply,
                causation_id=event.event_id,
                session_id=event.session_id,
                turn_id=event.turn_id,
            )
        )
