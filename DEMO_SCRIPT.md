# Kịch Bản Demo — K4 Ngày 12: Hạ Tầng Cloud & Deployment

> Dùng để trình bày trực tiếp: mỗi bước ghi rõ **lệnh chạy (input)**, **kết
> quả mong đợi (output)**, và **giải thích ý nghĩa**. Có thể demo bằng máy
> local (docker compose) hoặc bằng URL cloud thật
> (`https://chat-production-c335.up.railway.app`) — script ghi cả hai, chọn
> nhánh phù hợp lúc trình bày.

---

## Giai đoạn 0 — Chuẩn bị

**Input:**
```bash
cd K4-Day12-Cloud-Services-And-Deployment
cp .env.example .env          # rồi điền API_TOKEN riêng
docker compose up -d redis
docker compose ps
```

**Output mong đợi:**
```
NAME                                              STATUS
k4-day12-cloud-services-and-deployment-redis-1    Up (healthy)
```

**Giải thích:** Redis phải `healthy` trước khi chạy app, vì `store.py` và
`rate_limiter.py`/`cost_guard.py` đều đọc/ghi Redis. Đây là bước hạ tầng nền,
chưa liên quan tới code chính.

---

## Giai đoạn 1 — 12-Factor Config, Health & Logging (CP1)

### 1.1 Fail fast khi thiếu secret

**Input:**
```bash
API_TOKEN= uvicorn app.main:app --port 8000   # cố tình bỏ trống API_TOKEN
```

**Output mong đợi:**
```
pydantic_core._pydantic_core.ValidationError: 1 validation error for Settings
api_token
  Field required
```

**Giải thích:** App **không khởi động được** nếu thiếu `API_TOKEN` — đây là
minh chứng sống cho câu 1 trong `exercises.md`: fail fast ngay lúc khởi động
thay vì âm thầm chạy với giá trị mặc định nguy hiểm.

### 1.2 Health check + structured log

**Input:**
```bash
uvicorn app.main:app --port 8000 &
curl -i http://localhost:8000/healthz
```

**Output mong đợi:**
```
HTTP/1.1 200 OK
{"status":"ok","service":"day12-chat-service","version":"1.0.0"}
```

Log ra stdout cùng lúc:
```json
{"event": "service_started", "severity": "INFO", "ts": "...", "service": "day12-chat-service", "version": "1.0.0"}
```

**Giải thích:** `/healthz` không đụng Redis — kể cả khi Redis chết, endpoint
này vẫn trả `200`, vì nó chỉ trả lời "process còn sống không?" (khác
`/readyz` ở Giai đoạn 4). Log là JSON một dòng, khóa `severity` viết hoa —
máy đọc được, không phải log cho người.

---

## Giai đoạn 2 — Docker (CP2)

### 2.1 So sánh kích thước image

**Input:** (tạo tạm 1 Dockerfile 1-stage để đối chứng, không nằm trong repo)
```bash
cat > Dockerfile.single <<'EOF'
FROM python:3.11
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
EOF
docker build -f Dockerfile.single -t chat:single .   # bản 1-stage (đối chứng)
docker build -t chat:multi .                          # bản multi-stage thật (Dockerfile chính)
docker images | grep chat
```

**Output thực đo:**
```
chat:single   1.73 GB
chat:multi    270 MB
```

**Giải thích:** Multi-stage build chỉ mang `pip install` **kết quả** sang
image cuối (`COPY --from=builder /install /usr/local`), không mang compiler
hay cache pip. Giảm ~1.46GB — deploy nhanh hơn, kéo image nhanh hơn.

### 2.2 Chạy container, kiểm tra không phải root

**Input:**
```bash
docker run -d --name demo-chat -p 8000:8000 \
  -e API_TOKEN=$API_TOKEN -e REDIS_URL=fake:// chat:multi
docker exec demo-chat whoami
```

**Output mong đợi:**
```
appuser
```

**Giải thích:** `USER appuser` (UID 10001) trong Dockerfile — nếu có lỗ hổng
RCE trong code Python, kẻ tấn công chỉ có quyền user thường trong container,
không phải root.

---

## Giai đoạn 3 — API Security: Auth, Rate Limit, Cost Guard (CP3)

### 3.1 Không có token

**Input:**
```bash
curl -i -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" -d '{"message":"Hello"}'
```

**Output mong đợi:**
```
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer
{"detail":"invalid or missing bearer token"}
```

**Giải thích:** Thiếu header `Authorization` → 401 kèm `WWW-Authenticate:
Bearer` theo RFC 6750. Cùng thông báo lỗi này dùng chung cho cả sai scheme,
sai token — không lộ thông tin cho người đang dò.

### 3.2 Có token hợp lệ

**Input:**
```bash
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_TOKEN" -H "X-Client-Id: demo01" \
  -d '{"message":"Docker là gì?"}'
```

**Output mong đợi:**
```json
{"reply":"...", "client_id":"demo01", "turns_before":0,
 "usd_cost":0.0000384, "usage":{"prompt":3,"completion":41}}
```

**Giải thích:** Luồng đầy đủ chạy qua: auth → token bucket → cost guard →
lấy lịch sử → gọi mock LLM → ghi lịch sử → ghi chi phí → log.
`turns_before: 0` vì đây là tin nhắn đầu tiên của client này.

### 3.3 Vượt rate limit (token bucket)

**Input:**
```bash
for i in $(seq 1 15); do
  curl -s -o /dev/null -w "%{http_code} " -X POST http://localhost:8000/chat \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $API_TOKEN" -H "X-Client-Id: demo01" \
    -d '{"message":"test"}'
done; echo
```

**Output thực đo:**
```
200 200 200 200 200 200 200 200 200 200 429 429 429 429 429
```

**Giải thích:** `BUCKET_CAPACITY=10` — 10 request đầu tiêu hết xô, 5 request
sau bị `429` kèm header `Retry-After`. Xô không cho phép "bùng" quá
`capacity` dù client có im lặng bao lâu trước đó (nhờ `min(capacity, ...)`).

---

## Giai đoạn 4 — Scaling & Reliability (CP4)

### 4.1 Stateless — lịch sử dùng chung qua nhiều container

**Input:**
```bash
docker compose up -d --scale chat=3
for i in $(seq 1 5); do
  curl -s -X POST http://localhost:8000/chat \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $API_TOKEN" -H "X-Client-Id: demo01" \
    -d "{\"message\":\"lượt $i\"}" | python -c "import json,sys; print(json.load(sys.stdin)['turns_before'])"
done
```

**Output mong đợi:**
```
0
2
4
6
8
```

**Giải thích:** Dù load balancer đưa request vào container khác nhau mỗi
lần, `turns_before` vẫn tăng dần đều — vì lịch sử nằm trong Redis (`store.py`),
không nằm trong RAM của từng container.

### 4.2 `/readyz` phản ánh đúng trạng thái Redis

**Input:**
```bash
curl -i http://localhost:8000/readyz          # Redis đang sống
docker compose stop redis
curl -i http://localhost:8000/readyz          # Redis chết
docker compose start redis
```

**Output mong đợi:**
```
# Redis sống
HTTP/1.1 200 OK
{"status":"ready","redis":true}

# Redis chết
HTTP/1.1 503 Service Unavailable
{"status":"not ready","redis":false}
```

**Giải thích:** `/readyz` (khác `/healthz`) được phép kiểm tra dependency.
503 ở đây khiến load balancer **rút container khỏi vòng chia traffic**,
không phải restart — tránh biến sự cố Redis nhỏ thành cả cụm bị restart
đồng loạt.

### 4.3 Graceful shutdown (draining)

**Input:**
```bash
docker compose exec chat sh -c 'kill -TERM 1'   # gửi SIGTERM vào container
curl -i http://localhost:8000/healthz            # gọi ngay sau đó
```

**Output mong đợi:**
```
HTTP/1.1 503 Service Unavailable
{"status":"draining"}
```

**Giải thích:** Nhận SIGTERM → `shutdown_guard.draining = True` →
`/healthz` báo 503 ngay lập tức → load balancer ngừng gửi traffic mới, trong
khi uvicorn (được nhường lại quyền xử lý tín hiệu) vẫn hoàn tất các request
đang chạy dở rồi mới thoát.

---

## Giai đoạn 5 — Cloud Deployment thật (CP5)

**Input:**
```bash
URL=https://chat-production-c335.up.railway.app

curl -i $URL/healthz
curl -i $URL/readyz
curl -i -X POST $URL/chat -H "Content-Type: application/json" -d '{"message":"Hello"}'
curl -s -X POST $URL/chat -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_TOKEN" -H "X-Client-Id: demo-cloud" \
  -d '{"message":"Railway hoạt động không?"}'
```

**Output thực đo:**
```
GET /healthz  → 200 {"status":"ok",...}
GET /readyz   → 200 {"status":"ready","redis":true}
POST /chat (không token) → 401 {"detail":"invalid or missing bearer token"}
POST /chat (có token)    → 200 {"reply":"...", ...}
```

**Giải thích:** Đây là bằng chứng service chạy thật trên Railway, có Redis
add-on nối được (`redis:true`), và lớp bảo vệ token vẫn hoạt động trên môi
trường công khai — không chỉ trên máy local.

### Sự cố thật gặp lúc deploy (đáng nói khi demo)

**Input (log lỗi ban đầu):**
```
Error: Invalid value for '--port': '$PORT' is not a valid integer.
```

**Nguyên nhân:** `railway.toml` có `startCommand` đè lên `CMD` của
Dockerfile, chạy không qua shell nên `$PORT` không được interpolate — nhận
nguyên chuỗi ký tự `$PORT`.

**Sửa:** Xoá `startCommand` khỏi `railway.toml`, để Dockerfile tự lo (đã có
sẵn `sh -c "... --port ${PORT:-8000}"`). Deploy lại → chạy đúng port động.

---

## Tổng kết khi demo

| Giai đoạn | Chứng minh điều gì |
|-----------|---------------------|
| CP1 | Config ngoài code, log máy đọc được, health check nhẹ |
| CP2 | Image nhỏ, không root, cache layer đúng thứ tự |
| CP3 | 3 lớp bảo vệ: auth → rate limit → cost guard, đúng thứ tự trước khi tốn tiền LLM |
| CP4 | Stateless qua Redis, readyz/healthz tách biệt, graceful shutdown |
| CP5 | Chạy thật trên cloud, có domain HTTPS công khai, tự vá được lỗi deploy thực tế |
