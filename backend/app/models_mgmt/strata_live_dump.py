"""Strata 本体の目録を書き出す（Strata 自身の Python で動かす小さな台本。ControlDeck の中では import しない）。

使い方: <Strata の python> -I strata_live_dump.py <Strata の control のフォルダ>
Strata の `strata_cli.launch.catalog.Catalog().rows()`（`strata launch` の目録と同じもの）を JSON で標準出力へ出す。
何も取得しない・書かない（この PC の RAM と GPU を読むだけ）。2026-11-28 目録の作り直し。
"""
import dataclasses
import json
import sys

sys.path.insert(0, sys.argv[1])
from strata_cli.launch import catalog as C  # noqa: E402

real = sys.stdout
sys.stdout = sys.stderr          # 目録のコードが標準出力へ書いても JSON を壊さない
try:
    rows = [dataclasses.asdict(row) for row in C.Catalog(src=sys.argv[1]).rows()]
finally:
    sys.stdout = real
print(json.dumps(rows, ensure_ascii=False))
