from __future__ import annotations

import ast
import importlib.util
import re
import tempfile
from pathlib import Path
from typing import Any

from .llm import LLMError, OpenAICompatibleLLM
from .settings import settings


REPAIR_SYSTEM = r'''
You are a senior web-crawler repair engineer.
A deterministic crawler generator has already produced a crawler from a validated ExtractionSpec.
You are NOT the primary code generator.

Your only job is to repair concrete validator failures while preserving the extraction contract exactly.
Do not redesign fields, invent selectors, or replace verified selectors with generic heuristics.
Keep every template field and every verified extraction rule unchanged unless the validator proves that
one concrete implementation is broken.

Requirements:
- return one complete self-contained Python crawler
- no LLM/network calls at runtime other than the target website
- preserve --once mode and normal crawl mode
- preserve exact field names and types
- preserve full-body extraction and collection semantics
- use the verified selector/selector_type/attribute/scope_selector/JSON-LD path first
- only repair the specific failing behavior described by validation errors

Return ONLY JSON:
{"filename":"crawler_repaired.py","code":"<complete corrected Python source>","runtime":"requests_bs4|playwright","notes":[],"required_packages":[],"contract_preserved":true}
'''


class CodeGenerationError(RuntimeError):
    pass


class CrawlerCodeAgent:
    """Validation + optional repair agent. It never performs primary code generation."""

    def __init__(self) -> None:
        self.llm = OpenAICompatibleLLM()

    @staticmethod
    def validate_source(code: str, structure: dict[str, Any]) -> dict[str, Any]:
        errors: list[str] = []
        warnings: list[str] = []
        try:
            ast.parse(code)
        except SyntaxError as exc:
            return {
                "ok": False,
                "errors": [f"SyntaxError: {exc}"],
                "warnings": [],
            }

        for marker, message in [
            ("if __name__", "missing executable entrypoint"),
            ("--once", "missing --once mode"),
            ("missing_required", "missing missing_required validation"),
            ("extraction_ok", "missing extraction_ok result"),
        ]:
            if marker not in code:
                errors.append(message)

        if "START_URL" not in code and "ROOT" not in code:
            errors.append("missing root/start URL")

        fields = structure.get("fields") or []
        if fields and "FIELDS" not in code:
            errors.append("generated code does not expose its field contract")

        runtime = structure.get("method")
        if runtime == "playwright" and "playwright" not in code.lower():
            errors.append("structure requires Playwright but generated code does not use it")
        if runtime in {"requests_bs4", "static"} and "BeautifulSoup" not in code:
            errors.append("structure requires requests+BeautifulSoup but generated code does not use it")

        try:
            tree = ast.parse(code)
            forbidden = {"openai", "anthropic", "ollama", "httpx"}
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split('.')[0].lower() for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split('.')[0].lower())
            if imported & forbidden:
                errors.append("generated crawler imports an LLM/runtime provider package")
            if re.search(r"chat/completions|orcarouter|openrouter", code, re.I):
                errors.append("generated crawler contains an LLM provider endpoint")
        except Exception:
            pass

        if "articleBody" in str(structure) and "ld+json" not in code.lower() and "jsonld" not in code.lower():
            warnings.append("structure contains JSON-LD rules but generated code has no obvious JSON-LD parser")

        return {"ok": not errors, "errors": errors, "warnings": warnings}

    @staticmethod
    def smoke_validate_html(code: str, structure: dict[str, Any], html: str | None) -> dict[str, Any]:
        if not html or (structure.get("method") or "").lower() not in {"requests_bs4", "static"}:
            return {
                "attempted": False,
                "ok": True,
                "errors": [],
                "warnings": ["HTML smoke test skipped for this runtime."],
            }

        try:
            from bs4 import BeautifulSoup
            with tempfile.TemporaryDirectory(prefix="crawler_codegen_") as td:
                path = Path(td) / "generated_crawler.py"
                path.write_text(code, encoding="utf-8")
                spec = importlib.util.spec_from_file_location("generated_crawler", path)
                if not spec or not spec.loader:
                    raise RuntimeError("Cannot load generated crawler module")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                if not hasattr(module, "extract"):
                    return {
                        "attempted": True,
                        "ok": False,
                        "errors": ["Generated Requests crawler has no extract(soup) function."],
                        "warnings": [],
                    }

                soup = BeautifulSoup(html, "html.parser")
                data = module.extract(soup)
                missing_fn = getattr(module, "missing_required", None)
                missing = missing_fn(data) if callable(missing_fn) else []
                if missing:
                    return {
                        "attempted": True,
                        "ok": False,
                        "errors": [f"Source HTML smoke extraction missing: {missing}"],
                        "warnings": [],
                    }
                return {
                    "attempted": True,
                    "ok": True,
                    "errors": [],
                    "warnings": [],
                }
        except Exception as exc:
            return {
                "attempted": True,
                "ok": False,
                "errors": [f"HTML smoke test error: {exc}"],
                "warnings": [],
            }

    @classmethod
    def validate(cls, code: str, structure: dict[str, Any], html: str | None = None) -> dict[str, Any]:
        source = cls.validate_source(code, structure)
        smoke = cls.smoke_validate_html(code, structure, html)
        return {
            "ok": bool(source["ok"] and smoke["ok"]),
            "source": source,
            "smoke": smoke,
            "errors": source["errors"] + smoke["errors"],
            "warnings": source["warnings"] + smoke["warnings"],
        }

    @staticmethod
    def build_repair_prompt(structure: dict[str, Any], code: str, validation: dict[str, Any], html: str | None) -> str:
        html_excerpt = (html or "")
        if len(html_excerpt) > 24000:
            html_excerpt = html_excerpt[:12000] + "\n<!-- HTML MIDDLE OMITTED -->\n" + html_excerpt[-12000:]
        return (
            "EXTRACTION STRUCTURE (AUTHORITATIVE):\n"
            + __import__("json").dumps(structure, ensure_ascii=False, indent=2)
            + "\n\nVALIDATION ERRORS:\n"
            + __import__("json").dumps(validation, ensure_ascii=False, indent=2)
            + "\n\nGENERATED CRAWLER TO REPAIR:\n"
            + code
            + "\n\nSOURCE HTML EVIDENCE:\n"
            + html_excerpt
            + "\n\nRepair only the concrete failures. Return complete corrected JSON."
        )

    async def repair(self, structure: dict[str, Any], code: str, validation: dict[str, Any], html: str | None = None) -> dict[str, Any]:
        prompt = self.build_repair_prompt(structure, code, validation, html)
        try:
            response = await self.llm.chat_json(
                REPAIR_SYSTEM,
                prompt,
                max_tokens=settings.llm_repair_max_tokens,
            )
        except LLMError as exc:
            raise CodeGenerationError(str(exc)) from exc

        repaired_code = str(response.get("code") or "")
        if not repaired_code:
            raise CodeGenerationError("Repair agent returned empty code")

        final_validation = self.validate(repaired_code, structure, html)
        if not final_validation["ok"]:
            raise CodeGenerationError(
                "Repaired crawler failed validation: " + "; ".join(final_validation["errors"])
            )

        return {
            "code": repaired_code,
            "filename": str(response.get("filename") or "crawler_repaired.py"),
            "runtime": response.get("runtime") or structure.get("method") or "requests_bs4",
            "notes": response.get("notes") if isinstance(response.get("notes"), list) else [],
            "required_packages": response.get("required_packages") if isinstance(response.get("required_packages"), list) else [],
            "validation": final_validation,
            "repair_prompt": prompt,
            "llm_response": response,
        }
