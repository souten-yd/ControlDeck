"""W4a2 table evidence contract: no network, source integrity and hidden geometry."""
import hashlib
import json
from app.web_collector.html_tables import table_bundle


def test_table_bundle_integrity():
    html = '<table><tr><th>Model</th><th colspan="2">Speed (tok/s)</th></tr><tr><th>Model</th><th>Decode</th><th>Prefill</th></tr><tr><td>Foo</td><td>60</td><td>420</td></tr></table>'
    tables, meta, warnings = table_bundle(html)
    assert not warnings and len(tables) == 1
    assert meta["schema"] == "controldeck.html-table.v1"
    assert meta["html_sha256"] == hashlib.sha256(html.encode()).hexdigest()
    raw = json.dumps(tables, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert meta["tables_sha256"] == hashlib.sha256(raw).hexdigest()
    for attr in ('hidden', 'aria-hidden="true"', 'style="display:none"'):
        assert table_bundle(html.replace('<td>60', '<td '+attr+'>60'))[0] == []
    assert not table_bundle(html.replace('<td>60', '<td headers="unknown">60'))[0]
