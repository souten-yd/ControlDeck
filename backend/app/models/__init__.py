from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
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
    arguments_json: Mapped[str] = mapped_column(Text, default="[]")
    environment_json_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_as_user: Mapped[str | None] = mapped_column(String(64), nullable=True)
    auto_start: Mapped[bool] = mapped_column(Boolean, default=False)
    # no / on-failure / always / on-success
    restart_policy: Mapped[str] = mapped_column(String(32), default="no")
    stop_timeout_seconds: Mapped[int] = mapped_column(Integer, default=20)
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
