# Warranty Agent A0

MVP kích hoạt bảo hành qua tin nhắn sử dụng FastAPI, Gemini API và JSON.

## Luồng xử lý

1. Nhận tin nhắn.
2. Gemini nhận diện ý định, số điện thoại và mã đơn.
3. Tìm đơn trong `data/orders.json`.
4. Nếu có nhiều đơn, yêu cầu khách gửi mã đơn.
5. Kích hoạt và lưu vào `data/warranties.json`.
6. Cập nhật `warranty_status` trong đơn hàng.
7. Chống kích hoạt trùng theo mã đơn.

## Cài đặt

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Điền API key vào `.env`:

```env
GEMINI_API_KEY=your_key
GEMINI_MODEL=gemini-2.5-flash
```

Chạy server:

```bash
uvicorn app.main:app --reload
```

Swagger:

```text
http://127.0.0.1:8000/docs
```

## API kiểm thử

### Có số điện thoại và mã đơn

```json
{
  "customer_id": "facebook_user_001",
  "message": "Kích hoạt bảo hành đơn DH123456, số điện thoại 0912345678"
}
```

### Chỉ có số điện thoại và có nhiều đơn

```json
{
  "customer_id": "facebook_user_001",
  "message": "Kích hoạt bảo hành giúp mình, số 0912345678"
}
```

Endpoint:

```text
POST /api/warranty/message
```

## Kết nối Telegram

Thêm cấu hình vào `.env`:

```env
TELEGRAM_BOT_TOKEN=token_nhận_từ_BotFather
TELEGRAM_WEBHOOK_SECRET=chuỗi_bí_mật_ngẫu_nhiên
```

Khởi động API:

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Telegram chỉ gọi được webhook HTTPS công khai. Sau khi dùng ngrok,
Cloudflare Tunnel hoặc triển khai lên server, đăng ký webhook:

```powershell
$token = "TELEGRAM_BOT_TOKEN"
$secret = "TELEGRAM_WEBHOOK_SECRET"
$url = "https://your-domain.example/api/telegram/webhook"

Invoke-RestMethod `
  -Method Post `
  -Uri "https://api.telegram.org/bot$token/setWebhook" `
  -ContentType "application/json" `
  -Body (@{
    url = $url
    secret_token = $secret
    allowed_updates = @("message")
  } | ConvertTo-Json)
```

Kiểm tra trạng thái webhook:

```powershell
Invoke-RestMethod `
  -Uri "https://api.telegram.org/bot$token/getWebhookInfo"
```

Endpoint Telegram:

```text
POST /api/telegram/webhook
```

Bot hiện hỗ trợ tin nhắn văn bản, giữ tối đa 10 lượt user/model gần
nhất theo `chat_id`, bỏ qua update trùng và xác minh header webhook
nếu có `TELEGRAM_WEBHOOK_SECRET`.

## Lưu ý triển khai thực tế

Trong API A0, các phản hồi được trả về trong mảng `messages`. Khi kết nối Facebook,
Zalo hoặc Botcake, webhook nên gửi tin nhắn "chờ trong giây lát" trước, sau đó mới
gọi xử lý và gửi phản hồi kết quả.

JSON phù hợp để thử nghiệm. Khi chạy nhiều worker hoặc có nhiều yêu cầu đồng thời,
nên chuyển đơn hàng và bảo hành sang PostgreSQL.
