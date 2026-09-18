from app.structure_registry import StructureRegistry


def test_structure_registry_round_trip(tmp_path):
    registry=StructureRegistry(str(tmp_path/'generated'))
    data={'url':'https://example.com/article/1','host':'example.com','method':'requests_bs4','fields':[]}
    saved=registry.save(data,structure_id='job123')
    assert saved['structure_id']=='job123'
    loaded=registry.get('job123')
    assert loaded and loaded['url']==data['url']
    items=registry.list()
    assert any(x['structure_id']=='job123' for x in items)
