from pathlib import Path


def test_tool_loop_is_present_but_bounded():
    text = Path('app/extractor.py').read_text(encoding='utf-8')
    assert '_run_tool_agent' in text
    assert 'max_rounds=2' in text
    assert 'max_rounds=3' in text
