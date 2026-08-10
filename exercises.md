# Phiếu Phản Ánh — K4 Ngày 12

> **Bài làm cá nhân.** Trả lời bằng lời của chính bạn, dựa trên những gì bạn
> quan sát được khi chạy code — không sao chép đáp án của người khác.
>
> Cách trả lời: thay dòng `> *Câu trả lời của bạn*` bằng câu trả lời.
> `grade.py` đếm số câu đã trả lời (15 điểm cho 10 câu).
>
> Họ và tên: ..........................  Mã học viên: ..........................

---

### Câu 1 — Fail fast (CP1)

Trong `Settings`, `api_token` không có giá trị mặc định nên app chết ngay khi
khởi động nếu thiếu biến môi trường. Hãy mô tả một tình huống cụ thể mà việc
"chết sớm" này cứu bạn, so với việc để mặc định `"changeme"`.

> Khi deploy lên Railway/Render lần đầu, rất dễ quên set biến `API_TOKEN` trên
> dashboard (mình cũng suýt quên khi làm CP5). Nếu `api_token` có mặc định
> `"changeme"`, app vẫn khởi động và trả 200 bình thường — endpoint `/chat`
> công khai với một token ai cũng đoán được, và service chỉ lộ ra khi có ai
> đó gọi bằng `Bearer changeme` và tốn tiền LLM thật. Vì không có mặc định,
> app crash ngay lúc build/deploy với `ValidationError: api_token Field
> required` — lỗi hiện ra trong lúc mình đang nhìn log deploy, sửa được trong
> vài giây, thay vì phát hiện ra vài giờ sau qua hóa đơn hoặc log truy cập lạ.

---

### Câu 2 — Log cho máy đọc (CP1)

Chạy service và gọi `/chat` vài lần. Dán một dòng log JSON bạn thu được, rồi
nêu **hai** việc bạn làm được với dòng log đó mà `print("đã trả lời xong")`
không làm được.

> Dòng log thật lấy từ `docker logs`:
> ```json
> {"event": "chat_completed", "severity": "INFO", "ts": "2026-08-10T07:26:48.981036+00:00", "client_id": "sv01", "prompt_tokens": 48, "completion_tokens": 52, "usd_cost": 3.84e-05}
> ```
> Hai việc làm được mà `print` không làm được:
> 1. **Lọc/tổng hợp theo trường**: vì mỗi dòng là JSON có key rõ ràng, mình
>    `jq`/query để hỏi "client nào tốn nhiều `usd_cost` nhất trong ngày?" —
>    chỉ cần gộp theo `client_id` và cộng `usd_cost`. Với `print` thì chuỗi
>    tự do, phải viết regex đoán mới tách được số tiền ra.
> 2. **Cảnh báo theo `severity`**: log platform (Cloud Logging, Datadog...)
>    đọc đúng khóa `severity` để tô màu/lọc mức độ và bắn alert khi thấy
>    `ERROR`. `print("đã trả lời xong")` không có khái niệm mức độ, nên không
>    thể tự động dựng cảnh báo "tỷ lệ lỗi 5 phút qua" từ đó.

---

### Câu 3 — Kích thước image (CP2)

Build cả hai phiên bản và ghi lại số đo thật:

```bash
docker build -f <Dockerfile-1-stage> -t chat:single .
docker build -t chat:multi .
docker images | grep chat
```

| Bản | Dung lượng |
|-----|-----------|
| 1 stage (bản đầu) | 1730 MB |
| Multi-stage | 270 MB |

Giải thích: phần dung lượng chênh lệch đó là những gì?

> Chênh khoảng 1.46GB. Phần lớn là base image `python:3.11` đầy đủ (kèm toàn
> bộ toolchain build, header, các gói hệ thống không cần lúc chạy) so với
> `python:3.11-slim` chỉ có runtime tối thiểu. Phần còn lại là cache của
> `pip install` (`--no-cache-dir`) và các file build-time (`.git`,
> `__pycache__`, test, docs...) mà bản 1-stage copy nguyên `COPY . .` vào
> image cuối, còn bản multi-stage chỉ `COPY --from=builder /install` — tức
> chỉ mang theo *kết quả* cài đặt thư viện, không mang theo compiler hay rác
> build.

---

### Câu 4 — Thứ tự lệnh trong Dockerfile (CP2)

Sửa một ký tự trong `app/main.py` rồi build lại. Với Dockerfile của bạn, những
layer nào được dùng lại từ cache, layer nào phải chạy lại? Nếu bạn đặt
`COPY . .` lên trước `RUN pip install` thì kết quả khác thế nào?

> Đã thử thật: sửa `SERVICE_VERSION` trong `app/main.py` rồi `docker build`
> lại. Kết quả build log: `COPY requirements.txt .` và
> `RUN pip install ...` ở stage `builder` đều **CACHED**, kể cả
> `COPY --from=builder /install /usr/local` và `RUN useradd` ở stage
> runtime — chỉ có `COPY app ./app` (và `COPY utils ./utils` đứng sau nó)
> phải chạy lại, vì Docker so hash nội dung file thay đổi từ đó trở đi. Build
> lại mất vài giây thay vì tải lại toàn bộ dependency.
>
> Nếu đặt `COPY . .` lên **trước** `RUN pip install`: chỉ cần đổi 1 ký tự
> trong code, hash của layer `COPY . .` đổi theo, Docker huỷ cache từ layer
> đó trở đi — kéo theo `pip install` phải chạy lại từ đầu mỗi lần, dù
> `requirements.txt` không hề đổi. Build chậm hẳn (vài chục giây tới vài
> phút tùy số dependency) cho một thay đổi không liên quan gì tới thư viện.

---

### Câu 5 — Vì sao không chạy bằng root (CP2)

Container mặc định chạy bằng root. Mô tả chuỗi sự kiện dẫn từ "một lỗ hổng
trong code Python của bạn" tới "kẻ tấn công có quyền cao trên máy host", và
lệnh `USER` cắt đứt chuỗi đó ở chỗ nào.

> Chuỗi sự kiện: (1) code Python có lỗ hổng (ví dụ deserialize không kiểm
> soát, hoặc một dependency có RCE) → (2) kẻ tấn công chèn được lệnh hệ điều
> hành, chạy với quyền của process uvicorn đang chạy trong container → (3)
> nếu process đó là root trong container, kẻ tấn công có toàn quyền trong
> container: đọc file, cài thêm công cụ → (4) nếu container có cấu hình lỏng
> lẻo (mount volume host, chạy `--privileged`, hoặc gặp lỗ hổng escape của
> container runtime), quyền root *trong* container biến thành bàn đạp để leo
> ra root *trên host* — vì root trong container và root trên host chia sẻ
> cùng UID 0, kernel không tự phân biệt.
>
> `USER appuser` cắt chuỗi ở bước (3): process ứng dụng chạy với UID 10001
> không có quyền, nên dù kẻ tấn công chèn được lệnh ở bước (2), lệnh đó cũng
> chỉ chạy được với quyền hạn chế — không ghi được vào thư mục hệ thống,
> không cài được gói, và nếu có escape container thì cũng escape với quyền
> user thường chứ không phải root.

---

### Câu 6 — Bearer token (CP3)

Vì sao 401 phải kèm header `WWW-Authenticate: Bearer`? Và vì sao ta trả **cùng
một** thông báo lỗi cho cả ba trường hợp (thiếu header, sai scheme, sai token)
thay vì nói rõ sai ở đâu cho người dùng dễ sửa?

> `WWW-Authenticate` là bắt buộc theo chuẩn HTTP cho mọi response 401 — nó
> nói cho client biết *phải xác thực bằng cách nào* (ở đây là scheme
> `Bearer`). Không có header này, client hợp lệ (ví dụ một thư viện HTTP tự
> động xử lý auth) không biết phải gửi lại request kiểu gì, chỉ thấy "401,
> vô nghĩa".
>
> Trả cùng một thông báo cho cả ba trường hợp là để không lộ thông tin cho
> người đang dò: nếu "thiếu header" và "sai token" trả về lỗi khác nhau, kẻ
> tấn công biết được khi nào mình đã "đi đúng hướng" (ví dụ đã đoán đúng
> scheme, chỉ còn dò token) — biến việc dò token từ một bài toán khó thành
> dò từng phần dễ hơn. Trả đồng nhất một câu buộc kẻ tấn công phải đoán mù
> toàn bộ, giống hệt lý do dùng `secrets.compare_digest` thay vì `==`.

---

### Câu 7 — Token bucket (CP3)

Với `capacity=10`, `refill_per_minute=10`: một client im lặng 10 phút rồi gửi
liên tiếp. Nó gửi được bao nhiêu request trước khi bị 429? Nếu bỏ đoạn
`min(capacity, ...)` trong `available()` thì con số đó thành bao nhiêu, và tại sao?

> Đã kiểm chứng bằng test thật (bắn 15 request liên tiếp với `capacity=10`,
> `refill_per_minute=10`): 10 request đầu trả `200`, 5 request cuối trả
> `429`. Với client im lặng 10 phút, lẽ ra nạp được `10 phút × 10 token/phút
> = 100` token, nhưng `min(capacity, tokens)` chặn ở 10 — xô đầy tối đa là
> `capacity`. Nên client gửi được đúng **10** request trước khi bị 429.
>
> Nếu bỏ `min(...)`: xô không còn chặn trên, token tích lũy tuyến tính theo
> thời gian im lặng — sau 10 phút sẽ có 100 token thật sự trong xô, và client
> gửi được **100** request liên tiếp trước khi cạn. Đây chính là lỗ hổng nêu
> trong bài: bỏ `min` thì một client im lặng đủ lâu (một ngày = 14.400 token
> với refill 10/phút) tích được lượng token khổng lồ và xả hết trong một
> giây — token bucket lúc đó không còn giới hạn tốc độ nữa.

---

### Câu 8 — Ngân sách theo ngày (CP3)

So sánh hạn mức $30/tháng với hạn mức $1/ngày cho cùng một client. Giả sử có sự
cố khiến một client gọi liên tục từ 2h sáng. Với mỗi cách, thiệt hại tối đa là
bao nhiêu và service tự hồi phục khi nào?

> Với hạn mức **$30/tháng**: `check()` chỉ chặn khi tổng chi tiêu trong cả
> tháng vượt $30, nên nếu sự cố xảy ra ngày đầu tháng, client có thể tiêu hết
> toàn bộ $30 chỉ trong một đêm rồi bị khoá — thiệt hại tối đa gần như bằng
> cả hạn mức, và service chỉ "hồi phục" (được gọi tiếp) khi sang tháng mới,
> tức có thể phải chờ gần 30 ngày không dùng được nếu sự cố xảy ra sớm trong
> tháng.
>
> Với hạn mức **$1/ngày** (key `spend:<client>:<YYYY-MM-DD>`): thiệt hại tối
> đa bị chặn ở đúng $1 vì key đổi mỗi ngày UTC. Sự cố lúc 2h sáng chạy tới
> khi `spent() >= budget` thì bị 402 ngay trong đêm đó — thiệt hại tối đa chỉ
> bằng 1/30 so với hạn mức tháng. Và tới 00:00 UTC hôm sau, key ngày mới tự
> sinh với giá trị 0, service tự phục vụ lại bình thường mà không cần ai can
> thiệp thủ công (không cần restart, không cần xoá key tay).

---

### Câu 9 — /healthz khác /readyz (CP4)

Nếu gộp hai endpoint làm một và cho nó kiểm tra Redis, chuyện gì xảy ra với cụm
3 container khi Redis mất kết nối 30 giây? Trả lời theo đúng thứ tự sự kiện.

> Thứ tự sự kiện nếu gộp `/healthz` và `/readyz` làm một, cùng kiểm tra Redis:
> 1. Redis mất kết nối.
> 2. Cả 3 container gọi `store.ping()` đều trả `False` → endpoint gộp trả 503.
> 3. Vì đây cũng là endpoint mà orchestrator dùng làm **liveness probe**, 503
>    bị hiểu là "process chết, cần restart" — cả 3 container cùng bị
>    **restart** gần như đồng thời, dù process Python hoàn toàn khoẻ mạnh,
>    chỉ có Redis là chết.
> 4. Trong lúc cả 3 container đang khởi động lại, không còn container nào
>    phục vụ request — service downtime toàn phần, dù trước đó chỉ là một sự
>    cố Redis 30 giây, có thể tự hồi phục.
> 5. Redis quay lại sau 30 giây, nhưng lúc này service vẫn đang trong chu kỳ
>    restart (thời gian khởi động lại, warm-up) — thời gian phục hồi thực tế
>    dài hơn hẳn 30 giây gốc.
>
> Tách riêng thì khác hẳn: `/healthz` không đụng Redis nên luôn trả 200,
> orchestrator không restart gì cả; chỉ `/readyz` báo 503, load balancer đơn
> giản rút 3 container khỏi vòng chia traffic (không restart), và ngay khi
> Redis sống lại, `/readyz` trả 200 lại, LB đưa traffic vào ngay — không tốn
> thời gian khởi động lại container nào.

---

### Câu 10 — Deploy thật (CP5)

Ghi lại **một** lỗi bạn gặp khi deploy lên cloud (build fail, health check
timeout, sai REDIS_URL, app không đọc `$PORT`...): thông báo lỗi là gì, bạn
tìm ra nguyên nhân bằng cách nào, và sửa ra sao?

> Lỗi thật gặp khi deploy lên Railway, đọc bằng `railway logs`:
> ```
> Error: Invalid value for '--port': '$PORT' is not a valid integer.
> ```
> App restart loop liên tục, không lên nổi. Nguyên nhân: file `railway.toml`
> có sẵn dòng `startCommand = "uvicorn app.main:app --host 0.0.0.0 --port
> $PORT"`. Railway chạy `startCommand` **không** qua shell (exec trực tiếp),
> nên `$PORT` không được shell nào interpolate — uvicorn nhận đúng chuỗi ký
> tự `$PORT` làm giá trị `--port`, không phải số cổng thật. Trong khi đó
> `CMD` trong `Dockerfile` đã viết đúng dạng `sh -c "... --port
> ${PORT:-8000}"` nên chạy `docker run` ở máy mình không hề gặp lỗi này —
> đây là lý do lỗi chỉ lộ ra khi deploy thật, không lộ khi test local.
> Sửa: xoá dòng `startCommand` khỏi `railway.toml`, để Railway dùng thẳng
> `CMD` đã đúng trong Dockerfile (nơi có `sh -c` để shell tự expand biến).
> Deploy lại, log hiện `Uvicorn running on http://0.0.0.0:8080` và
> `/healthz` trả `200` bình thường.
