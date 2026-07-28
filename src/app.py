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

from prompts import CHATBOT_BASELINE_PROMPT, MAX_ITERATIONS, REACT_SYSTEM_PROMPT
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
    """Load and minimally validate ``config/test_cases.json``."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(project_root, "config", "test_cases.json")
    with open(config_path, encoding="utf-8") as file:
        test_cases = json.load(file)

    if not isinstance(test_cases, list):
        raise ValueError("config/test_cases.json phải chứa một JSON array.")
    for index, case in enumerate(test_cases, start=1):
        if not isinstance(case, dict) or not isinstance(case.get("question"), str):
            raise ValueError(f"Test case #{index} thiếu trường question dạng chuỗi.")
    return test_cases


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


def _execute_tool(name: str, args: list[Any], kwargs: dict[str, Any]) -> str:
    function = AVAILABLE_TOOLS.get(name)
    if function is None:
        valid_names = ", ".join(sorted(AVAILABLE_TOOLS))
        return f"TOOL_ERROR: Tool '{name}' không tồn tại. Tool hợp lệ: {valid_names}."

    try:
        inspect.signature(function).bind(*args, **kwargs)
    except TypeError as error:
        return f"TOOL_ERROR: Tham số của '{name}' không hợp lệ: {error}"

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
            observation = _execute_tool(tool_name, args, kwargs)
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
    provider = get_llm_provider()
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