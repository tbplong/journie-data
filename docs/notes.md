# Ghi chú Run2 → Run5

Run2 dùng `reinforced-v1`; Run5, dùng `semantic-v2`. Cả hai chạy trên model Gemini `gemini-3.5-flash-lite`, 120 testcase × 3 lượt.


**Thay đổi prompt:** bỏ schema lặp trong prompt nhưng giữ native structured output; bổ sung quy tắc về grounding, ngân sách minimum/cap/ngoại tệ, hard/soft, thời gian qua đêm, giữ nguyên tên và khử atom trùng. Không dùng full JSON output mẫu.

**Kỹ thuật:** **Instruction Structuring** để group các quy tắc, **Semantic Examples** để minh họa cách diễn giải; **Self-Verification** để kiểm tra ngầm trước khi trả kết quả.

| run              | provider | model                 | prompt_version | prompt_chars | requests | prompt_tokens | tokens_per_input_char | chars_per_prompt_token | max_abs_residual |
|------------------|----------|-----------------------|----------------|-------------:|---------:|--------------:|----------------------:|-----------------------:|-----------------:|
| run2             | gemini   | gemini-3.5-flash-lite | reinforced-v1  |         9081 |      360 |        2396.9 |                0.2659 |                   3.79 |             17.7 |
| run5-semantic-v2 | gemini   | gemini-3.5-flash-lite | semantic-v2    |         9986 |      360 |        2300.9 |                0.2659 |                   4.34 |             17.7 |