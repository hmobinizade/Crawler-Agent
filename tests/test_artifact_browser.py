from pathlib import Path

import pytest

from app.artifacts import ArtifactStore


def test_artifact_tree_and_safe_paths(tmp_path):
    store = ArtifactStore(str(tmp_path / 'generated'))
    job = store.root / 'example.com' / 'job_abc'
    job.mkdir(parents=True)
    (job / '09_crawler.py').write_text('print(1)', encoding='utf-8')
    (job / '02_extraction_structure.json').write_text('{}', encoding='utf-8')
    (job / '.browser-profile').mkdir()
    (job / '.browser-profile' / 'secret.txt').write_text('secret', encoding='utf-8')
    tree = store.tree()
    domain = tree[0]
    names = {child['name'] for child in domain['children']}
    assert '.browser-profile' not in names
    assert store.safe_resolve('example.com/job_abc/09_crawler.py').exists()
    with pytest.raises(ValueError):
        store.safe_resolve('../secret.txt')
