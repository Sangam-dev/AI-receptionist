import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from receptionist.api.protocol import WSAuth, WSIncoming
from receptionist.api.ws_manager import ConnectionManager
from receptionist.config import Settings, load_settings
from receptionist.core import events as ev
from receptionist.core.pipeline import Runtime, build_runtime

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime = build_runtime(load_settings())
    app.state.runtime = runtime
    logger.info("receptionist runtime up")
    try:
        yield
    finally:
        await runtime.shutdown()
        logger.info("receptionist runtime down")


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket):
        await ws_session(websocket, app)

    @app.get("/health")
    async def health():
        runtime: Runtime = app.state.runtime
        bus = runtime.bus
        handlers = {
            cls.__name__: bus.handler_count(cls)
            for name, cls in vars(ev).items()
            if isinstance(cls, type)
            and issubclass(cls, ev.BaseEvent)
            and cls is not ev.BaseEvent
        }
        return {
            "ok": True,
            "handlers": handlers,
            "connections": runtime.manager.count(),
        }

    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app


app = create_app()


async def ws_session(websocket: WebSocket, fastapi_app: FastAPI) -> None:
    await websocket.accept()
    try:
        auth = WSAuth.model_validate_json(await websocket.receive_text())
    except Exception:
        await websocket.close(code=1008, reason="bad auth frame")
        return
    runtime = getattr(fastapi_app.state, "runtime", None)
    demo_token = runtime.settings.demo_token if runtime else "dev-token"
    if auth.token != demo_token:
        await websocket.close(code=1008, reason="invalid token")
        return

    session_id = f"sess-{uuid.uuid4().hex[:8]}"
    runtime: Runtime = fastapi_app.state.runtime
    manager = runtime.manager
    manager.connect(session_id, websocket)
    writer = asyncio.create_task(manager.writer(session_id))
    try:
        await websocket.send_json({"type": "ready", "session_id": session_id})
        while True:
            message = WSIncoming.model_validate_json(await websocket.receive_text())
            if message.type == "user_text":
                if not message.text.strip():
                    manager.send(session_id, {"type": "error", "message": "empty text"})
                    continue
                runtime.bus.emit(
                    ev.UserTextReceived(
                        text=message.text.strip(),
                        session_id=session_id,
                        turn_id=uuid.uuid4().hex,
                    )
                )
            elif message.type == "interrupt":
                runtime.bus.emit(ev.UserInterrupted(session_id=session_id))
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("ws session %s crashed", session_id)
        await websocket.close(code=1011)
    finally:
        writer.cancel()
        manager.disconnect(session_id)
        runtime.bus.emit(ev.UserInterrupted(session_id=session_id))
