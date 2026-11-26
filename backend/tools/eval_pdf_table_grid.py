"""Pinned local public PDF table evaluation; optional PyMuPDF for evaluation only."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as E

from app.knowledge.pdf_table_grid import reconstruct

SOURCE = "c21c5e47bcf61e3c31889ef09d1cfd2ad544a8cb1162d709a4753b5eb5882f8f"
H = "{http://www.w3.org/1999/xhtml}"


def main(path):
    import fitz
    if path.stat().st_size > 100*1024*1024 or hashlib.sha256(path.read_bytes()).hexdigest() != SOURCE:
        raise ValueError("source mismatch")
    xml = subprocess.check_output(["pdftotext","-f","12","-l","12","-bbox-layout",str(path),"-"], timeout=30)
    page = E.fromstring(xml).find(f"{H}body/{H}doc/{H}page")
    def bbox(node):
        return tuple(float(node.attrib[k]) for k in ("xMin","yMin","xMax","yMax"))
    blocks = [dict(page=12,index=i,page_size=(float(page.attrib["width"]),float(page.attrib["height"])),
        lines=[dict(words=[dict(text=w.text or "",bbox=bbox(w)) for w in l.findall(H+"word")])
               for l in b.findall(H+"line")])
        for i,b in enumerate(page.findall(f"{H}flow/{H}block"))]
    layout = dict(page_count=12,layout_sha256=hashlib.sha256(xml).hexdigest(),blocks=blocks)
    with fitz.open(path) as doc:
        table = doc[11].find_tables().tables[0]
        result = reconstruct(layout,12,[c for c in table.cells if c is not None])
    expected = {(0,0):("\\lefthyphenmin",2,1),(0,1):("\\righthyphenmin",1,5)}
    for c in range(1,6):
        expected[1,c] = (str(c),1,1)
    for row in range(2,7):
        expected[row,0] = (str(row-1),1,1)
        for c in range(1,6):
            expected[row,c] = (("in-ter-est-ing" if row<=3 else "inter-est-ing") if c<=3 else
                               ("in-ter-esting" if row<=3 else "inter-esting"),1,1)
    actual = {(c["row"],c["col"]):(c["text"],c["rowspan"],c["colspan"]) for c in result["cells"]}
    print(json.dumps(dict(source_sha=SOURCE,layout_sha=layout["layout_sha256"],status=result["status"],
        rows=result["rows"],columns=result["columns"],cells=len(actual),expected=len(expected),
        matches=sum(actual.get(k)==v for k,v in expected.items()),unresolved=result["unresolved"]),indent=2))
    assert actual == expected and not result["unresolved"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf",type=Path)
    main(parser.parse_args().pdf)
