import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace

from receptionist.api.bridge import Bridge
from receptionist.api.ws_manager import ConnectionManager
from receptionist.config import Settings
from receptionist.conversation.agent import ConversationAgent
from receptionist.core.bus import EventBus
from receptionist.speech.chunker import SentenceChunker
from receptionist.speech.tts import FakeEngine, KokoroEngine, TTSHandler
from receptionist.tools import DBTools

logger = logging.getLogger(__name__)

FAKE_REPLY = "Hello there. How are you? I can help with appointments."


class FakeLLM:
    def __init__(self, reply: str = FAKE_REPLY) -> None:
        self._reply = reply

    def set_reply(self, reply: str) -> None:
        self._reply = reply

    async def invoke(self, history: list) -> SimpleNamespace:
        return SimpleNamespace(content=self._reply, tool_calls=[])

    async def stream(self, history: list[dict]) -> AsyncIterator[str]:
        for word in self._reply.split(" "):
            yield word + " "


@dataclass
class Runtime:
    settings: Settings
    bus: EventBus
    manager: ConnectionManager
    bridge: Bridge
    agent: ConversationAgent
    chunker: SentenceChunker
    tts: TTSHandler
    db_tools: DBTools | None = field(default=None)

    async def shutdown(self) -> None:
        await self.bus.drain(timeout=3)
        await self.bus.close()
        if self.db_tools is not None:
            await self.db_tools.aclose()


def build_runtime(settings: Settings | None = None) -> Runtime:
    settings = settings or Settings()
    bus = EventBus()
    manager = ConnectionManager()

    db_tools = DBTools(settings.db_api_base_url)
    langchain_tools = db_tools.as_langchain_tools()

    if settings.force_fake:
        llm = FakeLLM()
        logger.info("LLM: FakeLLM (forced)")
    elif settings.groq_api_key:
        from receptionist.conversation.llm import LLMClient
        from receptionist.conversation.prompt import SYSTEM_PROMPT

        today = datetime.now().strftime("%A, %Y-%m-%d")
        llm: object = LLMClient(
            api_key=settings.groq_api_key,
            model=settings.groq_model,
            system_prompt=f"{SYSTEM_PROMPT}\nToday's date is {today}.",
            timeout_s=settings.turn_timeout_s,
            tools=langchain_tools,
        )
        logger.info(
            "LLM: ChatGroq model=%s tools=%d", settings.groq_model, len(langchain_tools)
        )
    else:
        llm = FakeLLM()
        logger.warning("LLM: FakeLLM (no GROQ_API_KEY)")

    agent = ConversationAgent(
        bus, llm, tools={tool.name: tool for tool in langchain_tools}
    )
    agent.register()

    chunker = SentenceChunker(bus)
    chunker.register()

    if settings.tts_available and not settings.force_fake:
        engine: object = KokoroEngine(
            model_path=settings.tts_model_path,
            voices_path=settings.tts_voices_path,
            speed=settings.tts_speed,
        )
        logger.info("TTS: Kokoro voice=%s", settings.tts_voice)
    else:
        engine = FakeEngine()
        if not settings.force_fake:
            logger.warning("TTS: FakeEngine (model files missing)")

    tts = TTSHandler(bus, engine, settings.tts_voice)
    tts.register()

    bridge = Bridge(bus, manager)
    bridge.register()

    return Runtime(
        settings=settings,
        bus=bus,
        manager=manager,
        bridge=bridge,
        agent=agent,
        chunker=chunker,
        tts=tts,
        db_tools=db_tools,
    )
