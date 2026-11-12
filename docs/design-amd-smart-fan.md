# AMD GPU Smart Fan Control — 設計・安全性・検証手順

Status: Draft PR / R9700実機未検証

## 目的と範囲

ControlDeckの Settings > ファンコントロール から、外部ファン制御ツールなしにLinuxのamdgpu標準sysfsでGPUファンを制御する。対象はGPU本体（edge）・junction/hotspot・VRAM/memoryなど、選択GPUのhwmonで実際に公開される温度センサー。CPU・ケース・システムRAMのファンは制御対象外。

- AMD PMFWがファンカーブをハードウェア上で継続実行。ControlDeckは5秒ごとにセンサー別の目標温度までのheadroomを比較し、余裕が最小のセンサーを優先してカーブの最低PWMを補正する。
- 加速は即時、減速は5ポイント/周期以内。低温でも35%以上の最低PWM、hotspot高温ではファームウェア上で最大PWMとなるカーブを設定。ソフトウェア停止時も最後のカーブはPMFWに残る。
- 固定権限helper以外にroot処理を置かず、入力は有効なPCI BDF、5点カーブと数値のみ。任意パス・任意シェル実行・外部プログラムのインストールは不要。
- 初期状態はOFF。有効化済み設定はControlDeckデータディレクトリにmode 0600で原子的に保存し、再起動後のバックエンドが再適用。正常終了と明示OFFではファームウェアautoへ復帰（r→c）。
- ハードウェアが公開するtempN_crit / tempN_emergencyより最低5℃/10℃低い目標を強制。温度センサー消失、書込失敗、60℃以上・指令70%以上でRPMゼロが2回連続の場合はautoへ復帰を試み、制御を無効化し、エラーを表示する。

## セーフティ上の制約

温度目標は保証できる上限ではない。冷却能力不足・周辺温度・取付・電力・ケース通気のため実測が設定値を超える場合がある。GPU本体が実装するthermal throttlingやシャットダウン保護を上書きしない。

GPUのファンカーブが直接使う温度はhotspotで、VRAM温度はControlDeckの稼働中にソフトウェアでフィードバックする。アプリが予期せず停止した場合はVRAM温度についての閉ループ補正は中断し、最後の保守的なファームウェア曲線だけが有効になる。ファンRPMが取得不能なGPUでは回転停止を監視できない。VRAM温度自体が公開されないGPUではVRAM温度を目標制御しない。

driverにfan_curveが公開されない場合、OD_RANGEが0〜0など無効の場合、5点でない場合、helper/権限が無い場合は制御開始を拒否する。カーネル引数ppfeaturemaskやGPUの周波数・電力制限は自動変更しない。同じsysfsノードを操作する他のファン制御ツールとの同時利用は不可。

## API

GET /api/v1/system/fan-control: system.view権限でGPU、センサー、温度、RPM、状態を閲覧する。
PUT /api/v1/system/fan-control: settings.manage権限 + CSRF + auditで設定変更。
リクエスト例: {"enabled":true,"bdf":"0000:03:00.0","min_percent":35,"targets_c":{"temp1":75,"temp2":90,"temp3":85}}

対象GPUはサーバ側で検出したBDFに限定し、指定したセンサー目標の存在・範囲・ハードウェアcritを検証する。

## 開発用テスト（実施前）

backend/tests/test_fan_control.py でGPU hwmon/OD_RANGEの模擬、危険曲線の拒否、メモリの温度優先、欠損温度、RPM=0、OFF時リセットとパス検証を確認する。

cd backend && ../.venv/bin/python -m pytest tests/test_fan_control.py tests/test_amd_gpu_profiles.py -q
cd backend && ../.venv/bin/python -m pytest -q
cd frontend && npm run build

## R9700 実機確認（未実施）

1. 読み取り専用でuname -r、sys/class/drm/card*/device/hwmon/hwmon*/temp*_label/input/crit、fan1_input、gpu_od/fan_ctrl/fan_curve を確認し、対象R9700のBDFとOD_RANGEを特定する。
2. PC側で ./deck.sh service を実行して既存helper/sudoersの更新を登録する。
3. Settingsを開いてOFFから開始し、温度センサー・対象GPU・crit値・RPM取得の有無を表示確認する。
4. 安定したアイドル状態で明示ON。RPMが上昇するか監視する。VRAM中心のLLM負荷でもファン指令が増加するか、目標温度を超過しないかを測定。超過ならOFFにし冷却条件を確認する。
5. アイドル復帰時の回転数の揺れ、再起動/正常終了/再有効化/サスペンド復帰、helper無効化/センサー欠損時の復帰を確認する。
6. ローカルpytest、Frontend build、PCブラウザ、iPhone幅320pxで実機操作を確認する。手動CIは必要な場合のみ実施する。

## ロールバック

Settingsで「標準自動制御に戻す」をクリックし、温度/RPMを監視する。復帰が失敗した場合はGPU側ファンカーブの競合を調べてからOS/ドライバを再起動し、標準制御を回復していることを確認する。

参考: https://www.kernel.org/doc/html/latest/gpu/amdgpu/thermal.html

## 追加仕様: LLMバースト負荷に対する静音性とZero RPM (2026-11-12)

- LLMの使用率が0%/100%を往復してもGPU使用率はファン制御入力に使用せず、全温度センサーと約15秒以上の温度上昇傾向のみを使う。3秒周期で監視する。
- 上昇方向は待たない。headroomの厳しいセンサー、または上昇勾配から目標到達まで1/2/4分を切る予測時は、カーブ下限を即時引き上げる。従来の10秒sysfs書込制限を**加速時のみ**解除する。
- 低下方向は冷却余裕が戻った状態が連続180秒続くまで回転を保持する。以後も45秒につき最大5%しか減速せず、温度上昇した場合は降下待ちを破棄する。
- 通常は最低35%指令（設定30〜70%）を継続。静音停止は独立した **allow_zero_rpm: false** の明示ON/OFF設定。設定がOFFならPMFWのnative Zero RPM許可を解除し、ファンは最低指令に沿って常時回転する意図。ただし実際のRPMはハードウェア/SMU動作によるため実機確認が必要。
- Zero RPMはAMD標準の `fan_zero_rpm_enable` と `fan_zero_rpm_stop_temperature` が両方あり、STOP_TEMPのOD_RANGEに40°Cが含まれる場合のみ有効化可能。PWMを0%に強制しない。GPUにハードウェアの安全停止温度40°Cを設定してから許可する。ファームウェアが温度上昇時に独立して起動できることが必要。
- 温度停止許可を与える前に、全温度が余裕25°C以上あり、VRAM≤50°C、hotspot≤45°C、edge≤42°C（名称不明センサーは≤42°C）、20秒程度の観測で上昇が0.5°C/分以下の状態を**5分間**確認する。過去の危険温度からの冷却直後やLLM短時間のアイドルではZero RPMにならない。
- 停止許可後は、最悪センサーのheadroomが20°C未満、VRAM≥55°C、hotspot≥49°C、edge≥46°C、または20秒程度の上昇勾配が1°C/分以上で、ControlDeck側の停止許可を即解除。PMFW自身の40°C停止温度が独立した上限になる。
- Zero RPMが有効な間のRPM=0は故障とみなさない。許可を解除しても高温・指令≥70%の状態で2サイクルRPM=0なら既存の故障処理へ移行。native制御インターフェースがない/不正な範囲ならZero RPM設定欄は無効。
- アプリ強制終了時は各センサーへのソフトウェア側監視が失われる。この場合にも温度上昇を監視するPMFW native Zero RPMの停止温度(40°C)と、最大PWMへ達する基本ファンカーブが重要な安全策。ユーザー設定値以下の保証はできない。

## 追加の検証
1. VRAM負荷100%→0%を1分周期で繰り返すLLMワークロードを使い、180秒の保持と45秒/5%ずつの減速、先行温度上昇時の即時加速を測る。
2. native Zero RPM未対応GPUではUIが無効になること、対応GPUでは5分低温安定後にのみ許可されること、温度急上昇で即解除されることを検証。
3. Zero RPM許可は実機側PMFW挙動に依存するため、実際にRPM=0になった時にHotspot・VRAMの温度と再始動を必ず検証。実際にファンが再始動しない場合は機能OFFに戻す。
4. 既存のfan_curve/AMD電力・クロックの併用時に、native Zero RPMコマンドがファンカーブの適用を意図せず解除しないか確認。必要なら状態の再照会/再適用試験を追加。
