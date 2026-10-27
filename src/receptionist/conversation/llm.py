import asyncio
import logging
from collections.abc import AsyncIterator

from langchain_core.messages import AIMessage
from pydantic import SecretStr

logger = logging.getLogger(__name__)

from dotenv import load_dotenv
import os

load_dotenv()

api_key = os.getenv("GROQ_API_KEY")


class LLMClient:
    def __init__(
        self,
        api_key: str,
        model: str = "llama-3.3-70b-versatile",
        system_prompt: str = "",
        timeout_s: float = 30.0,
        tools: list | None = None,
    ) -> None:
        from langchain_groq import ChatGroq

        client = ChatGroq(
            api_key=SecretStr(api_key),
            model=model,
            streaming=True,
            temperature=0.6,
        )
        self._tools = tools or []
        if self._tools:
            client = client.bind_tools(self._tools)
        self._client = client
        self._system_prompt = system_prompt
        self._timeout_s = timeout_s

    def _messages(self, history: list) -> list:
        messages = []
        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})
        messages.extend(history[-20:])
        return messages

    async def invoke(self, history: list) -> AIMessage:
        try:
            async with asyncio.timeout(self._timeout_s):
                return await self._client.ainvoke(self._messages(history))
        except TimeoutError:
            logger.error("LLM invoke exceeded %.1fs", self._timeout_s)
            raise

    async def stream(self, history: list[dict]) -> AsyncIterator[str]:
        try:
            async with asyncio.timeout(self._timeout_s):
                async for chunk in self._client.astream(self._messages(history)):
                    text = getattr(chunk, "content", "")
                    if text:
                        yield str(text)
        except TimeoutError:
            logger.error("LLM stream exceeded %.1fs", self._timeout_s)
            raise
