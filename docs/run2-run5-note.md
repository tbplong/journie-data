# Notes

Run2 dùng `reinforced-v1`; Run5, dùng `semantic-v2`. Cả hai chạy trên model Gemini `gemini-3.5-flash-lite`, 120 testcase × 3 lượt.


**Thay đổi prompt:** bỏ schema lặp trong prompt nhưng giữ native structured output; bổ sung quy tắc về grounding, ngân sách minimum/cap/ngoại tệ, hard/soft, thời gian qua đêm, giữ nguyên tên và khử atom trùng. Không dùng full JSON output mẫu.

**Kỹ thuật:** **Instruction Structuring** để group các quy tắc, **Semantic Examples** để minh họa cách diễn giải; **Self-Verification** để kiểm tra ngầm trước khi trả kết quả.

