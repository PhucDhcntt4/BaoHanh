# Tóm tắt hệ thống nhận diện sản phẩm

## 1. Mục tiêu

Hệ thống nhận ảnh khách gửi qua Telegram, tìm các sản phẩm giống nhất trong catalog rồi dùng Gemini xác minh lần cuối.

Thông tin như tên sản phẩm, giá, màu, size và tồn kho luôn được lấy từ PostgreSQL, không suy đoán từ hình ảnh.

## 2. Quy trình hoạt động

```text
Ảnh khách gửi
→ xác định vùng sản phẩm
→ crop ảnh
→ OpenCLIP tạo vector embedding
→ pgvector tìm ảnh giống nhất
→ chọn tối đa 3 mã sản phẩm
→ dHash kiểm tra ảnh gần-trùng
→ Gemini xác minh sản phẩm
→ lấy thông tin từ PostgreSQL
→ gửi kết quả và album qua Telegram
```

## 3. Vai trò của từng thành phần

### OpenCLIP

Chuyển ảnh thành vector gồm 512 số.

Các ảnh có hình dáng và đặc điểm gần giống nhau sẽ có vector nằm gần nhau.

### pgvector

So sánh vector ảnh khách với vector ảnh catalog bằng cosine similarity:

```text
similarity = 1 - cosine_distance
```

Điểm càng cao thì ảnh càng giống nhau. Tuy nhiên, điểm `0.70` không có nghĩa là đúng 70%.

### Crop ảnh

Ảnh cần được crop để loại bỏ người mẫu, chữ quảng cáo, phông nền hoặc các vật thể không liên quan.

Embedding sau đó tập trung vào chính sản phẩm như thân túi, quai, khóa, đế hoặc gót giày.

### dHash

dHash kiểm tra ảnh khách có gần như trùng hoàn toàn với ảnh catalog hay không.

Nếu khoảng cách hash nhỏ hơn hoặc bằng `3/64`, hệ thống có thể chọn trực tiếp sản phẩm đó mà không cần Gemini.

dHash chỉ phù hợp với cùng một ảnh bị resize hoặc nén, không phù hợp với ảnh chụp ở góc khác.

### Gemini

Gemini nhận ảnh khách và tối đa ba mã sản phẩm ứng viên.

Gemini so sánh các chi tiết như:

- Hình dáng sản phẩm.
- Quai, khóa, logo.
- Đường may.
- Mũi, đế, gót.
- Chi tiết trang trí.

Gemini chỉ được chọn mã nằm trong danh sách ứng viên và phải có độ tin cậy từ `0.90` trở lên.

## 4. Cách chọn ứng viên

pgvector trả về danh sách ảnh, nhưng một sản phẩm có thể có nhiều ảnh.

Hệ thống sẽ:

1. Giữ ảnh tốt nhất của từng `product_code + color`.
2. Giữ biến thể có điểm cao nhất của từng `product_code`.
3. Chọn tối đa ba mã sản phẩm để Gemini xác minh.

Ví dụ:

```text
FE04 Vàng  0.7378
FE04 Vàng  0.7100
TM32 Bò    0.6811
KQ01 Kem   0.6366
```

Sau khi gom:

```text
FE04  0.7378
TM32  0.6811
KQ01  0.6366
```

## 5. Quy tắc quyết định của vector

### Không tìm thấy

```text
top_similarity < 0.35
```

Hệ thống không trả sản phẩm cho khách.

### Cần Gemini xác minh

```text
top_similarity >= 0.35
```

Nhưng chưa đủ điều kiện tự động chấp nhận.

### Kết quả rất mạnh

```text
top_similarity >= 0.96
margin >= 0.08
```

Trong đó:

```text
margin = điểm mã đứng đầu - điểm mã đứng thứ hai
```

Hiện tại, kể cả trường hợp này hệ thống vẫn dùng Gemini để đảm bảo an toàn.

## 6. Dữ liệu embedding

Mỗi ảnh catalog được lưu trong bảng `product_images`.

Vector của ảnh được lưu trong `product_image_embeddings`, gồm:

- Model OpenCLIP.
- Bộ pretrained.
- Vector 512 chiều.
- Checksum SHA-256 của ảnh.

Checksum chỉ dùng để kiểm tra ảnh có thay đổi hay không, không dùng để đo độ giống nhau.

Ảnh khách và ảnh catalog bắt buộc phải sử dụng cùng model và cùng cách preprocess.

## 7. Khi có sản phẩm mới

```text
Lấy dữ liệu Shopify
→ import vào PostgreSQL
→ tải ảnh về máy
→ tạo embedding cho ảnh mới
→ lưu vector vào pgvector
```

Lệnh tạo embedding:

```powershell
python -m app.scripts.build_product_image_embeddings
```

Ảnh không thay đổi và đã có đúng embedding sẽ được bỏ qua.

## 8. Chỉ số cần theo dõi

Không nên chỉ nhìn mã đứng đầu. Cần đánh giá:

- **Top-1 accuracy:** Mã đúng có đứng đầu không.
- **Recall@3:** Mã đúng có nằm trong ba ứng viên không.
- **Gemini accuracy:** Gemini có chọn đúng trong shortlist không.
- **False positive:** Hệ thống có trả nhầm sản phẩm ngoài catalog không.
- **Thời gian xử lý:** Embedding, vector search, Gemini và tổng thời gian.

## 9. Nguyên tắc quan trọng

- Vector search chỉ dùng để tìm ứng viên, không phải kết luận cuối.
- Không xem similarity là xác suất.
- Không giảm ngưỡng chỉ để một ảnh test nhận diện thành công.
- Gemini không được tự tạo mã sản phẩm ngoài shortlist.
- Không suy đoán giá, màu, size hoặc tồn kho từ ảnh.
- Không chắc chắn thì nên từ chối thay vì trả sai.
- Nếu vector search gặp lỗi, hệ thống vẫn giữ luồng Gemini cũ làm fallback.
