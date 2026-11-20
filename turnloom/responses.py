"""Translate between Turnloom's conversation and OpenAI's Responses API.

Turnloom keeps one conversation format, the chat-completions message list. This module turns that list
into Responses ``input`` items for a request and a Responses reply back into the chat shape, so the
loop, folding and the transcript are the same for both transports.

Requests are stateless (``store: false``). A reply's reasoning items arrive encrypted; they stay on the
assistant message that came with them, under ``response_items``, and go back in the same order on
later requests so the model continues from its earlier reasoning instead of repeating it.
"""

from __future__ import annotations

from typing import Any

# The order of a reply's output items: reasoning items whole, and a marker for the text and for each
# call, whose content lives in the message's own ``content`` and ``tool_calls``.
ITEMS = "response_items"


def url(base: str) -> str:
    return base.removesuffix("/chat/completions").removesuffix("/responses") + "/responses"


def text(content: Any) -> str:
    if isinstance(content, list):
        return "".join(part.get("text") or "" for part in content if isinstance(part, dict))
    return content if isinstance(content, str) else ""


def plain(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The conversation without stored Responses items, for anything that speaks or stores the chat shape."""
    if not any(ITEMS in message for message in messages):
        return messages
    return [{key: value for key, value in message.items() if key != ITEMS} for message in messages]


def items(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The conversation as Responses input items.

    A call is sent only with the result that directly follows its message, and a result only with its
    call, since the API refuses either one alone. Reasoning is sent only ahead of the output it led to.
    """
    sent: list[dict[str, Any]] = []
    waiting: set[str] = set()
    for index, message in enumerate(messages):
        role = message.get("role")
        if role == "tool":
            if message.get("tool_call_id") in waiting:
                waiting.discard(message["tool_call_id"])
                sent.append({"type": "function_call_output", "call_id": message["tool_call_id"],
                             "output": text(message.get("content"))})
            continue
        waiting = set()
        if role != "assistant":
            # Standing instructions go first in the input as a developer message, where they are cached.
            sent.append({"role": "developer" if role == "system" else "user", "content": text(message.get("content"))})
            continue
        answered = set()
        for later in messages[index + 1:]:
            if later.get("role") != "tool":
                break
            answered.add(later.get("tool_call_id"))
        calls = {call["id"]: call.get("function") or {} for call in message.get("tool_calls") or []
                 if call.get("id") in answered}
        waiting = set(calls)
        said = text(message.get("content"))
        turn: list[dict[str, Any]] = []
        # A message from the chat transport has no stored order: text first, then its calls.
        order = [*(message.get(ITEMS) or []), {"type": "message"}, *({"type": "function_call", "call_id": key} for key in calls)]
        for item in order:
            kind = item.get("type")
            if kind == "reasoning":
                turn.append(item)
            elif kind == "message" and said:
                turn.append({"role": "assistant", "content": said, **({"phase": item["phase"]} if item.get("phase") else {})})
                said = ""
            elif kind == "function_call" and item.get("call_id") in calls:
                function = calls.pop(item["call_id"])
                turn.append({"type": "function_call", "call_id": item["call_id"], "name": function.get("name"),
                             "arguments": function.get("arguments") or "{}"})
        while turn and turn[-1].get("type") == "reasoning":
            turn.pop()
        sent += turn
    return sent


def request(model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], max_tokens: int,
            effort: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {"model": model, "input": items(messages), "max_output_tokens": max(16, max_tokens),
                            "store": False, "include": ["reasoning.encrypted_content"]}
    if tools:
        # Without strict set to false the API tries strict mode, which Turnloom's optional parameters do not fit.
        body["tools"] = [{"type": "function", **tool["function"], "strict": False} for tool in tools]
        body["tool_choice"] = "auto"
    if effort:
        body["reasoning"] = {"effort": effort}
    return body


def usage(reported: Any) -> dict[str, Any] | None:
    """Responses token counts under the chat-completions names, with their details."""
    if not isinstance(reported, dict):
        return None
    mapped = {"prompt_tokens": reported.get("input_tokens"), "completion_tokens": reported.get("output_tokens")}
    for name, source in (("total_tokens", "total_tokens"), ("prompt_tokens_details", "input_tokens_details"),
                         ("completion_tokens_details", "output_tokens_details")):
        if reported.get(source) is not None:
            mapped[name] = reported[source]
    return mapped


def reply(response: dict[str, Any]) -> dict[str, Any]:
    """A Responses reply in the chat-completions shape the loop consumes."""
    if response.get("error"):
        return {"error": response["error"]}
    said, calls, kept = [], [], []
    for item in response.get("output") or []:
        kind = item.get("type") if isinstance(item, dict) else None
        if kind == "message":
            said.append("".join(part.get("text") or part.get("refusal") or ""
                                for part in item.get("content") or [] if isinstance(part, dict)))
            kept.append({"type": "message", **({"phase": item["phase"]} if item.get("phase") else {})})
        elif kind == "function_call":
            call_id = item.get("call_id") or item.get("id")
            calls.append({"id": call_id, "type": "function",
                          "function": {"name": item.get("name"), "arguments": item.get("arguments") or "{}"}})
            kept.append({"type": "function_call", "call_id": call_id})
        elif kind == "reasoning" and item.get("encrypted_content"):
            # Without its encrypted content a reasoning item cannot be read back by a stateless request.
            kept.append(item)
    message: dict[str, Any] = {"role": "assistant", "content": "\n\n".join(part for part in said if part) or None}
    if calls:
        message["tool_calls"] = calls
    if any(item["type"] == "reasoning" or item.get("phase") for item in kept):
        message[ITEMS] = kept
    reason = (response.get("incomplete_details") or {}).get("reason")
    finish = "length" if reason == "max_output_tokens" else reason or ("tool_calls" if calls else "stop")
    return {"choices": [{"message": message, "finish_reason": finish}], "usage": usage(response.get("usage"))}
