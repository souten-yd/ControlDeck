---
name: controldeck-development
description: Use when you implement, fix or change code in a repository — especially ControlDeck. Gives the order of work (read, find a model to copy, implement, test, gate, report), the rules to obey, the completion condition (`./deck.sh dev-check` passes and the reviewer's findings are fixed) and the report format. Triggers on "実装して", "直して", "機能を足して", "開発", "指示書", implement, fix, refactor.
license: MIT
metadata:
  author: ControlDeck
  version: "1.0.0"
---

# 開発の作業の進め方

間違いの少ない開発は、書く前の読み方と、終わる前の機械的な確かめで決まる。この順に進める。

## 共通の進め方（どのリポジトリでも）

### 1. 手本を探す

- 作る物と同じ形の既存のコードを `grep -rn` で探し、**その書き方に合わせる**（名前・置き場所・エラーの扱い・テストの形）。
- 新しい仕組み・新しい依存・新しい書式を作らない。手本が無いと思ったら、探す語を変えてもう一度探し、それでも無ければ手を止めて報告する。
- 手本から変えた点は、理由と一緒に最後の報告へ書く。

### 2. 小さく作り、先にテストを置く

- 変えるのは指示された範囲だけ。ついでの整理・名前替えをしない。
- 足すテストは、ファイルの先頭の docstring に **「目的」（何を守るか）と「根拠」（なぜ要るか: 起きた不具合・決まり・日付）** を書く。足す前に同じ観点のテストが無いか探し、あればそれを広げる。
- テストは変えた物を本当に確かめる（期待値を実装に合わせて書き換えただけにしない）。落ちるべき入力で落ちることも確かめる。

### 3. 関門を通す

- 完了の前に、機械的な関門（このリポジトリの `./deck.sh dev-check` など）を流す。不合格の項目は「直すもの」に従って直し、合格するまで繰り返す。
- レビュー役（別のモデルの子エージェント `reviewer`）に差分を見せ、**指摘を読んで直す**。指摘を直さないなら理由を報告に書く。
- 流せなかった確認（画面・実機・外部のサービス）は「確かめられなかった点」として必ず書く。「たぶん動く」を完了と言わない。

### 4. 報告の型

1. 変えたファイル（一覧と 1 行ずつの理由）
2. テスト（足したもの・流したコマンド・結果）
3. 手本から変えた点（無ければ「無し」）
4. 確かめられなかった点
5. 関門（dev-check）の結果とレビュー役の指摘への対応

## ControlDeck 固有

### 最初に読む（この順）

1. `AGENTS.md` と `CLAUDE.md`
2. `docs/ROADMAP.md` と、仕事に対応する `docs/HANDOFF-*.md`
3. `docs/implementation-status.md` の先頭の最新の数件（今の状態と直近の決定）
4. 指示書にある手本のファイル

### 守ること

- **外部からの取得をしない**（pip・npm install・モデルや資料のダウンロード・外部の API）。必要なら手を止めて報告する。依存（`requirements.txt`・`package.json`・`package-lock.json`）は利用者の了承なしに足さない。
- `shell=True`・`os.system` を使わない。subprocess は配列引数。パスは `Path.resolve()` で正規化して許可の範囲を確かめる。
- 秘密（鍵・トークン・パスワード）をログ・画面・テストの期待値・コミットへ出さない。
- `async def` の中で同期的に待つ処理（DB・同期 HTTP・`subprocess.run`・`time.sleep`）を呼ばない。
- 異常を検知する診断を足す決まり・目録の決まりは `AGENTS.md` の「不具合・診断・テストの決まり」。
- テストは **元の venv の python で pytest を直接流す**。箱の中で `./deck.sh test` は使わない（venv を作って PyPI へ取りに行って止まる）:
  `cd backend && env -u CONTROL_DECK_CONFIG <元のリポジトリ>/.venv/bin/python -m pytest -q -p no:cacheprovider <ファイル>`
  （`env -u CONTROL_DECK_CONFIG` を外すと本番の data に書く。必ず付ける）
- テストの目録 `docs/test-catalog.md`: 足したテストが載っていること。`./deck.sh test-catalog` で作り直してよい（手書きの説明は引き継がれる）。流した後は `git diff docs/test-catalog.md` で説明が減っていないか見る。
- 特許の案件・未公開の発明の内容は **ローカル LLM だけ**で扱う。外部のモデル・外部の検索に出さない。
- 状態の記録: コードを変えたら `docs/implementation-status.md` の先頭へ、利用者の言葉・決めたこと・テスト・確かめられなかった点を日本語で書く。関連する `docs/HANDOFF-*.md` の作業一覧も更新する。

### 完了の条件（全部そろって完了）

1. `./deck.sh dev-check` が「合格」（`--base <ref>` で比べる元を変えられる。全体のテストは `--full`）。
2. レビュー役の指摘を直した（または直さない理由を書いた）。
3. 状態の記録と HANDOFF を更新した。
4. `git commit` した（**push はしない**。箱の git の作者は箱ごとに設定済み）。
5. 上の「報告の型」で報告した。
