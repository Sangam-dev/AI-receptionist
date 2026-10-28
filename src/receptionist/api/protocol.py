from typing import Literal

from pydantic import BaseModel, Field


class WSAuth(BaseModel):
    type: Literal["auth"] = "auth"
    token: str = ""


class WSIncoming(BaseModel):
    type: Literal["user_text", "interrupt"]
    text: str = ""


class WSError(BaseModel):
    type: Literal["error"] = "error"
    message: str = ""
