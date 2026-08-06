Kết quả này đi qua 3 lớp khác nhau: **Gemini phân loại ảnh → OpenCLIP tính độ giống → Gemini xác minh mã cuối cùng**.

## 1. Phân loại ảnh và vùng sản phẩm

```text
intent=product_lookup
product_type=DEP CAO GOT (WDC)
bbox=[360, 200, 770, 840]
```

Gemini kết luận:

- Đây là ảnh khách muốn tìm sản phẩm: `product_lookup`.
- Loại sản phẩm: `DEP CAO GOT (WDC)`.
- Loại này phải tồn tại trong danh sách `product_type` lấy từ PostgreSQL.

`bbox` có định dạng:

```text
[y_min, x_min, y_max, x_max]
```

Theo thang từ `0–1000`, không phải pixel:

```text
y: 360 → 770 = từ 36% đến 77% chiều cao
x: 200 → 840 = từ 20% đến 84% chiều rộng
```

Hệ thống cộng thêm khoảng đệm 12% xung quanh sản phẩm rồi crop.

```text
original_bytes=239469
recognition_bytes=108281
```

Ảnh gốc khoảng 239 KB, sau crop và nén JPEG còn khoảng 108 KB. OpenCLIP sẽ phân tích ảnh đã crop này.

## 2. Cách tính điểm vector

OpenCLIP chuyển ảnh khách và từng ảnh catalog thành vector 512 chiều. Các vector được chuẩn hóa rồi pgvector tính cosine:

```text
similarity = 1 - cosine_distance
```

Kết quả:

| Vị trí | Mã    | Màu của ảnh tham chiếu | Similarity |
| -----: | ----- | ---------------------- | ---------: |
|      1 | D81V9 | Nâu                    |     0.7304 |
|      2 | D32I6 | Kem                    |     0.6711 |
|      3 | DTHC2 | Đỏ                     |     0.5641 |

Điểm `0.7304` không có nghĩa là “đúng 73,04%”. Nó chỉ cho biết vector ảnh D81V9 gần ảnh khách hơn hai mã còn lại.

Trước khi lấy ba kết quả này, hệ thống:

1. Chỉ tìm sản phẩm `ACTIVE`.
2. Ưu tiên nhóm `DEP CAO GOT (WDC)`.
3. Lấy nhiều ảnh gần nhất từ pgvector.
4. Giữ ảnh tốt nhất của mỗi `mã + màu`.
5. Giữ kết quả tốt nhất của mỗi mã.
6. Chọn tối đa 3 mã.

## 3. Cách tính `margin`

```text
top = 0.7304
second = 0.6711
margin = top - second
       = 0.7304 - 0.6711
       = 0.0593
```

Margin cho biết mã đứng đầu cách mã thứ hai bao xa.

- Margin lớn: mã đầu nổi bật hơn.
- Margin nhỏ: hai mã khá gần nhau, chưa nên kết luận chỉ dựa vào vector.

Trong trường hợp này:

```text
margin=0.0593
```

Trong khi ngưỡng tự động mạnh là:

```text
VECTOR_MIN_MARGIN=0.08
```

Vì `0.0593 < 0.08`, D81V9 chưa đủ nổi bật để vector tự kết luận.

## 4. Vì sao là `needs_verification`?

Code có ba trạng thái:

```text
top < VECTOR_MIN_SIMILARITY
→ no_match

top >= 0.96 và margin >= 0.08
→ auto_accept

Các trường hợp còn lại
→ needs_verification
```

Kết quả hiện tại:

```text
top=0.7304
margin=0.0593
status=needs_verification
```

Nó không đạt điều kiện mạnh:

```text
0.7304 < 0.96
0.0593 < 0.08
```

Vì vậy phải gửi ba ứng viên cho Gemini kiểm tra.

Lưu ý: mặc định trong code, `VECTOR_MIN_SIMILARITY=0.80`. Nhưng log này vẫn là `needs_verification` ở mức `0.7304`, chứng tỏ `.env` của bạn đang đặt ngưỡng nhỏ hơn hoặc bằng `0.7304`, khả năng là:

```env
VECTOR_MIN_SIMILARITY=0.35
```

## 5. Gemini xác minh mã cuối cùng

```text
exact=True
code=D81V9
confidence=1.000
```

Gemini được nhận:

- Ảnh khách đã crop.
- Ảnh khách gốc.
- Tối đa hai ảnh tham chiếu cho mỗi mã.
- Danh sách mã được phép chọn: D81V9, D32I6, DTHC2.

Gemini so sánh các chi tiết:

```text
braided strap     = quai tết
wide textured strap = quai bản rộng có họa tiết
square toe        = mũi vuông
spherical heel    = gót hình cầu
```

Nó kết luận các đặc điểm này khớp với D81V9.

`confidence=1.000` ở đây là confidence do Gemini tự trả về, không phải kết quả của công thức cosine. Nó cũng không phải xác suất chính xác 100%, nhưng đã vượt ngưỡng hệ thống yêu cầu:

```text
confidence >= 0.90
```

Đây không phải kết quả dHash. Nếu dHash khớp, reason sẽ có dạng:

```text
Near-duplicate catalog image matched by dHash
```

## 6. Vì sao ứng viên là màu Nâu nhưng album đầu tiên lại là màu Kem?

Vector tìm thấy ảnh gần nhất:

```text
D81V9 - Nâu - 0.7304
```

Nhưng sau khi xác minh, hệ thống chỉ giữ:

```text
product_code=D81V9
```

Thông tin màu `Nâu` không được truyền tiếp sang phần gửi album.

Log:

```text
color=None
first_url=...D81V9-kem-1.jpg
```

Có nghĩa là:

- Khách không ghi màu trong caption.
- Câu trả lời AI cũng không chỉ định màu.
- Hệ thống lấy 4 ảnh mặc định đầu tiên của D81V9.
- Ảnh đầu tiên trong PostgreSQL đang là màu Kem.

Nhận diện mã sản phẩm vẫn đúng, nhưng album có thể không đúng màu của ảnh khách. Nếu muốn gửi đúng album màu Nâu, cần truyền thêm `matched_color` từ kết quả vector đến `_send_product_photos()`.

## 7. Thời gian xử lý

```text
download=3.040s
ai=10.928s
total=17.764s
```

Trong đó:

- Tải ảnh Telegram: `3.040s`.
- Gemini phân loại, xác minh và tạo câu trả lời: `10.928s`.
- Phần còn lại khoảng `3.796s`: crop, OpenCLIP, pgvector và gửi Telegram.

Ba lần gửi Telegram là:

1. Tin nhắn thông tin sản phẩm: `0.894s`.
2. Album 4 ảnh: `0.963s`.
3. Câu hỏi tư vấn tiếp: `0.930s`.
