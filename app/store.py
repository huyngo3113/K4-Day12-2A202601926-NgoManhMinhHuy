"""CP4 — Stateless: state sống ngoài process.

Nếu lịch sử hội thoại nằm trong một dict trong RAM, thì khi scale lên 3
instance, client gửi tin 1 vào instance A và tin 2 vào instance B sẽ thấy
service "mất trí nhớ". Container còn bị restart bất cứ lúc nào. Vì vậy state
phải nằm ở nơi mọi instance cùng nhìn thấy: Redis.
"""

from __future__ import annotations

import json

import redis

from .config import get_settings

HISTORY_MAX_MESSAGES = 12
HISTORY_TTL_SECONDS = 3 * 24 * 3600


def get_redis_client(url: str | None = None):
    """CHO SẴN — tạo client Redis từ URL.

    ``fake://`` trả về Redis giả chạy trong RAM, dùng khi máy bạn chưa có
    Docker. Tiện cho lúc học, nhưng KHÔNG dùng khi deploy: nó vẫn là state
    trong process, đúng cái mà CP4 đang tìm cách loại bỏ.
    """
    url = url or get_settings().redis_url
    if url.startswith("fake://"):
        import fakeredis

        return fakeredis.FakeRedis(decode_responses=True)
    return redis.from_url(url, decode_responses=True)


class ChatStore:
    """Lưu lịch sử hội thoại của từng client trong Redis List."""

    def __init__(self, client) -> None:
        self.client = client

    @staticmethod
    def _key(client_id: str) -> str:
        """CHO SẴN."""
        return f"chat:{client_id}"

    def ping(self) -> bool:
        """Redis có trả lời không? Dùng cho endpoint /readyz.

        TODO (CP4): gọi ``self.client.ping()`` trong try/except.
        Trả ``True`` nếu thành công, ``False`` nếu có bất kỳ Exception nào
        (mất mạng, sai mật khẩu, Redis chưa khởi động...).
        """
        try:
            return bool(self.client.ping())
        except Exception:
            return False

    def add_turn(self, client_id: str, role: str, content: str) -> None:
        """Ghi thêm một lượt vào lịch sử.

        TODO (CP4):
          1. ``self.client.rpush(key, json.dumps({"role": role, "content": content},
             ensure_ascii=False))``
          2. ``self.client.ltrim(key, -HISTORY_MAX_MESSAGES, -1)`` — chỉ giữ
             ``HISTORY_MAX_MESSAGES`` message gần nhất, nếu không prompt sẽ
             phình vô hạn và tiền token cũng vậy.
          3. ``self.client.expire(key, HISTORY_TTL_SECONDS)`` — hội thoại cũ
             tự hết hạn, khỏi phải dọn tay.
        """
        key = self._key(client_id)
        self.client.rpush(key, json.dumps({"role": role, "content": content}, ensure_ascii=False))
        self.client.ltrim(key, -HISTORY_MAX_MESSAGES, -1)
        self.client.expire(key, HISTORY_TTL_SECONDS)

    def history(self, client_id: str) -> list[dict]:
        """Đọc lịch sử hội thoại, cũ nhất trước.

        TODO (CP4): ``self.client.lrange(key, 0, -1)`` rồi ``json.loads``
        từng phần tử. Chưa có gì → trả về list rỗng.
        """
        items = self.client.lrange(self._key(client_id), 0, -1)
        return [json.loads(item) for item in items]

    def reset(self, client_id: str) -> None:
        """CHO SẴN — xóa lịch sử của một client."""
        self.client.delete(self._key(client_id))
