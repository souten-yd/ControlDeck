import io
import zipfile
import pytest
from app.knowledge.structured_documents import pdf_bbox_blocks, docx_blocks, W, H, POPPLER_DTD


def package(body):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('word/document.xml', f'<w:document xmlns:w="{W[1:-1]}"><w:body>{body}</w:body></w:document>')
    return out.getvalue()


def test_pdf_boxes_and_empty():
    coords = 'xMin="1" yMin="2" xMax="3" yMax="4"'
    data = f'{POPPLER_DTD}<html xmlns="{H[1:-1]}"><body><doc><page width="10" height="20"><flow><block {coords}><line {coords}><word {coords}>150 kHz</word></line></block></flow></page><page width="10" height="20"/></doc></body></html>'.encode()
    r = pdf_bbox_blocks(data)
    assert r['empty_pages'] == [2] and r['page_count'] == 2
    assert r['blocks'][0]['lines'][0]['words'][0] == {'text':'150 kHz','bbox':(1.,2.,3.,4.)}
    with pytest.raises(ValueError): pdf_bbox_blocks(data.replace(b'xMin="1"',b'xMin="NaN"'))


def test_docx_paragraph_table():
    r = docx_blocks(package('<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>測定</w:t><w:tab/><w:t>150 kHz</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge/></w:tcPr><w:p><w:r><w:t>50 dB</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'))
    assert r['blocks'][0]['text'] == '測定\t150 kHz' and r['blocks'][0]['style_id'] == 'Heading1'
    c = r['blocks'][1]['rows'][0][0]
    assert c['paragraphs'][0]['text'] == '50 dB' and c['grid_span_raw'] == '2' and c['vertical_merge_raw'] == 'continue'


def test_unsupported_and_no_fields():
    r = docx_blocks(package('<w:p><w:r><w:instrText>DO NOT RUN</w:instrText><w:t>printed</w:t></w:r><w:ins><w:r><w:t>revision</w:t></w:r></w:ins></w:p>'))
    assert r['blocks'][0]['text'] == 'printed'
    assert r['unsupported_detected'] == ['ins','instrText']


def test_reject_dtd_and_wrong_package():
    with pytest.raises(ValueError): pdf_bbox_blocks(b'<!DOCTYPE html [<!ENTITY a "x">]><html/>')
    with pytest.raises(ValueError): docx_blocks(package(''.join(['<w:p/>']*10001)))
    with pytest.raises(zipfile.BadZipFile): docx_blocks(b'not zip')
