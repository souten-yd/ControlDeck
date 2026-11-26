import io
import zipfile
import pytest
from app.knowledge.office_structure import xlsx_blocks, pptx_blocks, docx_math, S, P, A, M, R, PR, W


def pack(parts):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        for path, text in parts.items():
            z.writestr(path, text)
    return out.getvalue()


def rels(kind, target, mode='Internal'):
    return f'<Relationships xmlns="{PR[1:-1]}"><Relationship Id="r1" Type="{R[1:-1]}/{kind}" Target="{target}" TargetMode="{mode}"/></Relationships>'


def excel(target='worksheets/sheet7.xml', mode='Internal'):
    return {'xl/workbook.xml': f'<workbook xmlns="{S[1:-1]}" xmlns:r="{R[1:-1]}"><sheets><sheet name="測定" r:id="r1"/></sheets></workbook>',
            'xl/_rels/workbook.xml.rels': rels('worksheet',target,mode),
            'xl/sharedStrings.xml': f'<sst xmlns="{S[1:-1]}"><si><r><t>周波</t></r><r><t>数</t></r></si></sst>',
            'xl/worksheets/sheet7.xml': f'<worksheet xmlns="{S[1:-1]}"><sheetData><row><c r="A1" t="s"><v>0</v></c><c r="B1"><f>1+2</f><v>3</v></c><c r="C1"><f>2+2</f></c><c r="D1" t="e"><v>#DIV/0!</v></c></row></sheetData><mergeCells><mergeCell ref="A2:B2"/></mergeCells></worksheet>'}


def test_xlsx_formula_and_cached_value():
    sh = xlsx_blocks(pack(excel()))['sheets'][0]
    assert sh['name'] == '測定' and sh['part'].endswith('sheet7.xml')
    a,b,c,d = sh['cells']
    assert a['text'] == '周波数' and a['raw_value'] == '0'
    assert b['formula']['text'] == '1+2' and b['raw_value'] == '3' and b['cached_only']
    assert c['raw_value'] is None and c['cached_only']
    assert d['type'] == 'e' and d['raw_value'] == '#DIV/0!'
    assert sh['merged_ranges'] == ['A2:B2']


@pytest.mark.parametrize('target,mode',[('https://example.org/a','External'),('../other.xml','Internal'),('worksheets/%2e%2e.xml','Internal')])
def test_reject_target(target,mode):
    with pytest.raises(ValueError): xlsx_blocks(pack(excel(target,mode)))


def test_pptx_slide_order_and_table():
    parts = {'ppt/presentation.xml': f'<p:presentation xmlns:p="{P[1:-1]}" xmlns:r="{R[1:-1]}"><p:sldIdLst><p:sldId r:id="r1"/></p:sldIdLst></p:presentation>',
             'ppt/_rels/presentation.xml.rels': rels('slide','slides/slide9.xml'),
             'ppt/slides/slide9.xml': f'<p:sld xmlns:p="{P[1:-1]}" xmlns:a="{A[1:-1]}"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>150 kHz</a:t></a:r></a:p></p:txBody></p:sp><p:graphicFrame><a:graphic><a:graphicData><a:tbl><a:tr><a:tc gridSpan="2"><a:txBody><a:p><a:r><a:t>50 dB</a:t></a:r></a:p></a:txBody></a:tc></a:tr></a:tbl></a:graphicData></a:graphic></p:graphicFrame><p:pic/></p:spTree></p:cSld></p:sld>'}
    sl = pptx_blocks(pack(parts))['slides'][0]
    assert sl['part'].endswith('slide9.xml') and sl['number'] == 1
    assert sl['shapes'][0]['paragraphs'] == ['150 kHz']
    assert sl['shapes'][1]['rows'][0][0] == {'paragraphs':['50 dB'],'merge_attributes':{'gridSpan':'2'}}
    assert sl['unsupported'] == ['pic']


def test_omml_preserves_fraction():
    doc = f'<w:document xmlns:w="{W[1:-1]}" xmlns:m="{M[1:-1]}"><w:body><w:p><m:oMath><m:f><m:num><m:r><m:t>1</m:t></m:r></m:num><m:den><m:r><m:t>2</m:t></m:r></m:den></m:f></m:oMath></w:p></w:body></w:document>'
    eq = docx_math(pack({'word/document.xml':doc}))['equations'][0]
    assert eq['tokens'] == ['1','2'] and 'num' in eq['omml'] and 'den' in eq['omml']
