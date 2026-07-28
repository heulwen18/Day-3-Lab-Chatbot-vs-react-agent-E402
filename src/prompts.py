"""
🧠 PROMPTS & SAFEGUARDS (Dành cho Role 3: Prompt & Safeguard Engineer)
Đề tài: Trợ lý Tư vấn Lá số tử vi và Độ tương thích
"""

# =============================================================================
# ⚠️ FAILURE MODES (Mốc 1) — bám theo src/tools.py (AstroAgent)
# Tool trả chuỗi bắt đầu bằng "TOOL_ERROR: ..." khi lỗi (không crash).
# Tools: validate_birth_info, interpret_tuvi_overview, interpret_study_and_career,
#        interpret_relationships, interpret_yearly_fortune, interpret_compatibility
# =============================================================================
FAILURE_MODES = [
    {
        "id": "FM01",
        "tool": "validate_birth_info / mọi interpret_*",
        "name": "Ngày sinh sai định dạng hoặc không tồn tại",
        "trigger": "_validate_birth_info → datetime.strptime('%d/%m/%Y')",
        "example": "birth_date='32/13/2000' hoặc '12-08-2003'",
        "observation": "TOOL_ERROR: Ngày sinh không hợp lệ. Vui lòng nhập theo định dạng DD/MM/YYYY.",
        "agent_should": "Hỏi lại DD/MM/YYYY; không bịa lá số.",
    },
    {
        "id": "FM02",
        "tool": "validate_birth_info / mọi interpret_*",
        "name": "Giờ sinh sai định dạng",
        "trigger": "_validate_birth_info → datetime.strptime('%H:%M')",
        "example": "birth_time='25:99' hoặc '2 giờ chiều'",
        "observation": "TOOL_ERROR: Giờ sinh không hợp lệ. Vui lòng nhập theo định dạng HH:MM.",
        "agent_should": "Hỏi lại HH:MM (ví dụ 14:30).",
    },
    {
        "id": "FM03",
        "tool": "validate_birth_info / mọi interpret_*",
        "name": "Ngày sinh nằm trong tương lai",
        "trigger": "parsed_date > today",
        "example": "birth_date='01/01/2099'",
        "observation": "TOOL_ERROR: Ngày sinh không được nằm trong tương lai.",
        "agent_should": "Báo lỗi và yêu cầu ngày sinh thật.",
    },
    {
        "id": "FM04",
        "tool": "validate_birth_info / mọi interpret_*",
        "name": "Giới tính không hợp lệ",
        "trigger": "_normalize_gender → None",
        "example": "gender='khác' / 'unknown'",
        "observation": "TOOL_ERROR: Giới tính không hợp lệ. Vui lòng nhập 'nam' hoặc 'nữ'.",
        "agent_should": "Chỉ chấp nhận nam/nữ (hoặc male/female); hỏi lại.",
    },
    {
        "id": "FM05",
        "tool": "validate_birth_info / mọi interpret_*",
        "name": "Loại lịch không hợp lệ",
        "trigger": "_normalize_calendar_type → None",
        "example": "calendar_type='julian'",
        "observation": "TOOL_ERROR: Loại lịch không hợp lệ. Vui lòng nhập 'solar' hoặc 'lunar'.",
        "agent_should": "Hướng dẫn dùng solar (dương) hoặc lunar (âm).",
    },
    {
        "id": "FM06",
        "tool": "validate_birth_info / mọi interpret_*",
        "name": "Nơi sinh trống",
        "trigger": "birth_place.strip() == ''",
        "example": "birth_place='   '",
        "observation": "TOOL_ERROR: Nơi sinh không được để trống.",
        "agent_should": "Hỏi nơi sinh (ví dụ: Hà Nội, Việt Nam).",
    },
    {
        "id": "FM07",
        "tool": "_get_client / mọi interpret_* gọi Gemini",
        "name": "Thiếu API key Gemini",
        "trigger": "Không có GEMINI_API_KEY / GOOGLE_API_KEY trong .env",
        "example": "File .env trống hoặc key sai tên biến",
        "observation": "TOOL_ERROR: Không thể gọi Gemini API: Không tìm thấy GEMINI_API_KEY...",
        "agent_should": "Final Answer: hệ thống tạm thời không luận giải được; không bịa nội dung tử vi.",
    },
    {
        "id": "FM08",
        "tool": "_call_gemini",
        "name": "Gemini API lỗi mạng / quota / model",
        "trigger": "Exception khi client.models.generate_content",
        "example": "Hết quota, model 'gemini-3.6-flash' không tồn tại, timeout",
        "observation": "TOOL_ERROR: Không thể gọi Gemini API: <chi tiết>",
        "agent_should": "Thông báo lỗi kỹ thuật; gợi ý thử lại sau; không hallucination.",
    },
    {
        "id": "FM09",
        "tool": "_call_gemini",
        "name": "Gemini trả về rỗng",
        "trigger": "response.text falsy",
        "example": "Model bị safety block / empty completion",
        "observation": "TOOL_ERROR: Gemini không trả về nội dung luận giải.",
        "agent_should": "Xin lỗi + đề nghị rút gọn câu hỏi / thử lại.",
    },
    {
        "id": "FM10",
        "tool": "interpret_compatibility",
        "name": "Thiếu dữ liệu một bên khi ghép đôi",
        "trigger": "Agent gọi tool khi mới có person_1 hoặc truyền thiếu tham số",
        "example": "Chỉ có ngày sinh A; thiếu birth_time/gender/place của B",
        "observation": "TOOL_ERROR: ... (validate person_2 thất bại) hoặc TypeError sai số tham số",
        "agent_should": "Không gọi interpret_compatibility; hỏi đủ 8 trường của cả hai người.",
    },
    {
        "id": "FM11",
        "tool": "Agent / parser (Role 4)",
        "name": "Gọi sai tên tool hoặc sai số tham số",
        "trigger": "Action không khớp AVAILABLE_TOOLS",
        "example": "Action: get_horoscope[...] hoặc interpret_tuvi_overview[] thiếu args",
        "observation": "LỖI tool không tồn tại / TypeError → nên map thành TOOL_ERROR",
        "agent_should": "Sửa Action đúng tên trong AVAILABLE_TOOLS hoặc Final Answer xin thêm thông tin.",
    },
    {
        "id": "FM12",
        "tool": "Mọi interpret_* + Guardrail prompt",
        "name": "User ép ngoài phạm vi an toàn",
        "trigger": "user_question yêu cầu chẩn đoán bệnh / đầu tư / ngày mất",
        "example": "Tử vi bảo tôi có nên dừng thuốc không?",
        "observation": "Gemini (nếu tuân SYSTEM_INSTRUCTION) từ chối; Agent phải ưu tiên an toàn",
        "agent_should": "Final Answer từ chối + nhắc kết quả chỉ tham khảo/giải trí.",
    },
    {
        "id": "FM13",
        "tool": "ReAct loop (MAX_ITERATIONS)",
        "name": "Lặp Thought–Action khi validate liên tục fail",
        "trigger": "User cố tình nhập sai liên tục / Agent retry cùng lỗi",
        "example": "Câu bẫy ngày 32/13 + giờ 99:99",
        "observation": "Nhiều TOOL_ERROR liên tiếp",
        "agent_should": "Dừng ở MAX_ITERATIONS → GUARDRAIL_FALLBACK_MESSAGE, không loop vô hạn.",
    },
]


# Baseline Chatbot Prompt (Chỉ dùng LLM, KHÔNG có Tool — Mốc 2)
# Protocol: system prompt + user message → 1 LLM call → final response
# KHÔNG được: gọi tool, nhúng sẵn kết quả tool, khẳng định đã lập/luận lá số xong.
CHATBOT_BASELINE_PROMPT = """Bạn là Chatbot baseline tư vấn về tử vi Việt Nam và độ tương thích (giải trí).

### Vai trò
- Trả lời bằng tiếng Việt, thân thiện, ngắn gọn, có cấu trúc rõ.
- Chỉ dựa trên kiến thức tổng quát có sẵn trong mô hình (1 lần trả lời, không có công cụ).

### Giới hạn BẮT BUỘC (để so sánh công bằng với ReAct Agent)
- Bạn KHÔNG có tool, database, hay engine lập lá số.
- Bạn KHÔNG được giả vờ đã:
  - kiểm tra / chuẩn hóa ngày–giờ–nơi sinh,
  - gọi API luận giải,
  - lập lá số cá nhân hóa theo đủ thông tin sinh,
  - tính điểm tương hợp chính xác giữa hai người.
- Khi người dùng đưa ngày sinh / giờ sinh / nơi sinh và yêu cầu luận giải cụ thể
  (tổng quan, học tập–nghề nghiệp, tình cảm, vận năm, ghép đôi):
  hãy thừa nhận rõ: "Mình là chatbot thuần, không có công cụ luận giải cá nhân hóa",
  rồi chỉ đưa gợi ý mang tính tham khảo chung (nếu phù hợp), hoặc hướng dẫn họ
  cần cung cấp đủ thông tin cho hệ thống có tool.

### Được trả lời trực tiếp (không cần tool)
- Khái niệm tử vi mang tính giải trí là gì.
- Tử vi giải trí khác gì tư vấn tâm lý / y tế / tài chính chuyên nghiệp.
- Câu hỏi kiến thức chung, chính sách an toàn, lời khuyên mềm không gắn lá số cá nhân.

### Phạm vi an toàn
- Không chẩn đoán bệnh, không dự đoán ngày mất / tai nạn / tuổi thọ.
- Không quyết định đầu tư, nghề nghiệp, hôn nhân thay người dùng.
- Không khẳng định sự kiện chắc chắn sẽ xảy ra.
- Không nói kết quả được khoa học chứng minh.
- Cuối câu trả lời (khi nói về tử vi): "Kết quả chỉ mang tính tham khảo và giải trí."

### Giọng điệu
Ấm áp, vui vẻ, không phán xét, trung thực về giới hạn của chatbot thuần.
"""


# ReAct Agent Prompt — ép Thought -> Action (Mốc 3)
# Lưu ý: tên tool phải khớp với Role 2 trong src/tools.py
REACT_SYSTEM_PROMPT = """Bạn là ReAct Agent — Trợ lý Tư vấn Lá số tử vi và Độ tương thích.

Bạn CÓ thể dùng công cụ. Không được bịa Observation. Chỉ kết luận sau khi có kết quả tool (nếu câu hỏi cần dữ liệu).

### Danh sách công cụ
1. get_zodiac_sign[birth_date]: Tra cứu cung Hoàng đạo từ ngày sinh (định dạng DD/MM/YYYY).
2. get_horoscope[sign]: Lấy luận giải / vận trình tham khảo cho một cung (ví dụ: 'Bạch Dương', 'Kim Ngưu').
3. calculate_compatibility[sign_a, sign_b]: Tính điểm và nhận xét độ tương thích giữa hai cung.

### QUY TẮC BẮT BUỘC — định dạng từng dòng
Thought: Suy luận bước tiếp theo (thiếu gì? cần tool nào?).
Action: tên_công_cụ[tham_số]
(Dừng lại, chờ hệ thống trả Observation — KHÔNG tự viết Observation.)

Khi đã đủ thông tin:
Thought: Tôi đã có đủ thông tin để trả lời.
Final Answer: Câu trả lời hoàn chỉnh bằng tiếng Việt cho người dùng.

### Guardrails nội dung
- Chỉ dùng đúng 3 tool ở trên; không gọi tool không tồn tại.
- Thiếu ngày sinh hoặc thiếu một bên khi tính tương thích → hỏi lại, đừng đoán.
- Observation chứa "LỖI" → giải thích lỗi, thử sửa tham số 1 lần hoặc Final Answer xin thêm thông tin.
- Câu hỏi y tế / tài chính / pháp lý → Final Answer từ chối + nhắc tính giải trí.
- Không cam kết tương lai chắc chắn; luôn nói kết quả mang tính tham khảo.

BẮT ĐẦU:
"""


# 🛡️ GUARDRAILS CONFIGURATION (PHANH AN TOÀN)
MAX_ITERATIONS = 5  # Đủ cho: cung A → cung B → tương thích (+ 1 lần retry lỗi)
TIMEOUT_SECONDS = 10  # Timeout cho mỗi lần gọi tool

# Câu trả lời khi chạm phanh (Role 4 nên dùng khi vượt MAX_ITERATIONS)
GUARDRAIL_FALLBACK_MESSAGE = (
    "🛡️ Hệ thống đã đạt giới hạn số bước suy luận an toàn. "
    "Mình chưa đủ dữ liệu để kết luận chắc chắn. "
    "Bạn thử gửi lại ngày sinh theo DD/MM/YYYY "
    "(và ngày sinh của đối phương nếu hỏi tương thích) nhé — "
    "nội dung tử vi chỉ mang tính giải trí."
)
