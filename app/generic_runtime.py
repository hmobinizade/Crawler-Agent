"""Public generic Playwright runtime for Extraction Structures."""
from standalone.playwright_structure_crawler import (
    ExtractionEngine,
    candidate_values,
    clean_body_locator,
    clean_text,
    crawl_playwright,
    get_path,
    missing_required,
    normalize_digits,
    run_structure,
    set_path,
    scroll_to_bottom,
)

__all__ = [
    "ExtractionEngine",
    "candidate_values",
    "clean_body_locator",
    "clean_text",
    "crawl_playwright",
    "get_path",
    "missing_required",
    "normalize_digits",
    "run_structure",
    "set_path",
    "scroll_to_bottom",
]
