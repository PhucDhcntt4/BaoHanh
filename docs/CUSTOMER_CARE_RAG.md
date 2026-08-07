# RAG cho chính sách và chăm sóc khách hàng

## Phạm vi

RAG chỉ lưu tài liệu chính sách, FAQ và hướng dẫn CSKH. Giá, màu, size, tồn
kho, đơn hàng và trạng thái bảo hành vẫn phải lấy từ PostgreSQL hoặc API
nghiệp vụ.

Luồng xử lý:

```text
Câu hỏi khách hàng
→ AI provider chọn tool
→ Embedding provider tạo vector câu hỏi
→ pgvector tìm các đoạn liên quan
→ tool trả nội dung và nguồn
→ AI provider soạn câu trả lời từ nội dung đã tìm được
```

## 1. Chuẩn bị tài liệu

Đặt file `.md` hoặc `.txt` vào thư mục theo category:

```text
knowledge/
├── warranty/chinh_sach_bao_hanh.md
├── returns/chinh_sach_doi_tra.md
├── exchange/doi_size_doi_mau.md
├── customer_care/huong_dan_bao_quan.md
└── faq/cau_hoi_thuong_gap.md
```

Nên dùng tiêu đề Markdown để bộ chia đoạn giữ đúng ngữ cảnh:

```markdown
# Chính sách bảo hành

## Bong keo

Nội dung chính thức...

## Gãy gót

Nội dung chính thức...
```

Không đưa dữ liệu khách hàng, số điện thoại hoặc đơn hàng vào các file này.

## 2. Cấu hình

Thêm vào `.env`:

```dotenv
RAG_ENABLED=false
RAG_EMBEDDING_PROVIDER=auto
RAG_EMBEDDING_MODEL=
RAG_EMBEDDING_DIMENSION=768
RAG_TOP_K=5
RAG_MIN_SIMILARITY=0.45
RAG_MAX_CONTEXT_CHARS=6000
RAG_CHUNK_SIZE=1200
RAG_CHUNK_OVERLAP=180
```

Schema đang dùng `vector(768)`, vì vậy không đổi
`RAG_EMBEDDING_DIMENSION` nếu chưa tạo migration mới.

`RAG_EMBEDDING_PROVIDER=auto` dùng cùng provider với `AI_PROVIDER`:

```text
AI_PROVIDER=gemini  → gemini-embedding-001
AI_PROVIDER=openai → text-embedding-3-small
```

Để chọn riêng embedding provider, đặt `gemini` hoặc `openai` thay cho
`auto`. Để `RAG_EMBEDDING_MODEL` trống sẽ dùng model mặc định phù hợp. Có thể
điền model embedding khác của provider nếu model đó hỗ trợ output 768 chiều.

Ví dụ Gemini trả lời và OpenAI tạo embedding:

```dotenv
AI_PROVIDER=gemini
RAG_EMBEDDING_PROVIDER=openai
RAG_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_API_KEY=...
```

## 3. Kiểm tra và import

Kiểm tra cách chia đoạn, không gọi API và không ghi database:

```powershell
python -m app.scripts.import_knowledge_to_db --source knowledge --dry-run
```

Nạp tất cả tài liệu:

```powershell
python -m app.scripts.import_knowledge_to_db --source knowledge
```

Nạp file chính sách cũ và gán category:

```powershell
python -m app.scripts.import_knowledge_to_db `
  --source prompts/warranty_policy.txt `
  --category warranty
```

Importer tự tạo schema. File không thay đổi sẽ được bỏ qua. Khi cần tạo lại
toàn bộ embedding của nguồn đã chọn, thêm `--force`.

Khi đổi embedding provider hoặc model, bắt buộc chạy lại với `--force`. Không
thể so sánh vector tài liệu của model cũ với vector câu hỏi của model mới.

## 4. Kiểm tra truy xuất

```powershell
python -m app.scripts.query_knowledge `
  "Giày bị bong keo có được bảo hành không?" `
  --category warranty
```

Kết quả gồm `content`, danh sách `sources` và điểm `similarity`. Terminal của
ứng dụng cũng ghi log dạng:

```text
RAG SEARCH status=found categories=['warranty'] top=0.8123
```

## 5. Bật RAG

Sau khi import và kiểm tra kết quả đúng, đổi:

```dotenv
RAG_ENABLED=true
```

Sau đó khởi động lại Uvicorn. Khi RAG được bật,
`search_warranty_policy` đọc các đoạn trong PostgreSQL và không còn cần đọc
`warranty_policy.txt` lúc bot trả lời. Khi RAG tắt, tool vẫn dùng file cũ để
có thể rollback an toàn.

## 6. Quy tắc cần có trong customer_agent.txt

```text
- Khi khách hỏi bảo hành, đổi trả, đổi size hoặc đổi mẫu, bắt buộc gọi
  search_warranty_policy.
- Khi khách hỏi FAQ, cửa hàng, bảo quản hoặc hướng dẫn chăm sóc khách hàng,
  gọi search_customer_care_knowledge.
- Chỉ trả lời từ content mà công cụ trả về, không tự bổ sung điều kiện.
- Nếu success=false, nói rằng hiện em chưa có thông tin chính thức và không
  suy đoán câu trả lời.
```

## 7. Điều chỉnh độ chính xác

- Kết quả không liên quan xuất hiện nhiều: tăng `RAG_MIN_SIMILARITY` từng bước
  0.05.
- Không tìm thấy đoạn đúng: giảm ngưỡng từng bước 0.05 hoặc chỉnh lại tiêu đề
  và nội dung tài liệu.
- Câu trả lời thiếu một phần: tăng `RAG_TOP_K`, tối đa khoảng 8.
- Sau khi thay embedding model, bắt buộc import lại với `--force`; không trộn
  vector của hai model.
