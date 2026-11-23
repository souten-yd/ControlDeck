"""AI アシスタントの会話をまたぐ記憶（docs/design-chat-memory.md）。

- store.py: 本体の DB と分けた SQLite（data_dir/memory/memory.sqlite3）。本文は暗号化して持つ。
- policy.py: どの会話に、どの記憶を入れてよいか（情報の流出を防ぐ守りの規則はここに集める）。
- service.py: 記憶を作る（手元の LLM だけ）・使う（答えの段にだけ）・消す。
- router.py: 設定・一覧・編集・削除・会話ごとの切り替え。
"""
