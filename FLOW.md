# Flow kích hoạt bảo hành qua tin nhắn

## Flow nghiệp vụ

```mermaid
flowchart TD
    A([Khách hàng gửi tin nhắn]) --> B{Đúng yêu cầu/cú pháp<br/>kích hoạt bảo hành?}

    B -- Không --> B1[Agent hướng dẫn khách gửi yêu cầu<br/>kích hoạt bảo hành]
    B1 --> Z([Chờ tin nhắn tiếp theo])

    B -- Có --> C[Agent phản hồi ngay:<br/>Dạ, anh/chị vui lòng chờ trong giây lát]
    C --> D{Tin nhắn có<br/>số điện thoại?}

    D -- Không --> D1[Đề nghị cung cấp số điện thoại đặt hàng<br/>và mã đơn nếu có]
    D1 --> Z

    D -- Có --> E[Chuẩn hóa số điện thoại<br/>và mã đơn nếu có]
    E --> F[Tra cứu đơn hàng theo số điện thoại<br/>và mã đơn nếu khách đã cung cấp]
    F --> G{Kết quả tra cứu}

    G -- Không có đơn --> G1[Thông báo chưa tìm thấy đơn]
    G1 --> G2[Đề nghị kiểm tra lại số điện thoại<br/>và gửi mã đơn nếu có]
    G2 --> Z

    G -- Có nhiều đơn --> H[Thông báo đã tìm thấy nhiều đơn<br/>và liệt kê các mã đơn]
    H --> H1[Đề nghị khách chọn mã đơn<br/>cần kích hoạt]
    H1 --> Z

    G -- Có đúng 1 đơn --> I[Thông báo đã tìm thấy đơn<br/>và đang tự động kích hoạt]
    I --> J{Đơn đủ điều kiện<br/>kích hoạt?}

    J -- Không --> J1[Thông báo chưa thể kích hoạt<br/>và chuyển nhân viên hỗ trợ]
    J1 --> K([Kết thúc])

    J -- Đã kích hoạt trước đó --> J2[Thông báo bảo hành đã được<br/>kích hoạt trước đó]
    J2 --> K

    J -- Có --> L[Agent gọi chức năng<br/>kích hoạt bảo hành]
    L --> M{Kích hoạt thành công?}

    M -- Không --> M1[Thông báo hệ thống chưa thể hoàn tất<br/>mời thử lại hoặc chờ nhân viên hỗ trợ]
    M1 --> K

    M -- Có --> N[Lưu thông tin bảo hành<br/>và cập nhật trạng thái đơn]
    N --> O[Thông báo kích hoạt thành công<br/>kèm mã đơn hàng]
    O --> K
```

## Trình tự giữa các thành phần

```mermaid
sequenceDiagram
    actor KH as Khách hàng
    participant AG as Warranty Agent
    participant OD as Dịch vụ đơn hàng
    participant WR as Dịch vụ bảo hành

    KH->>AG: Yêu cầu kích hoạt bảo hành
    AG-->>KH: Vui lòng chờ trong giây lát

    alt Chưa có số điện thoại
        AG-->>KH: Xin số điện thoại và mã đơn nếu có
    else Có số điện thoại
        AG->>OD: Tìm theo SĐT + mã đơn (nếu có)
        OD-->>AG: Kết quả tra cứu

        alt Không tìm thấy đơn
            AG-->>KH: Xin kiểm tra lại SĐT và gửi mã đơn nếu có
        else Tìm thấy nhiều đơn
            AG-->>KH: Liệt kê mã đơn và yêu cầu chọn một đơn
        else Tìm thấy duy nhất một đơn
            AG-->>KH: Đã tìm thấy đơn, đang tự động kích hoạt
            AG->>WR: Kích hoạt bảo hành cho mã đơn
            WR->>OD: Cập nhật trạng thái bảo hành của đơn
            WR-->>AG: Kết quả kích hoạt

            alt Kích hoạt thành công
                AG-->>KH: Thông báo thành công kèm mã đơn
            else Đã kích hoạt trước đó
                AG-->>KH: Thông báo đã kích hoạt trước đó
            else Kích hoạt thất bại
                AG-->>KH: Mời thử lại hoặc chờ nhân viên hỗ trợ
            end
        end
    end
```

## Trạng thái hội thoại đề xuất

| Trạng thái | Ý nghĩa |
|---|---|
| `waiting_request` | Chờ đúng yêu cầu kích hoạt bảo hành |
| `waiting_phone` | Chờ khách cung cấp số điện thoại |
| `searching_order` | Đang tra cứu đơn hàng |
| `waiting_order_code` | Có nhiều đơn, chờ khách chọn mã đơn |
| `activating` | Đã xác định duy nhất một đơn và đang kích hoạt |
| `activated` | Kích hoạt thành công |
| `already_activated` | Đơn đã được kích hoạt trước đó |
| `need_human_support` | Không đủ điều kiện hoặc hệ thống gặp lỗi |

## Nguyên tắc bắt buộc

1. Không thông báo tìm thấy đơn trước khi tra cứu thành công.
2. Không kích hoạt khi chưa xác định duy nhất một đơn hàng.
3. Nếu khách đã gửi số điện thoại hoặc mã đơn trong lịch sử hội thoại thì không hỏi lại.
4. Chỉ thông báo thành công sau khi dữ liệu bảo hành đã được lưu và đơn hàng đã được cập nhật.
5. Mỗi mã đơn chỉ được kích hoạt một lần.
6. Phản hồi “chờ trong giây lát” phải được gửi trước khi bắt đầu tác vụ tra cứu dài.
