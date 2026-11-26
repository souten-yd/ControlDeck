"""Read-only OOXML candidates; no recalculation, rendering or external access."""
from __future__ import annotations

import hashlib
import io
import posixpath
import re
import zipfile
from contextlib import contextmanager
from .structured_documents import _xml, MAX_XML, MAX_ZIP, MAX_EXPANDED, MAX_BLOCKS, W

S = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
P = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
M = '{http://schemas.openxmlformats.org/officeDocument/2006/math}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
PR = '{http://schemas.openxmlformats.org/package/2006/relationships}'
MAX_CELLS = 50_000


@contextmanager
def _package(data):
    if len(data) > MAX_ZIP:
        raise ValueError('archive size limit')
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        entries = z.infolist()
        if (len(entries) > 2048 or sum(i.file_size for i in entries) > MAX_EXPANDED
                or len({i.filename for i in entries}) != len(entries)):
            raise ValueError('archive limit or duplicate member')
        yield z


def _part(z, name):
    item = z.getinfo(name)
    if item.file_size > MAX_XML or item.flag_bits & 1:
        raise ValueError('unsupported XML part')
    with z.open(item) as f:
        return _xml(f.read(MAX_XML + 1))


def _targets(z, main, kind, allowed):
    folder, name = posixpath.split(main)
    root = _part(z, posixpath.join(folder, '_rels', name + '.rels'))
    if root.tag != PR + 'Relationships':
        raise ValueError('invalid relationships')
    out, seen = {}, set()
    for rel in root:
        rid = rel.get('Id')
        if not rid or rid in seen:
            raise ValueError('invalid relationship id')
        seen.add(rid)
        if rel.get('Type') != R[1:-1] + '/' + kind:
            continue
        if rel.get('TargetMode', 'Internal') != 'Internal':
            raise ValueError('external structural relationship')
        target = rel.get('Target', '')
        if not target or any(c in target for c in '\\:%?#') or '..' in target.split('/'):
            raise ValueError('invalid target')
        path = target.lstrip('/') if target.startswith('/') else posixpath.join(folder, target)
        if not re.fullmatch(allowed, path):
            raise ValueError('unsupported structural target')
        out[rid] = path
    return out


def _strings(node):
    if node is None:
        return None
    return ''.join((t.text or '') for t in node.findall(S + 't') + node.findall(f'{S}r/{S}t'))


def xlsx_blocks(data: bytes) -> dict:
    sheets, count = [], 0
    with _package(data) as z:
        book = _part(z, 'xl/workbook.xml')
        if book.tag != S + 'workbook':
            raise ValueError('unsupported workbook')
        targets = _targets(z, 'xl/workbook.xml', 'worksheet', r'xl/worksheets/[^/]+\.xml')
        shared = []
        if 'xl/sharedStrings.xml' in z.namelist():
            root = _part(z, 'xl/sharedStrings.xml')
            if root.tag != S + 'sst':
                raise ValueError('invalid shared strings')
            shared = [_strings(si) for si in root.findall(S + 'si')]
        entries = book.findall(f'{S}sheets/{S}sheet')
        if len(entries) > 400 or len({sh.get(R + 'id') for sh in entries}) != len(entries):
            raise ValueError('sheet limit or duplicate reference')
        if len(set(targets.values())) != len(targets):
            raise ValueError('duplicate worksheet target')
        for sh in entries:
            rid = sh.get(R + 'id')
            if rid not in targets:
                raise ValueError('unsupported sheet type')
            root = _part(z, targets[rid])
            if root.tag != S + 'worksheet':
                raise ValueError('invalid worksheet')
            cells, seen = [], set()
            for c in root.findall(f'{S}sheetData/{S}row/{S}c'):
                ref = c.get('r', '')
                if not re.fullmatch(r'[A-Z]{1,3}[1-9][0-9]{0,6}', ref) or ref in seen:
                    raise ValueError('invalid cell identity')
                seen.add(ref)
                typ = c.get('t', 'n')
                v, f = c.find(S + 'v'), c.find(S + 'f')
                raw = v.text if v is not None else None
                text = None
                if typ == 's' and raw is not None:
                    if not raw.isascii() or not raw.isdecimal() or len(raw) > 8 or int(raw) >= len(shared):
                        raise ValueError('invalid shared string index')
                    text = shared[int(raw)]
                elif typ == 'inlineStr':
                    text = _strings(c.find(S + 'is'))
                elif typ == 'str':
                    text = raw
                cells.append({'ref': ref, 'type': typ, 'style_id': c.get('s'), 'raw_value': raw,
                              'text': text, 'formula': None if f is None else {'text': f.text, 'attributes': dict(f.attrib)},
                              'cached_only': f is not None})
                count += 1
                if count > MAX_CELLS:
                    raise ValueError('cell limit')
            sheets.append({'name': sh.get('name'), 'state': sh.get('state', 'visible'), 'part': targets[rid],
                           'cells': cells, 'merged_ranges': [m.get('ref') for m in root.findall(f'{S}mergeCells/{S}mergeCell')]})
            if len(sheets) > MAX_BLOCKS:
                raise ValueError('sheet limit')
    return {'format': 'xlsx', 'source_sha256': hashlib.sha256(data).hexdigest(), 'sheets': sheets,
            'limitations': ['no_recalculation', 'cache_may_be_stale', 'raw_dates_and_styles', 'no_charts_or_external_links']}


def _paragraphs(node):
    return [''.join(t.text or '' for t in p.iter(A + 't')) for p in node.findall(A + 'p')]


def pptx_blocks(data: bytes) -> dict:
    slides, count = [], 0
    with _package(data) as z:
        main = 'ppt/presentation.xml'
        root = _part(z, main)
        if root.tag != P + 'presentation':
            raise ValueError('unsupported presentation')
        targets = _targets(z, main, 'slide', r'ppt/slides/[^/]+\.xml')
        entries = root.findall(f'{P}sldIdLst/{P}sldId')
        if len(entries) > 400 or len({entry.get(R + 'id') for entry in entries}) != len(entries):
            raise ValueError('slide limit or duplicate reference')
        if len(set(targets.values())) != len(targets):
            raise ValueError('duplicate slide target')
        for entry in entries:
            path = targets[entry.get(R + 'id')]
            slide = _part(z, path)
            if slide.tag != P + 'sld':
                raise ValueError('invalid slide')
            tree = slide.find(f'{P}cSld/{P}spTree')
            if tree is None:
                raise ValueError('missing shape tree')
            shapes, unsupported = [], []
            for i, node in enumerate(tree):
                body = node.find(P + 'txBody')
                table = node.find(f'{A}graphic/{A}graphicData/{A}tbl')
                if node.tag == P + 'sp' and body is not None:
                    shapes.append({'index': i, 'kind': 'text', 'paragraphs': _paragraphs(body)})
                elif node.tag == P + 'graphicFrame' and table is not None:
                    shapes.append({'index': i, 'kind': 'table', 'rows': [[{'paragraphs': _paragraphs(c.find(A + 'txBody')) if c.find(A + 'txBody') is not None else [], 'merge_attributes': dict(c.attrib)} for c in r.findall(A + 'tc')] for r in table.findall(A + 'tr')]})
                elif node.tag not in (P + 'nvGrpSpPr', P + 'grpSpPr'):
                    unsupported.append(node.tag.rsplit('}', 1)[-1])
                count += 1
                if count > MAX_BLOCKS:
                    raise ValueError('shape limit')
            slides.append({'number': len(slides)+1, 'part': path, 'shapes': shapes, 'unsupported': unsupported})
            if len(slides) > 400:
                raise ValueError('slide limit')
    return {'format': 'pptx', 'source_sha256': hashlib.sha256(data).hexdigest(), 'slides': slides,
            'limitations': ['no_group_expansion', 'no_notes', 'no_charts_or_images', 'no_visual_reading_order']}


def docx_math(data: bytes) -> dict:
    equations = []
    with _package(data) as z:
        root = _part(z, 'word/document.xml')
        body = root.find(W + 'body')
        if root.tag != W + 'document' or body is None:
            raise ValueError('invalid Word document')
        from xml.etree.ElementTree import tostring
        for index, block in enumerate(body):
            for equation in block.iter(M + 'oMath'):
                equations.append({'body_index': index, 'equation_index': len(equations),
                                  'omml': tostring(equation, encoding='unicode'),
                                  'tokens': [t.text or '' for t in equation.iter(M + 't')]})
                if len(equations) > MAX_BLOCKS:
                    raise ValueError('equation limit')
    return {'format': 'docx_omml', 'source_sha256': hashlib.sha256(data).hexdigest(), 'equations': equations,
            'limitations': ['serialized_not_byte_exact', 'no_math_evaluation', 'no_latex_conversion', 'no_revision_visibility_resolution']}
