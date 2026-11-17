"""診断: ControlDeck の不調を記録し、原因を追うための材料を集める。

- recorder: 警告・エラー、event loop の詰まり、遅い要求、メモリの推移を data/diagnostics/ に残す
- journal: 再起動・OOM・サービスの異常終了を journal から抜き出す
- selftest: その場で確かめられる自己診断
- report: 一式を 1 つにまとめ、秘密を伏せて AI へ渡せる形にする
"""
