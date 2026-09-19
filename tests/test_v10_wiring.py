from pathlib import Path

def test_direct_tool_agent_is_present_and_bounded():
    text = Path('app/extractor.py').read_text(encoding='utf-8')
    assert 'DIRECT_TOOL_SYSTEM' in text
    assert 'max_rounds=settings.llm_discovery_max_rounds' in text
    assert 'max_tool_calls=settings.llm_discovery_max_tool_calls' in text
    assert 'browser_snapshot' in text and 'browser_evaluate' in text
