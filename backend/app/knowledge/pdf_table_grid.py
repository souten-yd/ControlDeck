"""Read-only PDF table reconstruction from proposed cell rectangles and native words.
No automatic table detection, OCR, or measurement publication.
"""
from bisect import bisect_left
import math

MAX_CELLS = 2000
MAX_WORDS = 20000
MAX_AXIS = 200


def _rect(box):
    if len(box) != 4:
        raise ValueError('invalid rectangle')
    a, b, c, d = map(float, box)
    if not all(math.isfinite(v) for v in (a,b,c,d)) or a >= c or b >= d:
        raise ValueError('invalid rectangle')
    return a,b,c,d


def _axis(values, tolerance):
    groups = []
    for v in sorted(set(values)):
        if groups and v - groups[-1][0] <= tolerance:
            groups[-1].append(v)
        else:
            groups.append([v])
    if len(groups) > MAX_AXIS or len(groups) < 2:
        raise ValueError('grid axis limit')
    return [sum(g)/len(g) for g in groups]


def _index(axis, v, tolerance):
    i = bisect_left(axis, v)
    options = [j for j in (i-1,i) if 0 <= j < len(axis) and abs(axis[j]-v) <= tolerance]
    if len(options) != 1:
        raise ValueError('ambiguous grid boundary')
    return options[0]


def reconstruct(layout, page, rectangles, *, tolerance=0.5):
    """Cell geometry must be supplied in same top-left point coordinates as layout.

    Rectangles must tile a complete rectangular grid without overlap or holes.
    Merged cells are explicit, not inferred from absent words. Any boundary-crossing
    word leaves the whole candidate unverified. Empty cells are not numeric zero.
    """
    if type(page) is not int or not 1 <= page <= layout['page_count']:
        raise ValueError('invalid page')
    if not math.isfinite(tolerance) or not 0 < tolerance <= 1:
        raise ValueError('invalid tolerance')
    if not 1 <= len(rectangles) <= MAX_CELLS:
        raise ValueError('cell limit')
    rects = [_rect(r) for r in rectangles]
    xs = _axis([v for r in rects for v in (r[0],r[2])],tolerance)
    ys = _axis([v for r in rects for v in (r[1],r[3])],tolerance)
    nr,nc = len(ys)-1,len(xs)-1
    if nr*nc > MAX_CELLS:
        raise ValueError('grid size limit')
    slots = {}
    cells = []
    for r in rects:
        c0,c1 = [_index(xs,v,tolerance) for v in (r[0],r[2])]
        r0,r1 = [_index(ys,v,tolerance) for v in (r[1],r[3])]
        if c0 >= c1 or r0 >= r1:
            raise ValueError('collapsed cell')
        cell = dict(row=r0,col=c0,rowspan=r1-r0,colspan=c1-c0,bbox=r,words=[])
        for y in range(r0,r1):
            for x in range(c0,c1):
                if (y,x) in slots:
                    raise ValueError('overlapping cells')
                slots[y,x] = cell
        cells.append(cell)
    if len(slots) != nr*nc:
        raise ValueError('incomplete grid')
    blocks = [b for b in layout['blocks'] if b['page'] == page]
    if not blocks:
        raise ValueError('page has no native text blocks')
    width,height = blocks[0]['page_size']
    if xs[0] < 0 or ys[0] < 0 or xs[-1] > width or ys[-1] > height:
        raise ValueError('grid outside page')
    unresolved = []
    seen = set()
    count = 0
    for b in blocks:
        for li,line in enumerate(b['lines']):
            for wi,w in enumerate(line['words']):
                count += 1
                if count > MAX_WORDS:
                    raise ValueError('word limit')
                x0,y0,x1,y1 = _rect(w['bbox'])
                if x1 <= xs[0] or x0 >= xs[-1] or y1 <= ys[0] or y0 >= ys[-1]:
                    continue
                ref = (b['index'],li,wi)
                if ref in seen:
                    raise ValueError('duplicate word reference')
                seen.add(ref)
                owners = [c for c in cells if c['bbox'][0] <= x0 and c['bbox'][1] <= y0
                          and x1 <= c['bbox'][2] and y1 <= c['bbox'][3]]
                if len(owners) != 1:
                    unresolved.append(ref)
                else:
                    owners[0]['words'].append(dict(ref=ref,text=w['text'],bbox=(x0,y0,x1,y1)))
    for cell in cells:
        cell['words'].sort(key=lambda w:(w['ref'][0],w['ref'][1],w['bbox'][0],w['ref'][2]))
        cell['text'] = ' '.join(w['text'] for w in cell['words'])
    return dict(page=page,layout_sha256=layout['layout_sha256'],rows=nr,columns=nc,
                cells=sorted(cells,key=lambda c:(c['row'],c['col'])),unresolved=unresolved,
                status='ambiguous' if unresolved else 'candidate',
                limitations=['geometry_supplied','reading_order_unverified','headers_unverified','no_measurement_authority'])
