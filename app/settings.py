from __future__ import annotations

import json
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    llm_base_url: str = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
    llm_api_key: str = os.getenv("LLM_API_KEY", "ollama")
    llm_model: str = os.getenv("LLM_MODEL", "deepseek-flash")
    llm_timeout_seconds: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "180"))
    llm_max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "6000"))
    llm_seed: int | None = int(os.getenv("LLM_SEED")) if os.getenv("LLM_SEED") not in (None, "") else None
    llm_codegen_max_tokens: int = int(os.getenv("LLM_CODEGEN_MAX_TOKENS", "3200"))
    llm_repair_max_tokens: int = int(os.getenv("LLM_REPAIR_MAX_TOKENS", "5000"))
    llm_discovery_max_rounds: int = int(os.getenv("LLM_DISCOVERY_MAX_ROUNDS", "8"))
    llm_discovery_max_tool_calls: int = int(os.getenv("LLM_DISCOVERY_MAX_TOOL_CALLS", "14"))
    llm_discovery_max_output_chars: int = int(os.getenv("LLM_DISCOVERY_MAX_OUTPUT_CHARS", "32000"))
    llm_repair_max_rounds: int = int(os.getenv("LLM_REPAIR_MAX_ROUNDS", "4"))
    llm_repair_max_tool_calls: int = int(os.getenv("LLM_REPAIR_MAX_TOOL_CALLS", "6"))
    llm_repair_max_output_chars: int = int(os.getenv("LLM_REPAIR_MAX_OUTPUT_CHARS", "16000"))
    llm_confidence_threshold: float = float(os.getenv("LLM_CONFIDENCE_THRESHOLD", "0.95"))
    # 0 means no local truncation; useful with 1M-context DeepSeek models.
    llm_source_max_chars: int = int(os.getenv("LLM_SOURCE_MAX_CHARS", "0"))
    llm_thinking_mode: str = os.getenv("LLM_THINKING_MODE", "auto")  # auto|enabled|disabled
    llm_reasoning_effort: str = os.getenv("LLM_REASONING_EFFORT", "high")
    llm_terminal_max_tokens: int = int(os.getenv("LLM_TERMINAL_MAX_TOKENS", "8000"))
    llm_codegen_html_chars: int = int(os.getenv("LLM_CODEGEN_HTML_CHARS", "120000"))

    mcp_mode: str = os.getenv("MCP_MODE", "stdio")
    mcp_command: str = os.getenv("MCP_COMMAND", "npx")
    mcp_args: tuple[str, ...] = tuple(
        json.loads(
            os.getenv(
                "MCP_ARGS",
                '["@playwright/mcp@latest","--user-data-dir",".browser-profile"]',
            )
        )
    )
    mcp_url: str = os.getenv("MCP_URL", "http://127.0.0.1:8931/mcp")
    user_data_dir: str = os.getenv("PLAYWRIGHT_USER_DATA_DIR", ".browser-profile")


settings = Settings()
