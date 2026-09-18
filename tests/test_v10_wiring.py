from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_frontend_enables_approval_after_discovery():
    js = (ROOT / 'frontend' / 'static' / 'app.js').read_text(encoding='utf-8')
    assert "job.discovery.status === 'FAILED' || job.needs_browser_analysis === true" in js


def test_mcp_is_runtime_only_and_llm_does_not_tool_call():
    llm = (ROOT / 'app' / 'llm.py').read_text(encoding='utf-8')
    extractor = (ROOT / 'app' / 'extractor.py').read_text(encoding='utf-8')
    assert 'chat_json(' in llm
    assert 'chat_json(' in extractor
    assert 'browser_evaluate' in extractor
    assert 'chat_with_tools(' not in extractor
    assert 'self.browser.call("browser_navigate"' in extractor
