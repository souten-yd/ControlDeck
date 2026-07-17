from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    # 権限文字列の JSON 配列
    permissions_json: Mapped[str] = mapped_column(Text, default="[]")

    users: Mapped[list["User"]] = relationship(back_populates="role")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    password_hash: Mapped[str] = mapped_column(String(256))
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    totp_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 暗号化した使い捨てリカバリーコードの JSON 配列
    recovery_codes_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    role: Mapped[Role] = relationship(back_populates="users")


class UserSession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    session_token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship()


class ManagedApplication(Base):
    __tablename__ = "managed_applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    # python_script / shell_script / executable / systemd_service
    application_type: Mapped[str] = mapped_column(String(32))
    icon_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    working_directory: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    executable_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    script_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    python_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # url_shortcut タイプ用の URL
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # Web ボタンで開くポート（サーバーアプリ用。未設定時は検出ポートから選択）
    web_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    arguments_json: Mapped[str] = mapped_column(Text, default="[]")
    environment_json_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_as_user: Mapped[str | None] = mapped_column(String(64), nullable=True)
    auto_start: Mapped[bool] = mapped_column(Boolean, default=False)
    # no / on-failure / always / on-success
    restart_policy: Mapped[str] = mapped_column(String(32), default="no")
    stop_timeout_seconds: Mapped[int] = mapped_column(Integer, default=20)
    health_check_json: Mapped[str] = mapped_column(Text, default="{}")
    # systemd 由来のキャッシュ状態（一覧の初期表示用。真の状態は都度 systemd へ問い合わせ）
    status: Mapped[str] = mapped_column(String(16), default="STOPPED")
    systemd_unit_name: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(64), index=True)
    resource_type: Mapped[str] = mapped_column(String(32), default="")
    resource_id: Mapped[str] = mapped_column(String(64), default="")
    result: Mapped[str] = mapped_column(String(16), default="success")
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(256), default="")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")


class WorkflowVersion(Base):
    """ワークフロー定義のスナップショット（保存ごとに記録、ロールバック用）。"""

    __tablename__ = "workflow_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id"), index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    definition_json: Mapped[str] = mapped_column(Text, default="{}")
    note: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkflowSecret(Base):
    """{{secrets.名前}} で参照する暗号化シークレット（API キー等）。"""

    __tablename__ = "workflow_secrets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    value_encrypted: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Conversation(Base):
    """永続チャットの会話。localStorage ではなく DB に保存し、端末間で共有・復元する。"""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="新しい会話")
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ChatMessage(Base):
    """会話内のメッセージ。assistant はサーバー側ジョブが生成し、部分出力を随時保存する。"""

    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user / assistant
    content: Mapped[str] = mapped_column(Text, default="")
    thinking: Mapped[str] = mapped_column(Text, default="")  # 推論トレース（think 有効時）
    # generating / completed / failed / interrupted / canceled（user は常に completed）
    status: Mapped[str] = mapped_column(String(16), default="completed")
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str] = mapped_column(String(128), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    # モード別の付随データ（web/academic/deep の出典など） {"mode":..., "sources":[...]}
    meta_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ChatReference(Base):
    """会話内で再利用する文献。短いIDは会話内だけで一意とする。"""

    __tablename__ = "chat_references"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="uq_chat_reference_sequence"),
        UniqueConstraint("conversation_id", "short_id", name="uq_chat_reference_short_id"),
        UniqueConstraint("conversation_id", "canonical_key", name="uq_chat_reference_canonical"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    short_id: Mapped[str] = mapped_column(String(12))
    canonical_key: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(24), default="page")
    title: Mapped[str] = mapped_column(String(500), default="")
    url: Mapped[str] = mapped_column(String(2048), default="")
    provider: Mapped[str] = mapped_column(String(128), default="")
    excerpt: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Job(Base):
    """サーバー主導ジョブの永続レコード（再起動復元・履歴用）。

    実行中の高速イベントストリームはメモリ（app/jobs/service.py）が担い、
    ここには状態・進捗・結果・主要イベントのスナップショットを記録する。
    """

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    # running/succeeded/failed/canceled/interrupted
    status: Mapped[str] = mapped_column(String(24), default="running", index=True)
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    progress_json: Mapped[str] = mapped_column(Text, default="{}")
    events_json: Mapped[str] = mapped_column(Text, default="[]")  # 末尾N件のスナップショット
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class JobControl(Base):
    """ジョブの実行制御metadata。既存jobs表を変更せず拡張する。"""

    __tablename__ = "job_controls"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "kind", "idempotency_key", name="uq_job_control_idempotency"),
    )

    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ElectricityDaily(Base):
    """日別の消費電力量・電気料金（電気代の正となる値。月別は日別の SUM）。"""

    __tablename__ = "electricity_daily"

    local_date: Mapped[str] = mapped_column(String(10), primary_key=True)  # YYYY-MM-DD
    energy_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    cost_yen: Mapped[float] = mapped_column(Float, default=0.0)
    # その日に実際に適用した単価（単価変更の履歴保持。過去料金を後から書き換えない）
    price_per_kwh_yen: Mapped[float] = mapped_column(Float, default=35.69)
    sample_duration_sec: Mapped[float] = mapped_column(Float, default=0.0)
    first_sample_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sample_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ElectricityState(Base):
    """現在の起動セッション復元用（boot ID 単位）。単一行（id=1）。"""

    __tablename__ = "electricity_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    boot_id: Mapped[str] = mapped_column(String(64), default="")
    session_energy_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    session_cost_yen: Mapped[float] = mapped_column(Float, default=0.0)
    last_input_power_w: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_sample_wall_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_persisted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    # ノードグラフ {nodes: [...], edges: [...]}（トリガーもノードとして含む）
    definition_json: Mapped[str] = mapped_column(Text, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class WorkflowExecution(Base):
    __tablename__ = "workflow_executions"

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id"), index=True)
    # QUEUED / RUNNING / SUCCEEDED / FAILED / CANCELED / TIMED_OUT
    status: Mapped[str] = mapped_column(String(16), default="QUEUED")
    trigger_type: Mapped[str] = mapped_column(String(32), default="manual")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    # ノードごとの実行結果 {node_id: {status, output, error, started_at, finished_at}}
    context_json: Mapped[str] = mapped_column(Text, default="{}")


class RemoteConnection(Base):
    __tablename__ = "remote_connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    # rdp / vnc / ssh
    protocol: Mapped[str] = mapped_column(String(8))
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer)
    username: Mapped[str] = mapped_column(String(128), default="")
    # パスワードや秘密鍵など機微なパラメータを暗号化した JSON
    secret_params_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 非機微パラメータ（解像度・色深度など）の JSON
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    # この PC 自身への接続（最上段固定・削除/追加不可の特別扱い）
    is_self: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NotificationChannel(Base):
    __tablename__ = "notification_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    # discord / slack / webhook
    channel_type: Mapped[str] = mapped_column(String(16))
    url_encrypted: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    # cpu_percent / memory_percent / gpu_percent / vram_percent / gpu_temp_c /
    # cpu_temp_c / disk_percent / app_down
    metric: Mapped[str] = mapped_column(String(32))
    # gt / gte / lt / lte
    operator: Mapped[str] = mapped_column(String(4), default="gt")
    threshold: Mapped[float] = mapped_column(Float, default=90.0)
    # 継続時間（秒）: この時間しきい値を超え続けたら発火
    duration_seconds: Mapped[int] = mapped_column(Integer, default=60)
    # 同一アラートの連続通知を抑制するクールダウン（秒）
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=600)
    app_id: Mapped[int | None] = mapped_column(ForeignKey("managed_applications.id"), nullable=True)
    channel_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("alert_rules.id"), index=True)
    rule_name: Mapped[str] = mapped_column(String(128), default="")
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    message: Mapped[str] = mapped_column(Text, default="")
    # active / resolved
    status: Mapped[str] = mapped_column(String(16), default="active")
    notified: Mapped[bool] = mapped_column(Boolean, default=False)


class MetricMinute(Base):
    """1 分平均のメトリクス履歴。"""

    __tablename__ = "metrics_minute"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    cpu_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    memory_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    gpu_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    vram_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    disk_read_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    disk_write_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_rx_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_tx_bps: Mapped[float | None] = mapped_column(Float, nullable=True)


class GitRepository(Base):
    """GitHub 管理: 登録リポジトリ（クローン先は config.git_apps_dir 配下）。"""

    __tablename__ = "git_repositories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    url: Mapped[str] = mapped_column(String(2048))
    path: Mapped[str] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
