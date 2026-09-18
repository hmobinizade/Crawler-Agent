"""Project wrapper around the standalone Playwright structure crawler."""
from standalone.playwright_structure_crawler import (  # noqa: F401
    ExtractionEngine,
    build_page_pattern,
    candidate_values,
    clean_body_locator,
    clean_text,
    crawl_playwright,
    get_path,
    missing_required,
    normalize_digits,
    run_structure,
    same_page_type,
    same_site,
    set_path,
    scroll_to_bottom,
)

__all__ = [
    "ExtractionEngine",
    "build_page_pattern",
    "candidate_values",
    "clean_body_locator",
    "clean_text",
    "crawl_playwright",
    "get_path",
    "missing_required",
    "normalize_digits",
    "run_structure",
    "same_page_type",
    "same_site",
    "set_path",
    "scroll_to_bottom",
]
