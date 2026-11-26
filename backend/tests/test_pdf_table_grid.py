import pytest
from app.knowledge.pdf_table_grid import reconstruct


def layout(words):
    return dict(page_count=1,layout_sha256='test',blocks=[dict(page=1,index=0,page_size=(100,100),lines=[dict(words=[dict(text=t,bbox=b) for t,b in words])])])


GRID=[(0,0,20,10),(0,10,10,20),(10,10,20,20)]


def test_merged_and_empty():
    r=reconstruct(layout([('Header',(1,1,9,5)),('10',(1,11,5,15))]),1,GRID)
    assert (r['rows'],r['columns'],r['status']) == (2,2,'candidate')
    assert [(c['text'],c['rowspan'],c['colspan']) for c in r['cells']] == [('Header',1,2),('10',1,1),('',1,1)]
    assert r['cells'][1]['words'][0]['ref'] == (0,0,1)


def test_crossing_word():
    r=reconstruct(layout([('bad',(8,11,12,15))]),1,GRID)
    assert r['status']=='ambiguous' and r['unresolved']==[(0,0,0)]
    assert all(not c['text'] for c in r['cells'])


@pytest.mark.parametrize('grid',[GRID[:-1],GRID+[(0,0,10,10)],[(0,0,float('nan'),10)],[(0,0,101,20)]])
def test_invalid_grid(grid):
    with pytest.raises(ValueError): reconstruct(layout([]),1,grid)


def test_outside_ignored_and_partial_rejected():
    r=reconstruct(layout([('outside',(25,1,30,5)),('partial',(18,11,22,15))]),1,GRID)
    assert r['unresolved']==[(0,0,1)]
