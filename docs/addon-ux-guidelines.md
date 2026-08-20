# 拡張機能 UX ガイドライン

状態: Add-on Platform v2 normative / PR-0

本書はv2拡張機能の状態・待機・通知をControl Deckが一貫して描画するための規約です。
内部名のplugin/add-on/contribution/leaseはユーザー向け文言へ出さず、「拡張機能」に統一します。

## 状態は消さずに説明する

enabledなnavigationはhealth変化で出入りさせません。消えるのはdisable/uninstall時だけです。

| 状態 | 表示 | 必須の次アクション |
|---|---|---|
| healthy | chipなし | なし |
| degraded | 黄「一部機能が利用できません」 | 詳細を開く |
| unavailable | 灰「停止中」 | 再試行、設定、ログ、無効化のいずれか |
| setup_required | 青「セットアップが必要」 | checklistと再確認 |
| incompatible | 状態ページ | 必要versionと文書 |

navigationの存在と実行contributionのavailabilityは分離します。一部workerが停止してもnavigationは残し、
該当executorだけをdiscoveryから外します。

## 待機と最悪ケース

すべての待機は理由、queue位置、信頼度付き見込み、キャンセルを表示します。hostが描画するskeleton、
接続timeout、停止、setup、権限不足、version非互換には最低1つの実行可能な出口を置きます。
真っ白なiframe、無限wait、出口のないerrorは禁止です。embedded viewは8秒でhost状態画面へ切り替えます。

320pxの既定は`mobile: companion`です。Jobs、通知、直近asset、再実行と「デスクトップで開く」をhostが描画し、
PC workspaceを縮小して対応済みとは扱いません。

## 描画・入力

- labelはlocale fallback後24文字まで。制御文字を拒否し、省略時も詳細名へ到達可能にする
- iconはhost icon set名のみ。任意URL／inline SVGを許可しない
- degraded/unavailableの色はhostが決め、add-onに重大色を自己申告させない
- bridge handshake前は現在themeの背景skeletonを描画し、dark modeの白いFOUCを出さない
- route、戻る/進む、reload、共有URL、keyboard focusをhost shell内で維持する

## 通知

toastは成功・失敗・キャンセルの終端イベントだけに使い、進捗はJobsへ置きます。表示中workspaceのjobは
toastしません。add-onごとに6件/分、job IDでdedupeし、超過は「他N件」へまとめます。
toast本文は別tableへ重複保存せず、Job履歴と本文なしaudit/activityを正とします。

## セキュリティが見えるUX

enable前に要求capabilityを平易な文で表示し、詳細画面からgrantと直近activityへ到達可能にします。
file/projectはhost pickerによる都度grantとし、path文字列を入力させません。状態理由やerrorへtoken、path、
外部response本文を表示しません。
