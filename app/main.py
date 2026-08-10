"""Chat service — điểm ráp nối của cả lab (CP1, CP3, CP4).

Luồng một request tới /chat:

    client ──► verify_bearer_token ──► token bucket ──► cost guard
                                                            │
                                    store.history ◄─────────┘
                                          │
                                   generate_reply
                                          │
                              store.add_turn × 2 ──► guard.record ──► emit
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from utils.mock_llm import generate_reply

from .auth import verify_bearer_token
from .config import get_settings
from .cost_guard import CostGuard
from .lifecycle import shutdown_guard
from .logging_utils import emit
from .rate_limiter import TokenBucket
from .store import ChatStore, get_redis_client

SERVICE_NAME = "day12-chat-service"
SERVICE_VERSION = "1.0.0"


# ─────────────────────────────────────────────────────────────
# Providers — CHO SẴN
# Tách ra thành hàm để test có thể thay bằng Redis giả qua
# app.dependency_overrides, và để kết nối Redis chỉ tạo khi thật sự cần.
# ─────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def get_store() -> ChatStore:
    return ChatStore(get_redis_client())


@lru_cache(maxsize=1)
def get_bucket() -> TokenBucket:
    settings = get_settings()
    return TokenBucket(
        get_redis_client(),
        capacity=settings.bucket_capacity,
        refill_per_minute=settings.refill_per_minute,
    )


@lru_cache(maxsize=1)
def get_cost_guard() -> CostGuard:
    return CostGuard(get_redis_client(), get_settings().daily_budget_usd)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """CHO SẴN — chạy lúc app khởi động và lúc tắt."""
    shutdown_guard.arm()
    emit("service_started", service=SERVICE_NAME, version=SERVICE_VERSION)
    yield
    emit("service_stopped", service=SERVICE_NAME)


app = FastAPI(title="Day 12 Chat Service", version=SERVICE_VERSION, lifespan=lifespan)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


# ─────────────────────────────────────────────────────────────
# Health & readiness
# ─────────────────────────────────────────────────────────────
@app.get("/healthz")
def healthz():
    """Liveness probe — process còn sống không?

    TODO (CP1 + CP4):
      - Đang tắt dần (``shutdown_guard.draining``) → trả
        ``JSONResponse(status_code=503, content={"status": "draining"})``
      - Bình thường → ``{"status": "ok", "service": SERVICE_NAME,
        "version": SERVICE_VERSION}`` (mặc định FastAPI trả 200).

    Endpoint này phải **nhẹ**: không gọi Redis, không query DB. Nó chỉ trả
    lời câu hỏi "có cần restart container này không?". Nếu nó phụ thuộc
    Redis, Redis chết một nhịp là cả cụm container bị restart theo.
    """
    if shutdown_guard.draining:
        return JSONResponse(status_code=503, content={"status": "draining"})
    return {"status": "ok", "service": SERVICE_NAME, "version": SERVICE_VERSION}


@app.get("/readyz")
def readyz(store: ChatStore = Depends(get_store)):
    """Readiness probe — đã sẵn sàng nhận traffic chưa?

    TODO (CP4):
      - Đang tắt dần → 503 ``{"status": "draining"}``
      - ``store.ping()`` False → 503 ``{"status": "not ready", "redis": False}``
      - Ngược lại → ``{"status": "ready", "redis": True}``

    Khác /healthz ở chỗ: endpoint này ĐƯỢC PHÉP kiểm tra dependency. Load
    balancer dùng nó để quyết định có đẩy request vào instance này không.
    """
    if shutdown_guard.draining:
        return JSONResponse(status_code=503, content={"status": "draining"})
    if not store.ping():
        return JSONResponse(status_code=503, content={"status": "not ready", "redis": False})
    return {"status": "ready", "redis": True}


# ─────────────────────────────────────────────────────────────
# Endpoint chính
# ─────────────────────────────────────────────────────────────
@app.post("/chat")
def chat(
    payload: ChatRequest,
    client_id: str = Depends(verify_bearer_token),
    store: ChatStore = Depends(get_store),
    bucket: TokenBucket = Depends(get_bucket),
    guard: CostGuard = Depends(get_cost_guard),
):
    """Gửi một tin nhắn tới service.

    TODO (CP3 + CP4) — làm ĐÚNG THỨ TỰ sau:
      1. ``bucket.consume(client_id)``        → 429 nếu gọi quá nhanh
      2. ``guard.check(client_id)``           → 402 nếu hết ngân sách ngày
      3. ``history = store.history(client_id)``
      4. ``result = generate_reply(payload.message, history)``
      5. ``store.add_turn(client_id, "user", payload.message)`` và
         ``store.add_turn(client_id, "assistant", result["text"])``
      6. ``guard.record(client_id, result["usd_cost"])``
      7. ``emit("chat_completed", client_id=client_id,
         prompt_tokens=result["prompt_tokens"],
         completion_tokens=result["completion_tokens"],
         usd_cost=result["usd_cost"])``
      8. trả về::

            {
                "reply": result["text"],
                "client_id": client_id,
                "turns_before": len(history),
                "usd_cost": result["usd_cost"],
                "usage": {
                    "prompt": result["prompt_tokens"],
                    "completion": result["completion_tokens"],
                },
            }

    Vì sao check trước rồi mới gọi LLM? Vì tiền mất ở bước gọi LLM. Chặn sau
    khi đã gọi thì bạn vừa trả tiền vừa trả lỗi.

    ``client_id`` do ``verify_bearer_token`` trả về, nên request không có
    token hợp lệ sẽ dừng ở 401 trước khi chạm vào bất cứ dòng nào ở đây.
    """
    bucket.consume(client_id)
    guard.check(client_id)

    history = store.history(client_id)
    result = generate_reply(payload.message, history)

    store.add_turn(client_id, "user", payload.message)
    store.add_turn(client_id, "assistant", result["text"])
    guard.record(client_id, result["usd_cost"])

    emit(
        "chat_completed",
        client_id=client_id,
        prompt_tokens=result["prompt_tokens"],
        completion_tokens=result["completion_tokens"],
        usd_cost=result["usd_cost"],
    )

    return {
        "reply": result["text"],
        "client_id": client_id,
        "turns_before": len(history),
        "usd_cost": result["usd_cost"],
        "usage": {
            "prompt": result["prompt_tokens"],
            "completion": result["completion_tokens"],
        },
    }


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host="0.0.0.0", port=settings.port)
