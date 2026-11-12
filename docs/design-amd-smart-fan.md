# AMD GPU Smart Fan Control — 設計・安全性・検証手順

Status: Draft PR / R9700実機未検証

## 目的と範囲

ControlDeckの Settings > ファンコントロール から、外部ファン制御ツールなしにLinuxのamdgpu標準sysfsでGPUファンを制御する。対象はGPU本体（edge）・junction/hotspot・VRAM/memoryなど、選択GPUのhwmonで実際に公開される温度センサー。CPU・ケース・システムRAMのファンは制御対象外。

- AMD PMFWがファンカーブをハードウェア上で継続実行。ControlDeckは3秒ごとにセンサー別の目標温度までのheadroomを比較し、余裕が最小のセンサーを優先してカーブの最低PWMを補正する。
- 加速は即時、減速は180秒保持後、45秒あたり最大5ポイント。低温でも35%以上の最低PWM、hotspot高温ではファームウェア上で最大PWMとなるカーブを設定。ソフトウェア停止時も最後のカーブはPMFWに残る。
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

backend/tests/test_fan_control.py / test_fan_strategy.py でGPU hwmon/OD_RANGEの模擬、危険曲線の拒否、メモリの温度優先、欠損温度、RPM=0、OFF時リセットとパス検証を確認する。

cd backend && ../.venv/bin/python -m pytest tests/test_fan_control.py tests/test_fan_strategy.py tests/test_amd_gpu_profiles.py -q
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

## LLM断続負荷・適応フィードフォワード/PI制御

ControlDeckはGPU使用率の瞬間0%/100%をファン速度の直接入力に**使わない**。3秒間隔でGPU hwmonが公開するedge/hotspot/VRAM等の全温度センサー、power1_average/power1_input（µW）、power1_capを読み、以下の制御を行う。

1. **独立安全監督**：センサーごとの目標までの最小余裕（headroom）と温度上昇速度から保守的な最低ファン指令を求める。危険温度では最大ファン速度を優先し、学習値で弱められない。
2. **温度フィードバック**：正の温度変化率を20秒先まで外挿。先読みした温度誤差に対するPI補償を行う。積分器は0〜30に制限し、100%飽和時に積分を止める/減らす（anti-windup）。低温域では積分を解消する。
3. **消費電力フィードフォワード**：瞬間GPU使用率ではなく、消費電力を上昇20秒/減少120秒の非対称EWMAで平滑化。GPUの電力上限に対する相対負荷から予測加速量を生成する。電力センサー取得失敗時は温度のみで安全制御を継続し、Zero RPM停止許可は禁止する。
4. **受動オンライン学習**：通常運転から温度の逼迫と温度上昇傾向を観測してFF係数（25〜55）、P係数（1.8〜3.2）を遅い速度で調整する。PI積分項は実行時だけ保持し、係数のみ30分以上の間隔でローカルJSONに保存。意図的な100%負荷実験・ファン探索・発熱増加を行わない。学習係数による最適性の数学的保証はない。
5. **非対称の速度制御**：温度上昇方向は即時反映（10秒書込抑制も免除）。下降方向は180秒の維持を経て45秒ごと最大5ポイントだけ減速。LLMのデコード/待機など断続動作でファンを細かく上げ下げしない。低温であっても原則アイドル最低PWM（初期35%）を維持する。

## Zero RPMを許可する任意設定

- 独立した \`allow_zero_rpm: false\` が初期設定。OFF時は定速ではなく、35%以上のファームウェア温度カーブを使い**低速常時回転を意図**する。
- ON時もファンを直接0%へ書き込まない。gpu_od/fan_ctrl/fan_zero_rpm_enableとfan_zero_rpm_stop_temperatureが実際に存在し、ファームウェアOD_RANGEが安全な設定を許す場合だけ利用する。停止温度はハードウェア範囲内の50〜55°C以下に抑える。RDNA4では最低設定温度が50°Cとなる個体もあり、固定40°Cが有効とは限らない。
- 入場条件：全温度センサーが目標に対して25°C以上の冷却余裕、edge≤42°C、hotspot≤45°C、VRAM≤50°C、温度上昇0.5°C/分以下、低く平滑化されたGPU消費電力を**連続10分間実測**していること。センサー、消費電力、OD_RANGEが欠落・不正な場合は停止させない。
- PMFW自動モードへ一度復帰した上でファームウェアの停止許可を有効化する。以後の停止/再始動はPMFWが判断し、ControlDeckの停止許可と実際のRPM=0とは区別してUIへ表示する。ゼロRPM中は手動カーブを書き換えない。
- 解除条件：温度の逼迫、edge≥46°C、hotspot≥49°C、VRAM≥55°C、一定以上の温度上昇（1°C/分）、平滑GPU消費電力の上昇など。停止解除側に遅延は入れない。新たな停止には再び長い安定時間が必要。
- 正常終了・明示OFF・故障ではPMFW autoに復帰。Zero RPM停止許可の解除が失敗したときも故障扱いにし、自動復帰を繰り返し試みる。**GPU/ドライバのファームウェアが再始動を保証するかはR9700実機テストが必要**。

## 設計の限界と必須実機試験

- **温度目標以下に必ず維持できるとは限らない**。GPU電力制限/thermal throttling/シャットダウン等のハードウェア保護には介入しない。RPM未取得ならゼロRPM後の再始動確認ができない。R9700のPMFW版によりmanual fan_curveとZero RPMの動作差があり、ユーザーPCの実測が必要。
- R9700側でUbuntu/kernel、OD_RANGE、全温度/RPM/電力、Zero RPM標準復帰動作、suspend/resume、LLM負荷100↔0%の断続パターン、長時間アイドルでの10分停止判定、停止中のVRAM加熱時の即再始動、センサー断・権限不足を検証する。
- テストは本体への追加前にローカル集中テスト（test_fan_control.py、test_fan_strategy.py）、全体pytest、Frontend build、iPhone幅320pxを確認する。R9700での実機確認がない場合はDraftを維持し、mainマージ/Releaseを行わない。GitHub CIはローカルの代替にしない。

参考: https://www.kernel.org/doc/html/latest/gpu/amdgpu/thermal.html
