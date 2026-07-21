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
    # 許可root内の追加ログファイル（正規化済み絶対pathのJSON、最大16件）
    log_files_json: Mapped[str] = mapped_column(Text, default="[]")
    # systemd 由来のキャッシュ状態（一覧の初期表示用。真の状態は都度 systemd へ問い合わせ）
    status: Mapped[str] = mapped_column(String(16), default="STOPPED")
    systemd_unit_name: Mapped[str] = mapped_column(String(128), default="")
    # user: systemctl --user / system: root所有allowlist + privileged helper
    systemd_scope: Mapped[str] = mapped_column(String(16), default="user")
    system_service_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    version: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(128), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    definition_json: Mapped[str] = mapped_column(Text, default="{}")
    input_schema_json: Mapped[str] = mapped_column(Text, default="{}")
    output_schema_json: Mapped[str] = mapped_column(Text, default="{}")
    checksum: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    workflow_version_id: Mapped[int | None] = mapped_column(ForeignKey("workflow_versions.id"), nullable=True)
    definition_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    runtime_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    last_event_sequence: Mapped[int] = mapped_column(Integer, default=0)


class WorkflowExecutionEvent(Base):
    """再接続時に再送できる、redact済みの実行イベント。"""

    __tablename__ = "workflow_execution_events"
    __table_args__ = (
        UniqueConstraint("execution_id", "sequence", name="uq_workflow_execution_event_sequence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[int] = mapped_column(ForeignKey("workflow_executions.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(48))
    node_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class WorkflowPause(Base):
    """Service再起動をまたいで解決できるWorkflowのhuman checkpoint。"""

    __tablename__ = "workflow_pauses"

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[int] = mapped_column(ForeignKey("workflow_executions.id"), index=True)
    node_id: Mapped[str] = mapped_column(String(64), index=True)
    pause_type: Mapped[str] = mapped_column(String(24), default="approval")
    message: Mapped[str] = mapped_column(Text, default="")
    approver: Mapped[str] = mapped_column(String(64), default="")
    form_schema_json: Mapped[str] = mapped_column(Text, default="{}")
    # PENDING / APPROVED / REJECTED / EXPIRED / COMPLETED / CANCELED
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    # 平文tokenは生成直後にSHA-256化して破棄し、DBにはhashだけを保存する。
    token_hash: Mapped[str] = mapped_column(String(64), default="")
    response_json: Mapped[str] = mapped_column(Text, default="{}")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class WorkflowQueueItem(Base):
    """Workflow内で実行・service再起動を越えて保持するbounded FIFO item。"""

    __tablename__ = "workflow_queue_items"
    __table_args__ = (
        UniqueConstraint("workflow_id", "queue_name", "sequence", name="uq_workflow_queue_sequence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id"), index=True)
    queue_name: Mapped[str] = mapped_column(String(64), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    payload_json: Mapped[str] = mapped_column(Text)
    payload_size_bytes: Mapped[int] = mapped_column(Integer)
    enqueued_by_execution_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_executions.id"), nullable=True, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class WorkflowCacheEntry(Base):
    """Workflow内でservice再起動を越えて共有する期限付きJSON cache。"""

    __tablename__ = "workflow_cache_entries"
    __table_args__ = (
        UniqueConstraint("workflow_id", "namespace", "cache_key", name="uq_workflow_cache_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id"), index=True)
    namespace: Mapped[str] = mapped_column(String(64), index=True)
    cache_key: Mapped[str] = mapped_column(String(128))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_size_bytes: Mapped[int] = mapped_column(Integer)
    written_by_execution_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_executions.id"), nullable=True, index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class WorkflowStateEntry(Base):
    """Workflow内で共有する期限なし・型固定・version付きJSON state。"""

    __tablename__ = "workflow_state_entries"
    __table_args__ = (
        UniqueConstraint("workflow_id", "namespace", "state_key", name="uq_workflow_state_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id"), index=True)
    namespace: Mapped[str] = mapped_column(String(64), index=True)
    state_key: Mapped[str] = mapped_column(String(128))
    value_type: Mapped[str] = mapped_column(String(16))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_size_bytes: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)
    written_by_execution_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_executions.id"), nullable=True, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class WorkflowBusinessEvent(Base):
    """Workflow間で配送するredact済み業務イベントoutbox。"""

    __tablename__ = "workflow_business_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    event_name: Mapped[str] = mapped_column(String(128), index=True)
    source_workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id"), index=True)
    source_execution_id: Mapped[int] = mapped_column(ForeignKey("workflow_executions.id"), index=True)
    source_node_id: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_size_bytes: Mapped[int] = mapped_column(Integer)
    lineage_json: Mapped[str] = mapped_column(Text, default="[]")
    hop: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowEventDelivery(Base):
    """業務イベントoutboxのsubscriber別配送状態。"""

    __tablename__ = "workflow_event_deliveries"
    __table_args__ = (
        UniqueConstraint("business_event_id", "target_workflow_id", name="uq_workflow_event_delivery_target"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    business_event_id: Mapped[int] = mapped_column(ForeignKey("workflow_business_events.id"), index=True)
    target_workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id"), index=True)
    target_execution_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_executions.id"), nullable=True, index=True,
    )
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class WorkflowArtifact(Base):
    """Application-owned storageを指すWorkflow成果物metadata。本文はDBへ保存しない。"""

    __tablename__ = "workflow_artifacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[int] = mapped_column(ForeignKey("workflow_executions.id"), index=True)
    node_run_id: Mapped[int | None] = mapped_column(ForeignKey("workflow_node_runs.id"), nullable=True, index=True)
    node_id: Mapped[str] = mapped_column(String(64), index=True)
    storage_key: Mapped[str] = mapped_column(String(160), unique=True)
    filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(255), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[str] = mapped_column(String(64), default="")
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkflowNodeRun(Base):
    """再現・単体再実行に使うノード単位のredact済み実行記録。"""

    __tablename__ = "workflow_node_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[int] = mapped_column(ForeignKey("workflow_executions.id"), index=True)
    node_id: Mapped[str] = mapped_column(String(64), index=True)
    node_type: Mapped[str] = mapped_column(String(64))
    node_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(24), default="PENDING")
    resolved_inputs_json: Mapped[str] = mapped_column(Text, default="{}")
    outputs_json: Mapped[str] = mapped_column(Text, default="{}")
    error_json: Mapped[str] = mapped_column(Text, default="{}")
    logs_json: Mapped[str] = mapped_column(Text, default="[]")
    artifacts_json: Mapped[str] = mapped_column(Text, default="[]")
    token_usage_json: Mapped[str] = mapped_column(Text, default="{}")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    cache_source: Mapped[str] = mapped_column(String(64), default="")
    schema_version: Mapped[int] = mapped_column(Integer, default=1)


class WorkflowPinnedData(Base):
    """draftテスト専用の固定出力。WorkflowVersion/公開定義には含めない。"""

    __tablename__ = "workflow_pinned_data"
    __table_args__ = (UniqueConstraint("workflow_id", "node_id", name="uq_workflow_pinned_node"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id"), index=True)
    node_id: Mapped[str] = mapped_column(String(64))
    output_json: Mapped[str] = mapped_column(Text, default="{}")
    source_execution_id: Mapped[int | None] = mapped_column(ForeignKey("workflow_executions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class WorkflowTestCase(Base):
    """保存入力と期待値を用いるworkflow回帰テスト。"""

    __tablename__ = "workflow_test_cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    inputs_json: Mapped[str] = mapped_column(Text, default="{}")
    mocks_json: Mapped[str] = mapped_column(Text, default="{}")
    expected_outputs_json: Mapped[str] = mapped_column(Text, default="{}")
    assertions_json: Mapped[str] = mapped_column(Text, default="[]")
    last_execution_id: Mapped[int | None] = mapped_column(ForeignKey("workflow_executions.id"), nullable=True)
    last_status: Mapped[str] = mapped_column(String(16), default="NEVER")
    last_result_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ApplicationProject(Base):
    """Application Builderの独立draft。生成物・build状態はPhase Aでは持たない。"""

    __tablename__ = "application_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    workflow_id: Mapped[int | None] = mapped_column(ForeignKey("workflows.id"), nullable=True, index=True)
    application_spec_json: Mapped[str] = mapped_column(Text, default="{}")
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    target: Mapped[str] = mapped_column(String(32), default="csharp")
    application_type: Mapped[str] = mapped_column(String(32), default="web")
    ui_framework: Mapped[str] = mapped_column(String(64), default="aspnet-blazor")
    status: Mapped[str] = mapped_column(String(16), default="draft")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ApplicationBuild(Base):
    """決定的source snapshotを独立systemd user unitでbuildしたdurable記録。"""

    __tablename__ = "application_builds"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("application_projects.id"), index=True)
    target_id: Mapped[str] = mapped_column(String(128))
    framework: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    unit_name: Mapped[str] = mapped_column(String(128), unique=True, default="")
    build_root: Mapped[str] = mapped_column(String(1024), default="")
    source_checksum: Mapped[str] = mapped_column(String(64), default="")
    archive_checksum: Mapped[str] = mapped_column(String(64), default="")
    generator_json: Mapped[str] = mapped_column(Text, default="{}")
    sdk_name: Mapped[str] = mapped_column(String(32), default="dotnet")
    sdk_path: Mapped[str] = mapped_column(String(1024), default="")
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=900)
    result: Mapped[str] = mapped_column(String(64), default="")
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_redacted: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApplicationBuildArtifact(Base):
    """Build root配下だけを指す成果物metadata。本文はDBへ保存しない。"""

    __tablename__ = "application_build_artifacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    build_id: Mapped[int] = mapped_column(ForeignKey("application_builds.id"), index=True)
    path: Mapped[str] = mapped_column(String(2048))
    kind: Mapped[str] = mapped_column(String(32), default="file")
    mime_type: Mapped[str] = mapped_column(String(256), default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
    # discord / slack / webhook / email
    channel_type: Mapped[str] = mapped_column(String(16))
    url_encrypted: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    # cpu_percent / memory_percent / gpu_percent / vram_percent / gpu_temp_c /
    # cpu_temp_c / disk_percent / app_down / app_health_failed / app_restart_loop / app_log_error
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


class AlertLogCursor(Base):
    """ログ条件の再起動耐性を保つストリーム別読取位置。ログ本文は保存しない。"""

    __tablename__ = "alert_log_cursors"
    __table_args__ = (UniqueConstraint("rule_id", "stream", name="uq_alert_log_cursor_stream"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("alert_rules.id"), index=True)
    stream: Mapped[str] = mapped_column(String(8))
    file_identity: Mapped[str] = mapped_column(String(64), default="")
    offset: Mapped[int] = mapped_column(Integer, default=0)


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


class MetricHour(Base):
    """1 時間平均の長期メトリクス履歴。"""

    __tablename__ = "metrics_hour"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), unique=True, index=True)
    minute_count: Mapped[int] = mapped_column(Integer, default=0)
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


class ProjectRun(Base):
    """Project Labのbrowser接続と独立したsystemd user service実行記録。"""

    __tablename__ = "project_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), index=True)
    project_name: Mapped[str] = mapped_column(String(128), default="")
    profile_id: Mapped[str] = mapped_column(String(64))
    profile_type: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default="QUEUED", index=True)
    unit_name: Mapped[str] = mapped_column(String(128), unique=True, default="")
    command_json: Mapped[str] = mapped_column(Text, default="[]")
    environment_names_json: Mapped[str] = mapped_column(Text, default="[]")
    working_directory: Mapped[str] = mapped_column(String(1024), default="")
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=600)
    web_port: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    initial_artifacts_json: Mapped[str] = mapped_column(Text, default="{}")
    result: Mapped[str] = mapped_column(String(64), default="")
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_redacted: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProjectRunArtifact(Base):
    """ProjectRun開始後に作成・変更された成果物metadata。巨大file本文はDBへ保存しない。"""

    __tablename__ = "project_run_artifacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("project_runs.id"), index=True)
    path: Mapped[str] = mapped_column(String(2048))
    kind: Mapped[str] = mapped_column(String(32))
    mime_type: Mapped[str] = mapped_column(String(256), default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[str] = mapped_column(String(64), default="")
    change_type: Mapped[str] = mapped_column(String(16), default="created")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TerminalSnippet(Base):
    """管理者が登録し、Terminalから再利用するcode／prompt template。"""

    __tablename__ = "terminal_snippets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(320), default="")
    content: Mapped[str] = mapped_column(Text)
    variables_json: Mapped[str] = mapped_column(Text, default="[]")
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TerminalAutomationSchedule(Base):
    """Web processから独立したsystemd user timer用の実行定義。"""

    __tablename__ = "terminal_automation_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    snippet_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    parameters_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    mode: Mapped[str] = mapped_column(String(16), default="detached")
    target_session_id: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    working_directory: Mapped[str] = mapped_column(String(1024), default="")
    condition_type: Mapped[str] = mapped_column(String(24), default="always")
    condition_value: Mapped[str] = mapped_column(String(128), default="")
    recurrence: Mapped[str] = mapped_column(String(16), default="once")
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    run_if_missed: Mapped[bool] = mapped_column(Boolean, default=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="SCHEDULED", index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_result: Mapped[str] = mapped_column(String(32), default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_by_username: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TerminalCommandRun(Base):
    """Snippetの即時／予約実行を追跡するbounded log付きdurable記録。"""

    __tablename__ = "terminal_command_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int | None] = mapped_column(
        ForeignKey("terminal_automation_schedules.id"), nullable=True, index=True,
    )
    snippet_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    command_snapshot_encrypted: Mapped[str] = mapped_column(Text, default="")
    command_checksum: Mapped[str] = mapped_column(String(64), default="")
    mode: Mapped[str] = mapped_column(String(16), default="detached")
    target_session_id: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    working_directory: Mapped[str] = mapped_column(String(1024), default="")
    condition_type: Mapped[str] = mapped_column(String(24), default="always")
    condition_value: Mapped[str] = mapped_column(String(128), default="")
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    status: Mapped[str] = mapped_column(String(24), default="QUEUED", index=True)
    unit_name: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    output_path: Mapped[str] = mapped_column(String(1024), default="")
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_by_username: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
