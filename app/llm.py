from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

import httpx

from .settings import settings


class LLMError(RuntimeError):
    pass


class OpenAICompatibleLLM:
    def __init__(self) -> None:
        self.base = settings.llm_base_url.rstrip("/")
        self.model = settings.llm_model
        self.headers = {
            "Authorization": f"Bearer {settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        # Safe, structured trace. Never stores reasoning text itself.
        self.last_trace: list[dict[str, Any]] = []

    @staticmethod
    def _extract_json_text(content: Any) -> str:
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict):
                    parts.append(str(part.get("text", "")))
                else:
                    parts.append(str(part))
            return "".join(parts)
        return str(content or "")

    @staticmethod
    def _json_loads_resilient(text: str) -> dict[str, Any]:
        text = text.strip()
        if not text:
            raise LLMError("LLM returned an empty response")

        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        try:
            value = json.loads(text)
            if not isinstance(value, dict):
                raise LLMError("LLM JSON root must be an object")
            return value
        except json.JSONDecodeError:
            try:
                from json_repair import repair_json

                repaired = json.loads(repair_json(text))
                if isinstance(repaired, dict):
                    return repaired
            except Exception:
                pass

            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    value = json.loads(text[start : end + 1])
                    if isinstance(value, dict):
                        return value
                except Exception:
                    pass
            raise

    @staticmethod
    def _message_value(message: dict[str, Any], key: str, default: Any = None) -> Any:
        value = message.get(key, default)
        return value if value is not None else default

    def _is_official_deepseek(self) -> bool:
        base = self.base.lower()
        return "api.deepseek.com" in base or "deepseek.com" in base

    def _apply_thinking_options(self, payload: dict[str, Any], *, tools_enabled: bool) -> None:
        mode = settings.llm_thinking_mode.lower().strip()
        enable = mode == "enabled" or (mode == "auto" and self._is_official_deepseek())
        if not enable:
            return
        payload["reasoning_effort"] = settings.llm_reasoning_effort
        payload["extra_body"] = {"thinking": {"type": "enabled"}}

    def _record_trace(self, data: dict[str, Any], *, round_idx: int, phase: str) -> dict[str, Any]:
        choice = (data.get("choices") or [{}])[0] or {}
        message = choice.get("message") or {}
        usage = data.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        meta = {
            "phase": phase,
            "round": round_idx,
            "finish_reason": choice.get("finish_reason"),
            "content_chars": len(self._extract_json_text(message.get("content", ""))),
            "reasoning_chars": len(str(message.get("reasoning_content") or "")),
            "tool_call_count": len(message.get("tool_calls") or []),
            "completion_tokens": usage.get("completion_tokens"),
            "reasoning_tokens": details.get("reasoning_tokens"),
            "total_tokens": usage.get("total_tokens"),
        }
        self.last_trace.append(meta)
        return meta

    @staticmethod
    def _assistant_message_for_history(message: dict[str, Any]) -> dict[str, Any]:
        """Preserve the full DeepSeek thinking/tool-call assistant turn.

        DeepSeek requires reasoning_content to be replayed on subsequent requests
        when tools are used. content must remain non-null for compatibility.
        """
        assistant: dict[str, Any] = {
            "role": "assistant",
            "content": OpenAICompatibleLLM._extract_json_text(message.get("content", "")),
        }
        reasoning = message.get("reasoning_content")
        if reasoning is not None:
            assistant["reasoning_content"] = str(reasoning)
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            assistant["tool_calls"] = tool_calls
        return assistant

    async def _post(self, client: httpx.AsyncClient, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            r = await client.post(f"{self.base}/chat/completions", headers=self.headers, json=payload, timeout=20*60)
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"LLM HTTP {exc.response.status_code}: {exc.response.text[:500]}") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc
        try:
            return r.json()
        except ValueError as exc:
            raise LLMError(f"LLM returned non-JSON HTTP response: {r.text[:500]}") from exc

    async def chat_json(self, system: str, user: str, max_tokens: int | None = None) -> dict[str, Any]:
        self.last_trace = []
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "max_tokens": max_tokens or settings.llm_max_tokens,
            "response_format": {"type": "json_object"},
        }
        if settings.llm_seed is not None:
            payload["seed"] = settings.llm_seed
        self._apply_thinking_options(payload, tools_enabled=False)
        timeout = httpx.Timeout(settings.llm_timeout_seconds, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            data = await self._post(client, payload)
        meta = self._record_trace(data, round_idx=1, phase="json")
        message = (data.get("choices") or [{}])[0].get("message") or {}
        content = self._extract_json_text(message.get("content", ""))
        try:
            return self._json_loads_resilient(content)
        except Exception as exc:
            raise LLMError(f"LLM returned invalid JSON ({meta}): {exc}") from exc

    async def _finalize_without_tools(
        self,
        client: httpx.AsyncClient,
        messages: list[dict[str, Any]],
        user_instruction: str,
        *,
        round_idx: int,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
    ) -> dict[str, Any]:
        final_messages = list(messages)
        final_messages.append({"role": "user", "content": user_instruction})
        payload = {
            "model": self.model,
            "messages": final_messages,
            "temperature": 0,
            "max_tokens": settings.llm_terminal_max_tokens,
            "response_format": {"type": "json_object"},
        }
        if settings.llm_seed is not None:
            payload["seed"] = settings.llm_seed
        self._apply_thinking_options(payload, tools_enabled=False)

        data = await self._post(client, payload)
        meta = self._record_trace(data, round_idx=round_idx, phase="terminal")
        message = (data.get("choices") or [{}])[0].get("message") or {}
        text = self._extract_json_text(message.get("content", "")).strip()

        if not text:
            if on_event:
                await on_event("empty_response", {"round": round_idx, "reason": "terminal_content_empty", "meta": meta})
            # One and only one compact no-tools retry.
            retry_messages = list(final_messages)
            retry_messages.append({
                "role": "user",
                "content": (
                    "Return the required JSON object now. Do not use tools. "
                    "Use only the evidence already collected. Every template field must be present."
                ),
            })
            retry_payload = dict(payload)
            retry_payload["messages"] = retry_messages
            retry_payload["max_tokens"] = max(settings.llm_terminal_max_tokens, settings.llm_max_tokens)
            retry_data = await self._post(client, retry_payload)
            retry_meta = self._record_trace(retry_data, round_idx=round_idx, phase="terminal_recovery")
            retry_message = (retry_data.get("choices") or [{}])[0].get("message") or {}
            text = self._extract_json_text(retry_message.get("content", "")).strip()
            if not text:
                raise LLMError(
                    "LLM returned an empty final answer after one bounded no-tools recovery. "
                    f"initial={meta}; recovery={retry_meta}"
                )
            if on_event:
                await on_event("final_response", {"round": round_idx, "terminal_recovery": True, "empty_recovery": True, "meta": retry_meta})
        elif on_event:
            await on_event("final_response", {"round": round_idx, "terminal": True, "meta": meta})

        return self._json_loads_resilient(text)

    async def chat_with_tools(
        self,
        system: str,
        user: str,
        tools: list[dict[str, Any]],
        executor,
        max_rounds: int = 8,
        max_tool_output_chars: int = 24000,
        max_repeated_tool_calls: int = 1,
        max_tool_calls: int | None = None,
        round_delay_seconds: float = 0.0,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Bounded OpenAI-compatible MCP tool loop with DeepSeek thinking replay.

        Key invariants:
        - Every assistant turn is replayed with content, reasoning_content (when present), and tool_calls.
        - The finalization request contains no tools, preventing terminal tool loops.
        - A single bounded terminal recovery is allowed for empty content.
        - Safe response metadata is emitted without exposing model reasoning text.
        """
        self.last_trace = []
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        seen_calls: dict[str, int] = {}
        total_tool_calls = 0
        timeout = httpx.Timeout(settings.llm_timeout_seconds, connect=10.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            for round_idx in range(max_rounds):
                current_round = round_idx + 1
                if on_event is not None:
                    await on_event("round_start", {"round": current_round, "max_rounds": max_rounds})

                force_final = current_round >= max_rounds or (
                    max_tool_calls is not None and total_tool_calls >= max_tool_calls
                )

                # Never send tool_choice=none to the provider at the terminal boundary.
                # Some DeepSeek V4 integrations reject tool_choice in thinking mode.
                if force_final:
                    return await self._finalize_without_tools(
                        client,
                        messages,
                        (
                            "The browser-tool budget is exhausted. Do not use tools. "
                            "Return ONLY the final JSON object requested by the system. "
                            "Use only evidence already collected; do not invent missing values or selectors."
                        ),
                        round_idx=current_round,
                        on_event=on_event,
                    )

                payload = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0,
                    "max_tokens": settings.llm_max_tokens,
                    "tools": tools,
                    "parallel_tool_calls": False,
                }
                # DeepSeek thinking/tool mode works best without tool_choice; generic providers can opt in.
                if not self._is_official_deepseek():
                    payload["tool_choice"] = "auto"
                self._apply_thinking_options(payload, tools_enabled=True)

                data = await self._post(client, payload)
                meta = self._record_trace(data, round_idx=current_round, phase="tools")
                if on_event is not None:
                    await on_event("response_meta", meta)

                message = (data.get("choices") or [{}])[0].get("message") or {}
                tool_calls = message.get("tool_calls") or []
                content = self._extract_json_text(message.get("content", ""))
                reasoning_content = str(message.get("reasoning_content") or "")
                finish_reason = ((data.get("choices") or [{}])[0] or {}).get("finish_reason")

                # Terminal answer with actual content.
                if not tool_calls and content.strip():
                    if on_event is not None:
                        await on_event("final_response", {"round": current_round, "meta": meta})
                    return self._json_loads_resilient(content)

                # Preserve the assistant turn BEFORE adding tool results. This is mandatory for
                # DeepSeek thinking mode: reasoning_content is part of the conversation state.
                assistant_message = self._assistant_message_for_history(message)
                messages.append(assistant_message)

                # No tool call + no content means the model stopped without a usable final answer.
                # Do not burn another browser round. Finalize once without tools.
                if not tool_calls:
                    if on_event is not None:
                        await on_event(
                            "empty_response",
                            {
                                "round": current_round,
                                "reason": "provider_returned_no_tool_no_content",
                                "finish_reason": finish_reason,
                                "meta": meta,
                            },
                        )
                    return await self._finalize_without_tools(
                        client,
                        messages,
                        (
                            "The previous assistant turn completed without a usable answer. "
                            "Do not use tools. Return ONLY the final JSON object requested by the system. "
                            "Use the evidence already collected. Every template field must be present; "
                            "use null/found=false when the page evidence is insufficient."
                        ),
                        round_idx=current_round,
                        on_event=on_event,
                    )

                # Execute bounded tool calls.
                for tc in tool_calls:
                    fn = tc.get("function", {}) or {}
                    name = str(fn.get("name") or "")
                    raw_args = fn.get("arguments") or "{}"
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except json.JSONDecodeError as exc:
                        args = {}
                        result: Any = {"error": f"Invalid tool arguments JSON: {exc}"}
                    else:
                        if not isinstance(args, dict):
                            args = {}
                        signature = json.dumps(
                            {"name": name, "args": args},
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        seen_calls[signature] = seen_calls.get(signature, 0) + 1
                        if seen_calls[signature] > max_repeated_tool_calls:
                            result = {
                                "error": "Repeated identical tool call blocked. Use the evidence already collected and finish the JSON result."
                            }
                        elif max_tool_calls is not None and total_tool_calls >= max_tool_calls:
                            result = {
                                "error": "Tool budget exhausted. Stop inspecting and return the final JSON using the evidence already collected."
                            }
                            if on_event is not None:
                                await on_event("tool_blocked", {"round": current_round, "name": name, "reason": "tool_budget_exhausted"})
                        else:
                            total_tool_calls += 1
                            if on_event is not None:
                                await on_event(
                                    "tool_call",
                                    {
                                        "round": current_round,
                                        "name": name,
                                        "arguments": args,
                                        "tool_call_index": total_tool_calls,
                                    },
                                )
                            try:
                                result = await executor(name, args)
                            except Exception as exc:
                                result = {"error": str(exc)}
                            if on_event is not None:
                                await on_event(
                                    "tool_result",
                                    {
                                        "round": current_round,
                                        "name": name,
                                        "result_preview": str(result)[:800],
                                        "tool_call_index": total_tool_calls,
                                    },
                                )

                    if isinstance(result, str):
                        tool_content = result
                    else:
                        try:
                            tool_content = json.dumps(result, ensure_ascii=False)
                        except TypeError:
                            tool_content = str(result)
                    if len(tool_content) > max_tool_output_chars:
                        tool_content = tool_content[:max_tool_output_chars] + "\n...[tool output truncated]"
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": tool_content,
                    })

                if round_delay_seconds > 0:
                    await asyncio.sleep(round_delay_seconds)

        raise LLMError("LLM tool-calling loop exceeded the maximum number of rounds")
