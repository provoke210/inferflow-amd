import hashlib
import json
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class CompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=200)
    messages: List[Dict[str, Any]] = Field(min_length=1, max_length=100)
    tenant: str = Field(default="demo-shop", min_length=1, max_length=100)
    knowledge_version: str = Field(default="policy-v1", min_length=1, max_length=100)
    priority: Literal["interactive", "background"] = "interactive"
    temperature: float = Field(default=0, ge=0, le=2)
    top_p: float = Field(default=1, gt=0, le=1)
    max_tokens: int = Field(default=128, ge=1, le=2048)
    seed: Optional[int] = None
    stop: Optional[List[str]] = None
    tools: Optional[List[Dict[str, Any]]] = None
    cache: bool = True
    timeout_seconds: float = Field(default=30, ge=0.05, le=180)

    @field_validator("messages")
    @classmethod
    def validate_messages(cls, value):
        if len(json.dumps(value, ensure_ascii=False)) > 64000:
            raise ValueError("messages exceed 64 KB")
        for message in value:
            if message.get("role") not in {"system", "user", "assistant", "tool"}:
                raise ValueError("invalid message role")
        return value

    def payload(self):
        return self.model_dump(include={"model", "messages", "temperature", "top_p", "max_tokens", "seed", "stop", "tools"}, exclude_none=True)

    def cache_key(self):
        if not self.cache or self.temperature != 0 or self.top_p != 1 or self.tools:
            return None
        if any(m.get("role") == "tool" or m.get("tool_calls") or m.get("function_call") for m in self.messages):
            return None
        data = {"payload": self.payload(), "tenant": self.tenant, "knowledge_version": self.knowledge_version}
        return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
