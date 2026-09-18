from app.extractor import flatten_template


def test_template_leaves_are_required_by_default():
    leaves = flatten_template({"title": "", "media": {"image": "", "date": ""}})
    assert {x["path"] for x in leaves} == {"title", "media.image", "media.date"}
    assert all(x["required"] for x in leaves)
