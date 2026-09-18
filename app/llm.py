from __future__ import annotations

import json
from typing import Any

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

    @staticmethod
    def _extract_json_text(content: Any) -> str:
        if isinstance(content, list):
            return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        return str(content or "")

    @staticmethod
    def _json_loads_resilient(text: str) -> dict[str, Any]:
        text = text.strip()
        if not text:
            raise LLMError("LLM returned an empty response")

        # Strip markdown fences if the model ignored the JSON-only instruction.
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
            # Best-effort recovery for truncated/loosely formatted JSON.
            try:
                from json_repair import repair_json

                repaired = json.loads(repair_json(text))
                if isinstance(repaired, dict):
                    return repaired
            except Exception:
                pass
            # Last chance: isolate the largest JSON object-looking span.
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

    async def chat_json(self, system: str, user: str, max_tokens: int | None = None) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            # Keep discovery responses intentionally small. Values/evidence are capped in the prompt.
            "max_tokens": max_tokens or settings.llm_max_tokens,
            "response_format": {"type": "json_object"},
        }
        timeout = httpx.Timeout(settings.llm_timeout_seconds, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                r = await client.post(f"{self.base}/chat/completions", headers=self.headers, json=payload, timeout=20*60)
                r.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise LLMError(f"LLM HTTP {exc.response.status_code}: {exc.response.text[:500]}") from exc
            except httpx.HTTPError as exc:
                raise LLMError(f"LLM request failed: {exc}") from exc
            data = r.json()

        try:
            content = data["choices"][0]["message"]["content"]
            return self._json_loads_resilient(self._extract_json_text(content))
        except Exception as exc:
            finish = None
            try:
                finish = data["choices"][0].get("finish_reason")
            except Exception:
                pass
            raise LLMError(f"LLM returned invalid JSON (finish_reason={finish}): {exc}") from exc

    async def chat_with_tools(
        self,
        system: str,
        user: str,
        tools: list[dict[str, Any]],
        executor,
        max_rounds: int = 8,
        max_tool_output_chars: int = 24000,
        max_repeated_tool_calls: int = 1,
    ) -> dict[str, Any]:
        """Run a bounded OpenAI-compatible MCP tool loop.

        The loop is deliberately fail-closed: repeated identical tool calls are
        blocked and the final round forces the model to return its JSON result
        without requesting another tool. This prevents browser-tool loops.
        """
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        seen_calls: dict[str, int] = {}
        timeout = httpx.Timeout(settings.llm_timeout_seconds, connect=10.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            for round_idx in range(max_rounds):
                force_final = round_idx >= max_rounds - 1
                payload = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0,
                    "max_tokens": settings.llm_max_tokens,
                    "tools": tools,
                    "tool_choice": "none" if force_final else "auto",
                    "parallel_tool_calls": False,
                }
                if force_final:
                    messages.append({
                        "role": "user",
                        "content": (
                            "STOP USING TOOLS NOW. Return the final compact JSON object requested by the system. "
                            "Use only the evidence already collected. Do not request another tool call. "
                            "Every template field must be present; use null/found=false when unsupported."
                        ),
                    })
                    payload["messages"] = messages

                try:
                    r = await client.post(f"{self.base}/chat/completions", headers=self.headers, json=payload, timeout=20*60)
                    r.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise LLMError(f"LLM HTTP {exc.response.status_code}: {exc.response.text[:500]}") from exc
                except httpx.HTTPError as exc:
                    raise LLMError(f"LLM request failed: {exc}") from exc

                data = r.json()
                message = data.get("choices", [{}])[0].get("message", {})
                tool_calls = message.get("tool_calls") or []

                if not tool_calls:
                    content = message.get("content", "")
                    return self._json_loads_resilient(self._extract_json_text(content))

                assistant_message = {
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": tool_calls,
                }
                messages.append(assistant_message)

                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = str(fn.get("name") or "")
                    raw_args = fn.get("arguments") or "{}"
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except json.JSONDecodeError as exc:
                        result = {"error": f"Invalid tool arguments JSON: {exc}"}
                        args = {}
                    else:
                        if not isinstance(args, dict):
                            args = {}
                        signature = json.dumps({"name": name, "args": args}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                        seen_calls[signature] = seen_calls.get(signature, 0) + 1
                        if seen_calls[signature] > max_repeated_tool_calls:
                            result = {
                                "error": "Repeated identical tool call blocked. Use the evidence already collected and finish the JSON result."
                            }
                        else:
                            try:
                                result = await executor(name, args)
                            except Exception as exc:
                                result = {"error": str(exc)}

                    if isinstance(result, str):
                        content = result
                    else:
                        try:
                            content = json.dumps(result, ensure_ascii=False)
                        except TypeError:
                            content = str(result)
                    if len(content) > max_tool_output_chars:
                        content = content[:max_tool_output_chars] + "\n...[tool output truncated]"
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": content,
                    })

        raise LLMError("LLM tool-calling loop exceeded the maximum number of rounds")
