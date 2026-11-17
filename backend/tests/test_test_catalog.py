"""テストの目録の決まりを守らせる。

目的: 新しいテストファイルに「何を・なぜ確かめるか」（目的と根拠）を書かせ、docs/test-catalog.md から
重複を探せるようにする。根拠: 2026-11-17 の利用者の決まり（同じ項目を確かめるテストを増やさず、
目録を元に統廃合する）。目的の書かれていない既存のファイルは下の一覧にだけ残し、一覧は減らす一方にする。
"""
import ast
from pathlib import Path

TESTS = Path(__file__).parent

# 目的（ファイル冒頭の docstring）が未記入の既存ファイル。書いたら一覧から消す（新しく足さない）。
LEGACY_WITHOUT_PURPOSE = {
    "test_access_management.py",
    "test_addon_agent_mcp.py",
    "test_addon_api.py",
    "test_addon_bridge.py",
    "test_addon_contract.py",
    "test_addon_device_sessions.py",
    "test_addon_execution.py",
    "test_addon_failure_context.py",
    "test_addon_health.py",
    "test_addon_proxy.py",
    "test_addon_registry.py",
    "test_addon_relay_tasks.py",
    "test_addon_retrieval_ai.py",
    "test_addon_runtime_ai.py",
    "test_addon_runtime_auth.py",
    "test_addon_runtime_files.py",
    "test_addon_runtime_gateway.py",
    "test_addon_runtime_job_credentials.py",
    "test_addon_runtime_resources_jobs.py",
    "test_alerts.py",
    "test_amd_gpu_profiles.py",
    "test_api_rate_limit.py",
    "test_app_gpu_usage.py",
    "test_app_health.py",
    "test_apps_url.py",
    "test_assistant_planner.py",
    "test_auth.py",
    "test_bootstrap_retired_app.py",
    "test_chat_asr.py",
    "test_chatflow.py",
    "test_credential_security.py",
    "test_database_runtime.py",
    "test_disk_monitoring.py",
    "test_fake_addon_harness.py",
    "test_feature_boot_restore.py",
    "test_features.py",
    "test_files_api.py",
    "test_generation_provenance.py",
    "test_gpu_monitoring.py",
    "test_inline_code.py",
    "test_jobs_stream_cleanup.py",
    "test_llama_reload_cost.py",
    "test_llama_resource_provider.py",
    "test_llama_runtime.py",
    "test_llm_providers.py",
    "test_logs_redaction.py",
    "test_maintenance.py",
    "test_metrics_history.py",
    "test_migrations.py",
    "test_opencode_feature.py",
    "test_passkeys.py",
    "test_paths.py",
    "test_plugin_sdk.py",
    "test_power_schedule.py",
    "test_project_lab.py",
    "test_rag_db_nodes.py",
    "test_ratelimit.py",
    "test_release_bundle_feature.py",
    "test_release_bundle_publisher_hardening.py",
    "test_remote_desktop.py",
    "test_resource_api.py",
    "test_resource_broker.py",
    "test_resource_devices.py",
    "test_resource_load_profile_persistence.py",
    "test_resource_queue_receipt.py",
    "test_resource_scheduler.py",
    "test_resource_telemetry.py",
    "test_runtime_policy.py",
    "test_runtime_provider.py",
    "test_session_lifetime.py",
    "test_skill_execution.py",
    "test_system_services.py",
    "test_systemd_unit.py",
    "test_terminal_automation.py",
    "test_terminals.py",
    "test_totp.py",
    "test_totp_policy.py",
    "test_workflow_artifacts.py",
    "test_workflow_cache.py",
    "test_workflow_events.py",
    "test_workflow_flow_control.py",
    "test_workflow_intelligence.py",
    "test_workflow_llm_autoload.py",
    "test_workflow_node_catalog.py",
    "test_workflow_nodes_v2.py",
    "test_workflow_pauses.py",
    "test_workflow_queue.py",
    "test_workflow_resilience.py",
    "test_workflow_runner.py",
    "test_workflow_state.py",
    "test_workflow_subflow_map.py",
    "test_workflow_try_control.py",
    "test_workflows.py",
}


def _purpose(path: Path) -> str:
    return (ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or "").strip()


def test_every_new_test_file_states_its_purpose():
    missing = {p.name for p in TESTS.glob("test_*.py") if not _purpose(p)}
    new = sorted(missing - LEGACY_WITHOUT_PURPOSE)
    assert not new, f"テストファイルの冒頭に目的と根拠の docstring を書いてください: {new}"


def test_legacy_list_only_shrinks():
    """目的を書いた（または消した）ファイルは一覧から外す。一覧に残ったままだと、次に未記入へ戻っても気づけない。"""
    stale = sorted(name for name in LEGACY_WITHOUT_PURPOSE
                   if not (TESTS / name).exists() or _purpose(TESTS / name))
    assert not stale, f"LEGACY_WITHOUT_PURPOSE から外してください: {stale}"


def test_catalog_renders():
    from app.maintenance.test_catalog import render

    text = render()
    assert text.startswith("# テストの目録") and "test_test_catalog.py" in text
