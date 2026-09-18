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
    llm_model: str = os.getenv("LLM_MODEL", "qwen3:14b")
    llm_timeout_seconds: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "90"))
    llm_max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "3200"))
    llm_codegen_max_tokens: int = int(os.getenv("LLM_CODEGEN_MAX_TOKENS", "3200"))
    llm_repair_max_tokens: int = int(os.getenv("LLM_REPAIR_MAX_TOKENS", "5000"))
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
