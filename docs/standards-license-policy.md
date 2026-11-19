# 規格の利用可否の判断方法（保存・AI利用）

状態: 2026-11-19 調査・判定コード追加。ResearchDataHub で書き、同日 ControlDeck への統合に合わせて移した（`docs/PLAN-knowledge-platform.md`）。判定モジュール `backend/app/knowledge/standards_license.py` と単体テスト `backend/tests/test_standards_license.py` は実装済み。**規格DB（standards 分野）への取り込み・検索への組み込みは未実装**であり、現在の取り込みや検索の挙動は変えていない。

本書は法律判断ではない。規約の読み方とプロジェクトとしての決め方を記録し、後から誰でも同じ手順で確認・更新できるようにするためのもの。判断に迷う場合や、契約で AI 利用を認めさせたい場合は、発行団体または販売店（日本では JSA が ISO/IEC の窓口）に書面で確認する。

## 1. 結論（2026-11-19 時点）

| 入手経路 | 本文をサーバーに置く | 埋め込み・ローカルLLM | クラウドLLM |
|---|---|---|---|
| ISO / IEC / JIS（JSA Webdesk）/ ASTM / IEEE を購入 | ×（個人の1部まで） | × | × |
| JISC 閲覧・ISO OBP などの無料閲覧 | ×（閲覧のみ） | × | × |
| ITU-T・RFC など自由配布の文書 | ○ | 確認者の記録が必要（既定は×） | 同左 |
| 組織契約・個別の書面許諾 | 契約に書かれた範囲 | 契約に書かれた範囲 | 契約に書かれた範囲 |
| 法令・省令・告示・通達（例: 電気設備の技術基準の解釈） | ○ | ○ | ○ |
| 自分のメモ（本文を転記しないもの） | ○ | ○ | ○（ただし機密区分は別に判定） |

**どの経路でも許されるのは、規格番号・題名・版・発行日・条項番号といったメタデータの索引**である。購入した規格は「何という規格の何条を見るべきか」までを ResearchDataHub が示し、本文は利用者が自分の正規の複製で読む、という使い方になる。

## 2. 判断の手順

1. **入手経路（instrument）を決める。** 同じ規格でも、店頭購入・無料閲覧・組織契約で適用される規約が違う。経路が記録されていなければ、メタデータのみとする。
2. **その経路に適用される規約の原文を読む。** 購入なら販売店の利用許諾（JSA で買った ISO 規格には JSA の規約と ISO の条件の両方）、契約なら契約書。
3. **2 つの軸で読む。**
   - 保存: 台数制限、LAN・ネットワーク利用、複製部数、サーバー保存の可否
   - AI: 「artificial intelligence」「machine learning」「analogous computational purposes」「text and data mining」「vectorization / embedding」「AIツールに入力」「デジタルツール」への言及
4. **明示の許可がない限り「禁止」と読む。** 規約に AI の記載が無い場合も、確認者が判断を記録するまでは禁止のまま。埋め込みも AI 処理として扱う（ISO/IEC の販売店規約は vectorization・embedding を名指ししている）。
5. **記録する。** 文書ごとに、入手経路・規約の版・確認日・確認者・（契約なら）契約や許諾の参照番号・認められた保存範囲と AI 範囲を残す。
6. **1年ごとに見直す。** 2025〜2026 年に複数の団体が AI 条項を追加・改定した。確認から 365 日を超えた許可は、判定コードが自動で AI 利用を止める。

日本の著作権法第30条の4（情報解析）の例外は、利用規約という契約上の義務を消さない。ISO は各国の TDM 例外に依拠することを明示的に拒否している。

## 3. 発行団体ごとの調査結果

確認日はすべて 2026-11-19。「一次」は規約の原文を読んだもの、「二次」は図書館の案内などで間接的に確認したもの。二次のものは原文を読み直して `verified_primary` を更新すること。

| 団体 | 確認 | 規約と該当箇所 | 要点 |
|---|---|---|---|
| JSA Webdesk（JIS、JSA 経由の ISO/IEC） | 一次 | [電子書籍配信サービス及びネット通販サービス利用個別規約](https://webdesk.jsa.or.jp/common/W10K0030/?post_type=book_common&page_id=b_j_yakkan)（2026-10-01 改定）第2条第2項・第3項、第5条第4項 | 1台のコンピュータに限る。LAN その他ネットワークでの利用を禁止。いかなる形態のデジタルツール・AIツールへの入力も禁止。バックアップ1部と紙コピー1部のみ可 |
| IEC | 一次 | [IEC Webstore Terms & Conditions](https://webstore.iec.ch/en/terms-conditions)（2025-08-04 更新）Clause 2・3 | 機械学習・AI システムの開発・運用への使用を禁止。公開・私的を問わず AI ツールへの投入は IEC の明示的な許可が必要 |
| ISO | 二次（iso.org は自動取得で 403） | [Licence Agreement](https://www.iso.org/terms-conditions-licence-agreement.html)、[OBP Terms of Use](https://www.iso.org/home/store/iso-obp-terms.html)、[No-Cost CLA](https://www.iso.org/home/store/no-cost-access-cla.html)（2026-09-18） | 機械学習・AI・類似の計算目的での利用を禁止。例外は ISO・会員団体・正規販売店が別の許諾で明示した場合のみ。OBP は AI への取り込み・要約・言い換えを禁止。TDM 例外への依拠も拒否 |
| ASTM | 一次 | [ASTM License Agreement](https://www.astm.org/astm-license-agreement)（Web & Compass Ver 2025.06.26） | どのような形の AI とも一切使用不可。AI による派生物は ASTM 会長の書面許可が必要。2026.01.23 版は未読 |
| IEEE | 二次 | 大学図書館の案内による Xplore 契約条件 | AI システムへの提供、AI/LLM の発展に資する利用を禁止。IEEE 自身の現行条文は未読 |
| JISC 閲覧 | 二次 | 静岡県立・大阪府立図書館、東北大学図書館の案内 | 閲覧のみ。印刷・保存・ダウンロード不可。自動取得しない（JISC 閲覧画面のスクレイピングは規約違反になりうる） |
| ITU-T | 二次 | [ITU-T Recommendations](https://www.itu.int/en/ITU-T/publications/Pages/recs.aspx) | 多くの勧告は無料で、改変せずに複製・再配布できる。AI については記載を確認できていない。ITU-T と ISO/IEC の共通文書は ISO/IEC の条件 |
| IETF（RFC） | 未読 | [IETF Trust](https://trustee.ietf.org/) Legal Provisions | 一般に複製可能とされるが、現行条文を読めていない（取得時 404） |

UL、BSI、DIN、CEN/CENELEC など上記以外の団体は未調査。判定コードでは発行団体不明として扱い、メタデータのみとなる。

### JIS と IEC が同一内容（IDT）の場合

JIS C 61000-4-2 のように IEC 規格の翻訳である JIS も、適用されるのは**入手した出版物の規約**（JSA で JIS を買えば JSA の規約）である。原規格の団体の条件も重なるため、どちらか厳しい方に従う。

## 4. 判定コードの使い方

`backend/app/knowledge/standards_license.py`（標準ライブラリのみ。`backend/` で実行）。

```sh
python -m app.knowledge.standards_license "JIS C 61000-4-2:2012"            # 購入として判定
python -m app.knowledge.standards_license "ITU-T G.709" open_publication   # 経路を指定
```

結果は JSON で、`storage`・`ai_use`・許される操作（`operations`）・再確認の要否（`needs_review`）・判断理由（`reasons`、日本語）を返す。

| 要素 | 役割 |
|---|---|
| `PUBLISHERS` | 発行団体ごとの規約スナップショット。URL・規約の版・確認日・一次/二次・要点。**通常の購入・閲覧経路の上限**として働く |
| `identify_publisher()` | 規格番号から発行団体を推定（全角も可）。推定できなければ None → メタデータのみ |
| `LicenseRecord` | 文書ごとの記録（入手経路、契約参照番号、確認者、確認日、認められた範囲） |
| `evaluate()` | 2軸の判定。記録は上限を下げることはできるが、超えることはできない。上限を超えられるのは、参照番号と確認者のある組織契約・書面許諾だけ |
| `operations_for()` / `allowed()` | 判定を、メタデータ索引・本文保存・全文検索・埋め込み・ローカルLLM・クラウドLLMの具体的な操作に対応づける |

### fail-closed の規則（テストで固定）

- 入手経路が不明、発行団体が不明 → メタデータのみ
- 契約・許諾で参照番号・確認者・確認日のどれかが欠ける → メタデータのみ
- 確認から 365 日を超えた → AI 利用を停止（本文の全文検索は契約範囲内なら残す）
- 規約スナップショットが二次資料のみ、または記録時と規約版が違う → `needs_review`
- 既定で AI を許す団体スナップショットは無い。追加する場合は、本書の表に一次資料の根拠を書いてからテストを変更する

## 5. 規約を調べ直す方法

1. 表の URL を開き、改定日を確認する。改定日が `PUBLISHERS[...].terms_version` と違えば読み直す。
2. 次の語で本文を検索する: `artificial intelligence` `machine learning` `analogous computational` `text and data mining` `vectoriz` `embedding` `network` `LAN` `single user` `server`、日本語では「人工知能」「AI」「学習」「デジタルツール」「ネットワーク」「1台」「複製」。
3. 該当条項の番号と要旨を `finding` に、改定日を `terms_version` に、読んだ日を `checked_on` に書く。原文を読めたら `verified_primary=True` にする。
4. 結論が変わった場合は、本書の §1 と §3、テスト `test_no_snapshot_allows_ai_by_default` を同じ変更で更新する。

## 6. 規格DBへの組み込み（後続 PR の契約）

- 規格DB（`data_dir/knowledge/standards.sqlite3`）の各文書に `LicenseRecord` 相当の列を持たせ、取り込み時と検索時の両方で `evaluate()` を通す。本文の保存・FTS 登録・埋め込み・LLM への受け渡しの前に `allowed()` を必ず確認する。
- 判定の結果と理由は監査ログに残す。本文や秘密値は書かない。
- 判定が下がった（規約改定・期限切れ）場合、既存の埋め込みは使用を止め、削除ジョブの対象にする。本文の保存が許されなくなった場合も同じ。
- 「アイデアの流出防止」のための機密区分（`app/knowledge/store.py` の sensitivity）とは別の軸である。規格の利用条件で許されても、利用者の未公開アイデアを伴う問い合わせは機密区分の規則でクラウドへ出さない。
