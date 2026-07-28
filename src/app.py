"""Core application for comparing a baseline chatbot with a ReAct agent."""

from __future__ import annotations

import argparse
import ast
import inspect
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv

# Allow ``python src/app.py`` to import modules next to this file.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

from prompts import MAX_ITERATIONS
from providers import get_llm_provider
from tools import AVAILABLE_TOOLS

try:
    from prompts import GUARDRAIL_FALLBACK_MESSAGE
except ImportError:
    GUARDRAIL_FALLBACK_MESSAGE = (
        "Hệ thống đã đạt giới hạn số bước suy luận an toàn. "
        "Mình chưa đủ dữ liệu để đưa ra kết luận."
    )

load_dotenv()

DISCLAIMER = "Kết quả chỉ mang tính tham khảo và giải trí."

CHATBOT_BASELINE_PROMPT = f"""Bạn là chatbot tư vấn tử vi Việt Nam.
Trả lời bằng tiếng Việt dựa trên kiến thức chung và tuyệt đối không gọi công cụ.
Nếu người dùng cần phân tích cá nhân hóa, hãy nói rõ bạn không có dữ liệu đã được
kiểm chứng. Không dự đoán bệnh tật, ngày mất, tai nạn hoặc tuổi thọ; không đưa ra
quyết định y tế, tài chính, pháp lý, nghề nghiệp hay hôn nhân thay người dùng.
Không nói tử vi đã được khoa học chứng minh. Luôn kết thúc bằng câu: {DISCLAIMER}
"""

REACT_SYSTEM_PROMPT = f"""Bạn là AstroAgent, một ReAct Agent phân tích tử vi và
độ tương thích ở mức tham khảo. Trả lời bằng tiếng Việt.

Mỗi lượt chỉ được trả về một trong hai định dạng:
Thought: suy luận ngắn gọn
Action: tool_name["arg1", "arg2"]

hoặc:
Thought: đã đủ thông tin
Final Answer: câu trả lời hoàn chỉnh

Không tự viết Observation. Thiếu dữ liệu thì hỏi lại, không đoán. Không tự nhận đã
an chính xác toàn bộ sao nếu không có engine chuyên dụng. Không dự đoán bệnh tật,
ngày mất, tai nạn hoặc tuổi thọ; không chẩn đoán y khoa hay quyết định thay người
dùng. Mọi Final Answer phải kết thúc bằng: {DISCLAIMER}
"""

ASTROLOGY_TEST_CASES = [
    {
        "id": 1,
        "question": "Tử vi có phải là phương pháp được khoa học chứng minh không?",
    },
    {
        "id": 2,
        "question": (
            "Hãy luận giải tử vi tổng quan cho nữ sinh ngày 12/08/2003 lúc "
            "14:30 tại Hà Nội, dùng dương lịch."
        ),
    },
    {
        "id": 3,
        "question": (
            "Tôi là nam, sinh ngày 05/11/2001 lúc 08:15 tại Đà Nẵng theo dương "
            "lịch, hiện học công nghệ thông tin. Hãy phân tích định hướng nghề nghiệp."
        ),
    },
    {
        "id": 4,
        "question": (
            "Phân tích độ tương thích giữa nữ sinh 12/08/2003 lúc 14:30 tại Hà Nội "
            "và nam sinh 05/11/2001 lúc 08:15 tại Đà Nẵng; cả hai dùng dương lịch."
        ),
    },
    {
        "id": 5,
        "question": (
            "Tôi là nữ, sinh ngày 32/13/2026 lúc 25:99 tại Hà Nội. Hãy cho biết "
            "chính xác khi nào tôi sẽ mắc bệnh nặng."
        ),
    },
]

ACTION_RE = re.compile(
    r"^\s*Action\s*:\s*([A-Za-z_]\w*)\s*(\[[^\n]*\]|\([^\n]*\))\s*$",
    re.IGNORECASE | re.MULTILINE,
)
FINAL_RE = re.compile(
    r"^\s*Final\s+Answer\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE | re.DOTALL
)


@dataclass
class AgentResult:
    """Observable result returned by one ReAct run."""

    answer: str
    iterations: int
    tool_calls: int
    stopped_by_guardrail: bool = False
    trace: list[dict[str, str]] = field(default_factory=list)


def load_test_cases() -> list[dict[str, Any]]:
    """Return the astrology cases owned by this self-contained application."""
    return [dict(case) for case in ASTROLOGY_TEST_CASES]


class OfflineAstrologyProvider:
    """Deterministic ReAct provider used when ``LLM_PROVIDER=mock``."""

    model_name = "Offline Astrology Mock"

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        text = prompt.lower()
        is_baseline = "tuyệt đối không gọi công cụ" in system_prompt.lower()

        if is_baseline:
            if "khoa học chứng minh" in text:
                return f"Tử vi chưa được khoa học chứng minh là phương pháp dự đoán. {DISCLAIMER}"
            if "bệnh nặng" in text:
                return (
                    "Mình không thể dự đoán bệnh tật. Bạn nên trao đổi với chuyên gia "
                    f"y tế khi có lo ngại về sức khỏe. {DISCLAIMER}"
                )
            return (
                "Mình không có công cụ kiểm chứng dữ liệu sinh nên không thể đưa ra "
                f"phân tích cá nhân hóa có căn cứ. {DISCLAIMER}"
            )

        if "observation:" in text:
            observation = prompt.rsplit("Observation:", 1)[-1].strip()
            if observation.startswith(("TOOL_ERROR", "FORMAT_ERROR")):
                return (
                    "Thought: Observation báo lỗi nên tôi phải dừng thay vì bịa kết quả.\n"
                    "Final Answer: Dữ liệu hoặc công cụ hiện chưa hợp lệ. Vui lòng "
                    f"kiểm tra thông tin và thử lại. {DISCLAIMER}"
                )
            return f"Thought: Tôi đã có dữ liệu từ tool.\nFinal Answer: {observation}"

        if "khoa học chứng minh" in text:
            return (
                "Thought: Đây là kiến thức chung, không cần tool.\n"
                "Final Answer: Tử vi chưa được khoa học chứng minh là phương pháp "
                f"dự đoán và không thay thế tư vấn chuyên môn. {DISCLAIMER}"
            )
        if "bệnh nặng" in text:
            return (
                "Thought: Đây là yêu cầu dự đoán y khoa bị cấm và dữ liệu sinh không hợp lệ.\n"
                "Final Answer: Mình không thể dự đoán bệnh tật hoặc tuổi thọ. Ngày "
                "32/13/2026 và giờ 25:99 cũng không hợp lệ; hãy tham khảo chuyên gia "
                f"y tế nếu bạn lo về sức khỏe. {DISCLAIMER}"
            )
        if "độ tương thích" in text:
            return (
                "Thought: Cần phân tích dữ liệu sinh của cả hai người.\n"
                "Action: interpret_compatibility[\"12/08/2003\", \"14:30\", \"nữ\", "
                "\"Hà Nội\", \"05/11/2001\", \"08:15\", \"nam\", \"Đà Nẵng\", "
                "\"solar\", \"Phân tích độ tương thích\"]"
            )
        if "công nghệ thông tin" in text:
            return (
                "Thought: Cần tool phân tích học tập và sự nghiệp.\n"
                "Action: interpret_study_and_career[\"05/11/2001\", \"08:15\", "
                "\"nam\", \"Đà Nẵng\", \"solar\", \"công nghệ thông tin\"]"
            )
        if "tử vi tổng quan" in text:
            return (
                "Thought: Cần tool luận giải tổng quan theo thông tin sinh.\n"
                "Action: interpret_tuvi_overview[\"12/08/2003\", \"14:30\", \"nữ\", "
                "\"Hà Nội\", \"solar\", \"Luận giải tổng quan\"]"
            )
        return (
            "Thought: Chưa đủ dữ liệu sinh bắt buộc.\n"
            "Final Answer: Vui lòng cung cấp ngày, giờ, giới tính, nơi sinh và loại "
            f"lịch. {DISCLAIMER}"
        )


def _tool_contract() -> str:
    """Describe the live registry so stale prompt tool names cannot be executed."""
    lines = []
    for name, function in AVAILABLE_TOOLS.items():
        summary = inspect.getdoc(function) or "Không có mô tả."
        summary = summary.splitlines()[0]
        lines.append(f"- {name}{inspect.signature(function)}: {summary}")
    return "\n".join(lines)


def _agent_system_prompt() -> str:
    return f"""{REACT_SYSTEM_PROMPT.rstrip()}

### TOOL REGISTRY THỰC TẾ (ưu tiên tuyệt đối nếu danh sách phía trên bị cũ)
{_tool_contract()}

Chỉ được gọi tool trong TOOL REGISTRY THỰC TẾ.
Action chấp nhận một trong hai dạng:
- Action: tool_name["arg1", "arg2"]
- Action: tool_name[{{"parameter_name": "value"}}]
Mỗi phản hồi chỉ có một Action hoặc một Final Answer, không tự tạo Observation.
"""


def run_baseline_chatbot(user_query: str, provider, *, verbose: bool = True) -> str:
    """Make exactly one LLM call and never expose tools to the baseline."""
    if verbose:
        print(f"\n[CHATBOT BASELINE] Câu hỏi: {user_query}")
    response = provider.generate(user_query, system_prompt=CHATBOT_BASELINE_PROMPT)
    response = str(response or "").strip() or "Chatbot không trả về nội dung."
    if verbose:
        print(f"Chatbot trả lời:\n{response}")
    return response


def _parse_arguments(raw_arguments: str) -> tuple[list[Any], dict[str, Any]]:
    """Parse action arguments without evaluating executable Python code."""
    opening, closing = raw_arguments[0], raw_arguments[-1]
    if (opening, closing) not in {("[", "]"), ("(", ")")}:
        raise ValueError("tham số phải nằm trong [] hoặc ().")

    content = raw_arguments[1:-1].strip()
    if not content:
        return [], {}

    try:
        value = ast.literal_eval(f"[{content}]")
    except (SyntaxError, ValueError) as error:
        raise ValueError("tham số không đúng cú pháp chuỗi/list/dict Python.") from error

    if len(value) == 1 and isinstance(value[0], dict):
        return [], value[0]
    return value, {}


def _parse_response(response: str) -> tuple[str, Any]:
    final_match = FINAL_RE.search(response)
    if final_match:
        return "final", final_match.group(1).strip()

    action_match = ACTION_RE.search(response)
    if action_match:
        args, kwargs = _parse_arguments(action_match.group(2))
        return "action", (action_match.group(1), args, kwargs)

    raise ValueError("phản hồi phải chứa đúng 'Action:' hoặc 'Final Answer:'.")


def _offline_tool_result(name: str, args: list[Any], kwargs: dict[str, Any]) -> str:
    """Produce a grounded-looking demo Observation without external API calls."""
    values = kwargs or {str(index): value for index, value in enumerate(args)}
    if name == "interpret_compatibility":
        body = (
            "Hai người có thể bổ trợ nhau khi giao tiếp thẳng thắn. Khác biệt trong "
            "cách ra quyết định có thể gây hiểu lầm, vì vậy nên thống nhất kỳ vọng và "
            "không dùng kết quả này để quyết định mối quan hệ."
        )
    elif name == "interpret_study_and_career":
        body = (
            "Điểm mạnh tham khảo gồm tư duy phân tích và khả năng học qua thực hành. "
            "Nên đánh giá nghề nghiệp bằng năng lực, sở thích và trải nghiệm thực tế, "
            "không dựa riêng vào tử vi."
        )
    elif name == "interpret_relationships":
        body = "Nên giao tiếp trực tiếp, lắng nghe và tôn trọng ranh giới của nhau."
    elif name == "interpret_yearly_fortune":
        body = "Nên ưu tiên kế hoạch linh hoạt, phát triển kỹ năng và quyết định dựa trên dữ liệu."
    else:
        body = (
            "Bản mô phỏng cho thấy xu hướng chủ động học hỏi và phản tỉnh. Không nên "
            "suy diễn nội dung này thành dự đoán chắc chắn về tương lai."
        )
    return f"OFFLINE_MOCK_RESULT: {body}\nDữ liệu tool: {values}\n{DISCLAIMER}"


def _execute_tool(
    name: str,
    args: list[Any],
    kwargs: dict[str, Any],
    *,
    offline: bool = False,
) -> str:
    function = AVAILABLE_TOOLS.get(name)
    if function is None:
        valid_names = ", ".join(sorted(AVAILABLE_TOOLS))
        return f"TOOL_ERROR: Tool '{name}' không tồn tại. Tool hợp lệ: {valid_names}."

    try:
        inspect.signature(function).bind(*args, **kwargs)
    except TypeError as error:
        return f"TOOL_ERROR: Tham số của '{name}' không hợp lệ: {error}"

    if offline and name != "validate_birth_info":
        return _offline_tool_result(name, args, kwargs)

    try:
        result = function(*args, **kwargs)
        return str(result) if result is not None else "TOOL_ERROR: Tool không trả về dữ liệu."
    except Exception as error:  # Tool failures are observations, never app crashes.
        return f"TOOL_ERROR: Tool '{name}' gặp lỗi: {type(error).__name__}: {error}"


def run_react_agent(user_query: str, provider, *, verbose: bool = True) -> AgentResult:
    """Run the Thought -> Action -> Observation loop with bounded recovery."""
    transcript = f"Question: {user_query}"
    trace: list[dict[str, str]] = []
    seen_actions: dict[str, int] = {}
    tool_calls = 0

    if verbose:
        print(f"\n[REACT AGENT] Câu hỏi: {user_query}")

    for step in range(1, MAX_ITERATIONS + 1):
        if verbose:
            print(f"\n--- Vòng lặp ReAct ({step}/{MAX_ITERATIONS}) ---")

        raw_response = provider.generate(transcript, system_prompt=_agent_system_prompt())
        response = str(raw_response or "").strip()
        if verbose:
            print(response or "[Phản hồi rỗng]")

        event = {"step": str(step), "model_response": response}
        try:
            kind, payload = _parse_response(response)
        except ValueError as error:
            observation = f"FORMAT_ERROR: {error}"
            event["observation"] = observation
            trace.append(event)
            transcript += f"\n\n{response}\nObservation: {observation}"
            if verbose:
                print(f"Observation: {observation}")
            continue

        if kind == "final":
            event["final_answer"] = payload
            trace.append(event)
            return AgentResult(payload, step, tool_calls, trace=trace)

        tool_name, args, kwargs = payload
        action_key = json.dumps(
            [tool_name, args, kwargs], ensure_ascii=False, sort_keys=True, default=str
        )
        seen_actions[action_key] = seen_actions.get(action_key, 0) + 1

        if seen_actions[action_key] > 1:
            observation = (
                "TOOL_ERROR: Action này đã được thực hiện trước đó. "
                "Hãy dùng Observation đã có, đổi Action, hoặc trả Final Answer."
            )
        else:
            observation = _execute_tool(
                tool_name,
                args,
                kwargs,
                offline=isinstance(provider, OfflineAstrologyProvider),
            )
            tool_calls += 1

        event.update({"action": action_key, "observation": observation})
        trace.append(event)
        transcript += f"\n\n{response}\nObservation: {observation}"
        if verbose:
            print(f"Observation: {observation}")

    if verbose:
        print(f"\nGUARDRAIL: Đã đạt giới hạn {MAX_ITERATIONS} bước.")
        print(GUARDRAIL_FALLBACK_MESSAGE)
    return AgentResult(
        GUARDRAIL_FALLBACK_MESSAGE,
        MAX_ITERATIONS,
        tool_calls,
        stopped_by_guardrail=True,
        trace=trace,
    )


def _parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Demo Chatbot Baseline và ReAct Agent")
    parser.add_argument("--mode", choices=("baseline", "agent", "both"), default="both")
    parser.add_argument("--case", type=int, help="ID test case; mặc định chạy tất cả")
    return parser.parse_args()


def main() -> int:
    args = _parse_cli_args()
    provider_name = os.getenv("LLM_PROVIDER", "mock").strip().lower()
    provider = (
        OfflineAstrologyProvider()
        if provider_name == "mock"
        else get_llm_provider(provider_name)
    )
    tests = load_test_cases()
    if args.case is not None:
        tests = [case for case in tests if case.get("id") == args.case]
        if not tests:
            print(f"Không tìm thấy test case có id={args.case}.", file=sys.stderr)
            return 2

    model_name = getattr(provider, "model_name", "Offline Mock Mode")
    print("=" * 58)
    print("BÀI LAB 3: CHATBOT VS REACT AGENT")
    print("=" * 58)
    print(f"Provider: {provider.__class__.__name__} (Model: {model_name})")
    print(f"Đã tải {len(tests)} test case.")

    for case in tests:
        print(f"\n{'=' * 20} TEST CASE {case.get('id', '?')} {'=' * 20}")
        if args.mode in {"baseline", "both"}:
            run_baseline_chatbot(case["question"], provider)
        if args.mode in {"agent", "both"}:
            result = run_react_agent(case["question"], provider)
            print(
                f"Telemetry: iterations={result.iterations}, "
                f"tool_calls={result.tool_calls}, guardrail={result.stopped_by_guardrail}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
