import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TTS_VOICE = "af_heart"


@dataclass(frozen=True)
class Settings:
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"
    tts_voice: str = DEFAULT_TTS_VOICE
    tts_model_path: str = ""
    tts_voices_path: str = ""
    first_timeout_s: float = 10.0
    turn_timeout_s: float = 30.0
    session_ttl_s: float = 1800.0
    demo_token: str = "dev-token"
    force_fake: bool = False

    @property
    def tts_available(self) -> bool:
        return (
            bool(self.tts_model_path)
            and Path(self.tts_model_path).is_file()
            and bool(self.tts_voices_path)
            and Path(self.tts_voices_path).is_file()
        )


def load_settings(env_file: str | Path | None = None) -> Settings:
    load_dotenv(PROJECT_ROOT / ".env" if env_file is None else env_file)
    tts_model_path = os.getenv("TTS_MODEL_PATH", "~/kancha/tts/data/kokoro-v1.0.onnx")
    tts_voices_path = os.getenv("TTS_VOICES_PATH", "~/kancha/tts/data/voices-v1.0.bin")
    return Settings(
        groq_api_key=os.getenv("GROQ_API_KEY") or None,
        groq_model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
        tts_voice=os.getenv("TTS_VOICE", DEFAULT_TTS_VOICE),
        tts_model_path=str(Path(tts_model_path).expanduser()) if tts_model_path else "",
        tts_voices_path=str(Path(tts_voices_path).expanduser())
        if tts_voices_path
        else "",
        first_timeout_s=float(os.getenv("FIRST_TIMEOUT_S", "10")),
        turn_timeout_s=float(os.getenv("TURN_TIMEOUT_S", "30")),
        session_ttl_s=float(os.getenv("SESSION_TTL_S", "1800")),
        demo_token=os.getenv("DEMO_TOKEN", "dev-token"),
        force_fake=os.getenv("RECEPTIONIST_FAKE") == "1",
    )
