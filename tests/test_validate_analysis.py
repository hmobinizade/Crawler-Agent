from app.extractor import ExtractorAgent, flatten_template


def test_validate_analysis_reconstructs_contract_and_missing_fields():
    agent = ExtractorAgent.__new__(ExtractorAgent)
    contract = flatten_template({
        "title": "",
        "comments": {
            "value": [],
            "type": "array",
            "required": False,
            "items": {"author": "", "text": ""},
        },
        "date": "",
    })
    analysis = {
        "proposed_json": {"title": "Example"},
        "fields": [
            {
                "path": "title",
                "found": True,
                "value": "Example",
                "selector": "h1.article-title",
                "selector_type": "css",
                "confidence": 0.9,
            },
            {
                "path": "comments",
                "found": False,
                "value": None,
                "value_type": "array",
            },
        ],
    }

    fields, missing, proposed = agent._validate_analysis(analysis, contract)

    assert [f.path for f in fields] == ["title", "comments", "date"]
    assert [f.found for f in fields] == [True, False, False]
    assert missing == ["date"]
    assert proposed["title"] == "Example"
    assert proposed["comments"] is None
    assert proposed["date"] is None


def test_tool_loop_budget_is_bounded_in_source():
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / 'app' / 'extractor.py'
    text = src.read_text(encoding='utf-8')
    assert 'chat_with_tools(' not in text
    assert 'browser_evaluate' in text
    assert 'chat_json(' in text
    assert 'def _validate_analysis(' in text
