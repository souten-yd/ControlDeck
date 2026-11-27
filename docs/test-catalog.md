# テストの目録

`./deck.sh test-catalog` が作る。作り直しても既存の手書きの説明は残る（コードに説明が無いものだけ引き継ぐ）。テストを足す前にここを検索し、同じ観点のテストがあれば
足さずにそれを直す・広げる。決まりは `AGENTS.md` の「不具合・診断・テストの決まり」。

- backend: 277 ファイル、2867 テスト（目的の未記入 93 ファイル）
- E2E: 152 spec、348 テスト

## backend（backend/tests）

### backend/tests/test_access_management.py

目的: **未記入**

- `test_user_and_custom_role_lifecycle_revokes_sessions_and_audits`
- `test_management_prevents_self_change_presets_and_privilege_escalation`
- `test_viewer_cannot_manage_users`

### backend/tests/test_addon_agent_mcp.py

目的: **未記入**

- `test_agent_mcp_token_is_user_bound_and_long_ttl_is_explicit`
- `test_agent_mcp_catalog_uses_public_ids_and_namespaces_duplicates`
- `test_the_tool_listing_carries_what_the_addon_wrote_in_its_contract` — 一覧に出す説明へ、Add-on が契約に書いた使い方を載せる。
- `test_stdio_bridge_protocol_and_tool_result`
- `test_stdio_bridge_marks_host_post_as_control_deck_request`
- `test_runtime_config_lets_opencode_drop_old_tool_output` — 古い道具の出力を捨てられるようにする。
- `test_runtime_config_projects_mcp_only_with_user_authority`
- `test_managed_project_id_accepts_only_direct_codedev_child`
- `test_project_output_grant_tool_is_project_scoped_and_opaque`
- `test_project_input_grant_tool_reads_only_files_inside_the_project` — project へ置いた絵を Add-on に読ませる券。
- `test_published_tool_schema_drops_length_bounds_but_validation_keeps_them` — モデルへ出すスキーマから長さ制約を落とす（検証側の制約は残す）。
- `test_mcp_token_renews_itself_while_the_session_is_in_use` — OpenCode の session は何日も開く。使われている限り期限で切らさない。
- `test_mcp_token_renewal_refuses_claims_it_cannot_trust`
- `test_output_grant_is_issued_automatically_for_the_current_project` — 置き先の grant を agent に作らせない。
- `test_auto_grant_refuses_addons_and_projects_outside_the_session` — 自動発行でも、明示発行と同じ範囲しか許さない。
- `test_a_big_contract_is_left_out_of_the_listing_and_fetched_by_name` — 道具定義の費用の 94% は説明ではなく schema である。
- `test_the_contract_tool_appears_only_when_contracts_are_withheld` — 境目が 0 なら従来どおり全部載せる。取りに来る道具も出さない。
- `test_a_long_tool_result_is_trimmed_and_the_whole_thing_is_left_in_a_file` — 道具の結果は、そのまま載せると文脈を食い尽くしうる。
- `test_the_result_directory_is_readable_without_asking` — 落とした全文を読むのに確認が入ると、削った意味が無くなる。
- `test_the_session_is_told_how_to_spend_its_context` — 文脈の使い方を system prompt へ足す。
- `test_the_contract_description_is_published_once_not_twice` — 契約の説明を、道具の説明と schema の両方に載せない。
- `test_the_session_declares_the_window_so_compaction_can_fire` — 窓の大きさを宣言しないと、自動圧縮は一度も動かない。
- `test_the_window_comes_from_the_instance_that_serves_the_model` — 窓の大きさは llama の instance が持っている。決め打ちにしない。
- `test_heavy_addon_tools_move_to_specialist_subagents` — 重い道具は主の会話から外し、専門の子に持たせる。
- `test_stdio_bridge_passes_host_rejection_reason_to_the_caller` — host が返した拒否理由をそのまま呼ぶ側へ渡す。
- `test_stdio_bridge_keeps_generic_message_when_the_host_is_unreachable` — 接続そのものが立たないときは status が無いので、従来どおりの 1 文で返す。
- `test_stdio_bridge_reports_status_when_the_rejection_body_is_unreadable` — 本文が JSON でなくても status だけは返す。無言で潰さない。

### backend/tests/test_addon_api.py

目的: **未記入**

- `test_addon_api_requires_auth_and_does_not_leak_from_public_meta`
- `test_addon_api_install_enable_effective_etag_disable_and_audit`
- `test_addon_api_recheck_disabled_and_invalid_manifest_fail_closed`
- `test_addon_api_rejects_csrf`
- `test_effective_event_stream_emits_revision_and_user_specific_etag`
- `test_effective_event_stream_ends_quickly_after_disconnect_without_parking_a_thread` — 切断された SSE はすぐ終わり、変化待ちでスレッドを占有しない。

### backend/tests/test_addon_bridge.py

目的: **未記入**

- `test_bridge_handshake_binds_nonce_and_authorizes_granted_method`
- `test_bridge_filters_and_enforces_user_permissions_for_pickers_and_jobs`
- `test_bridge_rejects_unknown_method_bad_nonce_and_external_route_with_codes`
- `test_bridge_checks_capability_permission_and_disabled_state`
- `test_bridge_rate_limits_and_audits_without_message_body`
- `test_bridge_requires_csrf_and_available_view`

### backend/tests/test_addon_contract.py

目的: **未記入**

- `test_dispatch_keeps_v1_contract_compatible`
- `test_v2_manifest_dispatch_and_contract_range`
- `test_v2_manifest_rejects_unknown_execution_contracts_and_unsafe_urls`
- `test_v2_unknown_presentational_field_is_ignored_with_warning`
- `test_ext_lint_cli_accepts_v2_and_reports_warnings`
- `test_ext_lint_cli_rejects_invalid_and_unsafe_manifest_files`

### backend/tests/test_addon_device_sessions.py

目的: **未記入**

- `test_manifest_accepts_scoped_device_relay_contract`
- `test_pairing_requires_explicit_devices_relay_grant`
- `test_pairing_is_one_time_and_device_token_reconnects`
- `test_wrong_relay_does_not_consume_valid_pairing_code`
- `test_active_device_session_closes_when_relay_grant_is_revoked`

### backend/tests/test_addon_execution.py

目的: **未記入**

- `test_remote_execution_validates_schemas_tokens_sizes_and_disable_race`
- `test_remote_execution_rejects_redirect_invalid_schema_and_oversize`
- `test_execution_discovery_filters_permission_availability_and_invalid_schema`
- `test_workflow_catalog_dry_run_execution_and_saved_unavailable_node`
- `test_agent_tool_runs_as_owned_job_and_returns_asset_reference`
- `test_agent_tool_delegates_only_opaque_grants_in_validated_input`
- `test_context_action_uses_opaque_scoped_grant_and_rejects_paths`
- `test_context_action_open_route_is_confined_to_its_own_addon`
- `test_context_action_accepts_own_addon_route_and_query`
- `test_llm_agent_discovers_and_dispatches_remote_tool`
- `test_quick_action_invokes_the_declared_endpoint_and_opens_its_route` — 宣言だけして呼ばないと、利用者には押しても何も起きない項目に見える。
- `test_command_invocation_rejects_unknown_kind_and_missing_contribution`
- `test_command_open_route_is_confined_to_its_own_addon`
- `test_command_without_a_route_reports_success_without_navigating`
- `test_a_progressing_agent_tool_is_not_cut_by_the_total_time`
- `test_a_silent_agent_tool_is_still_cut_and_canceled`
- `test_prompt_state_is_preserved_only_for_addon_driven_release`
- `test_a_prompt_state_from_another_instance_is_discarded` — モデルや ctx が変われば、書き出した形と受け取る形が合わない。黙って捨てる。
- `test_an_idle_slot_is_saved_even_though_it_reports_no_token_count` — 空いている slot は抱えているトークン数を返さない（返すのは処理中の
- `test_a_snapshot_is_refused_when_ram_would_run_out` — tmpfs は RAM を食う。逼迫すれば swap へ落ち、落ちた時点で速さの理由が
- `test_the_unit_creates_the_slot_save_path_before_starting` — llama-server は --slot-save-path の置き場が無いと起動を拒否する（usage を
- `test_the_snapshot_is_off_until_the_restored_slot_can_rewind` — 復元した slot には文脈チェックポイントが無く、前方一致が途中で崩れると
- `test_the_addon_reason_reaches_the_caller` — Add-on が名乗った理由を握り潰さない。

### backend/tests/test_addon_failure_context.py

目的: **未記入**

- `test_upstream_failure_rejects_unbounded_or_nonopaque_references`
- `test_mcp_failed_job_preserves_both_identifiers_and_safe_error_code`
- `test_stdio_error_keeps_references_even_with_a_long_explanation`
- `test_wait_timeout_retains_host_id_and_does_not_invent_upstream_id`

### backend/tests/test_addon_gpu_lending.py

目的: Add-on（MediaForge / SonicForge）へ常駐 LLM が GPU を貸す（Models の「Add-on への GPU の譲り方」）。 実機で起きていたこと（2026-11-15）: Strata が GPU を埋めていると、LLM の provider は llama.cpp しか降ろさないので「0 バイト空けた」と答え、Add-on は RAM へ流れるか待ち続けた （画像が cpu_offload で 235 秒、GPU なら約 3 秒）。

- `test_a_resident_strata_is_released_for_an_addon` — llama.cpp だけを見ていたので、Strata が載っていると 0 バイトで断っていた。
- `test_llm_first_never_releases`
- `test_auto_does_not_wait_for_a_busy_llm` — auto は推論中の LLM を待たない（OpenCode のサブエージェントを止めない）。
- `test_swap_waits_for_the_running_inference_and_holds_new_ones` — swap: 走っている推論は終わるまで待ち、その間に来た要求で取り消さない。
- `test_an_llm_request_does_not_release_the_large_runtime` — 別の LLM のために大きなランタイムを降ろすのは prepare_switch の仕事。
- `test_broker_lends_the_gpu_before_placing_on_ram` — RAM が空いていても、LLM が暇なら先に GPU を借りる。
- `test_broker_places_on_ram_when_the_llm_keeps_the_gpu`
- `test_the_gpu_comes_back_after_the_addon_is_done`
- `test_a_waiting_llm_request_gets_the_gpu_back_without_the_delay`
- `test_lending_expires`

### backend/tests/test_addon_health.py

目的: **未記入**

- `test_health_failure_threshold_and_unavailable_recovery`
- `test_degraded_requires_two_healthy_checks_to_clear`
- `test_health_rejects_redirect_oversize_and_unapproved_origin`
- `test_health_poll_skips_disabled_addons`

### backend/tests/test_addon_navigation_icon.py

目的: Add-on が名乗ったアイコンが、実際に画面まで届くか。 実測: Quick Actions の Add-on 項目が Apps と同じ一覧アイコンで描かれていて、 どちらを押せばよいのか分からなかった。manifest には icon が書いてあり、 payload にも載っていたが、UI がそれを読まずに固定の形を使っていた。 片側だけ直しても意味が無い経路なので、両端を見る。

- `test_the_declared_icon_survives_serialisation` — payload から落ちると、UI は何を描けばよいか知りようがない。
- `test_an_addon_may_leave_the_icon_out`
- `test_the_ui_reads_the_declared_icon_wherever_addon_navigation_is_built` — 以前はサイドバーと Quick Actions の 2 か所で項目を作っていて、片方だけ直すと
- `test_an_unknown_icon_never_falls_back_to_the_apps_glyph` — 知らない名前を Apps の一覧アイコンに落とすと、Add-on が Apps と並んだ
- `test_first_party_addons_declared_icons_are_drawn` — SonicForge は "audio" を名乗る。名前空間に無いとプラグインの形に落ちて、

### backend/tests/test_addon_proxy.py

目的: **未記入**

- `test_addon_frame_strips_host_credentials_injects_scoped_token_and_drops_set_cookie`
- `test_addon_frame_cookie_authenticates_opaque_subresources`
- `test_addon_frame_requires_auth_enabled_and_effective_view`
- `test_addon_frame_preserves_sse_without_buffering`
- `test_addon_frame_rejects_oversize_response_before_rendering`
- `test_addon_frame_enforces_request_limit_and_upstream_timeout`
- `test_addon_service_token_is_audience_bound_and_expires`
- `test_addon_frame_websocket_relays_messages_with_scoped_token`
- `test_addon_frame_websocket_rejects_disabled_addon`
- `test_addon_frame_websocket_uses_bridge_subprotocol_when_opaque_frame_has_no_cookie`
- `test_addon_frame_data_works_without_a_cookie_over_plain_http` — cookie を保存できない経路でも add-on の API へ届くこと。
- `test_addon_frame_cookie_outlives_a_short_absence` — 画面を開いたまま少し離れただけで API が一斉に 401 にならないこと。
- `test_addon_frame_streams_audio_instead_of_buffering_it` — 音は溜めずに流す。溜める上限で断ると、長い曲が再生できない。
- `test_addon_frame_streams_a_range_response` — 範囲で取りに来られたら、相手は少しずつ読む気でいる。溜めない。
- `test_addon_frame_still_bounds_what_it_streams` — 流す側にも上限は要る。終わらない応答に付き合い続けない。
- `test_addon_frame_still_rejects_an_oversize_json_response` — 普通の応答は今までどおり溜めて、上限で断る。
- `test_sandbox_csp_keeps_restrictions_and_resolves_only_same_origin`

### backend/tests/test_addon_registry.py

目的: **未記入**

- `test_registry_install_enable_health_and_uninstall_are_confined`
- `test_registry_rejects_unknown_grants_and_symlinked_directory`
- `test_effective_registry_filters_permission_health_and_partial_availability`
- `test_a_newly_requested_capability_does_not_hide_the_whole_addon` — 更新で機能が増えても、既に使えている画面を取り上げないこと。
- `test_setup_required_keeps_setup_ui_but_hides_execution_contributions`
- `test_effective_etag_and_revision_change_deterministically`
- `test_disable_pending_remains_effective_until_completion_and_can_be_canceled`
- `test_incompatible_managed_manifest_remains_visible_but_never_effective`

### backend/tests/test_addon_relay_tasks.py

目的: **未記入**

- `test_relay_joins_every_sibling_before_returning`

### backend/tests/test_addon_residency_provider.py

目的: Add-on を「場所を空けられる側」として broker に参加させる。 これまで場所を空けられるのは常駐 LLM だけで、Add-on は時計で自分の model を 降ろすしかなかった。短くすれば続けて作るたびに読み直し、長くすれば他の枠を削る。 どちらに振っても片方が痛む。 実測: 2026-09-11  batch のあとに画像 worker が 19.4GB を抱えたまま残り、 音楽生成が 300 秒待って期限切れになった 2026-09-16  音楽の常駐を 180 秒で片付ける掃除が、走っている音楽 worker を 殺していた（失敗 12 件が 215.1 秒と 245.1 秒に割れ、差の 30 秒が 掃除の周期だった）

- `test_what_add_ons_hold_is_declared`
- `test_an_add_on_without_the_endpoint_is_counted_as_holding_nothing` — 古い Add-on はこの入口を持たない。持たないことを故障として扱わない。
- `test_an_unreachable_add_on_does_not_stop_admission`
- `test_asking_releases_and_reports_what_was_freed`
- `test_a_busy_add_on_is_left_alone` — 使用中のものを取り上げても、取り上げられた側が落ちるだけで取り合いは解決しない。
- `test_a_nonsense_declaration_is_bounded` — 壊れた Add-on が莫大な数を返しても、それに引きずられない。
- `test_another_device_is_not_this_provider_s_business`

### backend/tests/test_addon_retrieval_ai.py

目的: **未記入**

- `test_request_and_response_bounds`
- `test_resolver_requires_local_role_and_opaque_revision`
- `test_api_auth_bounds_and_no_identity_leak`
- `test_lease_released_on_success_failure_and_cancellation`
- `test_disconnect_cancels_work`
- `test_http_no_proxy_redirect_and_response_validation`

### backend/tests/test_addon_runtime_ai.py

目的: **未記入**

- `test_addon_ai_capability_is_manifest_valid`
- `test_addon_ai_complete_uses_host_selected_target_without_leaking_identity`
- `test_addon_ai_capabilities_do_not_expose_provider_or_model`
- `test_addon_ai_thinking_is_per_request_and_private`
- `test_addon_ai_thinking_rejects_coercion_before_provider`
- `test_addon_ai_rejects_unbounded_or_missing_vision_input`
- `test_ai_gateway_llama_vision_requires_mmproj_and_prefers_loaded`
- `test_addon_ai_release_is_refused_while_another_consumer_is_using_the_model` — ControlDeck chat / OpenCode / 他 add-on を横取りしないこと。
- `test_addon_ai_release_reports_the_freed_residency`
- `test_addon_ai_release_does_not_touch_an_unmanaged_runtime` — gateway 管理外の runtime（Ollama 等）には手を出さない。
- `test_addon_ai_release_requires_a_valid_service_token` — 認証されていない要求で共有モデルを降ろさせない。
- `test_an_open_opencode_session_does_not_block_an_explicit_release` — OpenCode から add-on へ生成を頼む経路を殺さないための境界。
- `test_a_live_stream_still_blocks_an_explicit_release`
- `test_an_operator_opt_out_still_blocks_an_explicit_release`
- `test_an_embedding_instance_is_never_released`
- `test_local_only_rejects_unverified_runtime_without_sending_prompt`
- `test_local_only_is_strict_boolean`
- `test_local_only_revalidates_after_host_admission`
- `test_local_target_requires_managed_matching_loopback_and_existing_gguf`
- `test_opt_in_provenance_requires_verified_continuity`
- `test_provenance_is_strict_opt_in_local_only`
- `test_provenance_stream_cancellation_releases_without_attesting`
- `test_strata_ai_target_uses_preference_after_capability_and_loaded_filters`

### backend/tests/test_addon_runtime_auth.py

目的: **未記入**

- `test_introspection_accepts_scoped_service_token_without_session_or_csrf`
- `test_introspection_projects_stable_actor_subject_for_cross_call_ownership`
- `test_introspection_returns_inactive_without_leaking_failure`
- `test_introspection_fails_closed_when_addon_disabled_or_disable_pending`
- `test_runtime_dependency_binds_path_header_and_granted_capability`
- `test_runtime_principal_separates_correlation_subject_from_delegated_authority`
- `test_token_issuer_rejects_invalid_delegated_actor`

### backend/tests/test_addon_runtime_files.py

目的: **未記入**

- `test_read_grant_metadata_and_content_never_expose_host_path`
- `test_context_actor_can_read_only_delegated_grant`
- `test_read_grant_rejects_inode_swap_and_symlink_escape`
- `test_output_staging_commit_is_job_and_export_grant_scoped`
- `test_output_commit_does_not_cross_device_replace_the_central_staging_file`
- `test_output_rejects_path_filename_and_size_mismatch`

### backend/tests/test_addon_runtime_gateway.py

目的: **未記入**

- `test_gateway_document_projects_only_granted_host_capabilities`
- `test_gateway_document_does_not_advertise_host_ai_without_grant`
- `test_device_session_requires_both_grant_and_declared_relay`

### backend/tests/test_addon_runtime_job_credentials.py

目的: **未記入**

- `test_active_host_job_refresh_rotates_same_scoped_short_lived_credential`
- `test_job_refresh_fails_closed_for_terminal_job`

### backend/tests/test_addon_runtime_resources_jobs.py

目的: **未記入**

- `test_terminal_reconcile_from_new_call_is_idempotent_and_preserves_conflict`
- `test_terminal_reconcile_is_bounded_and_authorized`
- `test_job_control_reads_terminal_history_without_reviving_job`
- `test_historical_job_control_preserves_authority`
- `test_user_subject_creates_job_and_job_subject_attaches_without_duplicate`
- `test_job_subject_can_create_detached_durable_job`
- `test_new_job_credential_preserves_actor_and_grant_scope`
- `test_delegated_actor_subject_creates_and_operates_scoped_job`
- `test_resource_api_forces_owner_binds_host_job_and_enforces_priority_ceiling`
- `test_active_lease_refresh_rotates_same_scoped_short_lived_credential`
- `test_lease_refresh_rejects_wrong_scope_disabled_addon_and_terminal_job`
- `test_lease_refresh_fails_closed_after_addon_disable`
- `test_job_updates_require_phase_are_monotonic_rate_limited_and_terminal`
- `test_disable_cancels_waiting_and_job_but_keeps_active_lease_until_release`
- `test_external_jobs_have_per_addon_user_active_limit`
- `test_addon_facing_request_carries_host_bytes_through_to_the_broker` — RAM 併載の申告は add-on 側の契約にも要る。
- `test_host_bytes_without_host_in_preferred_devices_is_refused`
- `test_a_request_without_host_bytes_still_works`
- `test_lease_inspection_retains_owner_scope_and_terminal_states`

### backend/tests/test_agent_tool_activity_label.py

目的: Add-on ツールの実行中表示が、何をしているか分かる語になること。 kind（addon.agent_tool.media-forge.media.generate）だけでは、画像を作っている のか動画なのか分からない。種別は引数にしか無いので、job を作る時に拾う。 拾えないと画面には「media-forge: media.generate」としか出ず、利用者からは 何が起きているのか分からない。

- `test_activity_is_readable`
- `test_unknown_tool_has_no_label` — 割り出せないものに嘘の名前を付けない。呼び出し側が従来の表記へ落とす。
- `test_label_is_english_only` — 画面の中で言葉を混ぜない。add-on の label もツール名も英語である。

### backend/tests/test_agent_tool_jobs_listing.py

目的: Add-on の agent tool（MCP）実行を kind の前方一致で取り出せること。 Home の widget は専用の API を持たず、job の kind だけで絞り込む。 kind の作り方が変わると widget が黙って空になるので、形を固定する。

- `test_agent_tool_job_kind_carries_the_addon_and_tool` — kind は `addon.agent_tool.{addon_id}.{contribution_id}`。
- `test_listing_filters_agent_tool_jobs_by_prefix` — 前方一致で add-on ツールだけを取り出せること。

### backend/tests/test_alerts.py

目的: **未記入**

- `test_channel_crud_and_masking`
- `test_email_channel_is_encrypted_masked_and_validated`
- `test_email_notification_uses_starttls_login_and_message`
- `test_notification_failure_log_does_not_include_destination_secret`
- `test_dispatch_reports_external_delivery_failure`
- `test_rule_crud`
- `test_app_down_rule_requires_app`
- `test_application_alert_rules_require_registered_target`
- `test_log_error_rule_tracks_only_new_lines_and_survives_restart`
- `test_application_alert_metric_values`
- `test_legacy_app_down_threshold_still_fires`
- `test_alert_evaluation_fires_and_resolves`
- `test_operator_and_metric_helpers`
- `test_viewer_can_read_but_not_edit_alerts`

### backend/tests/test_amd_gpu_profiles.py

目的: **未記入**

- `test_quiet_lowers_mclk_by_exactly_one_level`
- `test_presets_restore_auto_and_custom_keeps_explicit_clocks`
- `test_apply_profile_uses_fixed_helper_argv`
- `test_privileged_helper_validates_and_writes_only_gpu_attributes`

### backend/tests/test_android_sdk.py

目的: Android SDK（コマンドライン）の機能と、箱への受け渡し、ホストの adb の案内。 目的: - 取得元の目録（URL・sha256）が固定で、公式のホスト（dl.google.com・https）以外へ行かないこと。取得は偽物に差し替える（本物を落とさない）。 - sha256 が違えば取り込まず止まること。zip の中のパスの逃げ出し（.. ・絶対パス・リンク）を拒否すること。 - sdkmanager は argv（shell なし）で呼び、ライセンスの yes はライセンスに同意したときだけ渡すこと。同意なしでは何も取得しない。 - 選べる platforms / build-tools は目録の範囲だけ。入っていなければ箱へ何も足さない（argv・PATH・環境変数が変わらない）。 入っていれば読み取り専用で bind し、ANDROID_HOME・ANDROID_SDK_ROOT・PATH（platform-tools など）を渡し、エージェントへの前置きに道具の場所を書く。 - ホストの adb サーバー（adb のプロセス・5037 番の待ち受け）の検出と、kill-server（偽の実行・shell なし）。 - 機能の API は同意なしでは 422、同意があればジョブになること。自動更新の対象にならないこと。 根拠: 2026-11-27 利用者「Android とかにも対応してね」（M5・iOS・Android の開発を OpenCode で）。 本物の SDK・npm・pip・apt は取得しない（取得は fetch・run を差し替える）。

- `test_catalog_is_pinned_to_google_official`
- `test_real_fetch_refuses_hosts_outside_catalog`
- `test_install_without_consent_fetches_nothing`
- `test_install_runs_sdkmanager_with_argv_and_licenses_only_when_accepted`
- `test_sha256_mismatch_stops_before_extract`
- `test_zip_path_escape_is_rejected`
- `test_zip_symlink_member_is_rejected`
- `test_unexpected_zip_layout_is_rejected`
- `test_failed_sdkmanager_does_not_mark_installed`
- `test_missing_java_stops_before_fetch`
- `test_update_needs_recorded_consent_and_uninstall_stays_inside`
- `test_nothing_added_to_box_when_not_installed`
- `test_installed_sdk_goes_to_box_readonly_with_env_and_path`
- `test_usb_notes_include_android_only_when_installed`
- `test_host_adb_detected_by_process_or_port`
- `test_kill_host_adb_uses_argv_without_shell`
- `test_feature_is_registered_and_never_auto_updated`
- `test_install_api_requires_consent`
- `test_devices_api_reports_host_adb_only_with_android_device`
- `test_stop_host_adb_api_runs_kill_server`

### backend/tests/test_api_rate_limit.py

目的: **未記入**

- `test_sliding_window_limit_retry_and_key_bound`
- `test_http_api_and_download_limits_use_direct_peer`
- `test_websocket_handshake_rate_limit_closes_4429`

### backend/tests/test_app_gpu_usage.py

目的: **未記入**

- `test_parse_drm_fdinfo_prefers_resident_vram_and_engine_time`
- `test_collect_drm_usage_uses_bounded_deltas_and_process_tree`
- `test_collect_returns_zero_for_app_without_drm_clients`
- `test_collect_counts_inherited_drm_client_once_across_process_tree`

### backend/tests/test_app_health.py

目的: **未記入**

- `test_tcp_and_http_health_checks`
- `test_file_health_check_enforces_allowed_root`
- `test_health_config_api_and_manual_check`
- `test_failed_health_marks_running_app_degraded`
- `test_allowed_command_health_uses_fixed_systemd_unit_and_hides_output`
- `test_command_health_is_fail_closed_without_registered_id`
- `test_health_command_catalog_does_not_expose_argv`
- `test_the_cpu_fan_uses_the_label_then_the_chosen_number`
- `test_a_non_super_io_chip_is_never_read_as_the_cpu_fan` — PSU や GPU の fan を CPU fan として出さない。
- `test_the_sensors_naming_is_used_when_the_driver_gives_no_label` — ドライバが番号しか出さない板でも、/etc/sensors.d が名付けていれば拾える。

### backend/tests/test_approvals.py

目的: L2 の承認: 依頼・承認（その時点のコミットだけマージ）・却下と、Web の入口。

- `test_request_is_deduplicated_and_listed`
- `test_bad_requests_are_rejected`
- `test_approve_merges_only_the_reviewed_commit`
- `test_a_changed_pr_is_not_merged`
- `test_a_merge_github_reports_as_failed_but_did_land_counts_as_merged` — gh can answer 'Merge already in progress' although the merge went through (seen on #411).
- `test_a_pr_merged_at_another_commit_is_still_a_failure`
- `test_reject`
- `test_ids_cannot_escape`
- `test_web_requires_settings_manage`
- `test_an_approved_controldeck_pr_is_released_and_applied_promptly`
- `test_other_repositories_are_only_merged`

### backend/tests/test_apps_api.py

目的: アプリ内Webビュー（/appview reverse proxy）のテスト。

- `test_appview_proxy_unknown_app_returns_404`
- `test_appview_referer_fallback_redirects_absolute_paths`
- `test_appview_referer_fallback_ignores_api_and_assets`
- `test_updating_an_app_with_a_health_check_does_not_500` — model_dump は入れ子のモデルも dict にする。set_health_check は
- `test_updating_an_app_without_a_health_check_leaves_it_alone`

### backend/tests/test_apps_url.py

目的: **未記入**

- `test_url_shortcut_create`
- `test_url_shortcut_rejects_bad_url`
- `test_app_icon_upload_sanitizes_and_does_not_leak_path`

### backend/tests/test_archive_import.py

目的: チャットに添付した圧縮ファイルを OpenCode のプロジェクトへ展開する（archive_import）と、添付・送信の受け口。 目的: 2026-11-18 利用者の依頼「OpenCode の開発中に ZIP や RAR を添付し、中身がソースコードならプログラムの フォルダに置いて参照できるように」。外から来たファイルなので、展開する前と後の両方で危ないものを断る。 - ZIP・7z・tar を展開できる（RAR は 7z が読む。作る道具が無いので 7z で代表する） - 空のプロジェクトなら直下へ、既存のファイルがあれば imports/<名前>/ へ。上書きしない - 中身が 1 つのフォルダだけなら、その中を展開先の直下にする - `..`・絶対パス・リンク・宣言を偽った圧縮爆弾・パスワード付きを断る - OpenCode 以外の機能で圧縮ファイルを送ると断る（展開先が無い）

- `test_zip_into_an_empty_project_lands_at_the_root`
- `test_existing_project_gets_imports_folder_and_nothing_is_overwritten`
- `test_tar_and_7z_are_extracted`
- `test_dangerous_entries_are_rejected_before_extracting`
- `test_a_bomb_that_lies_about_its_size_stops_while_writing`
- `test_upload_accepts_archives_and_send_requires_opencode`
- `test_file_list_counts_only_what_was_extracted_not_git` — 新しいプロジェクトの git init の中身（.git/hooks など）を、展開したファイルとして数えない（レビュー指摘）。
- `test_7z_with_a_link_is_rejected_and_a_lying_7z_is_stopped_while_extracting`
- `test_imports_link_and_broken_layout_are_reported_as_archive_errors`
- `test_canceled_archive_is_deleted`

### backend/tests/test_assistant_auto_model.py

目的: 目的: AI アシスタントのヘッダの「auto」に出す実モデル（llm-endpoints の auto_resolves_to）が、 ゲートウェイの転送と同じ解き方（選択中のランタイムの優先モデル・載っているもの優先）になること。解けないときは None。 根拠: 2026-11-23 利用者の通し（評価 U11）。「auto」では実際に何のモデルが動くか分からなかった。

- `test_auto_target_follows_selected_runtime_preference_and_loaded`
- `test_auto_target_is_none_when_nothing_registered`

### backend/tests/test_assistant_planner.py

目的: **未記入**

- `test_rule_plan_combines_web_and_academic`
- `test_llm_plan_uses_structured_json`
- `test_invalid_llm_plan_falls_back_to_chat`
- `test_route_api_returns_validated_plan`
- `test_send_rejects_mismatched_plan`
- `test_research_combines_sources_and_deduplicates` — research は、依頼の理解 → 検索 → 本文 → 論文を 1 つの流れで集め、同じ URL を 1 つにまとめる。
- `test_rule_plan_matches_routing_cases_and_sets_depth` — ルールで決まるべき依頼はルールで正しく決まる（LLM に回してチャットにしない）。
- `test_frontend_and_backend_share_the_routing_words` — 画面とサーバーが同じ語の表を読む（ずれると判定が食い違う）。
- `test_llm_plan_can_choose_deep`

### backend/tests/test_auth.py

目的: **未記入**

- `test_login_requires_csrf_header`
- `test_login_wrong_password`
- `test_unauthenticated_access_denied`
- `test_login_success_and_me`
- `test_login_recorded_in_audit`
- `test_logout`
- `test_viewer_cannot_edit_apps`
- `test_login_bruteforce_rate_limited`

### backend/tests/test_auto_projects.py

目的: 作業場所の自動作成と表示名（Nexarch・AI アシスタント・Project Lab 共通）の試験。 目的: CodeDEV 直下に変わらないフォルダ名で作業場所を自動で作り、利用者に見える名前は manifest の name だけにして後から書き換えられること、Project Lab から見られること、 Nexarch の「新しい作業場所」と AI アシスタントの会話ごとの作業場所が同じ部品を使うことを守る。 根拠: 利用者の発言（2026-11-27）「AI アシスタントからの OpenCode もそうだが、作業場所は既定の 作業場所に自動で名前を振ってもらって、後で書き換えられるようにし、そこを Project Lab からも 見られればよくないか？」。フォルダ名を変えないのは、OpenCode の会話の履歴がフォルダのパスに 結び付くため（指示書）。CodeDEV の外は realpath で断る（AGENTS.md のパス検証）。

- `test_create_auto_project_folder_name_manifest_and_collision`
- `test_default_name_and_name_validation`
- `test_set_display_name_keeps_other_fields_and_folder`
- `test_paths_outside_codedev_are_refused`
- `test_project_lab_rename_api_and_created_by`
- `test_nexarch_new_workspace_creates_in_codedev_and_opens_there`
- `test_nexarch_new_workspace_refused_when_codedev_outside_roots`
- `test_chat_workspace_per_conversation` — 会話ごとに別の作業場所。作った後は _last_code_project が同じ場所を返す（次のターンで使い回す）。
- `test_chat_workspace_is_created_even_when_default_project_path_is_set` — OpenCode の設定の既定の作業場所があっても、AI アシスタントの会話は会話ごとの作業場所を作る（使い回さない）。

### backend/tests/test_auto_update.py

目的: 本体の自動適用（L1）: 更新があり、使われていない時間が続いたときだけ始める。

- `test_waits_for_the_idle_period_then_starts`
- `test_activity_resets_the_idle_clock`
- `test_nothing_to_do`
- `test_can_be_turned_off`
- `test_result_is_reported_once`
- `test_manual_updates_are_not_reported_as_automatic`
- `test_busy_reasons_cover_open_opencode_sessions`
- `test_an_approved_release_skips_the_idle_wait`
- `test_an_approved_release_still_waits_while_busy`
- `test_a_prompt_for_another_release_is_ignored`

### backend/tests/test_bootstrap_retired_app.py

目的: **未記入**

- `test_remove_retired_repair_app_deletes_seed_and_unit`

### backend/tests/test_change_policy.py

目的: 変更の危険度の区分（docs/POLICY-autonomous-development.md）。

- `test_controldeck_tiers`
- `test_overall_tier_is_the_highest_and_reasons_are_reported`
- `test_codedev_default_is_automatic_except_secrets`
- `test_patentbuddy_privacy_boundary_is_guarded`
- `test_glob_semantics`
- `test_bad_policy_is_rejected`

### backend/tests/test_chat_asr.py

目的: **未記入**

- `test_asr_status_reports_reusable_runtime`
- `test_asr_install_job_is_reused_while_running`
- `test_asr_transcribe_converts_and_cleans_temp`
- `test_asr_transcribe_rejects_non_audio`
- `test_haas_is_used_when_installed_and_whisper_is_the_fallback` — 既定は Haas-ja。失敗しても whisper があれば音声入力を止めない（2026-11-18 利用者の依頼で採用）。
- `test_repeated_phrases_are_collapsed_and_short_segments_keep_enough_context` — 短い区切りで whisper が同じ言葉を繰り返した（2026-11-18 本番の画面・手元の音声で再現）。
- `test_selected_asr_engine_is_tried_first_and_missing_choice_falls_back` — Models の TTS / ASR で選んだ ASR を先に使う。選んだものが未導入なら入っている方で動かす
- `test_english_choice_never_falls_back_to_a_japanese_engine` — 英語（Parakeet）を選んだら、日本語専用の Haas へは回さない（英語を片仮名の意味の無い文にして送らない）。
- `test_filter_rejects_segments_without_speech_and_does_not_fall_back` — 入力フィルター（Silero VAD）で声が無いとされた区切りは文字にせず、控えのエンジンにも回さない。
- `test_auto_ja_en_routes_each_segment_by_detected_language` — 日英自動: 区切りごとに言語を判定し、日本語は Haas、英語は Parakeet に渡す。応答の言語・モデルも判定に従う。

### backend/tests/test_chat_memory.py

目的: AI アシスタントの会話をまたぐ記憶（app/memory）。情報の流出を防ぐ守りを厚く確かめる。 目的: - 置き場: 本文が DB ファイル（WAL 含む）に平文で無い・秘密の会話と特許の流れから作った記憶は必ず protected・守りを外せない。 - 入れ分け（policy）: protected は秘密の会話の中にだけ入る・秘密でない会話には本文も件数も出ない・外部の LLM には既定で入らず、 設定で入れても normal だけ・OpenCode は既定で入らず、設定で入れても normal だけ。 - 検索への漏れ: 記憶は答えを作る段にだけ入り、Web 検索の語を作る段（_server_search に渡す文）・道具の引数に渡らない。 モデルが記憶の言葉を道具の引数へ写したときは、道具を実行する前に止める。 - 作る側: 取り出しは必ずローカルの LLM（会話がクラウドでも）。ローカルが無ければ作らない。切／明示／確認／自動・会話ごとの切り替え。 - 消す・見せる: 削除・会話と一緒に消す・一覧／使った記憶の API は秘密でない会話に protected を返さない。監査に本文が無い。 根拠: 2026-11-23 利用者「保護された会話や特許のアイデア関連の記憶はインターネット検索を保護されていない会話には露呈できない」。 実際の LLM・外部へは通信しない（すべてモック）。

- `test_text_is_encrypted_in_the_database_file`
- `test_confidential_and_patent_origin_are_forced_protected_and_cannot_be_unprotected`
- `test_unknown_sensitivity_falls_back_to_protected`
- `test_midway_secret_pulls_existing_memories_of_that_conversation_to_protected`
- `test_patent_flag_is_one_way`
- `test_policy_matrix`
- `test_rank_never_returns_a_kind_that_is_not_allowed`
- `test_rank_keeps_top_five_and_prefers_pinned_and_matches`
- `test_leak_guard_flags_copied_words_but_not_the_users_own`
- `test_protected_memory_enters_only_secret_conversation_and_is_not_even_counted_elsewhere`
- `test_memory_is_not_given_when_disabled_or_conversation_opted_out`
- `test_external_llm_conversation_gets_memory_only_when_enabled_and_only_normal`
- `test_search_query_stage_never_receives_memory_but_the_answer_stage_does` — Web 検索の語を作る段（_server_search。research_intent を呼ぶ）へ渡る文に、記憶は入らない。答えの段には入る。
- `test_tool_arguments_that_copy_memory_are_blocked_before_any_search` — モデルが記憶の言葉を検索語へ写しても、通信の前に止める（記憶が検索エンジンへ漏れない）。
- `test_opencode_turn_gets_memory_only_by_setting_and_only_normal`
- `test_extraction_always_goes_to_the_local_model_even_for_a_cloud_conversation`
- `test_no_local_model_means_no_memory_and_no_outbound_call`
- `test_creation_modes_off_explicit_confirm_auto`
- `test_conversation_opt_out_of_saving`
- `test_secret_and_patent_conversations_create_protected_memory`
- `test_secret_looking_text_and_bad_output_are_not_remembered`
- `test_patent_collection_detection`
- `test_api_settings_items_and_protection_rules`
- `test_used_endpoint_never_returns_protected_to_a_normal_conversation`
- `test_conversation_flags_api_and_delete_with_memories`
- `test_midway_secret_api_protects_memories_made_before`
- `test_audit_records_create_and_delete_without_the_text`
- `test_new_database_file_is_separate_from_the_main_database`

### backend/tests/test_chat_persist.py

目的: 永続チャットのテスト（DB保存・ジョブ継続・切断耐性）。 LLM は実在しない可能性があるため、chat_persist の生成ロジックは monkeypatch で置換し、 「サーバー側ジョブが assistant メッセージへ保存し、WS 接続と無関係に完結する」ことを検証する。

- `test_conversation_crud`
- `test_send_creates_messages_and_job_persists` — 送信で user+assistant が作られ、ジョブがサーバー側で assistant へ書き込み完了する。
- `test_send_rejects_foreign_conversation`
- `test_checkpoint_saves_partial_on_cancel` — 生成中にキャンセルされても、それまでの部分出力が DB に残る（切断で回答が消えない）。
- `test_run_chat_job_emits_generation_stats` — 生成中に stats イベント（フェーズ/tok/s/コンテキスト）が流れ、最終値は実測usageで確定する。
- `test_upload_attachment_image_and_document` — 📎添付: 画像は保存されID返却、文書は会話コレクションへRAG登録される。
- `test_ollama_native_messages_converts_image_content` — OpenAI互換のcontent配列がOllama native形式（content+images）へ変換される。
- `test_rag_ingest_job_endpoint` — RAG取り込みはサーバー側ジョブとして開始される（未存在コレクションは404）。
- `test_sent_image_is_kept_on_the_message_served_and_removed_with_the_conversation` — 送った画像は発言の meta に残り、本人だけが取得でき、会話を消すと一緒に消える。
- `test_unsupported_image_type_is_rejected_clearly` — HEIC など対応していない画像は、文書として読もうとせず理由を返す。
- `test_document_upload_says_when_the_embedding_model_is_unreachable` — 文書は読めたのに埋め込みモデルへ届かないときは、「読めない」ではなく接続できないと伝える。
- `test_fork_copies_up_to_the_message_and_keeps_the_original`
- `test_delete_message_removes_its_reply_and_refuses_while_generating`
- `test_fork_remaps_compaction_carries_edit_images_and_copies_documents`
- `test_stale_generating_message_can_be_deleted` — 再起動でジョブが消え「generating」のまま残った発言も消せる・分岐できる。
- `test_archive_hides_from_list_and_restores` — 会話のアーカイブ（2026-11-17 の依頼: 履歴を左右のスワイプでアーカイブか削除）。消さずに一覧から外し、戻せる。
- `test_japanese_questions_get_a_final_language_instruction` — 日本語の質問には、生成の直前に「日本語で」を足す（2026-11-17: DS-XS が検索資料に引かれて中国語で答えた）。
- `test_voice_conversation_gets_spoken_rules_and_the_chosen_style` — 会話モードの発言には、話し言葉の決まり（絵文字・箇条書きなし、短く、相手の言語で）と選んだ回答傾向を最後に足す。
- `test_voice_conversation_speaks_as_the_chosen_voice_gender` — 会話モードの話し方を、Models の TTS で選んだ声の性別に合わせる（F1〜F5 は女性、M1〜M5 は男性）。

### backend/tests/test_chat_queue.py

目的: AI アシスタント: 生成中に送った要望（送信待ち）をサーバーが持ち、回答が終わったら送る。 目的: 2026-11-18 利用者「実行中に要望を送信したい。送信した要望はサーバー側で保存しておき、適切なタイミングで LLM に送信してほしい。複数の要望の送信や削除、編集、取り消しなどを可能に」。 - 生成中に貯めた要望は、回答が正常に終わったら自動で送る。同じ送り方の続きは 1 つの発言にまとめる - 止めた・失敗した回答の後は送らずに保留し、「今すぐ送る」で送る - 編集・削除（元に戻す）・すべて取り消す。送った後は編集できない

- `test_requests_sent_during_a_run_are_sent_together_when_it_finishes`
- `test_a_different_way_of_sending_waits_for_the_next_turn`
- `test_after_a_stopped_run_the_queue_is_held_until_sent_now`
- `test_edit_delete_restore_and_clear`
- `test_a_request_queued_when_nothing_runs_is_sent_at_once`
- `test_the_asr_reads_only_the_spoken_length` — whisper は 30 秒に詰め物して読む。短い区切りは読む範囲を絞って速くする（6 秒で 14〜17 秒 → 約 3 秒。実測）。
- `test_selftest_warns_when_a_finished_conversation_still_has_waiting_requests` — 送り損ね（回答は正常に終わったのに、送信待ちが 2 分以上残る）に自己診断で気付けるように。
- `test_review_fixes_hold_restore_and_a_failing_group_does_not_block_the_rest` — レビュー指摘（2026-11-18）:
- `test_a_failure_after_writing_the_message_does_not_leave_it_generating` — 発言を書いた後でジョブを作れなかったら「生成中」のまま残さない（残ると送信待ちも OpenCode も止まる）。
- `test_groups_respect_the_attachment_budget` — まとめると添付が 8 個を超えるなら、そこで分ける（黙って落としていた）。

### backend/tests/test_chat_references.py

目的: 会話内文献ID、解決API、選択的コンテキスト注入のテスト。

- `test_reference_registry_uses_short_base36_ids_and_deduplicates`
- `test_send_expands_only_explicitly_requested_references`
- `test_reference_parser_accepts_compact_user_notation`

### backend/tests/test_chat_tools.py

目的: AI アシスタントの会話で、モデル自身が道具を呼ぶ仕組み（tool calling）。 根拠: 2026-11-20 利用者「ローカル LLM の会話でモデル自身が道具を呼ぶ仕組みを入れてほしい」「毎回チェックする必要はない。 Claude と同じ方法で」。資料が大きい会話では、検索の道具を渡し、探すかどうか・何で探すかをモデルが決める。 実機（Qwen3-4B-Instruct、llama.cpp）で、挨拶・お礼・計算では呼ばず、「さっき調べた番組表で夜 9 時は？」では 「神奈川 今日の地上波 番組表 夜9時」と具体的な語で検索してから答えた。

- `test_stream_joins_tool_call_fragments_and_sends_tools` — 道具の呼び出しは index ごとの断片で流れてくる。つないで 1 つの tool_call の chunk で返す。tools は渡したときだけ送る。
- `test_endpoint_rejecting_tools_raises_tools_unsupported` — tools 付きの要求を 400 で断る runtime は ToolsUnsupported（呼び出し側が道具なしで続ける）。道具なしの 400 は従来の失敗。
- `test_search_tool_runs_the_models_query_without_score_gate` — 道具はモデルが書いた検索語で探し、点数の門で切らずに上位を返す。空の検索語・不明な道具・検索の失敗も文で返す（会話は止めない）。
- `test_models_without_tools_fall_back_to_the_keyword_rule` — 道具を受け付けないモデルでは、道具の案内を外し、従来どおり発言が資料を指すときだけ探して抜粋を入れる。
- `test_chat_job_lets_the_model_search_materials_then_answers` — 大きい資料の会話: 道具を渡し、モデルが呼んだら探して結果を渡し、続きを生成する。前もっては探さない。
- `test_chat_job_falls_back_when_the_model_cannot_use_tools` — tools を受け付けないモデル: 覚えて、言い方の判定で探して道具なしで生成し直す。次の発言からは最初から道具を渡さない。
- `test_unverified_runtimes_keep_the_keyword_rule` — 道具を確かめていない runtime（Ollama・外部など）には道具を渡さず、従来どおり言い方の判定で探す。
- `test_auto_mode_lets_the_model_search_the_web_and_cites_sources` — 機能の選択が「自動」なら Web 検索も道具で渡す。呼ばれたら検索して出典を登録し、[R番号] 付きの結果を渡す。
- `test_route_skips_the_llm_decision_for_tool_capable_models` — 道具を使えるモデルでは、曖昧な依頼を LLM で判定しない（送信ごとに 1〜1.5 秒かかっていた）。対話として送り、
- `test_final_round_turns_tool_exchanges_into_plain_notes` — 上限まで呼んだ後の最後の回: 道具のやり取りを普通の資料にまとめる。残したままだと Radiance が 3 回中 2 回、
- `test_auto_mode_lets_the_model_hand_work_to_opencode` — 「自動」では、作業の依頼かどうかを会話の LLM が判断し、OpenCode に任せる道具を呼んだら同じ回答の枠のまま
- `test_hand_off_without_a_project_asks_the_user_to_choose_one` — 作業フォルダが決まっていなければ移らない（新しいプロジェクトを勝手に作らない）。選んで送り直すよう伝えさせる。
- `test_opencode_tool_is_not_offered_without_opencode_or_for_images_and_voice` — OpenCode が無効・声の会話では渡さない。画像つきは、OpenCode のモデルが画像を読めないときだけ渡さない

### backend/tests/test_chatflow.py

目的: **未記入**

- `test_trigger_injects_chat_input`
- `test_signal_display_node`
- `test_chatflow_end_to_end` — trigger(chat) -> signal.display で入力メッセージが表示信号に流れる。

### backend/tests/test_cli_features.py

目的: 設定 → オプション機能の「AI の CLI（Nexarch）」（Claude Code・Codex・Gemini）の試験。 目的: 利用者の npm のグローバル（端末でも使う実体）へ導入・更新・削除する。管理領域に別の写しを作らない。 根拠: 2026-11-27 利用者「各 CLI の導入と削除と更新ができるように Settings の機能に追加して」。npm は呼ばず （取得しない）、呼び方だけを確かめる。既存の npm の機能の試験は管理領域への導入で、同じ観点は無い。

- `test_cli_features_use_npm_global`

### backend/tests/test_cloud_models.py

目的: 外部のフロンティアモデル（docs/PLAN-frontier-models.md F1）。外部へは送らない（転送先はモック）。

- `test_nothing_is_sent_by_default`
- `test_allowed_caller_resolves_with_the_decrypted_key`
- `test_the_key_is_never_stored_or_shown_in_plain_text`
- `test_removing_the_key_disables_the_provider`
- `test_invalid_settings_are_rejected`
- `test_custom_endpoint_must_be_https`
- `test_monthly_budget_stops_further_calls`
- `test_unknown_caller_is_treated_as_external_api`
- `test_gateway_forwards_allowed_cloud_requests`
- `test_gateway_refuses_when_the_caller_is_not_allowed`
- `test_gateway_lists_enabled_cloud_models`
- `test_settings_api_requires_settings_manage`
- `test_opencode_lists_cloud_models_only_when_allowed`
- `test_data_policy_is_enforced_on_every_openrouter_request`
- `test_remote_models_carry_price_and_privacy`
- `test_account_reports_the_remaining_balance`
- `test_parameter_count_is_read_from_the_name`
- `test_remote_models_carry_size_capabilities_and_vendor`
- `test_other_providers_do_not_get_openrouter_only_fields`
- `test_models_for_a_caller_follow_the_egress_switch`
- `test_assistant_model_list_includes_cloud_models_only_when_allowed`
- `test_assistant_gets_a_gateway_entry_for_cloud_models_under_ollama`
- `test_internal_generation_keeps_cloud_models_on_the_gateway`
- `test_assistant_requests_name_their_caller_to_the_gateway` — チャット・構造化生成・Deep Research はどれも runtime provider を通る。ヘッダはそこで 1 回付ける。
- `test_gateway_refusal_reaches_the_assistant_as_a_readable_message`
- `test_local_only_requests_never_use_a_cloud_model`
- `test_assistant_chat_reaches_the_provider_through_the_gateway` — ゲートウェイは、AI アシスタントと名乗った要求だけを AI アシスタントの許可で通す。
- `test_only_assistant_paths_claim_the_assistant_caller` — Add-ons and other internal callers must not borrow the assistant's egress permission.
- `test_internal_requests_to_the_gateway_carry_its_key` — The assistant reaches cloud models through the gateway, which needs its key (was 401).
- `test_account_flags_a_management_key`
- `test_summary_reports_running_calls_totals_and_speed`
- `test_running_call_reports_time_to_first_token`
- `test_context_lengths_are_fetched_at_most_hourly`
- `test_summary_endpoint_requires_settings_manage`
- `test_excluded_models_are_counted_against_the_public_list` — 目的: アカウントの設定で除外されたモデルの数（うち無料）を、鍵なしの公開の一覧との差で出す。
- `test_public_list_failure_hides_the_count_without_breaking` — 目的: 公開の一覧が取れなくても、モデルの一覧は返し、除外の件数だけを出さない。
- `test_non_openrouter_has_no_excluded_report` — 目的: OpenRouter 以外では公開の一覧を問い合わせない。根拠: 依頼「OpenRouter のときだけ」（2026-11-23）。
- `test_refresh_ignores_the_zdr_cache` — 目的: 取り直すときは ZDR 一覧の 1 時間の覚えを使わず、問い合わせ直す。

### backend/tests/test_code_research.py

目的: OpenCode に渡す前に ControlDeck の調査で調べる（chat_persist._research_for_code）。 目的: 2026-11-18 利用者の依頼「『調べて OpenCode に渡す』をボタンを押さずに」。OpenCode の会話で「〜を調べて反映して」と 送ると、同じ 1 回の送信の中で調べ、出典つきの根拠を OpenCode への依頼に付ける。 - 調べた根拠は見出しを付けて返し、回答の meta は OpenCode のまま（道具の一覧は同じもの）。出典と調べた経過は残す - 量は上限まで（引き継ぎと合わせて OpenCode のコンテキストを超えない） - 調べられなくても OpenCode の作業は止めない（空を返し、meta を元に戻す）

- `test_research_for_code_returns_evidence_and_keeps_the_opencode_answer` — 「調べて反映して」: 調べた根拠を OpenCode への依頼に付け、回答の meta は OpenCode のまま（出典は残す）。

### backend/tests/test_commander_accounts.py

目的: AI Commander のアカウント管理（app/commander/accounts.py と /accounts API）の試験。 目的: アカウント = 設定ディレクトリという C1 の根幹を守る。 - CLI の表（環境変数の対応）が公式の方法（CLAUDE_CONFIG_DIR・CODEX_HOME・XDG_*）どおりであること。 - アカウント名の検証・フォルダの権限（0700）・ログイン情報を返さないこと。 - 既定のアカウント（環境変数を付けない）という扱いが壊れないこと。 根拠: c1-spec.md（2026-11-26 の指示書）と docs/PLAN-ai-commander.md C1。 秘密（auth.json の中身）を読まない・返さないことを、API の返事の形で確かめる。 既存の Terminal の試験（test_terminals.py）は CLI のアカウントを扱わないので 同じ観点の試験は無い（./deck.sh test-catalog で確認済み）。

- `test_cli_table_matches_official_account_isolation` — CLI の表: アカウントを分ける環境変数が公式の方法どおりであること。
- `test_account_folder_permissions_and_login_check` — アカウントのフォルダは 0700 で作られる。ログイン済みはファイルの有無だけ。
- `test_account_name_validation_and_reserved_default` — アカウント名の検証: 名前は folders の名前になるので素朴な形だけ。「default」は reserved。
- `test_env_for_default_account_is_none` — 既定のアカウントは環境変数を付けない（利用者の普段の ~/.claude などを使う）。
- `test_accounts_api_create_list_delete_and_audit` — アカウントの API: 作成・一覧・削除と監査ログ。返事に秘密（中身）を含めない。
- `test_commander_api_requires_authentication` — 認証の無い呼び出しは 401。Terminal と同じ権限で守る。
- `test_cli_list_reports_installed_state` — /clis は導入済みか・アカウントを分けられるかを返す（gemini は分けられない）。
- `test_delete_account_refuses_symlinked_folder` — アカウントのフォルダがリンクなら削除しない（リンク先＝accounts の外を消さない）。
- `test_account_labels_including_the_default_account` — アカウントに画面の名前を付けられる。既定のアカウントにも付けられ、消すと外れる。
- `test_login_name_from_claude_metadata_and_codex_status` — 既定のアカウントの見せ方を、ログインしている名前にする（Claude はメール、Codex はログインの方法）。

### backend/tests/test_commander_consoles.py

目的: AI Commander のコンソール（app/commander/consoles.py・manager の env と印・/consoles API）の試験。 目的: 「コンソール = Terminal の tmux セッション + 印」を守る。 - 既定のアカウントは環境変数を付けない・名前付きアカウントは CLI の表の環境変数を tmux へ配列引数で渡すこと（shell を使わない）。 - 印（@cd-commander-*）がセッションへ付き、一覧で読み返せること。 - Terminal の上限に当たったら部分的に開いて理由を返すこと。作業フォルダは許可の外なら 400。 根拠: c1-spec.md（2026-11-26）と AGENTS.md の「shell=True 禁止」「async の中で 同期的に待たない」（endpoint は def）。env をシェル文字列へ連結すると パスに空白が含まれるだけで壊れるので、配列引数であることを機械的に確かめる。 Terminal の manager を広げたので test_terminals.py と役割を分担する （あちらは engine・PTY の既存の振る舞い、こちらは env と印）。

- `test_create_session_passes_env_as_array_arguments` — 環境変数は tmux へ配列引数として渡ること（シェルの文字列に連結しない）。
- `test_commander_marks_roundtrip_with_real_tmux` — 印（@cd-commander-*）を付けたセッションが一覧で読み返せること。
- `test_env_reaches_tmux_session_environment` — create_session(env=) の値が tmux のセッション環境に実際に届くこと。
- `test_consoles_api_flow_default_account_and_batch` — コンソールの作成: 既定アカウントは環境変数無し・コマンドは表の実行ファイル名だけ。
- `test_consoles_api_named_account_uses_env` — 名前付きアカウントは CLI の表の環境変数（パスは accounts の管理下だけ）を付ける。
- `test_consoles_api_limit_reports_partial_open` — Terminal の上限に当たったら、開けた分と理由を返す（例外にしない）。
- `test_consoles_api_rejects_bad_requests` — 入力の断り: 不明な CLI・未導入・許可の外の作業フォルダ・無いアカウント・個数。
- `test_consoles_api_delete_only_marked_sessions` — コンソールの削除: 印のあるセッションだけを閉じられる（Terminal と同じ閉じ方）。
- `test_opencode_console_uses_the_controldeck_opencode_api` — OpenCode は OpenCode 画面と同じ tui_command（OpenCode の設定の API）で開く。素の opencode は使わない。
- `test_opencode_accounts_are_the_registered_api_connections` — OpenCode のアカウント = OpenCode 画面に登録した接続（設定のモデル＋送信を許した外部モデル）。
- `test_cli_found_outside_the_service_path_is_opened_by_absolute_path` — service の PATH に無い利用者の bin（~/.local/npm-global/bin など）の CLI も見つけ、絶対パスで開く。

### backend/tests/test_commander_no_api_key.py

目的: Nexarch の Claude・Codex は API キーを使わない（定額のログインだけ）の試験。 目的: 利用者の決定（2026-11-28「クロードとコーデックスは API キーを消してください。基本的にはもう定額でしか 使えません」）を仕組みで守る。コンソール（tmux）とチーム（画面のない実行）の両方で、 - API キーの環境変数（ANTHROPIC_API_KEY・OPENAI_API_KEY など）を CLI へ渡さないこと、 - Claude は --settings で apiKeyHelper を空・forceLoginMethod=claudeai にすること、 - Codex は forced_login_method="chatgpt" を付けること、 を確かめる。 根拠: 2026-11-28 の実機。Claude は forceLoginMethod を付けても ANTHROPIC_API_KEY を使う （"Invalid API key"）ので、環境変数を消すことが要。apiKeyHelper は --settings で空にすると使われない。 Codex は forced_login_method で API キーのログイン・環境変数の鍵を断る。 既存の試験（test_commander_consoles / team_t3a）は argv の形を見るだけで、鍵の除外は見ていない （docs/test-catalog.md で確認）。

- `test_team_launch_drops_api_key_env` — チームの実行の環境に、サービスの環境にある API キーが入らない。
- `test_team_claude_forces_subscription_login`
- `test_team_codex_forces_chatgpt_login`
- `test_console_command_unsets_api_keys` — コンソールは tmux のサーバーの環境を引き継ぐので、コマンドの頭で鍵を消す。
- `test_codex_login_status_does_not_see_env_keys` — `codex login status` に環境変数の鍵を渡さない（鍵を「ログイン」と取り違えない）。

### backend/tests/test_commander_resume.py

目的: AI Commander の上限からの自動再開（C3・LLM なし）の試験。 目的: 上限で止まったコンソールを回復の時刻に「続けて」で再開する。空振り（まだ上限）・送りすぎ・ 利用者が続けた後の誤送信を防ぐ歯止めを守る。 根拠: docs/PLAN-ai-commander.md C3。上限の文言は C0 の実機の記録（c1-spec.md）と Codex の表示。 既存の試験に同じ観点は無い（test-catalog で確認。Terminal の automation は条件つきの送信で、上限は扱わない）。

- `test_parse_claude_and_codex_reset_times` — Claude の「resets 10:20pm (Asia/Tokyo)」「resets Nov 30, 12am」・Codex の「try again at/in」。
- `test_plan_waits_until_reset_then_sends_with_guards` — 回復の時刻＋猶予までは待ち、過ぎたら送る。送った後は 10 分空ける・10 回で止める・止めたら送らない。
- `test_tick_sends_only_when_still_limited_and_records` — 送る直前に画面を読み直し、まだ上限のときだけ「続けてください」を送って回数・時刻を残す。
- `test_tick_clears_and_schedules` — 止まっていないコンソールは印を消し、待ちのコンソールは次の予定を残す（画面に出す）。
- `test_autoresume_toggle_api` — コンソールごとに自動再開を止める・戻す（off の印を付ける・外す）。無いコンソールは 404。
- `test_resume_message_console_then_global_then_default` — 送る文言: コンソールの設定 → 全体の設定 → 既定「続けてください」。空で上の段へ戻す。

### backend/tests/test_commander_status.py

目的: AI Commander の状態の判定（C2・LLM なし）の試験。 目的: コンソールの画面の文字から「作業中・入力待ち・上限で停止・エラー・終了」と最後の行を正しく出す。 自動再開（C3）はこの判定の「上限で停止」を見て動くので、誤判定は再開の空振り・止め損ねになる。 根拠: 2026-11-27 に実機で取った画面（Claude Code 2.1.332 の作業中、Codex 0.172.0・OpenCode v2.4.2 の 入力待ち）を縮めて使う。上限の文言は C0 の実機の記録（c1-spec.md）。 既存の Terminal の試験（test_terminals.py）は CLI の状態を扱わないので同じ観点は無い（test-catalog で確認）。

- `test_states_from_real_screens` — 実機の画面で 5 つの状態を正しく出す。
- `test_error_only_near_the_bottom_and_not_from_conversation_text` — エラーは画面の一番下の数行だけで判定する（会話の本文に出た「API Error」の話は拾わない）。
- `test_limit_wording_does_not_trigger_on_ordinary_numbers_and_working_wins` — 「429 tokens」などの本文では上限にしない。上限の後に再開して作業中なら作業中。
- `test_last_line_skips_frames_prompts_and_hints` — 最後の行は枠・空の入力欄・操作の案内を除いた、意味のある最後の行。
- `test_console_list_carries_state_and_last_line` — コンソールの一覧に状態・最後の行・上限の行が付く（画面は tmux の capture-pane）。
- `test_narrow_screen_with_queued_message_is_still_working` — 幅が狭くて中断の案内が切れても、作業中の行（記号＋動詞…＋経過時間）で作業中と判定する。

### backend/tests/test_commander_team_t2b.py

目的: Nexarch の AI チーム T2b（チームの前提・会話のリセット・確認のコマンドの自動実行と自動の再挑戦）の試験。 目的: 「合否はコードが確かめる・タスクごとに新しい会話・チームの前提を毎回渡す・落ちたら自動で直させる」を守る。 - brief が送り込みの文面の先頭へ付く・リセットのコマンドが CLI ごとに正しく、オフなら送らず、CLI 不明なら飛ばす。 - checks の検証（個数・長さ・制御文字）。 - 実行は本物の小さなコマンド（passed/failed/timeout）。shell を使わない（&& が効かない）・環境変数は最小限・ 作業フォルダが分からない／許可の外なら実行せず error。 - 落ちたら 2 回まで自動で再送（リセットしない）、3 回目も落ちたら blocked と通知。監査ログにはコマンドと終了コードだけ。 根拠: 利用者の方針（2026-11-27）「勝手に完成している状態を目指して欲しい。ユーザーの負担はない形」と docs/PLAN-nexarch-team.md（検証できる完了報告・落ちたら自動で直させる）。 Terminal は FakeManager（tmux は使わない）。隔離テストは env -u CONTROL_DECK_CONFIG で流す。

- `test_brief_is_prefixed_and_empty_brief_is_not`
- `test_brief_validation`
- `test_reset_command_per_cli_then_wait_then_body`
- `test_reset_off_and_unknown_cli_do_not_send_reset`
- `test_checks_validation`
- `test_passed_keeps_review_and_records_results`
- `test_failed_runs_stop_at_first_failure_and_keep_output_tail`
- `test_output_tail_is_limited_to_2000`
- `test_timeout_is_failed`
- `test_no_shell`
- `test_minimal_env`
- `test_runs_in_the_assignee_workdir`
- `test_unknown_or_forbidden_workdir_is_error_and_nothing_runs`
- `test_missing_executable_is_failed_not_crash`
- `test_task_without_checks_is_as_before`
- `test_failure_retries_twice_then_blocks_and_notifies`
- `test_blocked_notification_respects_team_notify_off`
- `test_manual_dispatch_resets_attempts_and_result`
- `test_stale_result_is_dropped_when_task_left_review`
- `test_watch_auto_collect_runs_checks`
- `test_watch_does_not_pick_old_report_after_retry_marker`
- `test_audit_has_commands_and_exit_codes_but_no_output`
- `test_check_output_does_not_fill_memory` — 確認のコマンドが大量に出力しても、出力は一時ファイルへ書いて最後だけ読む（backend のメモリを膨らませない）。

### backend/tests/test_commander_team_t3a.py

目的: Nexarch の AI チーム T3a（ヘッドレスの担い手・上限の方針・同時並列と配り手・チームの作業場所）の試験。 目的: 利用者（2026-11-27）の要望を仕組みで守る。 - 「勝手に完成している状態を目指して」: ヘッドレスの実行が報告を板へ取り込み、確認・再挑戦へつながり、待ちのタスクを配り手が自動で割り当てる。 - 「チームがトークンが尽きて止まった場合、作業内容を引き継いで自動でルーターが別の CLI を開くか、待つかを設定で選択できるように」: on_limit = handoff（前の報告・進み具合・git の変更を本文に付けて別の担い手へ。同じ担い手は選ばない。候補の既定の順）／ wait（回復の時刻まで待って同じ担当で再実行。読めなければ 30 分）。実際にやった担い手を runs に残す。 - 「同時並列処理の場合はどうするのか」: 同時の上限・担い手ごとに 1 つ・担当ファイルの重なりで待つ・ローカルの上限。 根拠: 利用者の要望（2026-11-27）と docs/PLAN-nexarch-team.md T3。 codex のサンドボックス（bwrap）がこの機械で使えない問題（Ubuntu の apparmor_restrict_unprivileged_userns=1。2026-11-27 実機で 書き込みも shell も全部 failed なのに終了コード 0 で「完了」）: 使えるかを一度だけ調べて覚え、使えなければ danger-full-access＋印、 codex_without_sandbox=false なら止める。codex の `status: failed` の item を失敗として数え、書き込みが全部失敗した実行は 未検証・review へ進めず、同じ担い手で 1 回再挑戦 → 続けて同じなら引き継ぎの方針へ。実際の codex の出力の形を試験に使う。 CLI は本物を呼ばない。`accounts.executable_path` を小さな偽の実行ファイル（stream-json・codex の json・opencode の json を出す script） へ差し替える。Terminal は FakeManager（tmux は使わない）。隔離テストは env -u CONTROL_DECK_CONFIG で流す。

- `test_create_team_makes_workdir_and_defaults`
- `test_workdir_is_recreated_when_missing`
- `test_headless_member_validation_and_id_kept`
- `test_update_team_policy_validation`
- `test_fallback_order_saved_and_pruned_when_member_removed`
- `test_claude_argv_and_account_env`
- `test_codex_argv_and_account_env`
- `test_opencode_argv_uses_provider_config`
- `test_missing_executable_blocks_without_retry`
- `test_claude_run_reports_progress_usage_and_runs`
- `test_brief_is_in_prompt_each_run`
- `test_codex_run_takes_report_from_last_message_file`
- `test_opencode_run`
- `test_no_report_makes_unverified_report`
- `test_secrets_not_logged_and_env_is_minimal`
- `test_audit_log_has_no_body`
- `test_failed_run_retries_twice_then_blocks_with_notification`
- `test_checks_failure_reruns_headless_then_blocks`
- `test_checks_pass_in_team_workdir`
- `test_stop_kills_running_process`
- `test_recover_interrupted_marks_and_waits_for_dispatcher`
- `test_limit_detection_and_reset_time`
- `test_stream_state_tolerates_garbage_and_each_cli`
- `test_candidates_default_order_never_same_member`
- `test_handoff_to_other_cli_carries_work_and_git`
- `test_handoff_skips_busy_and_members_that_already_limited`
- `test_handoff_waits_when_candidates_busy`
- `test_wait_policy_headless_waits_until_reset_then_reruns_same_member`
- `test_wait_policy_unknown_reset_waits_30_minutes`
- `test_codex_limit_reads_relative_reset`
- `test_opencode_rate_limit_json_error_is_limit`
- `test_console_limit_wait_leaves_to_auto_resume`
- `test_console_limit_handoff_to_headless`
- `test_handoff_to_console_injects_handoff_text`
- `test_max_parallel_limits_and_board_counts`
- `test_one_task_per_member`
- `test_overlapping_files_wait`
- `test_local_parallel_limit`
- `test_unassigned_task_goes_to_first_free_member_in_fallback_order`
- `test_auto_run_off_does_not_start`
- `test_finishing_a_task_starts_the_next_waiting_one`
- `test_console_task_auto_dispatched_when_ready`
- `test_watch_ignores_headless_tasks`
- `test_api_policy_run_and_stop`
- `test_stream_state_codex_counts_failed_ops_and_fills_progress`
- `test_codex_sandbox_probe_runs_once_and_remembers`
- `test_codex_without_sandbox_uses_danger_full_access_and_marks`
- `test_codex_without_sandbox_off_stops_with_launch_error`
- `test_sandbox_ok_machine_keeps_workspace_write`
- `test_all_writes_failed_retries_same_member_once_then_hands_off`
- `test_all_writes_failed_does_not_reach_review_and_report_is_unverified`
- `test_all_writes_failed_single_member_blocks`

### backend/tests/test_commander_team_t3b.py

目的: Nexarch の AI チーム T3b（チームリーダー・役割・プライマリ／セカンダリ・モデルと考えるレベル）の試験。 目的: 利用者（2026-11-27）の要望を仕組みで守る。 - 「チームはチームリーダーが自動で作成して欲しい。ロールの設定をできるようにして、それに基づいて必要数を呼び出すイメージ」: 目標からチームリーダーが JSON の計画を返し、brief・役割の数・タスクが板に入る（形が違えば 1 回だけ再実行・だめなら止めて通知・ 役割の名前の外は捨てる・数は 1〜4 に丸める・確認のコマンドは許す形だけ）。役割の数まで担い手を呼び出す。 - 「プライマリとセカンダリー。期限切れでセカンダリに移行しても回復後にはプライマリに戻れると良い」: 上限で止まったらセカンダリへ移り、回復の時刻の後の次のタスクからプライマリへ戻る（走っているタスクは止めない）。 - 「トークンが尽きた場合、プライマリ（引き継ぎ先）の設定が無い場合は止まるが、そのコンソールの設定を押すと引き継ぎ先を選べる」: chain が尽きたら止まる（blocked・no_handoff）＋通知、引き継ぎ先を選ぶ API で再開し、チェックでセカンダリに足す。 - 「チームリーダーを任命する感じで」「既定のチームリーダー設定ができると良い」: チームのチームリーダー・既定のチームリーダーと既定の役割の写し。 - 「設定はコンソールとモデルを個別に設定できること。モデルは自動でリストアップし、考えるレベルも選べるように」: モデルの一覧（claude 固定・codex の models_cache・opencode のゲートウェイ）。鍵のファイル（auth.json）は読まない。 model・effort を headless と console の引数へ渡し、不正な値（引数の取り違えになるもの）は断る。 根拠: 利用者の要望（2026-11-27）と docs/PLAN-nexarch-team.md T3。CLI は本物を呼ばない（T3a の試験と同じ偽の実行ファイル）。 隔離テストは env -u CONTROL_DECK_CONFIG で流す。

- `test_models_claude_fixed_aliases_and_efforts`
- `test_models_codex_reads_cache_only_never_key_files`
- `test_models_codex_unreadable_gives_empty_list_with_free_input`
- `test_models_opencode_lists_gateway_and_connection_default`
- `test_models_result_cached_five_minutes`
- `test_models_api`
- `test_claude_argv_gets_model_and_effort_only_when_set`
- `test_codex_argv_gets_model_and_reasoning_effort`
- `test_opencode_argv_model_and_variant`
- `test_endpoint_rejects_unsafe_model`
- `test_endpoint_accepts_listed_and_free_values`
- `test_headless_member_keeps_model_and_validates`
- `test_console_command_gets_model_and_effort`
- `test_new_team_gets_default_roles_and_leader_copy`
- `test_set_roles_validation`
- `test_task_role_validated_and_dropped_with_role`
- `test_dispatcher_calls_up_to_role_count_and_makes_auto_members`
- `test_run_passes_model_and_effort_from_role`
- `test_role_without_endpoints_uses_legacy_pool`
- `test_console_endpoint_opens_nexarch_console_in_team_workdir`
- `test_limit_moves_to_secondary_and_returns_to_primary_after_recovery`
- `test_running_task_is_not_interrupted_when_primary_recovers`
- `test_chain_exhausted_blocks_with_notification_then_choose_handoff`
- `test_choose_handoff_without_adding_and_rejects_running`
- `test_wait_policy_keeps_same_endpoint`
- `test_explicit_fallback_used_after_chain`
- `test_leader_resolution_team_then_default_then_installed`
- `test_api_roles_leader_auto_handoff_and_defaults`
- `test_api_consoles_accept_model_and_effort`
- `test_team_root_validation`
- `test_team_root_creates_named_folder_with_manifest`
- `test_without_team_root_uses_codedev_as_before`
- `test_vanished_team_root_falls_back_to_codedev`
- `test_assistant_team_uses_given_workdir_as_is`
- `test_api_team_root_and_assistant_create`

### backend/tests/test_commander_team_t3c.py

目的: Nexarch の AI チーム T3c（一文で頼む → チームリーダーが MCP で調べ・計画し・振り分け・評価・統合・試験する）の試験。 目的: 利用者（2026-11-27）の要望を仕組みで守る。 - 「一文リーダーに依頼したら、調査・計画・振り分け・個別の実装と評価・統合して評価・試験する。Opus が Haiku・Sonnet を使う流れを模倣。 ただし LLM は横断的に（司令塔も Claude・Codex・OpenRouter・OpenCode・ローカル LLM）」: - MCP サーバー nexarch_team の道具ごと: token の縛り（チーム・実行の番号・期限）・別チームを触れない・入力の検証と上限。 - 計画の保存（確認のコマンドは T2b の許す形だけ）・段（tier）→役割→担い手の自動の振り分け（上限なら secondary・担い手が無ければ チームリーダー自身）・worktree＋ブランチ・統合（merge）と競合・待つ（上限つき）・差分の上限・質問と autonomy。 - チームリーダーの実行: 起動の引数（claude・codex・opencode の MCP の渡し方。token を argv に出さない）・finish 無しの再開（最大 3）・ 上限で secondary が team_status から再開・落ちたら 1 回だけ再開・web_research=false で research(web) を断る・AI アシスタントの道具。 - 通しの試験: 偽のチームリーダー（試験の中の script。実際に stdio の橋渡し→HTTP の口→道具を呼ぶ）が、依頼→計画→explore→ implement 2 つ並列→review→integrate→checks→finish まで回し、結果と変更ファイルが板に残る。 根拠: 利用者の要望（2026-11-27）と docs/PLAN-nexarch-team.md T3c。CLI は本物を呼ばない（T3a の試験と同じ偽の実行ファイル）。 隔離テストは env -u CONTROL_DECK_CONFIG で流す。

- `test_new_team_has_four_tiers_and_leader_fallback_for_unassigned`
- `test_update_team_validates_t3c_settings`
- `test_token_binds_team_run_number_and_expiry`
- `test_http_door_needs_token_and_loopback`
- `test_write_plan_validates_and_keeps_status`
- `test_delegate_picks_endpoint_from_tier_and_falls_back_to_secondary_on_limit`
- `test_delegate_without_endpoint_uses_leader_itself_and_validates`
- `test_delegate_item_respects_depends_on_and_redo_needs_note`
- `test_worktree_per_implement_task_readonly_for_explore_and_merge`
- `test_integrate_conflict_is_reported_then_redo_merges_team_branch`
- `test_integrate_refuses_unfinished_or_failed_checks_and_non_write_tiers`
- `test_non_git_workdir_with_git_init_false_has_no_worktree`
- `test_git_init_creates_repo_in_plain_folder_and_fast_forwards_at_end`
- `test_worktreeinclude_copies_untracked_files`
- `test_wait_returns_progress_when_unfinished_and_limits_arguments`
- `test_read_result_diff_is_capped_and_other_team_run_is_invisible`
- `test_run_checks_in_worktree_and_team`
- `test_research_code_is_bounded_and_web_can_be_refused`
- `test_ask_user_autonomy_high_decides_without_asking_unless_required`
- `test_ask_user_waits_for_answer_in_ask_mode`
- `test_report_and_finish_validation`
- `test_team_status_is_enough_to_resume`
- `test_launch_args_pass_mcp_without_token_in_argv`
- `test_readonly_launch_for_explore_and_review`
- `test_leader_without_finish_is_resumed_up_to_three_times_then_stops`
- `test_leader_crash_is_retried_once_with_same_endpoint`
- `test_leader_limit_moves_to_secondary_which_resumes_from_team_status`
- `test_leader_all_endpoints_limited_fails_and_notifies`
- `test_leader_start_validation_and_double_run`
- `test_leader_launch_error_fails_with_reason`
- `test_through_fake_leader_runs_whole_flow`
- `test_api_leader_land_answer_resume_and_settings`
- `test_assistant_tools_create_team_in_conversation_workdir`

### backend/tests/test_commander_team_watch.py

目的: Nexarch の AI チームの見張り・通知・検証できる報告（T2a。commander/team_watch.py・teams.py）の試験。 目的: 「作業中のタスクの担当が入力待ちに戻ったら報告を自動で取り込む／報告なしで止まったら印を付ける／ 上限で止まったら印を付ける／閉じたら記録する」を、コード（LLM なし）が 1 回だけ・正しい順に行うこと、 通知が同じ出来事で重ならずチームごとに切れること、5 行（確認つき）・古い 4 行の報告を読めて、確認の無い 報告に「未検証」が付くことを守る。 根拠: 利用者「ローカル LLM・Claude・Codex・OpenRouter・OpenCode Go を横断で協調して開発を進めたい」と docs/PLAN-nexarch-team.md の「設計へ取り込むこと」1（検証できる完了報告）・6（通知）・段 T2。 実際の通知先へは送らず、`team_watch.send` を差し替える。Terminal は FakeManager（tmux は使わない）。

- `test_parse_five_line_and_legacy_four_line_report`
- `test_unverified_judgement`
- `test_unverified_in_list_and_manual_report_verify`
- `test_waiting_with_report_is_collected_and_notified_once`
- `test_old_report_before_dispatch_marker_is_not_picked`
- `test_stalled_once_then_cleared_when_working_returns`
- `test_grace_after_dispatch`
- `test_limited_marks_and_notifies_once`
- `test_console_closed_recorded_once`
- `test_capture_once_per_assignee`
- `test_notify_toggle_per_team`
- `test_run_once_sends_through_notification_channels` — send は有効な通知先すべてへ send_notification で送る（宛先の取得と送信の呼び出し）。

### backend/tests/test_commander_teams.py

目的: Nexarch の AI チーム（app/commander/teams.py・/commander/teams API）の試験。 目的: 「タスク板（ControlDeck が正本）＋担当のコンソールへの構造化した送り込み＋画面の 「完了報告:」の取り込み」を守る。保存（0600・壊れた JSON）・検証・メンバーは Nexarch の コンソールだけ・リーダー 1 人・担当ファイルの重なりの警告・dispatch の文面と断り・collect の正規表現・ 手入力の報告・監査ログ・権限。 根拠: 利用者の依頼（2026-11-27）「AI チームを作りたい。情報の橋渡し、リーダーはローカル LLM も フロンティアも自由に。使いやすい形で」と docs/PLAN-nexarch-team.md の T1。弱いモデルでも動くよう 状態の判断と取り込みは LLM でなくコードが持つので、文面の固定と正規表現を機械的に確かめる。 Terminal は FakeManager で置き換える（tmux は使わない）。

- `test_requires_login`
- `test_storage_mode_and_broken_json`
- `test_name_and_text_validation`
- `test_members_only_consoles_one_leader_and_role_marks`
- `test_overlap_warnings`
- `test_dispatch_text_transitions_and_refusals`
- `test_collect_parses_last_block_and_ignores_template`
- `test_manual_report_review_and_report_ready`
- `test_history_is_capped`
- `test_audit_log`

### backend/tests/test_commander_usage.py

目的: Nexarch の 5 時間・週の使用率と回復の時刻（C4 の一部）の試験。 目的: Claude は statusline（Claude Code が渡す rate_limits）、Codex は sessions の記録の rate_limits から、 アカウントごとに使用率と回復の時刻を出す。ログインの鍵は読まない。回復を過ぎた窓は 0 と見なす。 根拠: 2026-11-27 利用者「5 時間制限と週間制限の利用率とリセットまでの時間を追加して」。形は実機 （Claude Code 2.1.332 の statusline の説明・Codex 0.172.0 の rollout）。既存の試験に同じ観点は無い。

- `test_statusline_script_writes_only_limits_and_prints_summary` — statusline のコマンド: 使用率と回復の時刻と model だけを 0600 で書き、短い 1 行を出す。
- `test_claude_settings_file_points_the_statusline_at_the_account_file` — --settings のファイルは statusline と API キーを使わない設定だけで、書き出し先はアカウントごと。
- `test_claude_usage_reads_and_expires_windows`
- `test_codex_usage_from_session_records` — Codex は sessions の最新の rollout の最後の rate_limits（primary=5 時間・secondary=週）。
- `test_account_statusline_toggle_keeps_other_settings_and_never_overwrites` — アカウントの Claude の settings.json に使用率の statusline を足す・外す。ほかの設定は残し、利用者の別の

### backend/tests/test_commander_workdir.py

目的: Nexarch の作業フォルダの選択（app/commander/workdir.py・/commander/workdir/* API）の試験。 目的: コンソールの作業フォルダに選べるのは commander.workdir_roots（既定 /data1tb・/data2tb）の 中だけで、ルートの外・シンボリックリンクで外へ出る道・ControlDeck の data_dir の中・ files の拒否の場所は、一覧にも出さず、作らせず、コンソールも開かせないこと。 一覧はディレクトリだけ（ファイルは返さない）。 根拠: 利用者の指定（2026-11-27）「作業フォルダのルートは /data1tb, /data2tb として他は不要」。 AGENTS.md の「パスは必ず検証（symlink 脱出防止）」「秘密・DB を見せない」。 files.allowed_roots は Files 画面など他の画面の決まりなので変えず、Nexarch 専用の設定にした。

- `test_roots_only_existing`
- `test_list_directories_only_with_hidden_mark`
- `test_outside_root_is_refused`
- `test_symlink_escape_is_refused_and_hidden`
- `test_data_dir_and_denied_places_are_refused`
- `test_make_directory_audited_and_conflict`
- `test_create_consoles_validates_with_workdir_roots`
- `test_opencode_opens_nexarch_workdir_outside_files_roots` — Nexarch の作業フォルダ（files の許可ルートの外でも）で OpenCode を開ける。検証済みの実体のパスを渡す。
- `test_tui_command_validated_project_must_be_a_real_absolute_dir` — tui_command の validated_project は実体の絶対パスだけ（リンク・相対は断る）。

### backend/tests/test_compaction.py

目的: Automatic compaction: the threshold rule, the AI assistant's folding, and the Models flags.

- `test_threshold_follows_the_window`
- `test_frontend_uses_the_same_bands`
- `test_window_divides_fixed_slots`
- `test_long_conversation_is_folded_into_a_summary`
- `test_short_conversation_or_unknown_window_is_left_alone`
- `test_a_failed_summary_keeps_the_conversation`
- `test_next_turn_resumes_from_the_stored_summary`
- `test_only_llama_and_ollama_can_coexist`
- `test_history_bigger_than_the_window_is_summarized_in_pieces` — A conversation carried over from a wider model: no part of it is cut out.

### backend/tests/test_compression.py

目的: 応答の圧縮。逐次流すものは縮めない。

- `test_static_and_api_responses_are_compressed`
- `test_the_llm_gateway_is_left_alone` — token を 1 つずつ返す経路を縮めると、まとめてからでないと出せなくなる。
- `test_event_streams_are_left_alone_wherever_they_are`
- `test_websocket_scopes_are_never_touched`
- `test_the_addon_frame_actually_shrinks` — 実際に縮むこと。ここが効かないと携帯では待ち時間になる。

### backend/tests/test_confidential_local_only.py

目的: 発明の内容を外部の LLM へ出さない守り（ゲートウェイの層・Patent の流れ・埋め込みの宛先）。 目的: - 「機密」の印（ヘッダ x-control-deck-confidential）がある要求は、呼び出し元の許可（egress）があっても cloud: へ出ない（403）。ローカルは通り、印が無ければ従来どおり。 - Patent の流れ（案件の AI 支援・クレームチャート・調査の検索語・文献の分析・図の説明）の LLM 呼び出しは すべて local_only=True（= 機密扱い）で、cloud: を選べない。呼び出しを列挙して固定する。 - 埋め込みの宛先はループバックだけ（資料の本文を外部 URL へ送らない）。 根拠: 2026-11-21 利用者「知財の、私の方から出したアイデアは、ローカル LLM 以外の LLM へのアクセスを拒否する形に。 Patent の特許フローに従ってやる場合は基本的にローカルでしか使えないように」。docs/PLAN-knowledge-platform.md §10.2。

- `test_confidential_mark_blocks_cloud_even_when_the_caller_is_allowed`
- `test_without_the_mark_cloud_works_as_before`
- `test_confidential_mark_does_not_block_local_models` — ローカルのモデルは印があっても止めない（止まるのは cloud: だけ）。モデルが無ければ 404 系で、403 にならない。
- `test_confidential_request_adds_the_mark_only_for_the_gateway`
- `test_confidential_request_refuses_a_cloud_model_before_sending`
- `test_every_patent_flow_llm_call_is_local_only`
- `test_patent_package_has_no_other_llm_entrypoints` — patents/ の中の LLM 呼び出しは上の表にあるものだけ（新しい呼び出し口が黙って増えない）。
- `test_embedding_destination_must_be_loopback`

### backend/tests/test_context_budget.py

目的: LLM を使う機能の量を、モデルのコンテキスト長から決める（context_budget）と、チャットから OpenCode への引き継ぎ。 目的: 2026-11-18 利用者の依頼「コンテキスト長（512K〜1M）を考慮した値に、Deep Research・検索・調査・RAG・チャット・ コード開発で自動設定」「チャットの結果を同じ会話で OpenCode に引き継ぐ」「OpenCode の開発中に調査を挟んで反映」。 - 窓が広いほど多く入れるが、根拠の本文は上限で止める（長い入力ほど精度が落ちる: RULER・NoLiMa・Context Rot） - 短い窓では、根拠＋回答の分が窓を超えない - 窓が分からないときは従来の固定値（Deep Research 90,000 文字前後）と同程度 - 引き継ぐのは前回の OpenCode の回答より後のチャット・調査（出典つき）。上限を超えたら古いほうから落とす

- `test_bigger_windows_get_more_but_evidence_is_capped`
- `test_short_windows_leave_room_for_the_answer`
- `test_unknown_window_is_close_to_the_old_fixed_values`
- `test_exhaustive_stays_within_the_cap_and_the_window`
- `test_custom_deep_research_values_from_before_stay_in_use`
- `test_for_model_uses_the_managed_window`
- `test_handoff_carries_chat_and_research_after_the_last_opencode_answer`
- `test_nothing_to_hand_off_right_after_an_opencode_answer_even_if_it_was_stopped`

### backend/tests/test_control_deck_self_update.py

目的: No network or repository changes from read-only self-update preflight.

- `test_self_update_refuses_non_hex_commit`
- `test_preflight_rejects_non_official_origin`
- `test_preflight_rejects_dirty_main`
- `test_remote_main_rejects_non_sha`
- `test_release_uses_peeled_tag_commit`
- `test_release_rejects_untrusted_tag_or_prerelease`
- `test_failed_health_rolls_back_and_keeps_private_backup`
- `test_finished_updates_are_kept_in_history` — 終わった更新を、自動か手動か・Release 名つきで新しい順に残す（設定画面と、自動更新の後の知らせが読む）。
- `test_release_names_map_commits_to_the_newest_tag` — 更新の画面はコミットではなく Release の名前で出す（2026-11-20 利用者「数値の羅列で何から何へか分からない」）。
- `test_status_names_the_current_and_latest_release`

### backend/tests/test_cpu_fan.py

目的: CPU ファン（マザーボードの Super I/O の hwmon）の読み取りの試験。 目的: nct67xx などのドライバがあれば回転数と PWM(%) を読み、CPU ファンの選び方（ラベル優先・無ければ選んだ番号）、 回転数 0 の扱い、読めないときの無言、機能と拡張の案内コマンドを確かめる。読み取りだけ（書き込みはしない）。 根拠: 2026-11-24 利用者「CPU ファンの回転数とか PWM の変調率とか見えたりしますか？見えるなら CPU のところに表示を追加して」。 hwmon は tmp の偽物で、実機の /sys には触れない。

- `test_no_super_io_reads_nothing`
- `test_default_fan1_without_label`
- `test_label_wins_over_configured_index`
- `test_configured_index_is_used`
- `test_zero_rpm_is_fact_but_not_a_candidate`
- `test_selected_fan_missing_or_unreadable`
- `test_missing_hwmon_root_is_silent`
- `test_save_index_validates_and_broken_file_falls_back`
- `test_driver_guide_command` — 目的: 板に合うファンのドライバの読み込みと、再起動後も読み込む設定のコマンドを出すこと。読み込み済みなら空。

### backend/tests/test_credential_security.py

目的: **未記入**

- `test_password_change_reauthenticates_revokes_sessions_and_audits`
- `test_totp_verify_and_disable_have_shared_user_endpoint_limits`

### backend/tests/test_database_runtime.py

目的: **未記入**

- `test_database_url_accepts_only_sqlite_and_psycopg_without_exposing_password`
- `test_database_environment_file_requires_owned_0600_regular_file`
- `test_database_environment_file_rejects_extra_assignments`
- `test_database_environment_owner_is_current_user`
- `test_pg_dump_uses_fixed_argv_and_keeps_password_out_of_arguments`
- `test_pg_dump_failure_removes_partial_output`
- `test_pg_restore_rejects_symlink_input`
- `test_backup_archive_extracts_regular_known_root`
- `test_backup_archive_rejects_escape_and_links`

### backend/tests/test_deep_research.py

目的: 反復Deep Research、GitHub構造解析、CTX profileの回帰テスト。

- `test_deep_research_runs_multiple_rounds_and_source_portfolio`
- `test_deep_report_continues_sections_cut_by_output_limit`
- `test_code_structure_indexes_functions_variables_routes_and_integrations`
- `test_github_adapter_reads_tree_key_files_and_static_index`
- `test_deep_context_profile_applies_ollama_request_num_ctx`
- `test_deep_context_profile_unset_uses_model_normal_context`
- `test_deep_context_profile_restores_ollama_normal_options`
- `test_deep_context_profile_resizes_managed_llamacpp`
- `test_requested_local_sources_skip_web_search`
- `test_sources_without_urls_deduplicate_by_title_not_root_path`
- `test_local_project_adapter_excludes_secrets_dependencies_and_symlinks`
- `test_workflow_deep_node_uses_shared_assistant_engine`
- `test_claim_check_flags_sentences_their_sources_do_not_support` — 引用先の本文に支えられていない文・出典に無い数値を見つける（2026-11-17: 引用は番号の形式しか見ていなかった）。
- `test_synthesize_rewrites_weak_claims_once_and_keeps_the_better_report` — 弱い文があれば一度だけ直させ、弱い文が減ったときだけ採る。残りは末尾に並べる。

### backend/tests/test_dev_check.py

目的: 開発の関門 `./deck.sh dev-check`（app.maintenance.dev_check）の各検査。 目的: 検査ごとに「見つけるべきものを見つけ、問題の無い差分は通す」ことを確かめる（判定は文字列・辞書だけで決まる関数なので git・pytest・npm を呼ばずに確かめられる）。最後に、小さな git リポジトリで差分の収集と終了の判定を通しで確かめる。 根拠: 開発の作業場 1 段目（2026-11-21）。箱の OpenCode（ローカル LLM・GLM）が Opus の監査に頼らず開発するために、Opus の監査で 見つかった機械的な問題（目録の説明の消失・依存や venv の取得・状態記録の未更新・作者の不定）を、完了の前に自分で検出させる。 秘密や shell=True の見本は、この検査自身に引っかからないよう実行時に組み立てる。

- `test_select_tests_picks_importers_of_changed_modules_and_new_tests`
- `test_route_map_test_always_runs_with_app_code_changes_but_is_not_counted_as_coverage`
- `test_check_tests_fails_when_changed_code_has_no_test_and_when_pytest_fails`
- `test_frontend_build_is_skipped_not_failed_without_node_modules`
- `test_dependency_changes_are_rejected`
- `test_new_tests_need_purpose_basis_and_a_catalog_entry`
- `test_catalog_descriptions_may_not_shrink_but_removed_tests_are_fine`
- `test_status_doc_must_be_updated_when_code_changes`
- `test_secret_shapes_are_found_unless_marked_allowed`
- `test_shell_true_and_os_system_are_found_only_in_real_calls_on_added_lines`
- `test_alembic_needs_exactly_one_head`
- `test_report_lists_failures_and_the_verdict`
- `test_gather_diff_sees_changes_added_lines_and_untracked_files`
- `test_clean_env_does_not_pass_the_trusted_gate_path_to_tests` — U21: 関門を信頼できる版で動かすときの PYTHONPATH を子（作業場の pytest）へ渡さない。渡すと作業場のテストが

### backend/tests/test_dev_sandbox.py

目的: 開発用の軽量サンドボックス（./deck.sh sandbox）。podman は呼ばず、引数と後始末を確かめる。 ネットワークの境界（pod・中継）の目的と根拠: 箱は host network で本番のゲートウェイへ鍵なしで届き、 モデルの切り替え（POST .../models/{m}/load）まで呼べた。箱は pod に入れ、届くのは中継だけにする。 箱の持ち主と MCP（P2a、2026-11-21）の目的と根拠: 箱の AI に ControlDeck の道具を使わせる。誰の権限で動くかを持ち主 （--owner、省くと管理者が 1 人ならその人）で決め、そのトークンは箱の外の中継だけが持つ（0600 の環境変数ファイル、 argv・箱の opencode.json には入れない）。箱の bridge は中継を向く。 箱の設定（meta）を箱の外へ置く目的と根拠（2026-11-22 監査）: 設定が箱の書ける box.path/sandbox.json にあると、箱の中の AI が overlays に ~/.ssh などを足して、次にホストが作る podman の引数で見せられる。設定は `<root>/.meta/<名前>.json`（0700・0600）へ置き、 古い箱は検査を通した分だけ移し、load のたびに検査する（git_common_dir・overlays の 2 種類・ポートの範囲・owner の型）。

- `test_create_clones_without_touching_the_source`
- `test_names_are_validated`
- `test_podman_mounts_only_the_box_writable`
- `test_opencode_plugins_visible_readonly_without_exposing_other_features`
- `test_github_config_only_when_allowed`
- `test_destroy_stays_inside_the_sandbox_root`
- `test_mirror_config_points_inside_the_box`
- `test_opencode_cannot_stop_production_through_deck_sh`
- `test_dev_box_runs_in_its_pod_not_on_the_host_network` — 開発用の箱は --pod で動き host network を使わない。プロジェクトの箱（pod 無し）は従来どおり（箱から本番のモデル切り替えを鍵なしで呼べた）。
- `test_dev_box_has_its_own_tmp_that_goes_away_with_the_box` — 箱ごとの /tmp。空のルートの /tmp は全部の箱で共有され、テストの一時フォルダ（90GB の見かけの GGUF）が
- `test_ensure_pod_reuses_same_spec_and_recreates_on_change` — pod は設定が同じなら使い回し、違えば rm してから作る。pasta:-T と -p の引数。
- `test_old_sandbox_json_without_gateway_port_gets_one_and_saves_it` — 古い箱は load 時に中継のポートを割り当てて保存する。
- `test_opencode_config_has_no_production_key_and_points_at_the_relay` — 箱の opencode.json に本番の鍵を入れず、中継を向く。
- `test_zen_key_goes_to_opencode_only_through_a_private_env_file_outside_the_box` — 目的: Zen のキーは argv に出さず（ps から見える）、0600 の --env-file で箱の外の一時ファイルから渡し、opencode.json に書かない。
- `test_zen_key_is_not_given_when_the_box_disallows_it_or_for_shell_and_exec` — 目的: allow_zen が off の箱、および shell・exec（podman_argv を直接使う経路）にはキーを渡さない。
- `test_new_box_allows_zen_by_default_and_no_zen_turns_it_off` — 既定は許可、--no-zen で off。
- `test_opencode_model_keeps_provider_form_and_defaults_to_controldeck` — 目的: --model は provider/モデルの形ならそのまま、無ければ controldeck/ を付ける。
- `test_owner_is_the_only_admin_or_must_be_named` — 箱の持ち主（MCP の権限）は、管理者が 1 人ならその人、複数・0 人なら --owner を求め、指定は有効な利用者だけ（P2a）。
- `test_old_box_without_owner_gets_one_by_the_same_rule_and_saves_it` — 古い箱（owner 無し）は同じ規則で決めて保存。読むだけの操作は持ち主が決まらなくても通る。
- `test_relay_env_file_is_0600_outside_the_box_and_token_stays_out_of_argv` — MCP のトークンは箱の外の 0600 の環境変数ファイルで中継へ渡し、argv に出さない。
- `test_opencode_config_points_the_bridge_at_the_relay_without_the_mcp_token` — 箱の bridge は中継を向き、トークンは意味の無い値。結果のフォルダは箱の中。
- `test_opencode_config_refuses_to_write_when_the_mcp_token_would_leak` — MCP のトークンが設定に入るなら書き出しを中止する。
- `test_default_chain_skips_opencode_models_without_zen_key_or_when_box_disallows` — 目的: 既定の順番、キー無し・allow_zen off で opencode* を飛ばす、meta の model_chain が優先、local は別名。
- `test_run_switches_model_on_limit_error_and_continues_the_session` — 目的: 上限の印（type=error＋limit 等）で次のモデルへ、取れた sessionID は --session で続け、last_model を保存する。
- `test_run_does_not_switch_on_non_limit_errors_and_ends_with_last_exit_code_when_exhausted` — 目的: 道具の失敗では切り替えない。全部尽きたら最後の終了コードで終わる。
- `test_run_nudges_the_same_session_when_a_subagent_wait_stalls` — 目的: 子の結果待ち（background_wait）のまま動きが無ければ止め、同じモデル・同じ会話へ「待たずに続けて」を送る。
- `test_run_keeps_waiting_while_the_subagent_is_still_progressing` — 目的: 主の会話が background_wait で黙っていても、子の会話が更新され続けていれば止めない。
- `test_child_activity_reads_the_latest_update_of_running_subagents` — 目的: 箱の OpenCode の記録から、動いている子の会話の最後の更新時刻を読む（終わった子・記録が無いときは None）。
- `test_run_does_not_treat_slow_normal_output_as_a_stall` — 目的: background_wait でない間は、出力の間が空いても止めない（長いテストの実行などを切らない）。
- `test_explicit_model_is_used_alone_and_local_is_an_alias` — 目的: --model 明示は順番を使わず 1 つだけ。local はローカル LLM。
- `test_limit_detection_uses_word_boundaries` — 目的: 上限の印は語の区切りで見る。"rate" だけで判定すると generate・accurate を含むふつうのエラーで
- `test_a_box_has_its_own_git_author_and_the_users_gitconfig_is_not_needed` — 開発の作業場 1 段目: 箱の中にはコミットの作者が無く不定だった。作業ツリーの local の設定に固定の作者を入れる。
- `test_reviewer_model_differs_from_the_work_model` — レビュー役は作業と別のモデルにする（同じモデルは同じ見落としをする）。ローカルの作業→最初の外部、外部の作業→ローカル。
- `test_reviewer_stays_local_when_no_external_is_allowed` — allow_zen が off（またはキーが無い）なら外部は使わない。ローカルの作業のレビュー役は設定を変えない（None）。
- `test_opencode_config_gives_the_reviewer_another_model_and_ships_the_development_skill`
- `test_box_keeps_npm_and_pip_offline` — 目的: 箱の中から npm・npx・pip が外へ取りに行かない。
- `test_new_box_writes_meta_outside_the_box_with_private_modes`
- `test_legacy_sandbox_json_is_migrated_once_after_inspection_and_extra_overlays_are_dropped`
- `test_tampered_legacy_meta_is_refused`
- `test_git_common_dir_of_a_stored_meta_is_checked_on_every_load`

### backend/tests/test_dev_skill_planning.py

目的: 開発のスキル（controldeck-development）が、作業の前に指示書（計画）を書かせること。 目的: 要望から `.controldeck/plan.md` に指示書を書き、プランのモードでは見せて止まる段が、スキルから消えないこと。 根拠: 2026-11-22 の試験で、成果を一番左右したのは指示書の質だった（Opus が書いた指示書なら、ローカル LLM でも GLM でも 25〜60 分で仕上がった）。Claude が要る部分を減らすため、指示書の型をスキルに入れた（利用者「Claude が要る部分の開発にまず注力」）。

- `test_skill_asks_for_a_plan_before_building`

### backend/tests/test_dev_workspace_adopt.py

目的: 開発の作業場 3 段目（結果の要約・関門・取り込む）。 目的: 作業場の結果（元からの差分）が要約でき、関門が保存され、「関門が合格で、その後に変更が無い」ときだけ取り込めること。 本体は GitHub に PR（push と gh pr create はホスト側・箱の中からではない。マージはしない）、CodeDEV は元のリポジトリへブランチとして 取り込むだけで元の作業中のブランチは変えない。本体の取り込みは settings.manage。差分の中身は監査へ残さない。 箱の中の AI が書ける .git/config・フック（fsmonitor・credential.helper・sshCommand・insteadOf など）が、ホストの処理 （一覧・状態・差分・関門・取り込み・削除）で動かない（ホストの git は box.repo の中で動かさず、export.git へ取り込んで比べる）。 根拠: 2026-11-22 利用者「作業場の会話に結果のカードと取り込むを足す」（開発の作業場 3 段目。外部への push と権限に関わる L2）。 git は本物（一時のリポジトリ）、箱の中の実行・push・gh はモック（関門の結果は差し替え）。

- `test_changes_summarizes_files_commits_and_uncommitted` — 目的: changes の要約（ファイル数・増減）に未コミットの変更も数に入れること。
- `test_changes_counts_from_merge_base_with_main_not_the_original_revision` — 目的: 作業場が main を取り込んでも、結果のカードの差分は「PR の実際」（main との共通の祖先からの分）だけを数えること。
- `test_changes_falls_back_to_revision_when_main_is_unreadable` — 目的: main が読めなくても、結果のカードは元の revision との差で出る（落ちない・黙って 0 にしない）。
- `test_check_is_saved_with_items_and_goes_stale_after_a_change`
- `test_adopt_refuses_without_check_failed_check_changes_after_and_uncommitted`
- `test_adopt_can_be_forced_over_a_failed_check_but_not_over_changes` — 目的: 「それでも取り込む」（管理者の明示操作・2026-11-27 依頼）で、確認（テスト）に落ちていても
- `test_adopt_controldeck_pushes_from_export_repo_and_opens_pr_without_merging`
- `test_adopt_can_commit_uncommitted_changes_in_the_box_that_the_check_covered`
- `test_adopt_needs_gh_and_a_github_origin`
- `test_adopt_codedev_fetches_a_branch_from_export_and_leaves_the_source_branch_alone`
- `test_hostile_box_git_config_never_runs_on_the_host` — 箱の中の AI が .git/config に仕込んだ実行（fsmonitor・credential.helper・sshCommand・フック）が、ホスト側の
- `test_api_check_job_changes_and_adopt_with_audit_and_permissions`
- `test_api_controldeck_adopt_needs_settings_manage`
- `test_api_force_adopt_over_a_failed_check_needs_settings_manage` — 目的: API の「それでも取り込む」（force）は管理者（settings.manage）だけで使えることを守る。
- `test_suggested_title_skips_autosaves_and_falls_back_to_the_conversation` — U18: PR の題に「作業途中の保存 #N」を使わない。名前の付いたコミットが無ければ、自動保存に記録された会話の題。
- `test_suggested_title_without_conversation_uses_workspace_name`
- `test_check_runs_the_trusted_gate_code_not_the_boxs_copy` — U21: 本体の作業場の関門は、箱のコピーの dev_check ではなく、ホストの main から取り出した版（読み取り専用）で動く。
- `test_trusted_gate_code_is_extracted_from_host_main` — 取り出すのはホストの本体の origin/main の backend/app だけ。同じ版は作り直さず、古い版は 3 つまで。

### backend/tests/test_dev_workspace_devices.py

目的: 作業場へ渡す USB 機器（第 1 段: シリアルと adb の USB）。 目的: 既定は何も渡さず、利用者が選んだ機器だけが箱の podman 起動の --device に入ること。選んでいない機器・ 存在しない機器・by-id の外のパス・形の悪い指定は入らないこと。選べるのは管理者（settings.manage）だけで、 箱の中継は機器の API を通さないこと。付け外しは監査に残ること。 根拠: 2026-11-23 利用者「USB につながってる装置（Android・M5Stack・ラズパイ）を OpenCode から開発できないか。 安全にできないか。作業場ごとに許可する設定でもよい」。sysfs・/dev はテスト用の置き場、podman は呼ばない。

- `test_listing_shows_only_serial_ttys_and_adb_with_serial`
- `test_no_devices_by_default_and_no_device_args`
- `test_only_selected_devices_reach_podman_args`
- `test_missing_or_unplugged_device_is_not_passed_and_warns`
- `test_bad_entries_are_refused`
- `test_tampered_meta_devices_is_rejected_on_load`
- `test_other_users_workspace_cannot_be_changed`
- `test_api_list_set_audit_and_gateway_blocks`
- `test_box_gateway_does_not_expose_device_api` — 箱の中（AI）から届く道は、中継が許したものだけ。機器の API はその中に無い。
- `test_api_requires_settings_manage`

### backend/tests/test_dev_workspace_merge.py

目的: 開発の作業場 4 段目（行ごとの差分・画面からのマージ）。 目的: (1) 差分は箱の作業ツリーでホストの git を動かさず、ホストが管理する export.git の ref から取る。1 ファイル 256KB で 打ち切り、バイナリは中身を出さない。(2) 取り込んだ PR のマージは、PR が開いている・マージできる・PR の head が export の ref と 同じで関門で確かめた tree と同じ・最後の関門が合格、を全部満たすときだけ。区分 L2 は確認の文言（「L2 を理解した」）が要る。 settings.manage が要り、監査へ残す。マージするのは確認した head だけ（--match-head-commit）。Release はしない。 根拠: 2026-11-22 利用者「マージの判断を Claude（Opus）に頼らず、画面で行えるように」（外部への書き込みと権限に関わる L2）。 git は本物（一時のリポジトリ）、gh・箱の中の実行はモック。実際の PR はマージしない。

- `test_file_diff_lists_files_and_reads_one_from_export_without_running_host_git_in_the_box`
- `test_file_diff_truncates_large_files_and_hides_binary`
- `test_api_diff_needs_owner_and_returns_json`
- `test_merge_succeeds_for_l1_with_squash_delete_branch_and_match_head`
- `test_merge_refuses_with_409_reason_and_never_calls_gh_merge`
- `test_merge_refuses_when_the_box_moved_after_adopt_or_the_check_failed`
- `test_merge_refuses_when_pr_tree_differs_from_checked_tree`
- `test_merge_without_pr_or_for_codedev_is_refused`
- `test_l2_change_needs_the_confirmation_phrase_and_lists_guarded_files`
- `test_api_merge_needs_settings_manage_confirm_and_is_audited`
- `test_tier_is_judged_on_the_pr_diff_from_the_current_main` — 区分（L0/L1/L2）は、PR が実際に入れる差分（今の main との共通の祖先＝change_base から）で決める。

### backend/tests/test_dev_workspace_persist.py

目的: 開発の作業場の「保存と中断・再開」（2026-11-22）。 目的: 作業場のコードがいつ保存されるか（1 ターンの終わりの自動保存・起動時の立て直し）、停電や再起動で どうなるか（残った .git/index.lock を消す・生成中のまま止まった会話は「中断」・未保存の変更は保存）、 main の進み（取り込みの前に箱の中で main を取り込む・ぶつかったら止めてファイルを記録）が指示どおりに 動くこと。どの処理でもホストの git が箱の作業ツリーの中で動かない（external fixture の検査）。 根拠: 2026-11-22 利用者「作業場のコードはどういうタイミングで実際に保存されるのか。途中で停電や再起動した 場合はどうなるのか。中断して再開できるのか。他にも考えられる割り込みのシチュエーションを想定した上で、 問題がないように実装してほしい」。 git は本物（一時のリポジトリ）。箱の中の実行・外部への fetch は fixture の代役（origin はローカルの bare）。

- `test_autosave_commits_each_turn_with_sequence_and_leaves_clean`
- `test_autosave_at_startup_names_the_reason`
- `test_autosave_reports_box_failures_without_raising`
- `test_autosave_skips_while_a_merge_is_unresolved` — マージのぶつかりの解決の途中（unmerged が index に残る）はコミットしない — conflict marker 入りの
- `test_autosave_skips_when_box_is_gone`
- `test_disk_floor_blocks_create_and_skips_autosave`
- `test_disk_floor_is_configurable`
- `test_changes_report_disk_free_and_low_watermark`
- `test_busy_follows_the_workspace_chat_server`
- `test_main_progress_counts_origin_and_caches`
- `test_main_progress_returns_none_without_origin_or_unreachable`
- `test_merge_main_pulls_origin_main_into_the_box`
- `test_merge_main_does_nothing_when_up_to_date`
- `test_merge_main_reports_conflict_files_and_aborts`
- `test_adopt_refuses_on_conflict_and_reports_files`
- `test_adopt_merges_main_then_asks_to_rerun_the_check`
- `test_restore_interrupted_clears_stale_lock_and_saves_unsaved`
- `test_restore_interrupted_keeps_lock_while_git_runs`
- `test_restore_interrupted_marks_generating_messages_of_workspaces`
- `test_last_conversation_finds_the_latest_of_the_workspace`
- `test_api_resolve_conflict_sends_the_files_to_the_last_conversation`
- `test_api_fix_check_sends_the_failures_to_the_last_conversation` — 目的: 確認（テスト）で落ちた項目を、この作業場で動いた同じ会話の OpenCode に「直させる」文として
- `test_merge_main_shows_the_bundle_to_the_box_only_read_only` — bundle は箱の AI が書ける場所へ置かず、fetch の間だけ readonly で見せる（symlink を辿らされる脅威）。
- `test_fetch_main_refuses_unsafe_sources` — 実行を伴うリモート補助（ext::・fd::）とオプションに読める先頭の `-` は fetch しない（git も動かさない）。
- `test_autosave_runs_one_at_a_time_per_workspace` — 2 つのターンの終わり・起動時の立て直しが重なっても、番号が重ならず index.lock を取り合わない。
- `test_restore_interrupted_does_not_wake_untouched_workspaces` — 止まっていた作業場まで全部起こさない（podman の起動）。中断にした・lock の残った作業場だけを保存する。
- `test_autosave_returns_the_committed_head`
- `test_restore_resets_code_keeps_before_ref_records_meta_and_note_once`
- `test_checkpoints_list_saves_newest_first_with_count_and_conversation`
- `test_restore_rejects_malformed_sha`
- `test_restore_rejects_commits_outside_this_workspace_history`
- `test_restore_refuses_when_unsaved_changes_cannot_be_saved`
- `test_restore_api_404_409_and_audit`
- `test_agent_made_commit_is_listed_and_restorable`
- `test_autosave_drops_the_stale_probe_cache`

### backend/tests/test_dev_workspace_targets.py

目的: 開発の作業場の「開発対象」と機能（capabilities）。 目的: 作業場を「ControlDeck の改修か」ではなく機能（llm・tools・mirror・gpu）で分け、機能のない作業場では その機能が本当に使えないことを保つ（画面で隠すだけにしない）。 - 開発対象 app / llm_app / controldeck の既定の機能と取り込み先、元との組み合わせの検査 - 権限: controldeck と gpu の作業場の作成は settings.manage、それ以外は terminal.use。監査に target と capabilities - 強制: 中継（llm 無しで models・chat・load が 403、tools 無しで MCP が 403）・箱の環境の OPENAI_*・ モデルの順番からローカルが消える・mirror 無しで mirror-start を断る・adopt の branch / pr の振り分け - 古い作業場（capabilities 無し）は今までどおり（本体なら controldeck、それ以外は llm_app 相当） 根拠: 2026-11-22 利用者「汎用的にするには、ControlDeck の改修かどうかではなく、機能的な改修対象によって権限を分ける方がよい」 （権限と隔離に関わる L2）。中継・podman・systemd は呼ばない（モック）、git は本物。

- `test_presets_give_capabilities_and_adopt_and_default_target_follows_source`
- `test_combination_checks_controldeck_and_mirror_only_for_controldeck_source`
- `test_required_permission_is_manage_for_controldeck_and_gpu_only`
- `test_old_workspace_without_capabilities_behaves_as_before`
- `test_tampered_capabilities_in_meta_are_refused`
- `test_api_permissions_audit_and_listing`
- `test_relay_without_llm_denies_models_chat_and_load_but_keeps_tools`
- `test_relay_without_tools_denies_mcp_but_keeps_llm`
- `test_relay_cli_flags_decide_the_capabilities`
- `test_ensure_gateway_passes_capabilities_and_restarts_a_mismatched_relay`
- `test_gateway_health_must_match_the_boxes_capabilities`
- `test_llm_box_gets_standard_openai_env_and_others_do_not`
- `test_box_without_llm_drops_local_models_and_conversation_is_refused_without_external`
- `test_opencode_config_has_no_controldeck_tools_without_the_tools_capability`
- `test_mirror_start_is_refused_without_the_mirror_capability`
- `test_adopt_branch_for_non_controldeck_target_never_opens_a_pr`
- `test_adopt_pr_only_for_controldeck_source_and_branch_never_pushes`
- `test_api_adopt_permission_follows_the_adopt_mode`

### backend/tests/test_dev_workspace_visual_override.py

目的: 作業場の関門で、画面の見た目（画像を読むモデルの目）の誤検出を、管理者が人の目で確かめて外せること。 目的: 最後の関門が「画面の見た目」だけで落ちたとき、その項目だけ外して流し直せる（--skip-visual）。外したことは結果に残る。 見た目以外も落ちているときは外せない。 根拠: 2026-11-22、利用者として作業場から＋メニューの作り直しを頼んだ通しで、画像を読むモデルが重大の指摘を 3 件出した （撮影を Opus が見て 3 件とも誤検出と判断）。人が確かめた後も外す口が無く、正しい変更が取り込めなかった。

- `test_only_visual_failed`
- `test_run_check_passes_skip_visual_and_records_who`
- `test_other_failures_cannot_be_skipped`

### backend/tests/test_dev_workspaces.py

目的: 開発の作業場 2 段目（AI アシスタントの OpenCode の会話から、開発用の箱を選んで・作って、その中で開発させる）。 目的: 作業場（箱）の中で会話サーバー（opencode serve）を、箱の隔離（pod・中継だけに届く・鍵を置かない）を保ったまま 動かす。持ち主だけが入れ、作る・消す・一覧が API から安全にできる（未保存の変更は確認なしで消さない）。 根拠: 2026-11-22 利用者「Claude なしで、AI アシスタントに要望を出せば OpenCode が開発を進められる状態に」。 設計は 2026-11-21 の指示書（段 1 は #532）。podman・systemd は呼ばず（モック）、git は本物を使う。

- `test_new_box_gets_a_serve_port_and_old_box_gets_one_on_load`
- `test_pod_publishes_serve_port_and_spec_changes_with_it`
- `test_stale_pod_is_not_recreated_while_containers_run` — 目的: 古い箱の pod に動いているコンテナ（ミラー・作業中の OpenCode）があるとき、作り直して落とさない。
- `test_stale_pod_is_recreated_when_nothing_runs`
- `test_box_for_project_only_matches_a_box_work_tree`
- `test_workspace_chat_is_for_the_owner_only`
- `test_resolve_project_returns_a_workspace_path_without_importing`
- `test_workspace_server_starts_in_the_pod_with_env_file_and_relay_only_config` — 目的: 会話サーバーは箱の pod の中（podman --pod）で起き、パスワードは env-file で渡し argv に出さない。
- `test_workspace_server_refuses_unknown_network_and_reports_old_pod`
- `test_create_uses_origin_main_and_auto_names_and_lists_only_own`
- `test_create_from_codedev_requires_a_git_project`
- `test_delete_refuses_unsaved_changes_without_confirm`
- `test_delete_is_owner_only`
- `test_workspace_api_list_create_delete_confirm_and_audit`
- `test_workspace_api_requires_login_and_terminal_permission`
- `test_workspace_progress_states_todo_and_server_from_latest_message` — 目的: Project Lab の「作業場」タブの進み具合（作業中・質問待ち・完了・止まった・ToDo の n/m・最後に動いた時刻）が、
- `test_workspace_progress_flags_interrupted_turns` — 目的: 進み具合が「中断（interrupted）」と「失敗（failed）」を区別して返す（会話の一覧の「中断」の印の根拠）。
- `test_workspace_progress_with_no_workspaces` — 目的: 作業場が 1 つも無いとき、進み具合の API は落ちずに空で返る。

### backend/tests/test_devices_usb.py

目的: USB 機器の見つけ方（sysfs ベース）と、OpenCode のプロジェクトの箱への受け渡し。 目的: (1) M5Paper の WCH ドライバ（/dev/ttyCH343USB0・by-id 無し）のように、by-id が無い USB シリアルも /sys/class/tty の「USB の親」から見つかり、抜き差しで番号が変わっても同じ id で解決される。ブロックデバイス・USB でない tty は出ない。 (2) fastboot・iOS（usbmuxd のソケットの有無）が一覧に出る。(3) プロジェクトの箱は既定 off（argv が変わらない）で、 auto / selected のときだけ --device・keep-groups・usbmuxd の bind・環境変数・エージェントへの説明が入る。 入力のパスは --device に渡らない。設定は管理者だけで、監査 opencode.devices に残り、プロジェクトの外（data）に置く。 根拠: 2026-11-27 利用者「OpenCode で開発したソフトが USB 経由で書き込めない（M5Paper）」「M5 や iOS、Android の開発で アクセス・書き込みできるように」。podman は呼ばず argv を見る。sysfs・/dev は一時フォルダの偽物（devices.SYS_ROOT・DEV_ROOT）。

- `test_ch343_without_by_id_is_found_with_synthetic_id`
- `test_by_id_name_is_used_when_present`
- `test_id_without_serial_uses_usb_port_path`
- `test_same_device_resolves_after_replug_with_new_number`
- `test_non_usb_tty_block_device_and_bad_names_are_not_listed`
- `test_adb_fastboot_and_ios`
- `test_valid_entry_shapes`
- `test_default_off_changes_nothing`
- `test_auto_passes_device_group_env_and_notes`
- `test_selected_passes_only_chosen_and_unplugged_is_skipped`
- `test_auto_ios_binds_usbmuxd_socket`
- `test_input_path_is_never_passed_to_podman`
- `test_setting_is_outside_project_and_survives_garbage`
- `test_api_set_audit_and_restart`
- `test_api_requires_settings_manage_and_box_relay_hides_it`

### backend/tests/test_diagnostics.py

目的: 診断: 記録・詰まりの検出・journal の読み取り・レポートの秘密伏せ。

- `test_redact_hides_secrets`
- `test_test_selection_refuses_pytest_options`
- `test_store_round_trip`
- `test_journal_parses_oom_and_restarts`
- `test_stall_watcher_records_the_blocking_function` — loop を 1.6 秒塞ぐと、塞いだ関数の場所が記録に残る。
- `test_api_requires_admin_and_returns_report`
- `test_agent_mcp_offers_diagnostics_to_admins`
- `test_client_timing_is_recorded_and_summarised` — 画面側の時間（2026-11-17「会話の読み込みが遅い」: サーバー側は速く、通信か描画かを分けるため）。

### backend/tests/test_disk_monitoring.py

目的: **未記入**

- `test_disk_telemetry_snapshot_rates_temperature_and_smart`
- `test_sysfs_temperature_prefers_composite_and_rejects_out_of_range`
- `test_smartctl_uses_fixed_argv_and_parses_json`
- `test_physical_block_device_rejects_non_device_path`
- `test_disk_api_returns_extended_telemetry`

### backend/tests/test_electricity.py

目的: PSU 電力読み取りと電気代積算のテスト。

- `test_hwmon_detection_varies_number`
- `test_hwmon_absent`
- `test_label_case_and_space`
- `test_invalid_power_rejected`
- `test_unit_conversion_zero`
- `test_day_boundary_split`
- `test_trapezoidal_and_efficiency`
- `test_cost_24h` — DC74W・効率85%・35.69円/kWh・24h → 約74.57円。
- `test_accumulator_integrates_and_skips_gaps`
- `test_llm_energy_counts_only_while_an_llm_works` — LLM が処理していた区間だけ、PC 全体の電力を LLM の電気代へ数える。日別に JSON へ残す。
- `test_llm_energy_reloads_from_file`
- `test_llm_energy_reload_skips_a_broken_day`
- `test_llm_average_shows_even_after_a_few_seconds` — 4 秒だけ処理した日でも、平均（電力量 ÷ 時間）と ¥/h を出す。
- `test_config_validation`
- `test_overview_includes_power`

### backend/tests/test_engine_v2.py

目的: エンジン v2（並列/合流/リトライ/エラー分岐/承認/シークレット）と新 API のテスト。

- `test_parallel_fanout_runs_concurrently` — 独立した 2 本の待機（0.3 秒）が並列なら合計 ~0.3 秒で終わる。
- `test_join_all_waits_for_both_inputs` — join=all のノードは両方の入力が終わってから 1 回だけ実行される。
- `test_control_merge_modes_collect_direct_upstreams`
- `test_retry_then_success_counts_attempts` — 初回失敗 → リトライで成功（file.read: リトライ待ちの間にファイルを作る）。
- `test_on_error_branch_routes_to_error_edge` — error edgeへredact済み標準Error Contextを渡す。
- `test_timeout_uses_dedicated_route_and_keeps_error_fallback_compatible`
- `test_on_error_continue_proceeds`
- `test_secrets_template_rendering`
- `test_secrets_api_crud`
- `test_versions_snapshot_and_restore`
- `test_test_node_endpoint`
- `test_webhook_trigger_fires` — webhook トリガー: トークン一致で起動し、CSRF ヘッダーなしでも通る。
- `test_system_trigger_uses_published_definition_and_redacts_payload` — system triggerは公開版で選択し、内部イベントの秘密値をcontextへ残さない。
- `test_flow_call_subflow` — flow.call: サブフローを実行し signal.display の値を受け取る。
- `test_approval_gate_approve_and_reject` — human.approvalは承認者とmessageを表示し、approve APIで再開する。
- `test_human_approval_timeout_uses_timeout_route`
- `test_human_form_cancel_and_timeout_use_typed_error_routes`
- `test_human_approval_rejects_wrong_assignee`
- `test_jobs_api_lifecycle` — ジョブ基盤: 作成 → 進捗 → 完了が API で追える。

### backend/tests/test_event_loop_offload.py

目的: 重い同期処理を event loop の上で走らせていないことの試験。 目的: 画面の応答（event loop）を止める重い同期処理が、loop の外で動くこと。 診断で、loop のスレッドが manifest の読み込み・DB・subprocess・SSL コンテキストの生成で 数秒止まっていた。重い関数をわざと遅くし、その間に loop の別のタスクが進むことを確かめる。 根拠: 2026-11-23 の本番の診断（直近 24 時間）で、Add-on の見張り 6.1 秒・Workflow の回復 2.9 秒・ランタイムの状態 3.0 秒・ SSL の生成 2〜3 秒など、画面の応答が数秒ずつ止まっていた。

- `test_manifest_load_is_cached_until_file_changes`
- `test_addon_health_poll_does_not_block_loop`
- `test_pause_recovery_does_not_block_loop`
- `test_runtime_environment_probes_run_off_loop`
- `test_shared_ssl_context_is_reused`

### backend/tests/test_explicit_knowledge_query.py

目的: Explicit lexical operators must survive retrieval and document ranking.

- `test_fts_explicit_and_natural`
- `test_explicit_never_uses_vector_or_graph`

### backend/tests/test_fake_addon_harness.py

目的: **未記入**

- `test_fake_addon_manifest_is_valid`
- `test_fake_addon_health_states_and_partial_availability`
- `test_fake_gpu_job_completes_and_can_be_canceled`

### backend/tests/test_fan_control.py

目的: ファン制御 v2（PID）の、実機を使わないテスト。 目的: 2 方式の設定・旧設定の引き継ぎ・ファームウェアへ書くカーブ・故障時の安全側の動き・helper の許可範囲を守る。 根拠: 2026-11-17 に 4 方式・7 プリセットから作り直した（docs/design-amd-smart-fan.md）。helper・SMU14・起動時の テストは旧版から引き継いだ（R9700 の実機で踏んだ不具合）。

- `test_curve_is_flat_to_the_hotspot_limit_then_full` — ファームウェアのカーブ: 上限までは制御の出力で一定、上限 +1°C で最大。アプリが止まっても上限での最大は残る。
- `test_over_the_limit_goes_to_full_speed_at_once` — 上限を超えたら、なめらかさの上限を待たずに最大回転（どの方式でも）。
- `test_old_settings_are_carried_over` — 旧版の設定（本番の curve_r9700・温度追従）から引き継ぐのは有効/無効・GPU・最低回転・マージンだけ。
- `test_v068_settings_with_the_old_default_ti_get_the_new_default` — v0.68 で保存した設定は、画面が既定の Ti 50 秒も一緒に書いていた。今の既定（80 秒）へ。手で変えた値は残す。
- `test_margin_below_two_degrees_is_rejected`
- `test_history_records_limits_targets_and_fan`
- `test_autotune_requires_control_and_an_llm`
- `test_autotune_cancel_during_verify_restores_settings_and_stops_load` — 確認のために仮に保存した値は、中止したら元に戻る。負荷役も止める。
- `test_missing_sensor_stops_and_resets`
- `test_zero_rpm_under_heat_trips_fault`
- `test_disable_restores_firmware_auto`
- `test_unusable_firmware_range_is_not_enabled`
- `test_limits_cannot_exceed_reported_critical_margin` — 上限温度はハードウェアの crit − 5°C を超えられない。
- `test_settings_reject_untrusted_names_and_bdf`
- `test_helper_rejects_unsafe_curve_and_bad_path`
- `test_helper_commits_five_points_and_reset`
- `test_explicit_restore_retries_pending_auto_failure`
- `test_unexpected_temperature_read_error_fails_closed`
- `test_helper_zero_rpm_control_uses_fixed_nodes_and_stop_temp`
- `test_helper_refuses_zero_rpm_when_stop_temperature_is_unsafe`
- `test_reset_restores_curve_when_zero_rpm_node_is_unadvertised` — 実機R9700: fan_zero_rpm_enable は存在するが中身が空で、書くとEIO。
- `test_reset_still_fails_when_advertised_zero_rpm_cannot_be_restored`
- `test_helper_accepts_firmware_minimum_but_not_below`
- `test_rejected_auto_restore_stops_retrying_and_asks_for_reboot` — 実機R9700: SMUがリセットを拒否すると以後のOD書き込みは再起動まで全て失敗する。
- `test_smu14_never_resets_on_disable_fault_or_shutdown` — 利用者の判断: R9700(SMU 14)ではリセットを一切使わず、最後のカーブを保持する。
- `test_non_smu14_still_returns_to_firmware_auto`
- `test_helper_refuses_reset_on_smu14_and_skips_revert_on_failed_apply`
- `test_startup_mode_after_pc_boot` — PC起動後に保存設定を自動で再適用するか、GPU標準のまま起動するかを選べる。
- `test_legacy_settings_default_to_reapplying_on_boot`

### backend/tests/test_fan_core.py

目的: ファン制御 v2 の本体（fan_core）のテスト。 目的: PID（＋電力の先読み）が、利用者の要件どおりに動くことを模擬と単体の入力で確かめる。 - 休みから急に全力: 上限を超えず、緊急の最大回転に入らない - 上限を超えたら即座に最大回転。目標まで下がるまで保持 - 温度の上がりを遅らせずに見る（遅らせた版は実機で 93〜95°C まで行き過ぎた） - 上乗せ（積分）は休み中・緊急中に動かさず、熱モデルへ保存して再起動後も引き継ぐ - 外気温が 17°C 上がっても（冬に学んだ熱モデルのまま）、上乗せで目標に戻り上限を超えない 根拠: 2026-11-17・18 利用者の指示（温度マップは外し PID だけ、回転の振れは気にせず温度を一定に、 外気温 37°C まで）。熱モデルは R9700 の実測（data/amd-fan-model.json、210 W 上限）。

- `test_sudden_full_load_stays_inside_the_limit` — 休みから急に全力でも、上限を超えず緊急の最大回転に入らない。
- `test_over_the_limit_is_full_speed_until_back_at_the_target`
- `test_rising_temperature_is_seen_without_delay` — 上がる温度は遅らせない: 中央値の窓（3 回）がそろえば誤差に出る。下がる側だけゆっくり。
- `test_integral_is_kept_while_idle_and_during_an_emergency` — 上乗せ（先読みの不足分）は、休み中と緊急の最大回転の間に動かさない。
- `test_integral_is_saved_in_the_model_and_restored_after_a_restart`
- `test_hot_summer_with_a_winter_model_stays_near_the_target` — 外気温が 17°C 上がっても（熱モデルは冬のまま）、上乗せで目標近くに戻り、上限を超えない。
- `test_tune_uses_the_strong_simc_gains` — 自動調整の効き具合: Kp は閉ループの時定数を遅れ時間と同じにした SIMC（R9700 で約 2.5）、
- `test_without_a_thermal_model_the_pid_still_follows_the_target` — 熱モデルがまだ無い（先読みなし）でも、PI だけで目標へ向かって回転を上げる。
- `test_after_an_emergency_the_slow_descent_does_not_inflate_the_integral` — 緊急の最大回転の後、回転をゆっくり下げている間（冷えて見える）に上乗せを動かさない。
- `test_a_too_large_integral_comes_back_down_under_load` — 上乗せが大きすぎて、負荷中でも回転を下げきって冷えすぎているなら、ゆっくり減らす（冷えたまま残さない）。
- `test_idle_is_judged_from_the_raw_power_not_the_slow_feedforward_power` — 休み（上乗せを動かさない）は生の電力で判定する。先読みの電力はゆっくり下げてあるので、休みでも高く見える。
- `test_without_a_power_reading_the_integral_still_works` — 電力が読めないときも、休みとみなして上乗せを止めない（P だけの制御にならない。レビュー指摘）。
- `test_light_load_at_minimum_fan_does_not_inflate_the_integral` — 軽い負荷で最低回転・目標よりずっと下のとき、上乗せを上へ寄せない（レビュー指摘: 最低で寄せると +6% 膨らむ）。

### backend/tests/test_fan_log.py

目的: ファン制御の診断の記録（fan_log）と、記録からの作り直しのテスト。 目的: 2026-11-18 利用者の依頼「温度を診断に記録して、PID が逸脱したとき長い自動調整をせずに直したい」 「全部記録すると容量がパンクするので、AI に読み込める程度の容量に」を満たすこと。 - 3 秒ごとの値を 1 分 1 行にまとめる（1 日分でも AI が読める大きさ）。2 日より古い記録は消す - 目標 +2°C が 60 秒続いたときだけ逸脱として数える（全力の始まりの一瞬は数えない）。緊急も数える - 記録の「全力で落ち着いた 3 分」「休みで落ち着いた 3 分」から、熱モデルとゲインを作り直せる - 自己診断は、逸脱や緊急があれば「記録から作り直す」を勧める

- `test_one_row_per_minute_and_a_day_fits_an_ai_context`
- `test_deviation_needs_a_minute_over_target_and_emergencies_are_counted`
- `test_old_logs_are_pruned`
- `test_refit_rebuilds_idle_points_and_gain_from_the_log`
- `test_refit_needs_idle_and_loaded_records`
- `test_selftest_recommends_refit_after_deviations`

### backend/tests/test_fan_pid.py

目的: PID fan control, thermal-model feedforward and auto-tune (no hardware).

- `test_fan_for_interpolates_and_extrapolates`
- `test_simc_gains_are_bounded_and_calm`
- `test_pid_raises_when_hot_and_antiwindup_holds_at_max`
- `test_single_noisy_reading_barely_moves_the_output`
- `test_overshoot_ramps_to_full_speed` — 目標+2°Cから+5°Cで100%まで線形に上げる（以前は+5°Cで即100%）。
- `test_autotune_staircase_identifies_model_and_gains`
- `test_feedforward_settles_plant_at_target_after_tuning`
- `test_autotune_aborts_without_enough_points_when_overheating`
- `test_model_json_roundtrip_rejects_garbage`
- `test_pid_asymmetric_pv_filter_retains_cooling_dips_without_slowing_rise`
- `test_pid_ema_does_not_change_raw_median_safety_input`
- `test_ceiling_firmware_backstop_respects_hardware_range`
- `test_production_model_feedforward_is_not_starved_below_the_measured_range` — 本番の熱モデル（50〜70% だけ測った点）で、軽い局面の先読みが少なすぎないこと。

### backend/tests/test_fan_plant.py

目的: 熱の模型（2 段）をプロジェクトに持ち、最適化に使い、本番の 1 秒ごとの記録から当てはめ直せること。 目的: 2026-11-18 利用者「熱モデルもプロジェクトに書き込んでファン制御の最適化に使えるようにし、更新したら新しい ものに更新することとして」「ファンの制御周期が遅い気がする」。

- `test_the_r9700_model_is_packaged_and_chosen_by_pci_id`
- `test_fit_recovers_a_known_plant_from_one_second_samples`
- `test_raw_log_round_trip_and_keeps_two_hours`
- `test_minute_log_with_an_old_header_is_upgraded_before_appending` — 列（floor・rsv）を足したので、今日の古い見出しのファイルに足すと列がずれていた。書き直してから足す。
- `test_tune_optimizes_gains_on_the_plant_and_keeps_the_centre_when_differences_are_noise`
- `test_a_predicted_overshoot_raises_the_fan_to_the_peak_power_level_without_teaching_the_integral` — 全力に戻ると間に合わないと予測したら、最大電力で目標を保てる回転まで先に上げる（利用者の案 2026-11-18）。
- `test_refit_uses_the_longest_run_and_checks_on_held_out_data`

### backend/tests/test_fan_two_stage.py

目的: 実機に当てはめた 2 段の熱の模型（app/monitoring/plant_models/amd-r9700.json）で、ファン制御が緊急の最大回転に 入らないことを確かめる。 目的: 2026-11-18 利用者「温度がオーバーした」「模擬で確かめてマージン 5 度でゲイン調整できると良い」 「またファンがオーバースピードした。運転中も室温が微妙に上がるのと、電力負荷が急変した際はフィードフォワードを」 「熱モデルもプロジェクトに書き込んでファン制御の最適化に使えるように」。 - 記録した実際の電力の流れ・全力と休みの繰り返し・休みから全力を、外気温そのまま／+10°C で流す - LLM らしい負荷（軽い局面と重い局面を行き来する。14:26 の本番はこの形で、負荷中に回転を下げすぎた） - 上乗せは本番と同じく育った状態から（全力 15 分の慣らし）。制御は本番と同じ 3 秒ごと

- `test_margin_five_holds_without_emergency_on_the_fitted_r9700_model`
- `test_llm_like_load_holds_margin_five_without_emergency` — 軽い局面と重い局面を行き来する負荷で、マージン 5°C・外気温 +4°C から運転中に +3°C でも緊急の最大回転に
- `test_a_hotter_gpu_holds_margin_six_under_llm_load` — 模型より熱くなりやすい GPU（ダイ ×1.3・放熱 ×1.15）でも、マージン 6°C なら入らない（本番の今の設定）。

### backend/tests/test_feature_auto_update.py

目的: オプション機能の自動更新（1 日 1 回、決めた時刻に）。 目的: 時刻を過ぎたらその日に 1 回だけ、使われていないときに更新すること。対象は ControlDeck が導入した 壊れていない機能だけで、1 つの失敗で残りを止めないこと。根拠: 2026-11-20 利用者の依頼 「オプション機能を 1 日 1 回、時間を決めて自動でバージョンアップ。ボタンと時間設定を付けて」。

- `test_off_by_default_and_time_is_validated`
- `test_runs_once_a_day_after_the_time`
- `test_waits_while_in_use`
- `test_updates_only_managed_healthy_features_and_keeps_going`
- `test_api_routes_are_not_taken_by_feature_actions` — /features/auto-update は /{feature_id}/{action} に取られない（固定のパスを先に置く）。

### backend/tests/test_feature_boot_restore.py

目的: **未記入**

- `test_boot_restores_enabled_installation_once_without_changing_data`
- `test_boot_respects_addon_opt_out_even_with_stale_feature_flag`
- `test_missing_package_is_not_reinstalled`
- `test_running_service_is_not_rewritten_or_restarted`
- `test_enable_repairs_stopped_service_and_preserves_existing_grants`
- `test_start_failure_is_reported_without_changing_desired_state`
- `test_health_failure_is_not_misreported_as_repaired`
- `test_invalid_installed_entrypoint_is_never_started`
- `test_one_failed_feature_does_not_block_others`

### backend/tests/test_feature_categories.py

目的: オプション機能の画面での分け方（用途で分ける）。

- `test_every_feature_has_a_known_category`
- `test_opencode_tools_are_grouped_with_opencode`
- `test_status_reports_the_category`

### backend/tests/test_feature_search_tools.py

目的: 機能と拡張: 「検索と収集」の分類、Python ライブラリの機能（専用 venv）、更新ありの確認、まとめて導入。 目的: - Web 本文抽出（Trafilatura）は分類「検索と収集」に出て、専用 venv に**版を固定して**入れ、更新で最新版へ上げること（本体の venv を汚さない）。 - 導入済みかは「専用 venv の Python でライブラリを import できるか」で決め、版を出すこと（壊れていれば再導入を促す）。 - pip・npm の機能でも PyPI / npm の最新版と比べて「更新あり」を出せること。 - 分類ごとの「まとめて導入」が、未導入の pip・npm の機能だけを 1 つの Job で入れ、失敗を結果に残すこと。 根拠: 2026-11-21 利用者「必要なコードやプラグインは、ControlDeck の設定の機能と拡張に導入ボタンと更新ボタンを付けて、 個別管理・一括導入・更新ができるようにすること」。docs/PLAN-web-collector.md §3。外部（PyPI）へは出ない（差し替え）。

- `test_web_extract_is_a_pinned_python_library_in_the_search_category`
- `test_broken_library_asks_for_reinstall`
- `test_update_check_compares_with_pypi`
- `test_install_all_in_a_category_runs_one_job`
- `test_search_category_lists_the_search_components` — 検索と収集に、検索エンジン（SearXNG）・本文抽出・robots.txt・JS ページ用のブラウザが並ぶ。
- `test_searxng_is_managed_by_its_setup_script_and_not_uninstalled_here`
- `test_browser_is_this_pcs_chrome_and_nothing_is_downloaded` — Web 収集のブラウザは、この PC の Chrome を使う（Playwright 用の Chromium を別に入れない）。
- `test_install_only_the_checked_features`

### backend/tests/test_features.py

目的: **未記入**

- `test_the_feature_list_is_not_probed_on_every_request` — 一覧は少しの間だけ覚えておく。

### backend/tests/test_files_api.py

目的: **未記入**

- `test_roots`
- `test_mkdir_upload_download_roundtrip`
- `test_text_edit`
- `test_rename_copy_move_delete`
- `test_outside_root_rejected`
- `test_symlink_escape_rejected`
- `test_root_itself_not_deletable`
- `test_viewer_cannot_write`
- `test_trash_restore_and_permanent_delete`
- `test_resumable_upload_roundtrip_and_offset_guard`
- `test_resumable_upload_cancel`
- `test_archive_create_and_extract_roundtrip`
- `test_archive_extract_rejects_traversal_links_and_bombs_without_partial_output`
- `test_media_preview_supports_range_and_rejects_unsafe_inline_type`
- `test_archive_inspect_reports_single_root_for_project_zip` — AIが出力するzipは直下が1フォルダ。配置先名をそこから決められること。
- `test_archive_inspect_reports_no_single_root_for_flat_archive`
- `test_archive_inspect_rejects_broken_archive`
- `test_extract_with_strip_root_places_contents_without_doubling` — Project/Project/… にならずに配置されること。
- `test_extract_with_strip_root_rejects_multi_root_archive`
- `test_presets_list_codedev_and_installed_addon_libraries`
- `test_addon_library_is_hidden_and_closed_until_the_addon_is_installed`
- `test_installed_addon_opens_assets_only_not_the_rest_of_its_data`

### backend/tests/test_flow_app.py

目的: Flow App（Workflow → 単一実行ファイル）の書き出しと実行。

- `test_portable_analysis_blocks_host_only_nodes`
- `test_exported_app_runs_standalone` — 生成した.pyzが、リポジトリ非依存の別プロセスとして実行できる。
- `test_binary_export_runs_without_python` — 単一バイナリは配布先にPythonが無くても動く（ビルド環境アドオン導入時のみ検証）。
- `test_binary_export_requires_addon`
- `test_export_rejects_non_portable_workflow`
- `test_flow_app_api_exports_downloads_and_deletes`
- `test_flow_app_capability_reports_supported_nodes`
- `test_bundle_sources_stay_in_sync_with_host_nodes` — 同梱するのは本体のnodes.pyそのもの。コピー元が消えたら書き出しは壊れる。

### backend/tests/test_frame_proxy_loop_safety.py

目的: Add-on の frame が event loop を止めないこと。 実測（2026-09-06）: モバイルで SonicForge を開くと 1 画面から何十本も同時に 要求が飛び、pool（既定 5+10）が空になった。async の endpoint から ORM を触ると 接続待ちは event loop の上で起きるので、1 本の要求ではなくプロセス全体が 30 秒止まり、systemd の watchdog に SIGABRT で落とされていた。3 日で 68 回。 止まった瞬間の stack: addon_frame_proxy (async) → user_permissions → user.role の遅延読み込み → sqlalchemy pool/queue.py:201 get   ← 接続待ちで同期ブロック 守るのは 3 点。権限の判定が問い合わせを起こさないこと、上流への往復の間 pool の 接続を抱えないこと、枯渇しても watchdog より先に諦めること。

- `test_reading_permissions_does_not_query` — 権限の判定で問い合わせが走らないこと。
- `test_the_session_is_handed_back_before_the_upstream_round_trip` — 上流へ行く間、pool の接続を抱えたままにしない。
- `test_waiting_for_a_connection_cannot_outlast_the_watchdog` — 枯渇しても落とされない。watchdog は 30 秒で SIGABRT を送る。

### backend/tests/test_gateway_cold_start.py

目的: Concurrent gateway calls must remeasure after a shared model starts.

- `test_overlapping_cold_calls_remeasure_after_shared_start`
- `test_interrupted_startup_waiter_does_not_hold_the_model_lock`
- `test_failed_model_start_releases_lease_and_allows_the_next_call`

### backend/tests/test_generation_provenance.py

目的: **未記入**

- `test_public_identity_contains_no_alias_path_or_pid`
- `test_files_or_config_swapped_between_selection_and_inference_rejected`
- `test_serving_process_must_match_selected_instance`
- `test_file_modified_since_process_start_is_not_attested`
- `test_optional_projector_and_draft_are_part_of_identity`

### backend/tests/test_gitrepos.py

目的: GitHub 管理サービスのユニットテスト（ネットワーク不要分）。

- `test_validate_rejects_bad_url`
- `test_validate_rejects_bad_name`
- `test_validate_accepts_typical`
- `test_name_from_url`
- `test_repo_path_rejects_outside`
- `test_status_and_save_and_revert`

### backend/tests/test_gpu_monitoring.py

目的: **未記入**

- `test_sysfs_provider_selects_gpu_with_largest_vram`
- `test_detect_provider_prefers_complete_sysfs_without_cli`
- `test_detect_provider_falls_back_when_sysfs_is_incomplete`

### backend/tests/test_gpu_release.py

目的: GPUランタイム配布物の取得・検証・展開（ネットワーク不要な部分）。

- `test_extract_keeps_layout_and_finds_binary`
- `test_extract_rejects_path_traversal`
- `test_extract_rejects_links_pointing_outside`
- `test_extract_keeps_soname_symlinks` — 共有ライブラリの soname リンクは配布物に必ず含まれる。壊さず展開する。
- `test_extract_rejects_unknown_format`
- `test_download_verifies_published_checksum`
- `test_checksums_parse_only_wellformed_lines` — SHA256SUMS は壊れた行を含みうる。読める行だけを採り、例外にしない。
- `test_prune_versions_keeps_requested`
- `test_relink_swaps_target_in_place`

### backend/tests/test_gpu_runtime_addon.py

目的: アドオンとして管理するGPUランタイム（llama.cpp / Strata）。

- `test_catalog_registers_llama_and_strata`
- `test_status_reports_llama_backends_and_rocm_series`
- `test_status_reports_strata_terminal`
- `test_install_llama_targets_detected_backends_and_prefers_rocm`
- `test_install_llama_fails_clearly_without_a_usable_backend`
- `test_install_update_and_uninstall_strata`
- `test_uninstall_protects_llama_cpp` — llama.cpp を消すと登録済みモデルが全部起動できなくなる。アドオンからは消させない。
- `test_features_api_lists_gpu_runtimes`
- `test_strata_install_job_rejects_retired_track_option`
- `test_release_status_is_only_for_gpu_runtimes`

### backend/tests/test_gpu_tuning.py

目的: R9700 のコア周波数・電圧オフセット（疑似 sysfs。実機には書かない）。 根拠: 2026-11-18 利用者の依頼。対象の選別、符号と単位、片方だけ未対応、失敗時の復元、他の設定に書かないことを確かめる。

- `test_only_the_r9700_is_selected_and_negative_offsets_are_parsed`
- `test_core_and_voltage_change_independently_and_restore_only_what_changed`
- `test_the_preset_applies_both_offsets_and_never_sends_a_reset_or_other_settings`
- `test_out_of_range_and_unsupported_are_reported_separately`
- `test_a_failed_write_restores_the_values_before_it`
- `test_only_the_changed_item_is_restored`
- `test_a_read_failure_is_not_reported_as_missing_support`
- `test_helper_ready_needs_the_passwordless_rule` — 管理者は sudo -l コマンドが成功するので、それでは未導入を見分けられない（2026-11-18 実機）。
- `test_previous_values_do_not_survive_a_reboot`
- `test_api_requires_an_admin_and_maps_errors`

### backend/tests/test_hf_download.py

目的: HuggingFace からの GGUF 取得。

- `test_repo_files_lists_only_gguf_variants`
- `test_sharded_gguf_is_grouped_with_total_size` — 分割GGUFは1つだけ落としても使えないので、まとめて扱う。
- `test_incomplete_shard_set_is_flagged`
- `test_repo_name_must_be_owner_slash_name`
- `test_gated_repo_reports_token_requirement`
- `test_free_space_is_checked_before_downloading` — 入らないものを落とし始めない（途中で詰まると中途半端に残る）。
- `test_download_saves_under_repo_directory_and_can_register`
- `test_download_skips_files_already_present`
- `test_presets_find_files_across_libraries` — 既定ライブラリが変わっても、別の場所にある既存GGUFを再取得しない。

### backend/tests/test_hf_search.py

目的: Hugging Face のモデル検索（AI アシスタントの道具 huggingface_models）。 目的: 「Qwen の最新で Strata に対応しているモデル」を、取りこぼさず・誤判定せず答えられること。 根拠: 2026-11-22 利用者の依頼「検索の幅・十分な件数を担保して」。Strata は ISTA-DASLab の GSQ-RCO・Unsloth の UD-*・Q8_0 だけを 動かし、Q4_K_S などは動かない（strata_gateway.check_runnable、2026-11-21 本番の起動失敗）。秘密の会話は通信の直前で止める （app.security.offline）。HTTP はすべてモック。

- `test_strata_judgement`
- `test_variants_and_params`
- `test_official_first_with_ordered_placements_and_derivatives_below`
- `test_derivatives_can_be_excluded_and_tts_hidden_by_default`
- `test_runtime_strata_keeps_only_rows_with_strata_placement`
- `test_pagination_follows_cursor`
- `test_cache_and_registered_mark`
- `test_confidential_scope_never_connects`
- `test_tool_registered_and_answer_shape`
- `test_inkling_repos_map_to_the_inkling_family` — Inkling 2 Mini の Unsloth の置き場は Strata の inkling-2-mini 系列（strata-inkling、0.2.19〜）として出す。

### backend/tests/test_home_notice.py

目的: Home の通知の 1 行（/home-notices）の順位・解決で消える・閉じる。 目的: - 未解決の元（監視の警報・更新の失敗）を、重要度（エラー > 警告 > 注意 > 情報）→ 新しさの順に返す。 - 元が解決したら（警報の解除・次の更新の成功）一覧から消え、次に重要なものが先頭に来る。 - 「閉じる」は本人の画面だけ。閉じた件は出ず、元が解決した後は印も掃除される。 - 何も無ければ空（画面は行ごと出さない）。外へは出ない（承認・アプリ・更新の状態はモック）。 根拠: 2026-11-23 利用者「ホーム画面に最終的な通知の内容…重要度でインフォメーション、アテンション、ワーニング、エラー…解決されたら消して次に優先度の高い内容を表示」。

- `test_empty_when_nothing`
- `test_ranked_by_severity_then_resolves_to_next`
- `test_same_level_newer_first`
- `test_update_failure_shown_and_cleared_by_next_success`
- `test_dismiss_hides_and_cleans_after_resolution`

### backend/tests/test_hub_expand.py

目的: 目的: ハブの関係・概要で検索の語を広げる部品（app/knowledge/hub_expand.py）と、その使う場所（RAG・特許・Web 調査）・MCP の道具（control_deck.news_knowledge）の試験。 ハブの応答はモック（hub_link.call を差し替える。ハブ・インターネットへは通信しない）。 根拠: 2026-11-27 利用者「（ニュースハブの）関係と概要あたりは参考にすることで、検索ワードを広げるとかそういうのに利用できないか。例えば特許とかにも」。 AGENTS.md「機能を取り込む条件」に従い、効果と時間は hub_expand_eval で測り（docs/implementation-status.md）、効いた所（RAG）だけ既定オン。 守ること: - 重みは 別名 ＞ 上位・下位・置き換え ＞ 仲間・使う ＞ 概要の語。既定は 0.45 未満を足さない（測定で外れを足したため）。理由と元の Thing を付ける。 - 問いと同じ語・短すぎる語・版や hash・一般語（ハブで記事の多すぎる物・一般語の一覧）は外す。purpose=patent は製品名・社名より技術の言葉を重く。 - つながっていない・時間切れ・エラー・403（秘密の区分を許されていない）は空の結果。結果は覚える（失敗は短く）。 - 秘密の区分: 特許は常に confidential=true で問う。通常は今の会話のスコープに従う。MCP の道具は公開だけ（false）。 - 使う場所ごとに設定で入り切り（RAG の既定はオン、Web・特許はオフ）。つながっていなければオンでも呼ばない。広げても元の結果が消えない（融合）。 - MCP の道具の形・件数と大きさの上限。ハブへ呼ぶのは hub_link.call だけ（外部へ送らない）。

- `test_weights_reasons_and_exclusions`
- `test_canonical_name_is_added_when_the_query_used_an_alias`
- `test_short_version_and_generic_words_are_dropped`
- `test_patent_prefers_technical_words_over_product_names`
- `test_partial_matches_are_not_seeds`
- `test_not_linked_timeout_error_and_forbidden_are_empty`
- `test_results_are_cached_and_failures_too`
- `test_confidential_mark_follows_the_scope_and_patent_is_always_confidential`
- `test_enabled_follows_settings_and_link`
- `test_or_query_and_fusion_keep_the_original_order`
- `test_rag_search_fuses_the_expanded_query_only_when_on`
- `test_patent_search_widens_only_when_on_and_honours_excluded_terms`
- `test_web_search_adds_up_to_two_queries_when_on`
- `test_news_knowledge_tool_shape_limits_and_public_only`
- `test_news_cards_cap_size_and_are_empty_when_not_linked`
- `test_hub_link_call_accepts_a_short_timeout` — 検索の補助は待てないので、呼び出しごとに短い timeout を渡せる（既定の 30 秒を変えない）。
- `test_the_module_sends_nothing_outside_the_hub` — 外へ送るのは hub_link.call だけ（httpx・requests・guarded_client を直に使わない）。

### backend/tests/test_inline_code.py

目的: **未記入**

- `test_create_app_with_inline_python_code`
- `test_test_run_python`
- `test_test_run_shell`
- `test_test_run_captures_error`
- `test_test_run_requires_edit_permission`

### backend/tests/test_jobs_persistence.py

目的: ジョブ基盤の DB 永続化・再起動復元・API のテスト。

- `test_job_persisted_to_db` — ジョブ作成→完了が DB に記録され、get_any で再取得できる。
- `test_bounded_event_journal_uses_monotonic_cursor` — 300件で先頭を捨ててもcursorが止まらず、新規deltaを購読できる。
- `test_recover_on_startup_marks_interrupted` — DB に running のまま残ったジョブを起動時に interrupted へ。
- `test_jobs_api_list_and_get` — DB に残ったジョブが API 一覧・詳細で見える（メモリに無くても）。
- `test_job_failure_recorded`
- `test_idempotency_priority_and_queued_cancel`
- `test_waiting_resource_job_does_not_consume_runner_slot_and_cancel_reclaims_request`
- `test_resource_job_progress_is_phase_bound_monotonic_and_coalesced`
- `test_resource_job_oom_is_normalized_profiled_and_not_retried`
- `test_two_exclusive_resource_jobs_execute_serially`
- `test_jobs_api_hides_other_owners_and_streams_snapshot`
- `test_resumable_job_saves_only_resume_params_privately` — 目的: 再開できる Job の作成で、再開に要る引数だけを 0600 の JSON に残すこと。
- `test_finished_resumable_job_forgets_record` — 目的: 成功・失敗で終わった Job の再開用の記録を消すこと。
- `test_canceled_resumable_job_is_not_resumed` — 目的: 利用者が止めた Job の記録は消し、再起動の後に勝手に再開しないこと。
- `test_shutdown_does_not_cancel_resumable_job` — 目的: サーバーの停止で task が cancel されても、再開できる Job を「キャンセル」に
- `test_startup_resumes_interrupted_job_with_factory_and_owner` — 目的: 起動後に、中断された Job を保存した引数から作り直すこと。
- `test_startup_stops_after_three_resume_attempts` — 目的: 自動の再開を 3 回試したら、それ以上作り直さず理由を Job に残すこと。
- `test_resumed_record_keeps_the_first_created_at` — 目的: 自動で作り直した Job の記録は、最初に作った時刻を引き継ぐこと。
- `test_startup_does_not_resume_record_older_than_seven_days` — 目的: 作られてから 7 日を過ぎた Job は再開せず、記録を消すこと。
- `test_startup_resume_can_be_disabled_by_config` — 目的: 設定 jobs.auto_resume=false で、起動後の自動再開を止められること。
- `test_startup_discards_unregistered_broken_or_finished_records` — 目的: 再開できない記録（未登録の種類・壊れた JSON・中断以外で終わった Job）を
- `test_model_download_jobs_are_registered_as_resumable` — 目的: HF 取得と Strata 初回起動の Job が、自動再開の仕組みに載っていること。
- `test_huggingface_download_runner_forwards_saved_params` — 目的: 保存した引数から作った HF 取得の runner が、同じ引数で hf.download を呼ぶこと。
- `test_hf_download_endpoint_creates_resumable_job_without_secret` — 目的: 画面から始めた HF 取得が再開可能な Job になり、 HF のトークンを記録しないこと。

### backend/tests/test_jobs_stream_cleanup.py

目的: **未記入**

- `test_cancelled_stream_joins_update_and_disconnect_waiters`

### backend/tests/test_js_detect.py

目的: JavaScript が要るページの判定（W2）。固定の HTML の見本 15 件（tests/fixtures/js_pages）で評価する。 目的: - 要るページ（空の根・noscript の案内・script だけ）は needs_js、本文がある（SSR 済み・短い通知・分析タグ付き）ページは要らないと判定すること。 誤って「要る」にするとブラウザを無駄に起こし、誤って「要らない」にすると本文が空のまま返る。見本の全件で期待どおりであること。 - 初期 JSON（__NEXT_DATA__・window.__NUXT__・ld+json）に本文があれば、ブラウザを使わずそこから取ること。 本文が既に十分あるページでは JSON を足さないこと。 根拠: docs/PLAN-web-collector.md §7 W2、HANDOFF「W2 で最初にやること」（OpenAlex は「needs JavaScript」の案内 254 字だけ）。通信なし。

- `test_each_sample_is_classified_as_expected`
- `test_accuracy_over_all_samples_is_perfect` — 見本全体の正答率（要る / 静的 / JSON）。1 件でも外れたら基準を見直す。
- `test_html_inside_json_is_reduced_to_text`
- `test_broken_or_huge_json_is_ignored`

### backend/tests/test_js_render.py

目的: W2: 静的 → 初期 JSON → ブラウザの自動の切り替え（fetch.collect の render="auto"）と robots.txt の確認。 目的: - JS が要るページは、機能「Web 収集のブラウザ」が有効なときだけブラウザで取る（実 Chrome。ローカルの試験サーバー）。無効なら警告つきの静的の結果。 - 初期 JSON に本文があればブラウザを起こさず取る。静的で足りるページではブラウザを起こさない。 - robots.txt が禁じたページ・403・ログイン・CAPTCHA は止まり、状態と理由を返す（別の経路で取り直さない）。 - robots.txt の解釈（Allow/Disallow の最長一致・ワイルドカード・5xx は拒否扱い）。 根拠: docs/PLAN-web-collector.md §7 W2、§6。外へは出ない。

- `test_js_page_is_rendered_by_the_browser_when_the_feature_is_enabled`
- `test_without_the_feature_the_static_result_is_returned_with_a_warning`
- `test_initial_json_is_used_without_starting_the_browser`
- `test_static_sufficient_page_never_starts_the_browser`
- `test_render_never_is_the_default_for_other_callers`
- `test_robots_denied_page_is_not_opened_in_the_browser`
- `test_login_page_stops_with_a_reason`
- `test_read_page_uses_the_browser_for_js_pages`
- `test_robots_rules_longest_match_wildcards_and_failures`
- `test_robots_5xx_means_not_allowed_and_4xx_means_allowed`

### backend/tests/test_knowledge_documents.py

目的: 文書を単位にした RAG（書誌の関係・全文か骨格・規格・PDF・AI アシスタントの道具）。 目的: - 特許・規格は文書を単位に探し、入るなら全文、入らなければ骨格（要約・請求項・適用範囲・引用規格）と該当箇所を 元の順で渡すこと。reranker の上位 k 件で文書の中身を削らないこと。 - 公報番号・規格番号で書誌の関係（引用する・引用される・別の版）を結び、検索で 1 hop 広げること。 - 規格は条項で分割し、番号で引け、利用判定で本文の保存・AI 利用を止めること。旧版を現行にしないこと。 - PDF からページの文章・図のページの画像・原本を保存し、ページを locator に持つこと。 - AI アシスタントの道具は、cloud のモデルへ機密・AI 利用の許されない規格を渡さないこと。 根拠: 2026-11-21 利用者「特許・規格は書誌全体が関係する。上位何件に絞れない。コンテキストが十分なら全文を入れて回答」 「PDF から画像・文章・波形グラフ・表を抽出して記録し、体系的に引き出す規格検索」。docs/research-patent-standards-rag.md。

- `test_bibliographic_links_and_one_hop_expansion`
- `test_whole_documents_when_they_fit_else_outline_with_all_claims`
- `test_cloud_consumers_get_no_confidential_or_prohibited_documents`
- `test_standards_are_split_by_clause_and_gated_by_license`
- `test_new_edition_supersedes_the_current_one`
- `test_pdf_pages_figures_and_original_are_kept`
- `test_assistant_tool_respects_the_consumer`
- `test_knowledge_api_find_document_and_asset`

### backend/tests/test_knowledge_essence.py

目的: 知識エッセンス（K1a）: 文献から根拠付きの知識を抽出し、人のレビューで確かめる。 目的: - モデルの出力をそのまま信用しない。抽出（種類 8 つ・1 文献 12 件まで）は、引用が原文の [start, end) に一字一句そのまま一致することを検めてから版付きで保存すること。モデルが位置を 出せない場合は、引用が原文にちょうど 1 回だけ現れるときに限って位置を補うこと（2 回以上・ 0 回は捨てる）。形違い・上限超過は件を捨てて捨てた数を記録すること。 - AI の出力は `basis=model_inference`・`review_status=unreviewed` で固定され、承認しても 「原文の事実」にはならないこと（統合の決まり 1）。 - 人のレビュー（承認・却下・差し戻し）は消さず追記だけ。最新のレビューがその件の状態であること。 - 原文の版が変わったらエッセンスは stale として扱い、前の承認は効かないこと。同じ版へ 戻しても前の承認は戻らないこと（版ごとに数える番号 epoch を単調に増やす）。 - 利用条件で AI に使えない文書（購入した規格など）は抽出しないこと。LLM はローカル専用 （`local_only=True`）で、外部へは通信しないこと。 根拠: 2026-11-21 利用者「PatentBuddy の開いている PR は閉じてよい。開発した内容は ControlDeck にルールに従って反映して」（K1a〜K2 の作り直し。HANDOFF-knowledge-platform.md の K1 の行）。docs/PLAN-knowledge-platform.md §10 R3-4「引用の偽造・版の変更・推論の事実化を 防ぐテスト」。PatentBuddy Draft #29〜#33 を設計だけ流用し、保存先・認証・Job は ControlDeck の既存（analysis.py・survey.py・judge.py）を使い回す。

- `test_validate_keeps_exact_spans_and_drops_mismatched_positions`
- `test_validate_fills_position_only_when_quote_is_unique`
- `test_validate_rejects_revision_mismatch_and_drops_bad_shapes`
- `test_validate_fixes_basis_and_review_status`
- `test_start_runs_local_model_and_saves_validated_essence`
- `test_same_content_rerun_does_not_add_a_row`
- `test_unparsable_model_answer_is_recorded_as_failure`
- `test_documents_ai_may_not_read_are_not_extracted`
- `test_review_appends_only_and_latest_wins`
- `test_stale_when_source_changes_and_approvals_do_not_return`
- `test_api_extraction_and_review_flow`
- `test_api_permissions_separate_view_and_edit`
- `test_api_refuses_prohibited_documents_missing_documents_and_no_local_model`
- `test_review_id_is_shard_qualified_and_never_attaches_across_shards` — 抽出の id は分野ごとに振られる。分野つき（patents:1 など）で指定しないと、
- `test_run_keeps_sync_writes_off_the_event_loop` — run（async）の中の DB 書き込みは asyncio.to_thread を通すこと（AGENTS.md 絶対ルール 9。

### backend/tests/test_knowledge_essence_cross.py

目的: 承認済みの知識エッセンスの横断検索・比較（K2）: app/knowledge/cross.py と API。 目的: - 横断検索が、**承認済みで今の版の**エッセンスだけを返すこと。未承認・却下・差し戻し・原文が 変わった（stale）の item は出ないこと。 - 検索の入口を 1 つに保つこと（計画 §10.2 の 5）: どの文献を探すかは documents.search_documents が 決める。consumer は local なので、利用条件で AI に使えない文献（ai_use=prohibited）は出ないこと （エッセンスはローカル専用の AI の推論であり、横断検索は後のローカルの AI 支援へ知識を渡す入口）。 - 読むたびに引用を原文と確かめ直すこと（K1 の validate と同じ考え方。保存時に検めていても）。 引用が原文に一致しなくなった item は出ないこと。 - 件数の上限（limit）とページ送り（cursor）が、1 回の走査の上限（SCAN_DOCS）と共に守られること。 - 比較が、別の文献 2〜3 件の承認済みを種類ごとに並べるだけであること（AI の判断を足さない）。 - 権限: 読み取り（workflows.run）で見られ、その権限の無い利用者は断られること。 根拠: 2026-11-21 利用者「PatentBuddy の開いている PR は閉じてよい。開発した内容は ControlDeck にルールに従って反映して」（K2 の作り直し。docs/HANDOFF-knowledge-platform.md の K2 の行）。PatentBuddy Draft #36 を設計だけ流用し、保存先・認証・走査の仕組みは ControlDeck の 既存（search_documents・essence.list_essences）を使い回す（コードは写していない）。

- `test_search_returns_only_approved_items`
- `test_search_goes_through_search_documents_and_ai_use_gate`
- `test_search_rechecks_revision_and_quote_every_time`
- `test_search_kind_filter_limit_and_cursor`
- `test_compare_rows_by_kind`
- `test_compare_rejects_wrong_shapes_and_prohibited`
- `test_api_search_and_compare`
- `test_api_permissions_are_read_only_view`
- `test_search_keeps_sync_work_off_the_event_loop` — 検索・比較は同期の DB・ファイル I/O を event loop の外（threadpool）で実行すること

### backend/tests/test_knowledge_links.py

目的: 技術仮説のリンク（K3）: app/knowledge/links.py と API。 目的: - 承認済みの知識エッセンスどうし（別の文献）を、利用者が明示して結べること。 結ぶのは人だけ（AI を呼ばない）。basis は inferred で固定され、承認しても 「原文の事実」にならないこと（統合の決まり 1）。 - 理由のメモが必須であること。未承認の知識・同じ文献・同じ組合せは断ること。 - 作っただけでは「未確認」で、別の操作（承認・却下）で確かめること。 レビューは消さず追記だけ（K1 のレビューと同じ考え）。 - どちらかの知識が stale（今の版で承認済みでない）になったらリンクも stale になること。 stale なリンクにはレビューを付けられないこと。同じ文面へ戻っても承認は戻らないこと （K1 の epoch の決まりを使い回す）。 - 一覧は結ぶ側・結ばれる側のどちらの文献でも見えること。 - 権限: 一覧は読み取り（workflows.run）、作成・レビューは workflows.edit。 同期の DB 読み取りは event loop の外（threadpool）で動くこと。 根拠: 2026-11-21 利用者「PatentBuddy の開いている PR は閉じてよい。開発した内容は ControlDeck にルールに従って反映して」（K3 の作り直し。docs/HANDOFF-knowledge-platform.md の K3 の行）。PatentBuddy Draft #35 を設計だけ流用し（コードは写していない）、 保存先はナレッジの shard DB（K1 の表の足し方）、失効の判定は K1 の list_essences に任せる。

- `test_create_links_approved_items_of_different_documents`
- `test_create_refuses_same_document_empty_note_and_duplicates`
- `test_link_is_unreviewed_until_reviewed_and_reviews_append`
- `test_link_goes_stale_when_source_essence_stales`
- `test_link_goes_stale_when_target_essence_stales`
- `test_list_for_shows_both_sides_with_summaries`
- `test_link_endpoints_keep_sync_work_off_the_event_loop`
- `test_link_across_shards_survives_target_replacement` — target が別の分野にいるリンク: target の差し替えで文書の id が変わっても、
- `test_list_for_keeps_links_apart_by_shard` — リンクの id は分野ごとに振られる。同じ番号でも別の分野のリンクは別々に一覧に出ること
- `test_delete_document_and_collection_remove_links` — 文書・コレクションの削除が、リンクの表があっても落ちることなく通ること。
- `test_link_review_does_not_revive_after_reapproval` — リンクの承認は、知識の承認をやり直しても復活しないこと。
- `test_connect_adds_missing_review_columns_to_existing_tables` — レビュー表に後から足した列（source_epoch・target_epoch）が、既に作られた DB にも
- `test_api_create_list_and_review`
- `test_api_link_permissions_separate_view_and_edit`

### backend/tests/test_knowledge_map.py

目的: 知識マップ（K3）: app/knowledge/map.py と API。 目的: - 1 つの文献を中心に、書誌の関係（引用する・される・別の版）で 1〜2 hop の文献と、 それぞれの **承認済み・今の版の** 知識エッセンスを、ノードとエッジで返すこと。 未承認・却下・差し戻し・原文が変わった（stale）の知識は地図に載らないこと。 - 読み取りだけであること（LLM を呼ばない・書き込みしない・表を足さない）。 - 上限（ノード 61・エッジ 60）を超えたら打ち切りの印（truncated）を出すこと。 hop は 1 か 2 だけで、無い文書は「ありません」で断ること。 - 権限: 読み取り（workflows.run）で見られ、その権限の無い利用者は断られること。 同期の DB 読み取りは event loop の外（threadpool）で動くこと。 根拠: 2026-11-21 利用者「PatentBuddy の開いている PR は閉じてよい。開発した内容は ControlDeck にルールに従って反映して」（K3 の作り直し。docs/HANDOFF-knowledge-platform.md の K3 の行）。PatentBuddy Draft #34 を設計だけ流用し（コードは写していない）、 関係の正本は ControlDeck の書誌の関係（documents.related_documents）、知識の判定は K1 の list_essences を使い回す。上限 36/52 は 61/60 に広げた（文献と知識を 1 つの 地図に載せる分。指示書どおり）。

- `test_map_one_hop_with_essences`
- `test_map_two_hop_reaches_documents_one_hop_cannot`
- `test_map_shows_only_approved_and_current_essences`
- `test_map_cuts_at_limits_with_a_mark`
- `test_map_validates_hops_and_missing_documents`
- `test_map_marks_truncation_when_hop_docs_cut` — 1 回の広げで見る文献の上限（HOP_DOCS）で切ったときも打ち切りの印を出すこと
- `test_map_keeps_sync_work_off_the_event_loop`
- `test_api_map_and_links_list`
- `test_api_map_permissions`

### backend/tests/test_knowledge_r3.py

目的: 統合後の再設計 R3-1: 文献ごとの AI 分析・審査基準と分類辞書の引用・MCP のナレッジ検索。 目的: - 文献の AI 分析はローカル専用のモデルだけで動き、未確認の推論として原文の版付きで残ること。原文が変わったら 分析を消さずに「stale（原文が変わった）」として持ち越し、古い承認を復活させないこと（統合の決まり 2）。 - 利用条件で AI に使えない文書（購入した規格など）は分析しないこと。 - 種類 law のコレクションを Patent の AI 支援が [基準n] で引き、種類 classification を分類候補として渡すこと （ResearchDataHub v0.2 にあった機能。v0.102 の移植で抜けていた）。 - MCP のナレッジ検索は相手を cloud とみなし、機密の資料を返さないこと（統合の決まり 3）。 根拠: 2026-11-21 利用者「各リポジトリの計画を ControlDeck 側に反映し、統合したときに整合性を持つように再検討して進める」。 docs/PLAN-knowledge-platform.md §10。

- `test_document_analysis_is_local_unreviewed_and_marked_stale_when_source_changes`
- `test_documents_that_may_not_reach_ai_are_not_analyzed`
- `test_assist_quotes_law_and_offers_classification_candidates`
- `test_mcp_knowledge_search_treats_the_client_as_cloud`

### backend/tests/test_knowledge_rebuild.py

目的: ナレッジの作り直しと、会話の資料・AI の回答の扱い。 目的: - AI の回答（「web回答: …」）は推論（model_inference）として保存し、旧データも作り直しで直すこと。 - 別の会話の資料（雑談・AI の回答）を、横断検索・アシスタントのナレッジ検索に混ぜないこと（その会話の中では使う）。 - 埋め込みの無い断片・別のモデルの断片を、いまのモデルで作り直すこと。 - 原本 PDF のある古い公報を新しい取り込み（ページ・書誌・図）で作り直し、案件の先行文献の参照を付け替えること。 - 計画の表示は何も書かないこと。 根拠: 2026-11-21 利用者「RAG もデータの再構築が必要ではないか。今のデータは使い物になるか」。本番で調べると AI の回答 57 件が 原文と同じ扱いで、ResearchDataHub から移した公報は埋め込み・書誌が無かった。

- `test_other_conversations_are_not_mixed_into_cross_search`
- `test_chat_answers_are_saved_as_inference`
- `test_plan_writes_nothing_and_apply_fixes_answers_and_embeddings`
- `test_old_patent_pdf_is_reingested_and_case_refs_follow`
- `test_embedding_input_is_capped_for_the_embedding_server` — llama.cpp の埋め込みは 1 件 512 トークンまで。1200 文字の断片は HTTP 500 で断られた（2026-11-21 本番）。
- `test_figure_reading_is_planned_but_left_to_the_server` — 図表の読み取りは画像モデル（GPU）を使うので、サーバーの外の CLI では行わず、画面からの作り直しに回す。
- `test_ingest_keeps_text_when_the_embedding_server_is_down` — 埋め込みのサーバーに届かなくても、本文と全文索引は残し、作り直しの「埋め込み」に出す。
- `test_rebuild_job_fails_visibly_when_some_documents_fail` — 作り直しの一部が失敗したら、Job を失敗にして件数と理由を出す（以前は「成功」と出て、失敗が結果の中に埋もれていた）。

### backend/tests/test_knowledge_storage.py

目的: ナレッジの保存先・保存対象・バックアップ（app.knowledge.storage / backup）。 目的: - 保存対象（DB・図・原本・抽出結果）ごとに保存先を分けられ、許可ルートの外・ネットワーク上の FS の SQLite を拒むこと。 - NAS が外れて目印が無いとき、そこへ黙って書かず「この PC」に置き、後で移せること。 - バックアップの世代を照合でき、稼働中のデータを上書きせずに空のディレクトリへ戻せること。 根拠: 2026-11-19 利用者「ローカル保存・サーバーへのバックアップ・図表などを分けて設定できるように。 保存場所と保存対象を分けて設定できれば柔軟にできる」。SQLite をネットワーク越しに開くとロックが効かず壊れる。

- `test_targets_must_be_inside_allowed_roots`
- `test_sqlite_is_refused_on_network_filesystems`
- `test_unmounted_target_is_not_written_and_blobs_move_later`
- `test_backup_verify_and_restore_to_an_empty_directory`

### backend/tests/test_knowledge_store.py

目的: 統合ナレッジストア（app.knowledge）と旧データの取り込み。 目的: - 旧 rag.py の欠陥を直したまま戻らないこと: 2,000 チャンクでの黙った切り詰め、2 文字の日本語語が 全文検索に当たらない（trigram）、検索のたびに全件を読み 1 件ずつ計算する。 - 旧 data/rag/*.db と ResearchDataHub の library.sqlite3 を、元を書き換えずに取り込めること。 何度流しても同じ結果で、消したコレクションは復活しないこと。 - 分野（一般・特許・規格）を別ファイルに分けても、呼び出し側は名前だけで検索できること。 - クラウド LLM 向けの横断検索で、会話に紐づかない機密文書を返さないこと（アイデアの流出防止）。 根拠: 2026-11-19 利用者の決定「PatentBuddy・ResearchDataHub を ControlDeck に統合し、データを回収する。 DB は分けるが ControlDeck からは意識しない。守りたいのはアイデアの流出」。docs/PLAN-knowledge-platform.md。

- `test_no_silent_truncation_and_batched_embedding`
- `test_two_character_japanese_terms_hit_fulltext`
- `test_vector_cache_follows_writes_and_deletes`
- `test_same_ext_id_and_hash_writes_nothing`
- `test_legacy_collection_is_imported_on_first_use_without_touching_source`
- `test_migrate_cli_plan_writes_nothing_then_apply_is_idempotent`
- `test_researchdatahub_import_goes_to_patent_shard_with_assets`
- `test_researchdatahub_cases_stop_the_import`
- `test_shards_are_separate_files_but_search_is_by_name`
- `test_cloud_cross_search_hides_confidential_except_bound_conversation`
- `test_blob_path_rejects_traversal`
- `test_concurrent_first_use_imports_legacy_once`
- `test_failed_copy_leaves_no_empty_target`

### backend/tests/test_lab_mcp_tools.py

目的: ControlDeck の MCP の Project Lab の道具（control_deck.lab_*）。 目的（何を守るか）: - 一覧・開始・状態・ログ・停止が、project_lab の既存の関数（runs・service）を呼ぶだけで動く。 - 権限は router.py と同じ（見る = project_lab.view、始める・止める = project_lab.run）。権限の無い道具は一覧にも出さず、呼んでも断る。 - 他人の run は見えない・止められない（管理者 = settings.manage は全員分）。無い run と区別しない。 - 開始・停止は監査に残る。ログは上限（最大 256 KB）で切る。 - web の run は preview の URL（鍵付きの相対パスと、ControlDeck の 127.0.0.1 の絶対 URL）を返す。 根拠: 2026-11-21 利用者の決定（HANDOFF-opencode-dev-environment.md の P2a）。OpenCode が Web・ソフトの動作確認に Project Lab を使えるようにする。project_lab の関数はモック（本物の systemd は呼ばない）。

- `test_listing_shows_only_the_lab_tools_the_user_may_use` — 権限の無い道具は一覧に出ない。
- `test_projects_lists_profiles_with_id_label_type`
- `test_start_returns_preview_urls_and_leaves_an_audit` — web は鍵付きの相対パスと 127.0.0.1 の絶対 URL を返し、監査に残る。
- `test_start_refusals_without_permission_bad_args_and_run_error` — 権限無し 403・引数の不正 422・実行の失敗 409（失敗も監査）。
- `test_status_logs_and_stop_work_for_the_owner`
- `test_logs_are_cut_to_the_tail_limit` — 既定 32 KB、最大 256 KB。
- `test_other_users_runs_are_invisible_but_admin_sees_all` — 他人の run は 404、管理者は全員分。

### backend/tests/test_large_model_lifecycle.py

目的: Idle unload, on-demand start and auto-start for Maya-S and Strata.

- `test_idle_model_is_unloaded_after_the_shared_idle_time`
- `test_idle_unload_respects_exclude_disabled_policy_and_unknown_use`
- `test_external_client_keeps_model_loaded`
- `test_maya_starts_on_request_and_frees_memory_first`
- `test_maya_on_demand_start_can_be_turned_off`
- `test_release_for_maya_stops_strata_and_idle_llama`
- `test_release_for_strata_stops_running_maya_only`
- `test_strata_context_accepts_any_k_size_in_range`
- `test_release_for_keeps_a_resident_model`
- `test_gateway_switch_frees_the_other_large_model_only_when_target_is_stopped`
- `test_gateway_switch_never_cuts_a_running_request`
- `test_registered_strata_port_wins_over_an_empty_llama_endpoint`
- `test_selection_follows_the_runtime_that_started`
- `test_selection_is_left_alone_when_runtimes_coexist`
- `test_reconcile_points_selection_at_the_only_loaded_runtime`
- `test_llama_chat_model_start_frees_a_resident_large_model`
- `test_autostart_prefers_model_without_enabling_disabled_models`

### backend/tests/test_live_stats.py

目的: Prefill / generation timing for engines without llama.cpp-style metrics.

- `test_relayed_stream_gives_prefill_and_generation_speed`
- `test_model_names_are_uniform`
- `test_busy_during_covers_requests_that_ended_since_the_last_sample`
- `test_context_from_engine_metrics`
- `test_external_model_calls_are_not_a_local_llm_working` — 外部モデル（ゲートウェイ経由の cloud:・外の URL）を待っている間は、LLM の電気代に数えない。

### backend/tests/test_llama_idle_external_clients.py

目的: Control Deckを経由しない利用（OpenCode等）でllama instanceを止めない。

- `test_connected_client_blocks_idle_unload` — 外部 client が繋いでいる間は降ろさない。
- `test_recently_used_opencode_session_marks_endpoint_in_use`
- `test_disabled_feature_does_not_hold_instances`
- `test_revive_skips_when_opencode_goes_through_the_gateway` — ゲートウェイ経由ならリクエスト時に起きるので、使っていない間は起こし直さない。
- `test_revive_slow_state_probe_does_not_block_other_requests`
- `test_revive_starts_the_resolved_port_when_connected_directly` — 直結設定では、attachされたセッションの転送先portを起こす。
- `test_auto_restart_loop_is_reported_as_failed` — 起動失敗の再試行ループを「起動中」と見せない。
- `test_loading_model_is_still_reported_as_starting` — 通常の読み込み中（再試行ループではない）は従来どおり起動中として扱う。
- `test_throughput_is_measured_from_the_cumulative_counter` — 全slot合算の tok/s は累計トークンの差分から出す。

### backend/tests/test_llama_kv_capacity.py

目的: 共有KV(kv_unified)の空き容量観測と受け入れ制御。 実機で確認した llama.cpp の挙動が前提: - kv_unified では ctx_size が全sequence共有のプール総量になる（各slotの n_ctx は上限値）。 - プールが尽きると待機せず HTTP 500 "Context size has been exceeded" を返し、 実行中の他リクエストごと失敗する。 - 実測: ctx の 74% は通り、97% は失敗した。

- `test_capacity_sums_prompt_and_generated_tokens` — 使用量は稼働中slotの prompt + 生成済みの合計。空きslotは数えない。
- `test_not_accepting_when_pool_is_nearly_full` — 実測で97%は失敗したので、余白を割り込んだら受け入れない。
- `test_not_accepting_when_all_slots_busy`
- `test_capacity_unavailable_for_unmanaged_port` — 停止中・管理外は available=False を返し、呼び出し側は素通しできる。
- `test_await_capacity_returns_immediately_when_idle` — 誰も使っていなければ待たない（単発の大きなリクエストを妨げない）。
- `test_await_capacity_waits_until_room_frees_up` — 混雑中は空くまで待つ。llama.cpp は待たずに落とすため、投げる前に待つ。
- `test_capacity_error_is_detected_only_for_pool_exhaustion` — 枯渇(500)だけ再試行する。単一リクエストのCTX超過(400)は投げ直しても無駄。
- `test_only_llamacpp_retries_on_capacity` — 共有KVを持たないproviderは再試行しない（挙動を変えない）。
- `test_capacity_api_aggregates_running_endpoints` — ダッシュボード表示用。稼働中のエンドポイントだけをまとめて返す。
- `test_unavailable_endpoint_reports_zero_slots_not_full` — モデル読込中は slots=0 になる。これを「満杯」と読ませてはいけない。
- `test_capacity_api_excludes_embedding_and_reranker` — embedding / reranker は RAG の補助で並列駆動の対象ではない。
- `test_throughput_survives_polling_slower_than_the_window` — 見に来る間隔が窓より広くても速度が出ること。
- `test_throughput_starts_over_after_a_long_silence` — 間が空きすぎたら測り直す。

### backend/tests/test_llama_reload_cost.py

目的: **未記入**

- `test_successful_llama_stop_marks_the_next_load_warm`

### backend/tests/test_llama_resource_provider.py

目的: **未記入**

- `test_llama_resource_request_reserves_cold_load_but_not_resident_model`
- `test_llama_large_vision_request_includes_mmproj_and_fits_observed_free_vram`
- `test_gateway_lease_helper_activates_renews_and_releases`
- `test_gateway_disconnect_cancels_waiting_broker_request`
- `test_helpers_are_kept_by_default_and_released_only_when_asked` — RAG が常時使うので毎回外すのは無駄な載せ替えになる。ただし、足りないと
- `test_widening_the_role_filter_does_not_bypass_the_other_guards` — 役割を広げても、operator の「絶対に降ろすな」と接続中は依然として拒む。
- `test_our_own_connection_does_not_pin_the_model`
- `test_the_server_side_socket_is_not_what_gets_inspected` — laddr.port == port の socket の pid は常に llama 自身で、誰が繋いで
- `test_disk_streamed_moe_reservation_is_not_full_gguf_size`

### backend/tests/test_llama_runtime.py

目的: **未記入**

- `test_backend_asset_matching`
- `test_prompt_cache_size_is_passed_when_set` — 一度やった prefill を捨てずに済ませる量を渡せるようにする。
- `test_config_roundtrip`
- `test_old_config_is_migrated_with_new_typed_defaults`
- `test_multi_instance_catalog_uniqueness_and_unit_names`
- `test_resolve_instance_by_port_prefers_running_then_active_then_priority` — 同一ポートを共有したとき、どのモデルがそのポートを代表するかを1件に決める。
- `test_reorder_sets_priority_and_autostart_picks_top_of_endpoint` — 同一エンドポイントで auto_start が複数あっても、enable するのは最優先の1件だけ。
- `test_duplicate_instance_shares_endpoint_without_stealing_autostart`
- `test_mark_used_matches_local_instance_port`
- `test_status_shape`
- `test_unit_content_requires_model`
- `test_unit_content_generation`
- `test_unit_content_typed_mtp_moe_and_cache`
- `test_llama_api_status`
- `test_llama_config_api_rejects_untyped_args_and_bad_values`
- `test_llama_multi_instance_api`
- `test_llama_vision_detection_is_same_folder_and_disabled_by_default`
- `test_unit_content_role_embedding_and_reranker`
- `test_find_role_instance`
- `test_start_instance_restarts_running_unit_to_apply_saved_settings` — 保存済み設定が確実に反映されること。
- `test_delete_instance_can_remove_gguf_but_protects_shared_files` — GGUF本体の削除は取り消せないので、他が参照中なら消さない。
- `test_delete_instance_keeps_selection_when_other_model_removed` — 使っていないモデルを消しただけで既定チャット先が変わらない。
- `test_new_spec_types_are_accepted` — b10793 で dflash / dspark / eagle3 と ngram 各方式が増えた。
- `test_draft_model_is_passed_for_types_that_need_one`
- `test_the_draft_max_you_set_is_the_one_that_runs` — 画面で変えた先読み上限が、そのまま起動引数になる。
- `test_the_other_backend_keeps_its_own_draft_max` — backend を切り替えると、その backend の値が見える。
- `test_draft_ngl_auto_is_left_to_the_binary`
- `test_missing_draft_model_fails_with_a_clear_message`
- `test_ngram_and_mtp_need_no_draft_model`
- `test_acceptance_thresholds_are_only_passed_when_set`
- `test_unknown_spec_type_never_reaches_the_unit`
- `test_draft_cache_types_follow_the_target_when_empty`
- `test_spec_types_are_read_from_the_installed_binary` — 種別はバイナリ側で増える。ハードコードで古くならないようにする。
- `test_spec_type_accepts_a_comma_separated_list` — --spec-type はカンマ区切りで複数取れる（draft系 + draftless系の併用）。
- `test_combined_spec_types_reach_the_unit`
- `test_load_mode_replaces_the_deprecated_mmap_flags` — b10793 で --mmap / --no-mmap / --mlock は deprecated。--load-mode へ寄せる。
- `test_legacy_mmap_settings_map_onto_load_mode` — 既存instanceの mmap/mlock を、等価な load-mode へ写して deprecated を避ける。
- `test_old_binaries_keep_using_the_legacy_flags`
- `test_unknown_load_mode_is_rejected`
- `test_image_min_tokens_is_passed_only_for_vision_instances` — 画像1枚に割く下限は、mmproj を持つ instance にだけ渡す。
- `test_existing_vision_instances_inherit_the_image_token_floor` — この鍵を持たない既存の instance も、読み込みのときに既定を拾う。
- `test_boot_enablement_uses_preferred_model_on_shared_endpoint`
- `test_exclusive_preferred_boot_model_wins_across_endpoints_but_keeps_helpers`

### backend/tests/test_llama_update.py

目的: llama.cpp の最新リリース解決と更新（ROCm 10 統一）。

- `test_tag_ordering_is_numeric_not_lexicographic` — b9544 < b10001 < b10687。文字列比較だと逆転して「更新なし」と誤判定する。
- `test_available_update_compares_against_the_installed_tag`
- `test_available_update_reports_network_failure_without_raising`
- `test_update_stream_refreshes_every_installed_backend` — Vulkan と ROCm を両方入れている環境では、両方を同じタグへ揃える。
- `test_update_stream_is_a_noop_when_already_latest`
- `test_update_stream_requires_a_managed_install`
- `test_rocm_builds_are_declared_as_rocm10`

### backend/tests/test_llm_gateway.py

目的: OpenAI互換ゲートウェイ。 OpenCode のように ControlDeck を経由せず llama.cpp を直接叩くクライアントにも KVの受け入れ制御を効かせるための層。認証はセッションCookieではなく専用APIキー。

- `test_key_is_issued_and_reported_with_base_url`
- `test_rotate_invalidates_previous_key`
- `test_gateway_requires_bearer_key`
- `test_models_listing_returns_registered_llm_aliases`
- `test_models_listing_reports_vision_and_loaded` — 画面の見た目の関門が、画像を読めるローカルのモデルを選べるよう vision・loaded を返す。
- `test_unknown_model_falls_back_to_highest_priority_llm`
- `test_admission_waits_using_shared_capacity` — ゲートウェイ経由でも await_capacity を通る（直結との差を無くすのが目的）。
- `test_direct_port_still_works_and_is_resolvable` — 8090 直結のままでも壊れない（ゲートウェイは任意）。
- `test_gateway_url_resolves_to_backend_port_for_idle_tracking` — ゲートウェイ経由でも「どのモデルが使われているか」を引けること。
- `test_autoconfigure_sets_everything_needed_to_connect` — 導入直後に手作業なしで繋がること。
- `test_install_autoconfigures_opencode_connection` — アドオン導入だけで通信できる状態になること（手作業を残さない）。
- `test_autoconfigure_failure_does_not_break_install` — 自動設定に失敗しても導入自体は成功させる（後から設定画面で直せる）。
- `test_default_target_prefers_loaded_endpoint` — 未指定時は起動中のエンドポイントを優先する。
- `test_internal_calls_resolve_gateway_url_to_real_endpoint` — 内部の生成はゲートウェイへHTTPで戻らず、同じ規則で実エンドポイントを叩く。
- `test_provider_selection_follows_resolved_endpoint` — ゲートウェイ宛のrequestは解決後のllama.cpp providerで処理される。
- `test_auto_model_follows_the_running_endpoint` — autoは「今動いているモデル」へ流す。クライアント側のモデル固定を外すための逃げ道。
- `test_auto_model_uses_the_selected_runtime` — Strata を選んでいるなら、何も載っていなくても先頭の llama.cpp モデルを起こさない。
- `test_stream_failure_is_reported_instead_of_an_empty_ok_stream` — 転送先の失敗を 200 の空ストリームにしない。
- `test_upstream_error_keeps_the_reason_and_bounds_the_body` — 転送先の本文はそのまま渡すが、上限は付ける（文法全体が返ることがある）。
- `test_gateway_endpoint_reports_the_selected_runtime` — ゲートウェイのアドレスはランタイムを変えても同じ。画面が選択の変化に気づけるよう返す。

### backend/tests/test_llm_last_seen.py

目的: 最後に見えていたローカル LLM（Home の LLM カードのアンロード表示）。

- `test_remembers_the_running_model_and_returns_it_after_unload`
- `test_idle_zero_speed_does_not_erase_the_last_speed`
- `test_writes_only_on_model_change_and_unload`
- `test_survives_a_restart_and_ignores_a_loading_model`
- `test_broken_file_is_treated_as_nothing`
- `test_a_failing_disk_is_not_retried_every_poll`
- `test_two_loaded_models_keep_the_same_pick_while_both_idle`

### backend/tests/test_llm_providers.py

目的: **未記入**

- `test_provider_catalog_normalizes_available_and_managed_unavailable`
- `test_detected_provider_ids_include_endpoint_but_managed_ids_are_stable`
- `test_provider_api_and_workflow_compatibility`
- `test_ollama_adapter_normalizes_models_and_lifecycle`
- `test_llama_adapter_lists_and_controls_each_catalog_instance`
- `test_external_provider_rejects_mutation`
- `test_llama_common_config_rejects_identity_changes`
- `test_common_provider_api_routes`
- `test_managed_provider_is_marked_so_ui_can_hide_endpoint_url` — 管理下のモデルは接続先を意識させない。
- `test_gateway_is_the_default_endpoint_for_llama_runtime` — llama.cpp運用時の既定接続先はゲートウェイ。
- `test_gateway_is_hidden_from_runtime_management_listing` — モデル管理の一覧には出さない。モデルを保有するのはllama.cpp側のため。
- `test_concurrent_loads_cannot_exceed_the_limit` — 判定と、判定の前提になる状態の変更が別々だと、両方が「空きがある」を見る。
- `test_a_failed_load_gives_its_slot_back` — 返さないと、以後その分だけ上限が目減りする。
- `test_reloading_a_model_that_is_already_loaded_is_allowed`
- `test_ollama_provider_orders_preferred_registered_model_first`

### backend/tests/test_local_llm_gateway.py

目的: Regression coverage for the remaining managed llama.cpp runtime.

- `test_local_llm_lists_only_managed_llama`
- `test_local_llm_ready_dispatches_to_llama`
- `test_provider_selection_keeps_llama_and_generic_endpoints`
- `test_gateway_never_forces_greedy_sampling_for_llama`
- `test_gateway_dispatches_strata_alias_without_rewriting_other_models`
- `test_strata_registration_remains_explicit_and_preserves_models`
- `test_auto_does_not_choose_unactivated_strata`
- `test_cold_auto_uses_runtime_preference_but_keeps_running_and_explicit_models`

### backend/tests/test_localhost_access.py

目的: 手元からの呼び出しだけ資格情報を省く。外から名乗れないことを確かめる。

- `test_loopback_addresses_with_the_client_header_are_local`
- `test_addresses_reachable_from_outside_are_never_local`
- `test_the_client_header_is_required`
- `test_forwarded_headers_cannot_claim_to_be_local`
- `test_a_request_without_a_peer_address_is_not_local`
- `test_llm_gateway_needs_no_key_from_this_machine_but_still_does_from_outside`
- `test_expired_mcp_token_still_works_from_this_machine_only`
- `test_a_tampered_token_is_refused_even_from_this_machine` — 期限を見ないだけで、署名を見ないわけではない。

### backend/tests/test_logs_redaction.py

目的: **未記入**

- `test_log_api_download_and_websocket_redact_secrets`
- `test_redacted_line_buffer_handles_split_and_oversized_lines`
- `test_custom_log_file_is_contained_redacted_streamed_and_deleted`
- `test_journal_source_uses_registered_unit_and_cannot_be_deleted`
- `test_journal_reader_uses_fixed_argv_and_redacts`

### backend/tests/test_maintenance.py

目的: **未記入**

- `test_log_rotation_copytruncate`
- `test_session_and_audit_purge`
- `test_run_maintenance_all_tasks_ok`
- `test_health_checks`
- `test_self_status_api`
- `test_sd_notify_without_socket`

### backend/tests/test_maya_probe.py

目的: maya_probe の Hy4-Flash の行（hy4_entry）: tools/hy4/download.json を catalog の形へ正しく写す。 目的: Models の Maya 登録画面に Hy4 の 4 サイズ（XS・S・M・L）を出し、n-gram テーブル（全サイズで 同じ 1 ファイル）と mmproj（画像）の在り処を Maya 本体（maya.py の hy4_entry・write_config_hy4）と 同じに保つこと。対応する登録の流れは test_maya_register.py の Hy4 の行で確かめる。 根拠: 2026-11-22 の依頼「Maya の設定に hy4 の対応を追加して」。Hy4 は mm3 と違い、trunk と n-gram テーブルが別々（00001 と 00002 のシャード）で、テーブルが XS のフォルダ 1 か所にだけ在る （S・M・L の setup が --models-dir から hard-link する）。この写し方が違うと、ファイルが在っても 「足りない」と出たり、登録が通っても setup がテーブルを見つけられない。 Maya 本体（v1.1.73、GitHub の mw00/project-maya）の maya.py の hy4_entry・hy4_model_step と tools/hy4/download.json で確かめた見方。

- `test_hy4_list_row_reads_the_manifest_sizes_and_memory`
- `test_hy4_xs_files_cover_trunk_ngram_and_mmproj_in_one_folder`
- `test_hy4_bigger_sizes_keep_the_shared_ngram_and_mmproj_in_the_xs_folder`

### backend/tests/test_maya_register.py

目的: More Maya models beside the first: own alias, port, unit and config; registered from files on disk only. No engine, GPU, systemd or network: the setup and systemctl are replaced.

- `test_first_model_keeps_its_identity_beside_registered_ones`
- `test_starting_one_model_stops_the_other_first`
- `test_stop_without_alias_stops_whichever_runs`
- `test_catalog_is_ready_only_with_every_required_file`
- `test_register_refuses_missing_files_without_running_the_setup`
- `test_register_refuses_while_a_maya_model_runs`
- `test_register_runs_the_setup_offline_and_keeps_other_configs`
- `test_unregister_removes_only_the_config`
- `test_the_first_model_can_be_removed_but_keeps_its_engine_config`
- `test_registry_ignores_rows_that_could_escape_the_engine_folder`
- `test_download_plan_uses_the_repo_named_folder_and_checks_space`
- `test_download_fetches_only_the_manifest_files_at_the_pinned_revision`
- `test_download_refuses_without_space`
- `test_deleting_files_keeps_shared_tables_and_anything_outside`
- `test_refresh_after_an_engine_update_keeps_port_and_tuning`
- `test_glm_maya_s_from_the_sheet_comes_back_as_the_first_model`
- `test_registered_model_settings_write_the_engine_config` — 登録したモデル（DS-XS）の個別設定（2026-11-17 の依頼: 設定が一切無い）。エンジンの args・config へ書き、
- `test_deepseek_vision_is_detected_from_the_pack_and_can_be_switched` — DeepSeek（maya-ds4）は pack に vision tower が入っていて --vision off でなければ画像を読む（Maya #416）。
- `test_vision_flag_is_written_only_when_chosen_and_a_refusal_saves_nothing` — レビュー指摘（2026-11-18）: tower の無いモデルでほかの項目を保存すると --vision off まで書いていた
- `test_hy4_registers_its_alias_and_engine_arguments` — Hy4-Flash の登録: 別名 maya-hy4-xs、表示名「Hy4-Flash · Maya-XS」、setup への引数
- `test_hy4_vision_comes_from_the_pack_and_deepseek_settings_stay_away` — Hy4-Flash の画像の判定: pack（hy4.txt の vision.dim）に入っていて --vision off でないとき。
- `test_hy4_download_plan_counts_the_shared_ngram_table` — Hy4-Flash の「ダウンロードして登録」の見積もり: trunk と共通の n-gram 表（S・M・L も
- `test_hy4_bigger_sizes_need_the_shared_ngram_placed_or_linked` — S・M・L の n-gram 表（全サイズ共通、XS のフォルダに在り）: このサイズのフォルダにも在るか、
- `test_selftest_warns_when_the_running_engine_and_the_vision_judgement_disagree` — 同じ食い違い（エンジンは読めるのに判定は読めない）が起きたら、自己診断で気付けるように。

### backend/tests/test_maya_s_addon.py

目的: Maya-S is strictly opt-in: code install, 96.5 GB download, and serving are separate. No network, GPU, systemd or real Maya payload is required for this test suite.

- `test_feature_registered_independently_from_strata`
- `test_runtime_uninstalled_has_no_files_or_automatic_models`
- `test_install_only_clones_official_release_and_never_runs_maya`
- `test_model_download_needs_an_explicit_prepare_and_selects_only_maya_s`
- `test_prepare_checks_space_and_refuses_invalid_gpu`
- `test_prepare_downloads_into_a_chosen_folder`
- `test_start_requires_preparation_and_uses_user_unit`
- `test_uninstall_rejects_running_service_and_preserves_weights`
- `test_uninstall_refuses_symlink_directory`
- `test_gateway_ready_never_starts_or_downloads_model`
- `test_installer_api_does_not_start_model`
- `test_prepare_endpoint_requires_installed_runtime`
- `test_prepare_endpoint_rejects_untrusted_extra_fields`
- `test_partial_download_config_is_not_marked_ready`
- `test_old_s24_config_or_marker_never_claims_maya_s_ready`
- `test_lock_prevents_parallel_model_operations`
- `test_update_refuses_active_model_before_git_fetch`
- `test_default_gateway_does_not_autoselect_dormant_maya`
- `test_maya_provider_never_auto_starts_on_discovery`
- `test_health_requires_real_loaded_maya_s`
- `test_maya_port_conflict_prevents_start`
- `test_privileged_process_never_mutates_managed_maya`
- `test_settings_save_does_not_start_or_download`
- `test_settings_reject_outside_allowed_roots`
- `test_settings_while_running_rejected`
- `test_build_only_never_invokes_installer_or_engine`
- `test_maya_policy_selection_never_starts_model`
- `test_addon_maya_rejects_unready_and_vision`
- `test_models_page_status_endpoint_reports_without_side_effects` — Models画面は未準備かどうかで「ダウンロード／既存モデル」を出し分ける。
- `test_model_preparation_jobs_are_listed_as_model_jobs` — Modelsの進行表示は kind=model. のジョブを拾う。Maya-Sの準備・起動もそこへ出す。
- `test_built_reflects_upstream_hip_build_dir`
- `test_maya_provider_shrinks_output_budget_to_fit_context`
- `test_maya_provider_reports_prompt_longer_than_context`
- `test_maya_endpoint_uses_maya_provider`
- `test_gateway_fits_maya_output_budget`
- `test_prompt_room_reads_maya_token_count`
- `test_save_settings_enables_maya_output_clamp`
- `test_reasoning_effort_default_is_high_for_coding`
- `test_resetup_keeps_measured_env_and_turns_pictures_on`
- `test_vision_requests_reach_maya_once_its_encoder_is_set_up`
- `test_start_lowers_ondemand_helpers_but_refuses_other_llms` — 資料検索の補助（埋め込み・並べ替え）は Maya の起動を塞がず、先に降ろす。本物の LLM は 409。
- `test_chat_starts_the_requested_maya_model_when_stopped` — 止まっている Maya を AI チャットから使うと、要求したモデルを起動してから答える（他の runtime と同じ）。

### backend/tests/test_memory_net_usage.py

目的: RAM の正味の利用量。 目的: Home・履歴・警告の RAM の割合が、プログラムが映して使っているファイル（モデル）を含めた正味の量になること。 根拠: 2026-11-17 DS-XS（Maya）でモデル約 75GiB をメモリに映して RAM をほぼ使い切っていたのに、psutil の percent（キャッシュを数えない）で 15% と出ていた（利用者「正味の利用量を出して欲しい」）。

- `test_mapped_model_files_count_as_used_but_idle_cache_does_not`
- `test_falls_back_to_psutil_when_meminfo_is_unreadable`

### backend/tests/test_metrics_history.py

目的: **未記入**

- `test_hourly_metrics_are_recomputed_idempotently`
- `test_metrics_history_selects_minute_and_hour_resolution`
- `test_metrics_history_keeps_raw_resolution_for_full_day`

### backend/tests/test_metrics_stream.py

目的: metrics の配信が黙って止まらないことを守る。 いずれも実際に起きた不具合の再発防止。購読者が外されて無音になる、 保存の例外で毎分ぶんの記録が消える、無出力のときに何も送らない—— どれも画面からは「再接続中が居座る」「グラフが止まる」としか見えず、 症状から原因へ辿るのに時間がかかった。

- `test_slow_subscriber_stays_subscribed_and_gets_newest` — 遅い購読者を切り離さない。
- `test_flush_minute_persists_when_rows_for_the_hour_already_exist` — 保存が毎分 TypeError で落ちないこと。
- `test_metrics_stream_sends_stale_heartbeat_while_no_new_value` — 新しい値が来なくても黙らない。

### backend/tests/test_migrations.py

目的: **未記入**

- `test_new_database_upgrades_to_head_without_legacy_backup`
- `test_legacy_database_is_backed_up_verified_and_adopted_once`
- `test_stamped_schema_drift_fails_instead_of_being_silently_hidden`
- `test_versioned_upgrade_creates_verified_pre_upgrade_backup`
- `test_business_event_outbox_migration_has_delivery_constraints`
- `test_hourly_metrics_migration_backfills_existing_minutes`
- `test_job_resource_wait_columns_are_nullable_and_preserve_existing_rows`
- `test_postgresql_offline_migrations_render_to_head_without_sqlite_statements`
- `test_postgresql_migration_uses_advisory_lock`
- `test_unversioned_nonempty_postgresql_schema_is_not_auto_stamped`

### backend/tests/test_mirror_llm_upstream.py

目的: ミラーの「上流の LLM」（config の llm_upstream。計画の P3）。 目的と根拠: 開発用の箱のミラーはモデルの登録が空で、AI アシスタント・Patent の AI・ナレッジの分析が LLM を使えなかった（2026-11-22）。ミラー（CONTROL_DECK_ISOLATED=1）は llm_upstream（箱の中の中継）を 「ローカルの対象」として扱い、中継の先の本番のローカルのモデルへ送る。守ること: - 本番（ISOLATED でない）では llm_upstream を読まない（本番の LLM の経路を変えない）。 - loopback 以外の URL は掴まない（設定ミスで箱の外へ出さない）。 - モデル一覧・chat（ストリーム含む）は上流へそのまま渡り、機密の印（x-control-deck-confidential）も落ちない。 - 内部の AI 機能（resolve_ai_target・RuntimeChatRequest）は上流を使い、local_only の検査を通る。 - mirror_config が中継の URL を書く。 上流は httpx.MockTransport の偽物。

- `test_upstream_is_read_only_when_isolated`
- `test_upstream_rejects_unset_and_non_loopback`
- `test_internal_target_goes_to_upstream_only_in_mirror`
- `test_ai_target_uses_upstream_and_passes_local_check`
- `test_ai_target_unavailable_when_upstream_is_empty_or_down`
- `test_runtime_chat_request_streams_via_upstream_with_confidential_mark` — 内部の AI 機能（RuntimeChatRequest, local_only）が上流へ送り、機密の印・呼び出し元が付く。
- `test_mirror_gateway_relays_models_and_chat_stream`
- `test_relay_forwards_confidential_header`

### backend/tests/test_model_choice.py

目的: AI アシスタントと OpenCode のモデルの選び方をそろえる（2026-11-27）。 目的: (1) 会話で選んだモデルが、OpenCode のモードに切り替えても変わらない（Qwen で始めた会話は OpenCode でも Qwen）。 (2) OpenCode で使えないモデルを選んでいるときは、Models の既定の OpenCode のモデルへ置き換えて 1 行出す。 (3) モデルの一覧は種類ごとのまとまりで、使えない経路（外部送信が許可されていない・Zen のキー無し・秘密の会話）は並べない。 (4) Models で選ぶ OpenCode の既定のモデルが保存・読み出しでき、作業場の順番の先頭になる。 根拠: 2026-11-27 利用者「AI アシスタントで Qwen で会話を始めたが、OpenCode に切り替えた後は GLM が選択されていた」。 原因は、OpenCode の回が会話のモデル（params の model）を見ず、作業場の順番の最初（opencode-go/glm-5.3）で 動いていたこと。OpenCode・会話サーバーは偽物（test_opencode_workspace_persist.py の _fake を使う）。

- `test_conversation_model_follows_into_opencode` — Qwen で選んだ会話を OpenCode へ切り替えても、OpenCode のセッションのモデルは Qwen（GLM に戻らない）。
- `test_unselected_model_keeps_the_existing_order` — 会話でモデルを選んでおらず既定も無ければ、従来どおり作業場の順番（切り替えない）。
- `test_unselected_model_uses_the_models_default`
- `test_unusable_model_is_replaced_by_the_default_with_one_line` — OpenCode への外部送信が許可されていない外部モデルは、既定へ置き換えて 1 行出す。
- `test_model_choice_is_recorded_on_the_user_message` — 会話ごとに保存（再読み込み・別の端末でも同じ）: 選んだモデルが発言の meta に残る。
- `test_agent_switch_keeps_the_chosen_model_for_roles_that_follow_the_session` — プランに切り替えても、セッションのモデルに従う役割は会話で選んだモデルのまま（別に指定した役割は変えない）。
- `test_resolve_rules`
- `test_groups_are_split_by_kind_and_hide_unusable_routes`
- `test_secret_conversation_lists_only_local_models`
- `test_zen_group_needs_a_key`
- `test_model_choices_endpoint`
- `test_secret_flag_follows_the_conversation`
- `test_default_model_roundtrip_and_validation`
- `test_local_default_keeps_the_host_model_in_step`
- `test_default_model_leads_the_workspace_order`

### backend/tests/test_model_display_names.py

目的: モデルの表示名。 目的: - 利用者が付けた名前を、モデルの一覧（Models）とモデルを選ぶ欄（AI アシスタント）に出すこと。空にすると既定へ戻ること。 - ゲートウェイでの名前（alias）は変えないこと（既定のモデル・会話・Workflow の設定が alias を覚えているため）。 - Strata の手元の GGUF の登録は、系列名ではなくファイルの名前と量子化を既定にすること（同じ系列のモデルが同じ名前にならない）。 根拠: 2026-11-21 利用者「モデルが同じ名前になるので分かりにくい。任意の名前に変えれるようにしたい」。

- `test_names_are_saved_per_runtime_and_alias_and_blank_restores_default`
- `test_strata_lists_file_names_and_custom_names`

### backend/tests/test_model_libraries.py

目的: モデルライブラリ（複数ドライブのモデル保存先）。

- `test_detect_volumes_skips_boot_pseudo_and_uuidless` — ブート領域・擬似FS・UUIDなしはモデル置き場の候補にしない。
- `test_library_resolves_through_volume_uuid_not_mount_name` — マウント名が変わっても UUID で追従する。
- `test_unmounted_volume_does_not_fall_back_to_system_drive` — 未接続ドライブは「未接続」として扱い、/ 側へ暗黙に落ちない。
- `test_default_falls_back_to_mounted_library` — 既定ライブラリが未接続なら、接続済みのものへ退避する。
- `test_no_available_library_raises`
- `test_scan_marks_registered_and_orphan_files` — instance から参照されていない GGUF を孤児として識別する。
- `test_validate_rejects_path_outside_allowed_roots`
- `test_validate_rejects_escaping_subpath`
- `test_validate_rejects_duplicate_ids`
- `test_validate_forces_a_default`
- `test_unconfigured_policy_synthesizes_builtin_library` — 未設定環境では従来の data_dir/models/gguf が既定になる（後方互換）。
- `test_library_api_routes`

### backend/tests/test_model_thinking.py

目的: 思考（reasoning）のモデル個別設定。

- `test_normalize_mode_accepts_current_and_legacy_vocabulary`
- `test_budget_only_applies_to_custom`
- `test_levels_map_to_budgets`
- `test_custom_budget_rounds_to_nearest_level_for_level_only_runtimes` — Ollama / OpenAI互換はバジェット非対応なので、最も近いレベルへ落とす。
- `test_auto_sends_nothing_and_off_disables`
- `test_llama_unit_args_reflect_per_model_think` — llama.cpp では instance の CLI 引数になる。
- `test_embedding_role_does_not_get_reasoning_args`
- `test_resolve_reads_llama_instance_by_model_name` — 同一ポートに複数モデルが載るため、モデル名（alias）優先で引く。
- `test_resolve_reads_ollama_model_config`
- `test_resolve_unknown_endpoint_defaults_to_auto`
- `test_shared_policy_no_longer_carries_reasoning` — 共通設定から think を外した。旧キーが残ったJSONもそのまま読める。
- `test_openai_payload_carries_reasoning_effort`
- `test_llama_instance_api_accepts_think`
- `test_migration_moves_shared_reasoning_to_each_model` — 共通設定を消しただけだと体感が変わるので、旧値を個別未設定のモデルへ落とす。

### backend/tests/test_models_local_register.py

目的: ローカル GGUF 登録（スキャン・名前提案・API）のテスト。

- `test_scan_gguf_finds_files_with_depth_limit`
- `test_scan_gguf_rejects_outside_roots_and_files`
- `test_suggest_model_name`
- `test_list_models_matches_running_alias_and_digest` — tagsとpsの表記差があってもロード状態を失わない。
- `test_gguf_scan_endpoint`
- `test_register_rejects_bad_name_and_non_gguf`
- `test_model_config_crud` — モデル個別設定（keep_alive/idle_exclude）の保存・取得・クリア。
- `test_model_options_typed_and_effective` — 生成/ロードパラメータが型検証され、effective_options が options だけ返す。
- `test_kv_cache_settings_and_env`
- `test_ollama_settings_has_no_dead_idle_unload_keys` — ollama-settings.json に idle アンロードの死んだキーを持たせない。
- `test_think_normalize_and_config` — think の正規化と個別設定の保存/クリア。
- `test_native_base_helper`

### backend/tests/test_news.py

目的: 目的: ニュースの専門ページ（app/news）の試験。HTTP は全部モック（外へ通信しない）。 根拠: 2026-11-23 利用者「ニュース機能もつけて…私専門のニュースチャンネルを開設して。よく開くニュースとか傾向を把握してカスタマイズして」 「過去のニュースはアーカイブにして、いつでも閲覧できるようにすること」。 守ること: ソースの整形（外から来た文は平文・url は http/https だけ）・同じ url は更新だけ・1 つのソースの失敗が他を止めない・ event はその人宛てだけ（秘密の会話の題は伏せたまま）・好みの重み（秘密の会話は使わない）・50,000 件の切り捨てで star が残る・移行の up/down。

- `test_topics_use_dictionary_proper_nouns_and_skip_versions`
- `test_release_items_for_github_and_codeberg_skip_drafts_and_flatten_markdown`
- `test_rss_and_atom_pick_images_and_drop_unsafe_links`
- `test_hn_hf_discourse_reddit_shapes`
- `test_reddit_is_fetched_only_with_keys`
- `test_non_http_urls_and_image_urls_are_dropped`
- `test_config_defaults_and_validation`
- `test_sources_api_needs_settings_manage`
- `test_same_url_updates_without_moving_published_at`
- `test_one_failing_source_does_not_stop_others`
- `test_refresh_does_not_run_twice_at_once`
- `test_refresh_api_runs_and_reports`
- `test_events_are_recorded_for_recipients_only_even_when_disabled_or_quiet`
- `test_event_per_permission_and_secret_chat_title_stays_hidden`
- `test_interest_weights_from_actions_and_chats_exclude_secret_chats`
- `test_recommended_orders_by_recency_times_interest_and_hides_hidden`
- `test_profile_api_and_mute`
- `test_actions_toggle_and_exclusive_like_dislike`
- `test_filters_pagination_search_archive_trends_starred`
- `test_response_urls_are_http_or_internal_only`
- `test_prune_keeps_starred_and_removes_oldest`
- `test_news_tables_live_in_their_own_file_not_the_main_db`
- `test_fts_triggers_follow_insert_update_delete`
- `test_late_columns_are_added_to_an_existing_file` — あとから足した列は、古いファイルを開いたとき 1 回足される（知識基盤と同じ仕組み）。
- `test_codeberg_without_releases_reads_tags` — 目的: Release の機能を切った Codeberg のリポジトリでも、タグから更新の記事が取れること。

### backend/tests/test_news_catalog.py

目的: 目的: ニュースの取得先の目録（catalog.json）・ソースごとの取得の間隔・フィードを作る手伝い・取得先の管理（API・取得の履歴）の試験。外へ通信しない。 根拠: 2026-11-24 利用者「これらを元に、情報収集の蓄積、ナレッジ化を効率的に実施してほしい」「有料なのは使わない」 「登録時に HTTP 応答、XML 形式、最終更新日時を検証し、配信停止済みのフィードを除外する方式を推奨」「設定の中にボタンを置き、別の管理ページに飛ばして、疎通確認だったりとか、 これまでの取得実績、最終取得日時などを…新しいサイトの追加、削除などができるように」。 守ること: 目録は https・URL の重複なし・分野あり・確かめた日あり・有料でない・Reddit はキー印／間隔の来たソースだけを 1 回の上限つきで選ぶ／ 作った URL の組み立てと不正な入力の拒否／履歴は 1 ソース 200 回まで／一括の操作は設定を壊さない。

- `test_catalog_shape_unique_https_grouped_verified_free`
- `test_catalog_has_requested_groups_intervals_and_recommended`
- `test_due_keys_only_sources_whose_interval_has_come_and_cap`
- `test_intervals_and_disabled_in_config_and_build_sources`
- `test_feedgen_builds_urls_and_rejects_bad_input`
- `test_fetchlog_keeps_200_per_source_and_summarizes`
- `test_refresh_with_keys_records_history_and_skips_other_sources`
- `test_manage_api_list_detail_bulk_add_and_permissions`
- `test_manage_write_needs_settings_manage`

### backend/tests/test_news_channels.py

目的: 目的: ニュースチャンネル（app/news/channels.py・home.py・prefs.py）の試験。HTTP は全部モック（外へ通信しない）。 根拠: 2026-11-23 利用者「設定で検索テーマや表示のデザインなど、ニュースを便利に使うための設定を詳細にできるように」「OZY のようなページのデザインを参考に」。 守ること: チャンネルの語での所属判定と外す語・Google ニュースの RSS の整形と URL・検索のソースの組み立て・/home の形（1 回で全区画）・ 設定の検査（チャンネル・表示・好み）・好みの強さがおすすめの順位に効く・ミュートの語とソース・書き出し（JSON・CSV の式の無害化）。

- `test_matching_terms_exclude_kinds_and_search_origin`
- `test_default_channels_are_valid_and_cover_the_examples`
- `test_channels_validation_and_save_roundtrip`
- `test_gnews_url_and_items`
- `test_gnews_fetch_goes_through_safe_http_get`
- `test_search_sources_follow_channels_languages_and_switches`
- `test_hn_discourse_reddit_search_use_the_channel_terms`
- `test_search_results_are_saved_and_belong_to_the_channel`
- `test_home_shape_and_channel_assignment`
- `test_numbers_count_reads_and_top_channel`
- `test_home_and_channel_items_api`
- `test_channels_api_permissions`
- `test_prefs_defaults_validation_and_per_user_storage`
- `test_taste_strength_changes_ranking` — 強さ 0 は新しさだけ、強さ 1 は興味が効いて古くても好みの記事が上へ来る。
- `test_chat_toggle_and_half_life_reach_interests`
- `test_mute_terms_and_sources_hide_from_lists`
- `test_archive_keep_prunes_but_keeps_starred`
- `test_export_json_csv_and_formula_guard`
- `test_refresh_uses_channel_search_sources` — 取得の流れ: チャンネルの設定からも検索のソースを作って取り、記事は保存される。
- `test_forum_search_drops_posts_without_the_channel_terms` — 目的: フォーラムの検索で、見出しにも抜粋にもチャンネルの語が無い投稿はチャンネルに入れないこと（Google ニュースも今の語が無ければ入れない）。

### backend/tests/test_news_cpu_llm.py

目的: 目的: ニュースの下ごしらえを CPU と RAM だけの LLM（Gemma 4 E2B）で行う仕組みの試験。llama-server は全部ニセの実行ファイル・モックで、本物のモデル・GPU・外部通信は使わない。 根拠: 2026-11-24 利用者「CPU で動く Gemma とかで記事の翻訳できないか？画像生成と同じような管理で LLM を動かしたい。基本的に CPU と RAM で必ず動かすようにしたい」 「基本的に CPU で動かした Gemma の小を使う…それを既定に設定する。なお中と大は不要」。 守ること: - 起動の引数は --device none と -ngl 0、環境は GPU を見せない（HIP/ROCR/CUDA を空に）。ログに GPU を使った印が出たら失敗にする。 - 下ごしらえの回が終われば、正常・例外・時間切れのどれでも llama-server のプロセスが残らない。同時に 2 つは起動しない。 - CPU のときは 1 回 10 件・出力の上限 2500・system と日本語の見本つきの指示・出力の形を json_schema で強制する。 - 設定の既定は CPU の LLM。まとめ・一言要約には選べない。機能の状態（未導入 → 導入済み → 削除）。

- `test_command_and_env_hide_the_gpu` — 目的: 起動の引数に --device none と -ngl 0 があり、環境は GPU を見せない変数が空で、親の環境（秘密など）は引き継がれないこと。
- `test_gpu_marks_in_the_log_are_detected_but_cpu_lines_are_not` — 目的: 起動のログに GPU を使った印があれば検知し、「GPU が見つからない」「CPU のバッファ」は誤検知しないこと。
- `test_session_starts_with_cpu_only_args_and_stops_the_process` — 目的: 実際にプロセスを起動して、引数・環境が GPU を見せない形で、抜けたら（正常でも）プロセスが残らないこと。
- `test_session_stops_the_process_on_exception_and_on_timeout` — 目的: 本体の処理が例外で終わっても、起動が時間切れになっても、GPU の印が出ても、プロセスが残らないこと。
- `test_never_two_servers_at_once` — 目的: 下ごしらえが重なっても llama-server は同時に 2 つ起動しない（後の回は待つ）こと。
- `test_not_ready_gives_guidance_and_does_not_start` — 目的: モデルまたは llama.cpp が無いときは、起動せず機能と拡張への案内を返すこと（記事は済みにしない）。
- `test_cpu_prep_splits_into_10_and_sends_system_sample_and_schema` — 目的: 既定（CPU）では 1 回 10 件ずつ（23 件 → 10・10・3）、出力の上限 2500、system（英日翻訳者）と日本語の見本つきの指示、json_schema で形を強制し、
- `test_cpu_prep_reports_a_start_failure_without_marking_items` — 目的: llama-server を起動できなかった・未導入のときは、理由を返し、記事の試行回数を進めないこと。
- `test_settings_default_is_cpu_and_cpu_is_prep_only` — 目的: 下ごしらえの既定は CPU の Gemma。知らない cpu: は断り、まとめ・一言要約には CPU を選べず、今までのモデルは選べること。
- `test_feature_status_install_and_uninstall` — 目的: 機能「ニュースの下ごしらえ（CPU の LLM）」: 未導入 → ファイルがあれば導入済み（置いてあるものは導入済みとみなす）→ アンインストールでファイルだけ消える。

### backend/tests/test_news_hub_discovery.py

目的: 目的: ControlDeck の設定「ハブを探す」（app/news/hub_discovery.py）と、ハブの URL のポート補いの試験。 根拠: 2026-11-25 利用者「公開されている場合自動でアドレスを検知できないか？」（URL にポートを付けず「ハブにつながりません（ConnectError）」になった）・ QnapNewsHub の docs/DESIGN.md 3.5。守ること: - URL にポートが無ければ 8787 を補う（http・https とも）。明示があればそのまま。つながらないときの知らせに試したアドレスを出す。 - 発見の答えは、送り元の IP と同じホストの URL だけ採る（なりすまし・SSRF の誘導を防ぐ）。形（魔法の文字列・版・型・長さ・指紋の形・大きさ）が違う答えは捨てる。 - 発見は情報を受け取るだけ（つなぐ状態・トークンに触れない）。管理権限が要る。 - 本物のハブ（QnapNewsHub に discovery.py がある版の venv。無ければ skip）を一時に起こし、UDP で見つける往復を通す。外へは通信しない（127.0.0.x だけ）。

- `test_default_port_is_added_only_when_missing`
- `test_bad_urls_are_still_refused`
- `test_connect_error_message_names_the_address_tried`
- `test_parse_reply_accepts_matching_source_only`
- `test_parse_reply_drops_malformed`
- `test_parse_reply_rejects_bad_source`
- `test_discover_sends_one_query_and_collects_checked_replies`
- `test_discover_does_not_touch_link_state`
- `test_discover_needs_permission`
- `test_find_a_real_hub_over_udp` — 本物のハブ（発見の応答つきの版）を 127.0.0.2 で起こし、UDP の問いで見つけて、health が答えることまで確かめる。

### backend/tests/test_news_hub_link.py

目的: 目的: ControlDeck の「QNAP ニュースハブとつなぐ」（app/news/hub_link.py・hub_sync.py・hub_jobs.py と News 設定の hub 節）の試験。 **ハブの本物のコード（QnapNewsHub の uvicorn を一時の置き場で起こす）と往復**させ、つなぐ → 指紋 → 鍵を渡す → 差分 → 仕事を借りて返す → 画像を送る を通す。 ハブのリポジトリ（既定 /data1tb/ControlDeck/QnapNewsHub。環境変数 NEWSHUB_REPO で変える）の venv が無い環境では、往復の試験だけ skip する（それ以外はハブ無しで動く）。 外へは通信せず（127.0.0.1 だけ）、LLM と画像生成のモデルは模擬（ローカル LLM を呼ばない）。 根拠: 2026-11-25 利用者「ControlDeck に QNAP ニュースハブとつなぐ機能（v0.3 の ControlDeck 側）」（docs/PLAN-integrated-knowledge.md 1.5 章・案 C、QnapNewsHub の docs/DESIGN.md 3 章）。 守ること: - 封じ方（ECIES: X25519＋HKDF-SHA256＋AES-GCM・追加データに名前）がハブの link.py と同じで、**ハブが開けた鍵が送った鍵と一致する**（ハブの本物のコードで確かめる）。 - 指紋はハブの公開鍵から自分で計算し、ハブの画面の指紋と同じ。利用者が「同じ」と確かめるまで、同期・仕事・鍵の受け渡しをしない。 - トークン・秘密鍵・OpenRouter の鍵は、保存ファイル（暗号化）・画面の応答・ログに平文で出ない。 - 差分は冪等（二重に入れない）・cursor は 1 ページごとに保存・reset や範囲の変更で取り直す・delete と archive で消す（star は残す）。 - 出どころ: 既定は local（今の動きを変えない）・auto はハブが 2 回答えなければ local へ、答えたらハブへ・hub のあいだは自分で集めない。 - 借りた仕事は種類ごとに既存の処理で返し、画像は送ってから結果を返す。ハブが取り消されたら認証エラーを画面に出す。

- `test_defaults_keep_todays_behavior_and_settings_are_validated`
- `test_fingerprint_matches_hub_formula_and_url_is_checked`
- `test_effective_source_auto_goes_local_after_two_failures_and_back`
- `test_apply_changes_is_idempotent_merges_same_url_and_removes_except_starred`
- `test_sync_saves_cursor_per_page_resets_and_refetches_on_scope_change`
- `test_failed_job_page_does_not_move_cursor`
- `test_hub_image_is_checked_against_its_sha256_name`
- `test_hub_items_follow_feature_switches_and_manual_refresh`
- `test_hub_endpoints_need_settings_manage`
- `test_round_trip_with_real_hub_pair_secret_sync_jobs_image`
- `test_mismatched_fingerprint_discards_the_pairing`

### backend/tests/test_news_imagegen.py

目的: 目的: ニュースの「画像の無い記事に AI でイメージ画像を作る」（CPU の専用実行）の組み立て・上限・置き場・配る API・削除・未導入の振る舞いの試験。 子プロセス（モデル）は全部モック。外へは通信せず、GPU も使わない。 根拠: 2026-11-23 利用者「ニュースから画像が取れない場合は本文の意図を汲み取って、AI で生成画像をする…CPU と RAM の利用のみ…画像を生成したらすぐに アンロード…連続で生成…メディアフォージとかを使わずに、ニュースフィード独自の生成 AI を保有してほしい」。 精度の段（2026-11-23 利用者「このモデルを残しておいて、小中大みたいな感じで、選べるようにしてほしい、設定で」）: 設定の既定は小・未導入の段は何も作らない・ 段ごとに台本の引数（段・スレッド・seed=記事の id・時間の上限）と件数の上限（大は少なめ）が変わる・段ごとに導入・削除できる（小は消せない）。 守ること: - プロンプトは LLM を使わない規則（英語の見出しはそのまま・日本語は対応表か種類の決まり文句）で、文字・ロゴ・顔を避ける言い回しを含む。 - 設定が切・機能が未導入なら何もしない。件数・スレッド数の上限を子へ渡す。失敗した記事は再起動まで試さない。 - 画像は id（整数）だけでパスを組み、置き場の外・リンクは返さない。API はログインした人だけ、その人に見える記事だけ。 - 古い記事を消すと画像も消える。

- `test_prompt_english_title_kept_japanese_mapped_and_style_guards`
- `test_disabled_or_not_installed_does_nothing`
- `test_generates_with_limits_sets_url_and_remembers_failures`
- `test_worker_timeout_is_passed_and_child_killed`
- `test_image_path_only_inside_dir_and_no_symlink`
- `test_images_api_needs_login_and_visibility`
- `test_prune_deletes_generated_images`
- `test_settings_defaults_and_validation`
- `test_feature_is_python_lib_with_pinned_cpu_only_packages_and_models`
- `test_tier_normalize_and_models_ready_per_tier`
- `test_selected_tier_not_installed_skips_with_tier`
- `test_job_carries_tier_seed_and_per_tier_cap_and_timeout`
- `test_worker_args_per_tier` — 段ごとの台本の引数（子プロセスはモック）。
- `test_tier_specs_are_consistent`
- `test_lcm_schedule_and_guidance_embedding`
- `test_remove_models_by_tier_keeps_other_tiers`
- `test_feature_model_tier_install_and_remove`

### backend/tests/test_news_images_presets.py

目的: 目的: ニュースの「画像の取得」「自分のリポジトリの除外」「プリセット」「チャンネルの語を変えたときの振り分け」「ホームの表示数」の試験。外へ通信しない（HTTP は全部モック）。 根拠: 2026-11-23 利用者「本来ここには、ソースから取得した画像が表示されるべき…極力画像を撮ってほしい」「私が作っているコントロールデックとか私のリポジトリのデータは特に表示する必要はない」 「ニュースのソースの右側にプリセットボタン…無料のものを優先」「チャンネル02…以前設定していた AMD とか GPU とかの設定がそのまま残っている」「ホームに出す記事の数をどこかで設定できるように」。 守ること: og:image・twitter:image の取り出しと壊れた HTML・私的な IP・大きすぎる応答・取れなかった記事を再試行しない・自分のリポジトリは設定の移行（1 回）と一覧・チャンネルから除く・ プリセットの形（https・重複なし・分野・有料のサイトを入れない・設定の検査を通る）・語を変えたとき古い検索の結果が出ない・検索のやり直しの対象・home_items の範囲。

- `test_extract_image_prefers_og_then_twitter_and_resolves_relative`
- `test_extract_image_rejects_unsafe_and_survives_broken_html`
- `test_rss_media_enclosure_and_first_img_are_used`
- `test_fetch_page_image_reads_only_the_head_and_follows_redirects`
- `test_fetch_page_image_refuses_private_addresses` — 実物の guarded_client を使う（私的・loopback の IP は接続前に断る。外へは出ない）。
- `test_fill_only_new_items_without_image_and_never_retries`
- `test_fill_is_capped_per_run`
- `test_backfill_old_items_up_to_40_within_the_run_cap` — 目的: 取得のたびに、画像を試していない古い記事を新しい順に最大 40 件、新着と合わせて 1 回 60 件の中で試すこと。
- `test_site_logo_shared_by_two_articles_is_dropped_but_other_domains_kept` — 目的: 同じドメインの 2 件以上の別の記事に同じ画像 URL が付いていれば、その URL はロゴとして外す（既に付いた分も）。別のドメインの共有・1 件だけ・生成画像は残す。
- `test_google_news_items_resolve_to_original_url_for_the_image` — 目的: Google ニュースの記事は転送を解いた元の URL からページの画像を取り、記事の url は置き換えないこと。解けなければ画像は付けない（画像生成へ）。
- `test_upsert_reports_new_ids`
- `test_own_repo_not_in_defaults_and_removed_once_from_saved_config`
- `test_own_repo_items_hidden_from_list_home_channels_and_digest`
- `test_preset_list_shape`
- `test_presets_exclude_paid_sites_and_pass_source_validation`
- `test_presets_api`
- `test_old_search_results_do_not_show_after_terms_change`
- `test_changed_search_channels_detects_term_lang_and_new_channels`
- `test_refresh_with_channel_ids_fetches_only_those_searches`
- `test_home_items_pref_range`

### backend/tests/test_news_prep_gnews_rank.py

目的: 目的: ニュースの Google ニュースの転送の解き方（手順の単体。中身はモック）と、おすすめの順位（チャンネルに合う記事が上）と、画像生成が下ごしらえの描写を使うことの試験。外へ通信しない。 根拠: 2026-11-24 利用者「Google ニュースの記事はページの画像が 0 件」「ON AIR とホームの 1 位が Bored.com（HN のトップ）でチャンネルに合わない」「日本語の題の記事は決まり文句に落ち、3 件が同じ地球儀の絵になった」。

- `test_gnews_legacy_id_is_decoded_offline_and_new_style_is_not`
- `test_gnews_signature_body_and_response_parsing`
- `test_gnews_resolve_new_style_does_two_requests_and_gives_up_on_failure`
- `test_gnews_resolve_refuses_private_addresses_with_real_guard` — 実物の guarded_client を使う（Google の API が私的な IP を返しても、後段の取得で断る）。ここでは通信せず legacy の解きだけ確かめる。
- `test_channel_fit_beats_a_newer_unrelated_article_in_ranking_and_onair` — 目的: おすすめの点でチャンネルに合う記事を強く上げ、合わない記事（HN のトップなど）は下げる（消さない）。ON AIR は合う記事から選ぶ。新着の並びは変えない。
- `test_imagegen_prompt_uses_prep_description_over_fixed_phrase` — 目的: 下ごしらえの英語の描写があれば、決まり文句（地球儀）ではなくそれを絵のプロンプトにすること。
- `test_gnews_signature_at_the_end_of_a_large_page_is_found` — 目的: Google ニュースの中継のページが 512KB を超え、署名が末尾にあっても解けること。

### backend/tests/test_news_probe.py

目的: 目的: ニュースの「サイトを追加」（RSS の発見・記事の一覧のページの巡回）と「通信確認」の試験。外へ通信しない（HTTP は probe.http_fetch の差し替えで全部モック）。 根拠: 2026-11-24 利用者「任意のサイトのクロール機能を追加できるのか、サイト追加後に通信確認みたいなことがあるといい」 「日本の AI だったりとか政治の情報メディアのサイトからクローリング先を追加して欲しい」。以前から「有料なのは使わない（無料を優先）」。 守ること: RSS/Atom の判定・HTML からの RSS の発見（link rel と /feed など）・一覧のページから記事らしいリンクだけを拾う（外のサイト・案内・タグを除く・件数の上限）・ robots.txt を守る・頻度の上限（前回の結果を返す）・ロボット確認とログインを回避せず理由を言う・通信確認の結果の形（成功と各種の失敗）・設定の検査（pages）・プリセットの形。

- `test_feed_detection_and_non_feed`
- `test_list_links_picks_only_article_like_same_site_links`
- `test_list_links_limit_and_www_is_same_site`
- `test_discover_feed_url_as_is`
- `test_discover_finds_link_rel_and_common_places`
- `test_discover_falls_back_to_list_page_and_reports_robots`
- `test_discover_does_not_bypass_challenge_or_login`
- `test_fetch_follows_redirects_only_when_asked`
- `test_crawl_respects_robots_and_gap`
- `test_robots_missing_allows_but_server_error_denies`
- `test_check_success_shape_and_failures`
- `test_check_page_and_other_kinds`
- `test_pages_config_validation_and_build_sources`
- `test_new_presets_are_free_https_unique_and_grouped`
- `test_check_and_discover_api_for_admin`
- `test_list_links_cleans_headlines_ledge_style` — 目的: 一覧の巡回の見出しから、画像の説明・先頭の分類と日付を外し、同じ URL では見出しらしいものを選ぶ。

### backend/tests/test_news_rag_llm.py

目的: 目的: ニュースの RAG への蓄積・LLM の選択と外部送信の許可・更新のタイミング・日刊週刊のまとめの試験。外へは通信しない（LLM・埋め込み・ページ取得は全部モック）。 根拠: 2026-11-23 利用者「ニュースページ及びフィードの更新タイミング、その際に利用する LLM は個別に設定できること。このデータは基本公開情報だと 思うので OpenRouter の無料 LLM の利用を想定している。もちろんローカル LLM でも良い。検索した情報については、RAG に蓄積し、後で串刺し検索へ 利用したりできるようにして。ニュース用のデータベースは他と分けておいて。」 守ること: - 記事は知識基盤の分野 news（public）へ入り、横断検索（クラウド向けを含む）に出る。本文の取り込みは設定を入れたときだけ。 - クラウドのモデルを選んでいて外部への送信が許可されていなければ、ローカルへ黙って切り替えず、その回のまとめを作らない（理由を残す）。 - クラウドへ出来事・チャットの題・好みは送らない。LLM が落ちたら、印つきの見出しの一覧のまとめになる。 - まとめの時刻・曜日・取得しない時間帯・通知。

- `test_items_go_to_the_news_shard_as_public_and_show_in_cross_search`
- `test_same_url_is_updated_not_duplicated_and_unchanged_is_skipped`
- `test_events_are_never_put_in_rag`
- `test_fulltext_is_read_only_when_the_setting_is_on`
- `test_fulltext_stops_when_external_reading_is_blocked`
- `test_prune_keeps_rag_by_default_and_removes_it_when_set`
- `test_refresh_ingests_new_items_and_reports`
- `test_rag_failure_does_not_fail_the_fetch`
- `test_semantic_search_endpoint_marks_archived_items`
- `test_knowledge_find_scope_accepts_news`
- `test_news_is_a_cloud_caller_shown_in_the_egress_screen`
- `test_options_put_free_models_first_and_mark_paid`
- `test_cloud_model_without_permission_is_not_allowed_and_never_falls_back_to_local`
- `test_cloud_call_goes_through_the_gateway_as_the_news_caller`
- `test_llm_options_endpoint`
- `test_digest_with_llm_cites_only_real_items_and_excludes_events_and_old_items`
- `test_digest_cloud_without_permission_makes_nothing_and_does_not_use_local`
- `test_cloud_digest_sends_only_public_article_fields`
- `test_digest_falls_back_to_marked_headlines_when_llm_fails`
- `test_digest_notifies_when_made_and_regenerate_replaces`
- `test_digest_api_lists_and_returns_sources`
- `test_gist_is_off_by_default_and_adds_gist_and_topics_when_on`
- `test_gist_with_cloud_model_but_no_permission_does_nothing`
- `test_settings_defaults_and_validation`
- `test_daily_digest_is_due_after_the_time_only`
- `test_weekly_digest_follows_weekday_and_time`
- `test_quiet_hours_including_past_midnight`
- `test_run_due_makes_each_missing_digest_once`
- `test_news_loop_skips_fetch_in_quiet_hours`
- `test_semantic_search_reranks_and_merges_same_titles` — 目的: 意味で探すは、並べ替え（reranker）の順で返し、見出しが同じ記事（別の URL）は 1 件にまとめること。
- `test_focus_goes_into_the_prompt_and_prompt_asks_for_trend_tech_attention_and_three_lines` — 目的: 利用者が書いた重点の文がプロンプトに入り、話題ごとの傾向・主な技術・注目点と最後の 3 行を求めること。
- `test_weekly_prompt_uses_weekly_wording`
- `test_cloud_prompt_carries_focus_but_no_other_user_info` — 目的: 外部へ送る中身は、公開の記事の項目と重点の文だけ（出来事・利用者名・チャットの題は入らない）。
- `test_focus_matching_articles_are_ranked_first`
- `test_broken_answers_fall_back_to_headlines` — 目的: 存在しない [n] だらけ・見出し無し・引用無し・傾向無し・空の返事は、見出しの一覧（fallback）に落ちること。
- `test_focus_setting_is_validated_and_limited`
- `test_prep_batches_20_per_call_saves_and_presents` — 目的: 下ごしらえは 20 件で 1 回の呼び出し。日本語の概要・画像の描写・話題の語を保存し、一覧は訳を出して元の抜粋も返すこと。呼び出し回数を数えること。切のときは呼ばない。
- `test_prep_parse_rejects_broken_and_misnumbered_replies` — 目的: JSON が壊れていれば ValueError、番号が範囲外・整数でない項目は捨て、コードの囲み・配列の形も読み、英語だけの ja は使わないこと。
- `test_prep_resends_only_missing_once_then_leaves_to_next_fetch` — 目的: 欠けた・合わない記事だけを同じ回の中で 1 回だけ送り直し、それでもだめな記事は試行を +1 して次の取得へ回す（3 回で諦める）こと。
- `test_prep_respects_per_run_limit_and_unreadable_reply` — 目的: 1 回の取得の上限（既定 100 件 = 5 回）を超えて送らないこと。JSON が読めない返事は、記事を済みにしないこと。
- `test_prep_without_egress_permission_does_nothing`
- `test_prep_calls_shown_in_settings_and_day_rollover` — 目的: 設定の画面に「今日の下ごしらえの呼び出し n 回」を出す値があり、日付が変われば 0 に戻ること（無料のモデルの 1 日の上限 50 回に気づけるように）。
- `test_digest_rejects_english_and_retries_once` — 目的: まとめが日本語で書かれていなければ、1 回だけ理由を伝えて書き直させ、それでも英語なら見出しの一覧に落とすこと。
- `test_news_llm_retries_when_busy_and_not_when_over_budget` — 目的: 混み合い（429・temporarily rate-limited）で断られたら待ってやり直し、上限額や許可の失敗はやり直さないこと。
- `test_digest_shows_headlines_while_the_llm_is_working` — 目的: まとめを作っている間も、先に見出しの一覧（作成中の印つき）が見られ、LLM ができたら置き換わること。
- `test_prep_topics_are_not_split_into_letters` — 目的: LLM の返した話題の語が 1 文字ずつに分かれて保存されないこと。
- `test_split_topics_already_saved_are_repaired_on_open` — 目的: すでに崩れて保存された 1 文字の語を、DB を開くときに外す（何度流しても同じ）。根拠: 上と同じ不具合の残り。

### backend/tests/test_news_reader.py

目的: 目的: ニュースリーダー（app/news/reader.py）の試験。本文の取り出し・LLM・通信は全部モック。 根拠: 2026-11-24 利用者「各ニュースの項目を押すと…本文を取得して、英語だった場合は日本語に翻訳して、リーダーみたいな形で表示…冒頭にその全文の要約を書いて、 要約の下に元リンク先のアドレスを記載した上で、翻訳文をその下から書く」「LLM で変換が終わったものから順次変えていけば、見れない不具合は発生しない」。 守ること: 本文の段落の分け方・訳の途中の状態を API が返す・要約と訳の保存と再利用（2 回目は LLM を呼ばない）・取り出せないとき・ CPU の LLM を 2 つ起動しない（リーダーは順番を取り、下ごしらえは譲る）・Google ニュースは元の URL に解いてから取る。

- `test_split_blocks_kinds_and_title_dropped`
- `test_long_paragraph_is_split_and_window_limits`
- `test_detect_lang_and_parse_rules`
- `test_job_extracts_summarizes_translates_and_saves`
- `test_api_returns_midway_state_and_requires_visibility`
- `test_japanese_article_is_summarized_not_translated`
- `test_extraction_failure_keeps_excerpt_and_can_retry`
- `test_llm_stops_midway_and_resumes_from_there`
- `test_more_raises_limit_and_translates_rest`
- `test_google_news_resolves_before_fetching`
- `test_external_model_respects_egress_permission`
- `test_reader_takes_turn_before_prep_and_prep_yields` — リーダーが順番を待っている間は、下ごしらえが次のバッチの前に譲る（CPU の LLM を 2 つ起動しない）。
- `test_reader_failed_flag_only_for_failed_extraction` — 目的: 本文の取り出しに失敗した記事だけ、一覧・ホームの応答に reader_failed=true が付く（成功・未取得・要約だけ失敗は false）。

### backend/tests/test_notify_events.py

目的: 通知の入口（種類の有効・無効・お休みモード）と、各検出の所からの呼び出し。 目的: - 種類を切った人には送らない。既定（chat_done は無効）どおりに動く。 - お休みモード（既定 22:00〜07:00）の間は送らない。日またぎ・境界（22:00 は休み、07:00 は送る）・無効・開始=終了。 - 緊急の監視の警報（urgent）は、お休み中でも「緊急は送る」を有効にしたときだけ送る（既定は無効）。 - 設定の API は本人の分だけ読み書きできる（他人の行に触れない）。不正な種類・時刻は 422。 - 会話の回の終わり（コード開発・質問待ち・失敗・調査・長い会話）と、起動時の中断・待ちの通知が入口へ届く。 - 秘密の会話は、題も要約も載せない。要約にパス・鍵・URL を載せない。 - 実際の Web Push は外へ送らない（webpush.send_in_background / send_to を差し替える）。 根拠: 2026-11-23 利用者の依頼（お休みモード・通知の種類の拡充）。Push は Apple・Google の push service を通るため、秘密を載せない。

- `test_disabled_kind_is_not_sent_and_defaults_apply`
- `test_quiet_hours_cross_midnight_and_boundaries`
- `test_quiet_hours_custom_same_day_range_and_off`
- `test_urgent_alert_passes_quiet_hours_only_when_allowed`
- `test_cooldown_sends_same_tag_once`
- `test_settings_api_is_own_only_and_validates`
- `test_turn_end_calls_entry_with_kind_and_conversation_url`
- `test_plain_chat_is_silent_by_default_and_only_when_long`
- `test_canceled_turn_is_not_announced`
- `test_secret_conversation_and_summary_carry_no_content`
- `test_waiting_and_startup_interruption_notify`
- `test_other_detection_points_go_through_entry`

### backend/tests/test_offline_research.py

目的: 秘密の会話（一方通行）の外部への調査・外部の LLM を、通信の直前・送信の前で止める。 目的: 会話ごとの「この会話は秘密」（解除できない）が付いた会話の中で、外部への調査（Web 検索・ページ取得・学術/特許/市場/GitHub・ Deep Research）が **通信の前に** ExternalResearchBlocked で止まること、外部のモデルを選んでいてもローカルで答えること、 OpenCode へは流さないこと、途中で押すと動いている生成が止まること。秘密でない会話・会話の外（特許の公報の取得・Workflow）は止めないこと。 根拠: 利用者の決定（2026-11-21）「AI に利用を制限させるのではなく、根本的につながらないようにする」「途中から秘密の話をしたくなって 押すことはよくある。一度押した会話の文脈には秘密が入っているので、解除して外部へ調査に出られてはいけない」。 httpx・ネットワークバックエンドは記録用に差し替え、実際の外部へは通信しない。

- `test_scope_only_blocks_inside_and_resets`
- `test_egress_blocked_before_resolving_or_connecting`
- `test_node_web_search_blocked_before_any_http`
- `test_external_search_every_source_blocked_before_http`
- `test_external_search_outside_scope_still_works` — 特許の公報の取得など、会話の外は止めない。
- `test_github_inspection_blocked`
- `test_deep_research_uses_only_local_sources_in_secret_conversation`
- `test_deep_research_default_sources_in_normal_conversation`
- `test_confidential_is_one_way_audited_and_can_start_secret`
- `test_fork_of_secret_conversation_stays_secret`
- `test_search_endpoint_refuses_web_and_academic_for_secret_conversation`
- `test_local_target_replaces_cloud_with_local_default_or_refuses`
- `test_confidential_mark_only_when_request_supports_it`
- `test_chat_auto_mode_web_search_tool_only_in_normal_conversation`
- `test_chat_job_in_secret_conversation_runs_inside_scope` — 会話の実行の入口がスコープを立てる（中の外部への通信は通信の前に止まる）。抜けたら戻る。
- `test_chat_web_mode_becomes_plain_chat_in_secret_conversation` — 明示の「Web」「学術」でも、秘密の会話は検索をせず普通の会話にして、止めていることを示す。
- `test_send_in_secret_conversation_uses_local_model_and_refuses_opencode`
- `test_pressing_midway_cancels_running_generation`

### backend/tests/test_opencode_chat_server.py

目的: AI アシスタントの OpenCode（opencode serve の API）: 権限プラグイン・設定・1 回分の対話・許可の返答。

- `test_permission_plugin_auto_allows_safe_work_and_asks_for_danger`
- `test_permission_plugin_asks_for_every_command_outside_the_box` — 箱の外（host）では境界が無いので、Auto でもコマンドは確認（編集は自動のまま）。
- `test_permission_plugin_falls_back_to_ask_when_the_mode_file_is_unreadable`
- `test_chat_server_config_asks_before_the_deny_list_and_loads_the_plugin`
- `test_container_argv_passes_secrets_by_env_file_not_argv`
- `test_permission_mode_setting_is_validated`
- `test_run_turn_streams_text_tools_and_permissions`
- `test_run_turn_reports_failure_and_interrupts_when_cancelled`
- `test_mode_file_and_idle_reaping`
- `test_idle_reap_loop_stops_idle_servers_without_a_new_chat` — 最後に使ったサーバーも止める。前は次の会話を始めたとき（ensure）にだけ止めていたので、
- `test_code_mode_records_tools_and_permissions_and_answers_them`
- `test_permission_mode_api_saves_and_applies_to_running_servers`
- `test_run_turn_token_counts_follow_delta_text_length` — 目的: 進捗の gen_tokens・tok_per_sec が delta の「回数」ではなく文字数の見積もりに従うことを守る。
- `test_run_turn_corrects_token_total_with_step_usage` — 目的: step の完了イベントに使用量（tokens.output・reasoning）があるとき、見積もりの合計を実測で
- `test_run_turn_reports_context_usage_from_step_events` — 目的: OpenCode の会話でも進捗にコンテキストの使用量（prompt_tokens・context_max）が乗ることを守る。
- `test_run_turn_uses_the_context_max_the_caller_passes` — 目的: チェーンでモデルを切り替えた後、呼び出し元（chat_persist）が渡した窓の大きさが使われること。
- `test_run_turn_passes_child_permissions_and_survives_a_reconnect`
- `test_running_servers_are_not_reaped`
- `test_code_mode_rejects_a_second_message_while_working_and_never_stays_generating`
- `test_japanese_project_directory_is_sent_url_encoded` — 日本語のプロジェクト名でも、ヘッダーを送れる（ASCII だけ）。OpenCode は decodeURIComponent で戻す。
- `test_plan_agent_changes_nothing_even_in_auto_mode` — プラン（plan エージェント）では何も変えない。2026-11-18 利用者「プランの際はビルドモードではないことが明確に」。
- `test_run_turn_switches_to_the_plan_agent`
- `test_switch_agent_is_checked_by_reading_the_session_back` — v2 は知らない名前でも 204 を返す（実測）。読み直して切り替わっていなければ止める（レビュー指摘）。
- `test_run_turn_waits_for_a_background_subagent_and_keeps_the_follow_up` — 裏のレビュー: 親のターンが一度終わっても聞き続け、子が終わった後の親の続き（結果）を受け取る（2026-11-18 本番）。
- `test_the_follow_up_window_starts_when_the_parent_turn_ends` — 子が先に終わり親がその後も動いていても、親の終わりから続きを待つ（レビュー指摘）。
- `test_a_failed_parent_stops_background_children`
- `test_switching_agent_also_switches_the_session_model_to_the_role` — 役割ごとのモデル: プランへ切り替えたら計画のモデル、ビルドへ戻したら開発のモデルへ、セッションのモデルも切り替える。
- `test_child_activity_is_stamped_on_the_running_task_row_at_most_every_ten_seconds` — 目的: 裏の子エージェントが動いている印（最後に動いた時刻）を道具の行に載せ、送りすぎない。
- `test_a_subagent_row_gets_the_role_model_and_the_childs_last_activity` — 目的: task の道具の行に、係のモデル（設定の agents.<係>.model）と子の最後に動いた時刻が付く。

### backend/tests/test_opencode_chat_timeline.py

目的: AI アシスタントの OpenCode: 思考・道具・本文を届いた順に記録する／作業フォルダを会話で続ける／箱ごとの /tmp。 目的: 2026-11-18 利用者の依頼。 - 「思考が全部 1 つの窓に入っている。Bash などの操作の前後で分かれるはず」→ 届いた順の並び（timeline）を記録する （思考・本文は文字の位置、道具は ID）。画面はそれで思考を道具の前後に分けて出す - 「AI アシスタントを開き直すと作業フォルダの設定が外れる」→ 指定が空なら、その会話で最後に OpenCode が 動いたプロジェクトで続ける - 「proto1〜3 が Project Lab に無い」→ 箱の /tmp は全部の箱で共有されていた。プロジェクトごとに別の、 箱の外に残る場所へつなぐ

- `test_thinking_tools_and_text_are_recorded_in_arrival_order`
- `test_changing_the_project_starts_a_new_session_and_hands_over` — 2026-11-18 本番: mokei を選んでも conv のセッションのまま、箱の中の空の conv で作業していた。
- `test_the_chosen_mode_is_kept_on_the_sent_message` — 別の端末や開き直しでも、機能の固定を送った発言から戻せる。
- `test_changes_are_recorded_and_the_latest_turn_can_be_reverted` — 回答ごとの変更・差分・取り消し。取り消せるのは最新の回だけ。
- `test_a_queued_request_can_be_told_to_the_running_turn` — 送信待ちを作業中の OpenCode へ伝えると、回答の並びに残り、送信待ちから消える。
- `test_the_action_list_records_when_it_was_updated_and_the_request_asks_for_updates` — 本番: 91 回の操作の間 todowrite が 1 回だけで、1 番目が作業中のままに見えた。
- `test_timeline_offsets_use_the_browsers_character_positions` — 絵文字の前後で本文の位置がずれない（画面は UTF-16 で切り出す）。
- `test_an_empty_project_continues_in_the_conversations_last_project`
- `test_a_deleted_last_project_is_not_reopened`
- `test_each_project_box_gets_its_own_tmp_that_outlives_the_box`
- `test_other_projects_are_hidden_inside_a_project_box` — 箱の中で CodeDEV を空の場所で覆い、このプロジェクトだけをつなぐ。前に開いた別のプロジェクトの
- `test_deleting_a_project_removes_its_box_tmp`
- `test_selftest_checks_the_project_box_isolation` — 箱の /tmp が専用で、CodeDEV が覆われているかを自己診断で確かめる（同じことが起きたら気付けるように）。
- `test_plan_is_passed_to_opencode_and_recorded_on_the_answer` — プランで送ると、OpenCode に plan のエージェントで渡り、回答に印が残る（画面が「プラン」と出す）。
- `test_a_conversation_is_titled_when_it_starts_and_todos_keep_their_times`
- `test_chat_after_opencode_is_told_it_has_no_tools_and_tool_text_is_reported` — OpenCode の後のチャットには道具が無いと伝える。文字だけの呼び出しは自己診断で分かる（2026-11-18 本番）。
- `test_attached_images_are_given_to_opencode_when_its_model_reads_images` — AI アシスタントで添付した画像を、OpenCode の発言に data: の URI で付ける（OpenCode が画像を読めるモデルへ渡す）。
- `test_image_reading_is_judged_like_the_models_tab` — 画像を読めるかは Models の表示と同じ: llama.cpp は mmproj、Strata は vision が off でない、Radiance は常に、Maya は vision。
- `test_telling_is_refused_while_opencode_waits_for_an_answer_to_its_question` — 目的: 質問の答え待ちの回へ「途中で伝える」を送っても、止まったままにならない（利用者に理由を返す）。
- `test_the_chat_server_denies_the_question_tool` — 目的: AI アシスタントの OpenCode は question の道具を使えない（答える欄が無く、実行が止まるため。上の根拠）。

### backend/tests/test_opencode_dev_tools.py

目的: OpenCode の作業の道具（UI 検証 = Playwright MCP）と、作業の結果（control_deck.report）。 計画: docs/PLAN-opencode-dev-environment.md 第 1 段階。

- `test_mcp_entry_reads_the_package_bin`
- `test_mcp_entry_refuses_a_bin_outside_the_package`
- `test_browser_is_isolated_and_kept_off_controldeck_and_the_internet`
- `test_external_sites_only_when_allowed`
- `test_no_server_when_off_or_missing`
- `test_the_browser_tools_go_to_the_subagent_only` — 道具の説明だけで数万トークンになる。主の会話には載せない。
- `test_report_takes_its_screenshots_with_it`
- `test_reports_are_private_to_their_owner`
- `test_screenshot_names_cannot_escape`
- `test_report_tool_is_offered_and_saves`
- `test_research_stays_local_unless_allowed`
- `test_research_project_must_be_inside_codedev`
- `test_research_tool_runs_deep_research_and_keeps_a_report`
- `test_research_tool_can_be_turned_off`
- `test_context7_is_off_until_turned_on`
- `test_reviewer_cannot_edit_and_gets_ast_grep`
- `test_github_cli_only_when_allowed`
- `test_gh_token_and_destructive_commands_are_denied`
- `test_opencode_runs_in_a_project_box`
- `test_host_mode_and_missing_podman_fall_back`
- `test_isolation_setting_is_validated`
- `test_browser_has_video_caps_for_the_operation_test` — 操作テストの動画（browser_start_video）はこの版では --caps=devtools が要る。

### backend/tests/test_opencode_feature.py

目的: **未記入**

- `test_feature_default_is_disabled_and_external_uninstall_is_preserved`
- `test_managed_install_uses_private_prefix_and_does_not_enable`
- `test_update_installs_latest_and_reports_previous_version`
- `test_update_rejects_external_only_install`
- `test_update_job_endpoint_requires_managed_install`
- `test_disabled_feature_has_no_router_or_workflow_node`
- `test_opencode_provider_builds_array_argv_and_parses_json`
- `test_opencode_provider_stops_transient_unit_when_cancelled`
- `test_project_symlink_escape_is_rejected`
- `test_runtime_config_lets_opencode_send_images_and_roam_codedev` — VLM を載せていても宣言が無いと画像は送られない。CodeDEV は毎回聞かない。
- `test_addon_assets_are_readable_without_asking_every_time` — 生成物の置き場は開ける。閉じていると生成のたびに確認が入る。
- `test_runtime_config_declares_the_window_even_for_the_auto_model` — 窓を宣言しないと、先回りの自動圧縮が一度も動かない。
- `test_v2_installs_into_its_own_prefix`
- `test_opencode_v1_and_omo_are_gone` — v1（opencode-ai）と v1 専用の OMo は撤去した。v2 だけが残る。
- `test_v2_runtime_config_uses_v2_schema_and_xdg_config_home`
- `test_v2_run_argv_drops_dir_and_adds_standalone`
- `test_active_binary_refuses_when_v2_is_disabled`
- `test_v2_error_event_is_reported`
- `test_v2_caps_tool_output_against_the_window_and_formats_after_edits`
- `test_v2_tui_settings_survive_across_sessions` — keybind を変えても次の起動で消えない（config directory は毎回作り替えられる）。
- `test_strata_context_limits_reach_opencode`
- `test_v2_project_environment_overrides_stale_systemd_pwd`
- `test_opencode_compaction_follows_the_shared_setting` — 圧縮を切ると発火点を宣言しない（窓 - 返答）。auto も false になる。

### backend/tests/test_opencode_recovery.py

目的: Surviving agent observation and explicit, owner-scoped cancellation.

- `test_restart_survivor_is_observed_without_stop_or_db_rewrite`
- `test_missing_result_and_unknown_state_are_never_success`
- `test_unavailable_systemctl_is_unknown_without_stop`
- `test_only_eligible_saved_jobs_are_observed`
- `test_explicit_cancel_stops_only_owned_unit_and_records_audit`
- `test_unconfirmed_stop_is_not_recorded_as_canceled`
- `test_foreign_job_is_rejected_before_any_systemctl`
- `test_stream_observes_unit_exit_without_db_revision`
- `test_read_only_role_cannot_stop_survivor`
- `test_query_timeout_reaps_only_systemctl_client`

### backend/tests/test_opencode_roles.py

目的: OpenCode の「役割ごとのモデル」（plan / build / research / quick_check / final_check / evaluate）。 目的: 役割ごとに自由にモデルを選べ（全部同じでも全部違ってもよい）、全ての役割にそのモデルが明示されることを、 本番の session・開発の作業場の両方で守る。同じ段に別のローカルがあればお知らせだけ出す（断らない）。 根拠（2026-11-22 利用者、2 回目）: 「個別にモデルを設定できるようにして。全部同じも全部違うもできるように。変な制限は 設けなくて良い」。ゲートウェイは別々のローカルへの同時の要求を順番に載せ替え、生成中の要求は最後まで返す（Opus 実測）。 根拠: 2026-11-22 利用者「計画を実行するモデルと、調査、開発、検証、評価など。制約: 同時に 1 モデルしかできないので、 同時に実行する可能性のある作業は同じモデルにしたい」。ローカルは GPU で同時に 1 つしか動かせない。 最終検証が開発と同じモデルなら別の目で見られないので注意を出す。秘密（confidential）は全役割をローカルへ落とす。 旧 review_model は最終検証として読み、古い設定は動き続ける。

- `test_stage_defaults_follow_build_and_plan`
- `test_any_combination_is_allowed_and_only_noted`
- `test_external_models_mix_freely_and_stages_may_swap_locals`
- `test_final_check_same_as_build_warns_but_saves`
- `test_save_accepts_two_locals_in_a_stage`
- `test_save_allows_cloud_in_a_stage_and_swapping_between_stages`
- `test_review_model_migrates_to_final_check` — 旧設定（review_model だけ）は、そのまま最終検証として動く。roles を保存したら空にする。
- `test_session_config_puts_a_model_on_every_role_agent`
- `test_adversarial_reviewers_follow_final_check`
- `test_confidential_drops_every_role_to_local`
- `test_box_agents_get_models_from_roles_and_the_chain`
- `test_box_default_final_check_is_plan_then_the_reviewer_chain` — reviewer_model_for は final_check の既定に統合された: 計画が未設定なら、作業と別のモデル。
- `test_box_without_llm_cannot_choose_a_local_model`
- `test_box_keeps_two_locals_in_a_stage`
- `test_api_saves_any_combination_and_returns_the_notes`
- `test_host_never_defaults_a_role_to_an_external_model` — ホストの AI アシスタントでは、外部を許可していても、役割を明示しない限り外部へコードを送らない
- `test_box_lists_every_local_role_model` — 役割で選んだローカルのモデルが、箱の OpenCode のモデルの一覧に載ること。

### backend/tests/test_opencode_tui_resume.py

目的: OpenCode の TUI: 閉じても一覧に残り、続きから開ける。

- `test_tui_command_adds_resume_flags`
- `test_closed_session_stays_listed_and_can_be_forgotten` — OpenCode のルーターは機能を入れたときだけ載るので、手続きを直に呼ぶ。
- `test_resume_last_skips_assistant_sessions` — 「続きから開く」は AI アシスタントの会話を掴まない（同じ会話を 2 つのサーバーが書かない）。

### backend/tests/test_opencode_workflow.py

目的: OpenCode development-workflow extensions: Superpowers, Adversarial Review, Auto Advisor.

- `test_extensions_are_off_by_default`
- `test_develop_with_flash_next_and_review_with_maya`
- `test_turning_off_removes_plugins_and_advisor_file`
- `test_an_enabled_extension_without_its_plugin_is_left_out` — Enabled in OpenCode settings but not installed in オプション機能: skip it, do not fetch.
- `test_plugins_are_managed_options_requiring_opencode_v2`
- `test_workflow_validation`
- `test_gateway_switch_request_loads_the_named_model`
- `test_cloud_role_models_need_the_opencode_egress_switch`
- `test_cloud_models_serve_as_reviewer_advisor_and_developer`
- `test_revoking_egress_drops_cloud_role_models_from_new_sessions`
- `test_developer_agent_is_optional_and_can_use_a_local_model`
- `test_workflow_api_exposes_roles_and_cloud_choices`
- `test_auto_review_is_on_by_default_and_adds_the_review_procedure`
- `test_auto_review_can_be_turned_off`
- `test_review_model_applies_to_the_builtin_reviewer`
- `test_old_developer_and_advisor_models_move_to_roles_when_read` — 目的: 古い設定の developer_model・advisor_model が、読むときに roles.build・roles.advise へ移り、保存で空になる。根拠: 2026-11-22 利用者「他の設定と競合しないように…自動で適用」。モデルを選ぶ場所を役割の表の 1 か所にしたため。
- `test_developer_model_moves_to_build_only_when_build_is_empty` — 目的: developer_model は roles.build が空のときだけ build へ移り、選んである開発のモデルを上書きしない（古い API も同じ）。根拠: 2026-11-22 利用者「他の設定と競合しないように…自動で適用」。モデルを選ぶ場所を役割の表の 1 か所にしたため。
- `test_advise_role_is_the_auto_advisor_target_and_developer_follows_build` — 目的: 役割「相談」が Auto Advisor の相談先になり、開発担当の子エージェントが build のモデルで動く。根拠: 2026-11-22 利用者「他の設定と競合しないように…自動で適用」。モデルを選ぶ場所を役割の表の 1 か所にしたため。
- `test_old_advisor_model_api_value_sets_advise_and_unknown_model_is_refused` — 目的: 古い API の advisor_model を受けても壊れず advise へ移る。未登録のモデルは断る。根拠: 2026-11-22 利用者「他の設定と競合しないように…自動で適用」。モデルを選ぶ場所を役割の表の 1 か所にしたため。
- `test_confidential_scope_drops_advise_and_developer_to_local` — 目的: 秘密の会話では相談・開発担当の外部モデルがローカルへ自動で落ちる。根拠: 2026-11-22 利用者「他の設定と競合しないように…自動で適用」。モデルを選ぶ場所を役割の表の 1 か所にしたため。

### backend/tests/test_opencode_workspace_persist.py

目的: 作業場の会話の「保存と中断・再開」の chat 側（2026-11-22）。 目的: 作業場（開発用の箱）の OpenCode の回が終わるたび（成功・失敗・止めた、どれでも）自動保存が呼ばれること。 使用量の上限・残高切れ・レート制限で失敗したら、モデルの順番（chain）の次へセッションを切り替えて同じ依頼を 再送し、会話に 1 行「〜が使えなくなったので〜で続けます」が出ること。中断・止めた回を「続きから」で再開 できること（同じセッションへ・前回の理由と ToDo を本文へ渡す）。 根拠: 2026-11-22 利用者「作業場のコードはどういうタイミングで実際に保存されるのか。途中で停電や再起動した 場合はどうなるのか。中断して再開できるのか。他にも考えられる割り込みのシチュエーションを想定した上で、 問題がないように実装してほしい」。OpenCode・会話サーバー・autosave は偽物（記録だけ）。

- `test_workspace_turn_autosaves_on_success`
- `test_workspace_turn_autosaves_even_when_interrupted`
- `test_workspace_turn_autosaves_when_the_turn_crashes`
- `test_non_workspace_turns_do_not_autosave`
- `test_workspace_turn_reports_disk_shortage_in_the_reply`
- `test_rate_limit_failure_switches_to_the_next_model_and_resends`
- `test_model_switch_resumes_from_the_last_index_of_previous_turns` — 前の回で chain[2] まで進めていれば、この回で上限が起きたら chain[3] へ（前の分をやり直さない）。
- `test_rate_limit_exhausts_the_chain_and_keeps_the_failure`
- `test_other_failures_do_not_switch_models`
- `test_failed_switch_keeps_the_original_failure`
- `test_resume_sends_reason_and_todos_to_the_same_session`
- `test_resume_of_a_failed_turn_carries_the_error`
- `test_resume_refuses_when_there_is_no_unsettled_opencode_turn`
- `test_resume_picks_the_latest_unsettled_opencode_turn_without_an_id`
- `test_model_index_is_not_carried_into_a_new_session` — セッションを作り直した（前のが無い）ときのモデルは既定へ戻っている。前の回の chain の位置を引きずらない
- `test_resume_cannot_be_done_twice_for_the_same_turn`
- `test_resume_refuses_while_the_session_still_runs` — ControlDeck の再起動の後も OpenCode のセッションが箱の中で動き続けていれば、続きからは送らずに 409 で知らせる
- `test_turn_records_checkpoint_and_sends_restore_note_to_the_agent`
- `test_turn_records_checkpoint_when_the_agent_committed_by_itself`
- `test_turn_without_new_commit_has_no_checkpoint`

### backend/tests/test_parallel_slots.py

目的: ローカル LLM の並列のスロット数から調査の同時実行数を決める部分のテスト（LLM・GPU・通信はすべてモック）。 目的: ランタイムの種類・設定の上書き・読めないときの安全側の値を固定し、調査で LLM を呼ぶ処理がスロット由来の本数を超えず、LLM を使わないページ読み取りはスロットに縛られないことを守る。 根拠: 2026-11-23 利用者「並列数が 5 並列なら、1 並列でメインの会話を継続して、あとの 4 並列で調査の要約など。並列数は事前に設定した ものを読み取って。並列 1 の場合は従来通り」。2026-11-15 の実測: Radiance・llama.cpp は並列で伸び、Strata は伸びず、Maya-S は並列で起動しない。

- `test_helper_count_by_runtime`
- `test_override_is_capped_by_slots_and_zero_means_auto`
- `test_unreadable_config_is_one_for_local_and_default_for_external`
- `test_model_alias_is_used_when_port_is_not_a_model`
- `test_limit_setting_accepts_zero_to_eight`
- `test_page_reads_ignore_slots_and_llm_work_is_capped_by_slots`
- `test_reads_per_aspect_never_exceed_read_parallel`

### backend/tests/test_passkeys.py

目的: **未記入**

- `test_real_signature_login_single_use`
- `test_invalid_proof`
- `test_registration_password_uv_session_replay`
- `test_origin_csrf_limit_delete`
- `test_totp_enrollment_cannot_be_bypassed`
- `test_another_user_cannot_list_or_delete_key`

### backend/tests/test_patent_analytics.py

目的: 特許の分析（書誌の読み取り・集計・類似マップ・案件に近い公報）。 目的: - 公報の 1 ページ目（INID コード）から出願人・IPC・日付・請求項の数を読み、読めないものは空のまま（推測で埋めない）。 2 段組みで (71) の名前が別の列に回っても出願人を拾い、「…株式会社内」（発明者の住所）は出願人にしない。 - 集計は書誌の読めた公報だけを数え、読めた件数（coverage）と偏りの注意を返す。 - 類似マップは埋め込みのある公報だけを 2 次元に置く。書誌は手で直せる（source=manual）。 根拠: 2026-11-21 利用者「特許の傾向・書誌・請求項・類似特許などをグラフ化して表示する機能はどうなっているか」。 ResearchDataHub v0.3 の集計と PatentBuddy の 10 種のグラフの試作は本体に入っていなかった（計画 §10）。

- `test_front_page_fields_are_read_and_unknowns_stay_empty`
- `test_overview_counts_only_what_was_read_and_says_so`
- `test_similarity_map_places_documents_with_vectors`
- `test_api_analytics_manual_biblio_and_similar`

### backend/tests/test_patent_egress.py

目的: 外部の検索サービスへ送る前の自動の検査（手動の確認 approved_public の置き換え）。 目的: - 検索語・検索式が案件の未公開の文章（請求項・claims_draft・hearing）と **連続で 25 文字以上** 一致するときだけ止め（422・理由・ 重なった部分は 20 字まで・言い換えの案）、キーワード・短い句は止めないこと。境目（24 字は通る・25 字は止まる）。 - 入口を全部覆うこと: 取得の手動実行（案件が無ければ全案件と照らす）・ウォッチの保存と実行のたび（止めたら last_status に理由）。 - approved_public が無くても動くこと。監査には長さと理由だけを残し、検索語の本文を残さないこと。 根拠: 2026-11-22 利用者「ユーザー側に余計な操作はさせずに自動で適用」「特許の検索自体はインターネット」 「制限で検索が不十分になることは避けたい」。検索語は請求項から取る言葉なので、8 文字の重なりで止めると 「冷却ファンの回転数」のような普通のキーワードまで止まる。backend/app/patents/egress.py。

- `test_keywords_pass_and_the_boundary_is_25_chars`
- `test_blocked_query_gets_a_keyword_suggestion_that_passes`
- `test_hearing_and_claims_draft_are_checked_too`
- `test_no_case_checks_all_cases`
- `test_fetch_api_blocks_long_copy_audits_without_text_and_sends_keywords`
- `test_watch_save_and_every_run_are_checked`
- `test_watch_api_blocks_with_suggestion_but_can_still_be_stopped`

### backend/tests/test_patent_judge.py

目的: AI クレームチャートの判定（R3-3）。 目的: - 構成要件ごとの ○/△/×/? を、ローカル専用のモデルでだけ作ること（ローカルが無ければ 503 で止まり、記録も作らない）。 - ○/△ の引用が原文に無ければ「?」に落とすこと（モデルの作った引用を根拠に残さない）。OCR の改行・全角半角の揺れは許すこと。 - モデルが判定しなかった構成要件も「?」で必ず行に出すこと。 - 人が行の判定を直せ、直したら未確認へ戻ること。確認済み・却下の状態を持つこと。 - 請求項の版・文献の原文が変わったら stale を出すこと。案件を消したら判定も消えること。 - AI に渡せない文献（規格の利用条件など）は判定を始めないこと。 根拠: docs/PLAN-knowledge-platform.md §10 R3-3「引用が原文に無ければ ? に落とす」。PatentBuddy Draft #10。

- `test_fabricated_quotes_fall_to_question_mark_and_missing_elements_are_listed`
- `test_quote_check_tolerates_ocr_line_breaks_and_width`
- `test_unparsable_answer_fails_the_chart_without_inventing_rows`
- `test_human_review_and_stale_when_claims_or_document_change`
- `test_api_refuses_without_local_model_and_for_documents_ai_may_not_read`

### backend/tests/test_patent_survey.py

目的: 特許の調査（P1）の最初の部分: 調査テーマ → 検索語の案 → 外へ送る語の確認。 目的: - 案件と調査テーマ（空なら請求項 1）から、ローカル専用のモデルで検索語の案（構成要素ごとの ja/en・IPC・ 各取得先の検索式）を JSON で作り、judge と同じ専用 sqlite に保存すること。 - モデルの答えをそのまま信用しないこと: 読めない JSON・形違いは失敗として記録する。語は 1〜80 文字で 前後の空白を取り、重複を除き、1 構成要素につき各 20 個まで。IPC は ^[A-H]\d{2}[A-Z] ?\d{1,4}/\d{1,6}$ に 合うものだけ（合わないものは捨てて、捨てた数を記録）。 - 未公開の内容の検出: 語・検索式が案件の請求項・ヒアリングの記録の文章と 8 文字以上重なっていたら （一部分が写されている場合も）risk を付けること。risk は参考表示で、選択・送信は止めない（2026-11-22 に変更）。 止めるのは 25 文字以上の連続した写し（egress）だけ。確認（approved_public）は要らない。安全な語は最初から選ばれる。 - 権限の無い利用者は API を使えないこと。案件を消したら案も消えること（案件番号は使い回される）。 根拠: docs/PLAN-web-collector.md P1「テーマ/文言 → 検索語の案（ローカル LLM）→ 外へ送る語の確認」、 同 §6「外へ出す検索語・URL は、未公開の発明の内容を含まないことを利用者が確かめたものだけ」。 2026-11-21 の作業指示（trial/instruction.md）。手本は judge.py（start → Job でローカル LLM → 出力を検めて 保存 → 人の確認）と watch.py（approved_public の決まり）で、同じ観点の既存テストは docs/test-catalog.md で 確認した（test_patent_judge.py は判定の引用検査、test_patent_watch.py は取得語の確認。本件は案の作成と 語ごとの risk 検出で、重複しない新しい観点）。

- `test_valid_json_is_cleaned_saved_and_unpublished_terms_flagged`
- `test_unparsable_or_wrong_shape_answers_fail_the_survey`
- `test_theme_validation_and_claim1_fallback`
- `test_partially_copied_terms_are_also_flagged` — 語が請求項の一部を 8 文字以上写していても（全体としては含まれなくても）risk が付くこと。
- `test_approve_needs_no_flag_allows_risky_keywords_and_refuses_long_copies_and_unknown_terms`
- `test_survey_api_start_list_and_detail`
- `test_survey_api_approve_flow`
- `test_viewer_cannot_read_or_approve_surveys`
- `test_deleting_a_case_removes_surveys_and_reboot_interrupts_running`

### backend/tests/test_patent_watch.py

目的: 公開データの取得・特許ウォッチ・期限の通知（統合後の再設計 R3-2）。 目的: - 外へ送る検索語の検査は自動（2026-11-22 に手動の確認から置き換え。詳細は test_patent_egress.py）。確認（approved_public）が無くてもウォッチは動く。 - 取得は https の公開アドレスだけ。内部アドレス・リダイレクトを拒む（SSRF）。鍵は暗号化して保存し、応答に出さない。 - EPO OPS の書誌を統合ストアの書誌の形（meta["biblio"]）にそろえる。 - ウォッチは新しく入った文献だけをヒットにし、2 回目は同じ文献を出さない。ヒットから X/Y/A で先行文献に登録できる。 - 期限の通知はしきい値ごとに 1 回だけで、本文に案件名を書かない（通知は外部のサーバーを通る）。 根拠: ResearchDataHub v0.2 にあった機能（watch.py・notify.py・connectors.py）で、v0.102 の統合で抜けていた。 2026-11-21 利用者「各リポジトリの計画を ControlDeck 側に反映して進める」。docs/PLAN-knowledge-platform.md §10。

- `test_epo_records_use_the_shared_biblio_shape`
- `test_internal_addresses_plain_http_and_redirects_are_refused`
- `test_keys_are_encrypted_and_never_returned`
- `test_watch_runs_without_manual_approval_and_only_new_documents_become_hits`
- `test_deadline_notices_once_per_threshold_without_case_names`
- `test_api_accepts_queries_without_manual_approval`
- `test_deleting_a_case_removes_its_watches_hits_and_notices` — 案件の番号は使い回される。消した案件のウォッチ・候補・通知の記録が次の案件に出てはいけない（2026-11-21 E2E で発見）。
- `test_jpo_needs_registration_and_ten_digit_application_numbers` — 特許庁 API は利用登録の後に通知されるトークン取得の URL・ID・パスワードで使う。番号で引くだけ（言葉では探せない）。

### backend/tests/test_patents.py

目的: Patent 機能（特許調査・検討）と、PatentBuddy Add-on の撤去。 目的: - 案件・出来事・請求項の版・先行文献・クレームチャート・AI 支援の記録が API から使えること。 - 期限の目安と構成要件分解が ResearchDataHub から移植したとおりに動くこと。 - AI 支援はローカル専用のモデルでだけ動き、検証できなければ 503 で止まり、外部へ回さないこと（アイデアの流出防止）。 - AI の答えは「モデルの推論・未確認」として保存されること。 - PatentBuddy は機能追加のカタログに出ず、導入済みなら起動時に外れ、データ（feature-data）は残ること。 根拠: 2026-11-21 利用者「PatentBuddy のアドオンは不要。アドオンと機能追加画面の導入項目を一式削除し、 特許調査検討用ツールである Patent 機能をつけて」。docs/PLAN-knowledge-platform.md（K-PR3）。

- `test_deadlines_and_claim_elements_follow_japanese_practice`
- `test_case_lifecycle_over_api`
- `test_search_and_claim_chart_use_the_shared_knowledge`
- `test_assist_refuses_without_a_verified_local_model`
- `test_assist_runs_local_only_and_is_saved_as_unreviewed_inference`
- `test_patentbuddy_is_gone_from_the_catalog_and_retired_when_installed`
- `test_assists_cut_by_restart_are_marked_interrupted`

### backend/tests/test_paths.py

目的: **未記入**

- `test_inside_allowed`
- `test_traversal_rejected`
- `test_symlink_escape_rejected`
- `test_prefix_sibling_rejected`
- `test_secret_masking`

### backend/tests/test_pdf_import.py

目的: 添付 PDF: 文字の復元・原本とページ画像の保存・OpenCode のプロジェクトへ安全に置くこと。 根拠: 2026-11-18 本番「PDF を添付したが全く読まれていない」。文字が /uni30E6… のまま登録され、原本も無かった。

- `test_glyph_names_become_text`
- `test_same_pdf_is_kept_once_and_summarised_for_research`
- `test_pages_are_placed_in_the_project_once`
- `test_links_in_the_project_are_not_followed` — プロジェクトはモデルが書ける。リンクを置かれても箱の外へ書かない。

### backend/tests/test_playwright_browsers_feature.py

目的: 機能と拡張: Playwright のブラウザ（3 つの版・別々の置き場）と、OS のパッケージ（apt）の案内の項目。 目的: - E2E（frontend）・本体の Python・Playwright MCP が別々の置き場を使い、箱から見えること（同じ置き場だと、版の違う `playwright install` が互いのブラウザを消す。2026-11-23 に MCP で `install ffmpeg` したら E2E の chromium-1228 などが消えた）。 - 状態は置き場の中身と、その版が求める revision（browsers.json）の比較で決まり、足りないものが分かること。 - 導入は argv（shell なし）で、版ごとに別の PLAYWRIGHT_BROWSERS_PATH で走り、MCP が未導入なら飛ばすこと。実際には走らせない（run はモック）。 - apt のパッケージは sudo が要るのでボタンでは入れず、足りないものだけのコマンドをコピーさせること。エラーの文が項目を案内すること。 根拠: 2026-11-23 利用者「設定の機能の追加のところに導入ボタンを」「追加で入れないといけないものについては機能と拡張に導入ボタンを付けること」。

- `test_three_versions_use_separate_stores`
- `test_sandbox_sees_e2e_and_mcp_stores_read_only`
- `test_mcp_environment_points_to_its_own_store`
- `test_status_lists_what_is_missing_then_complete`
- `test_old_revision_counts_as_missing_after_version_bump`
- `test_install_runs_each_version_into_its_own_store_without_shell`
- `test_install_skips_mcp_when_not_installed_and_complete_parts`
- `test_install_failure_is_reported_in_japanese`
- `test_registry_feature_never_auto_updates_and_installs_through_module`
- `test_system_packages_status_and_copyable_command`
- `test_system_packages_are_never_installed_by_a_button`
- `test_errors_point_to_the_feature_item`
- `test_maya_tokenizers_guidance_and_ensure`

### backend/tests/test_plugin_sdk.py

目的: **未記入**

- `test_manifest_rejects_unknown_fields_permissions_and_unsafe_urls`
- `test_registry_install_enable_and_uninstall_are_confined`
- `test_registry_ignores_symlinked_plugin_directory`
- `test_registry_rejects_symlink_to_another_managed_directory`
- `test_manifest_file_requires_safe_regular_user_owned_file`
- `test_plugin_api_requires_admin_and_audits`
- `test_plugin_api_rejects_csrf`

### backend/tests/test_power_schedule.py

目的: **未記入**

- `test_durable_power_timer_units_do_not_run_missed_action`
- `test_power_schedule_api_uses_persistent_backend`
- `test_platform_reload_is_scheduled_as_user_systemd_unit`
- `test_power_worker_records_failure`
- `test_immediate_power_uses_fixed_force_argv`
- `test_power_api_without_body_remains_graceful`
- `test_power_safety_returns_counts_without_session_details`
- `test_power_totp_reauthentication_blocks_invalid_code`

### backend/tests/test_project_lab.py

目的: **未記入**

- `test_manifest_rejects_shell_string_secret_literal_and_escape`
- `test_project_discovery_manifest_artifacts_and_containment`
- `test_project_lab_api_is_read_only_authenticated_and_safe`
- `test_external_preview_setting_applies_without_query` — 設定で常時許可にすると、query指定なし（サブページ遷移含む）でも外部読み込みを通す。
- `test_split_app_subresources_are_served_with_correct_types` — HTML・JS・CSS・アセットに分割されたアプリを、相対パスのまま配信できる。
- `test_project_lab_permission_is_available_to_operator_only`
- `test_project_run_uses_systemd_argv_tracks_artifacts_and_redacts_logs`
- `test_file_run_executes_single_source_without_manifest` — manifestを持たないprojectでも、成果物のPython fileを1本だけ隔離実行できる。
- `test_project_run_rejects_secrets_and_non_sdk`
- `test_web_run_allocates_localhost_port_and_substitutes_argv`
- `test_web_preview_requires_unit_owned_listen_port`
- `test_project_web_proxy_strips_control_deck_credentials`
- `test_project_lab_api_enforces_operator_and_viewer_permissions`
- `test_preview_token_serves_relative_assets_without_cookies` — sandboxの不透明originから読めるよう、token付きURLはcookie無しで配信する。
- `test_preview_token_is_scoped_and_rejects_tampering` — tokenはプロジェクト単位。改竄や別プロジェクトへの流用はできない。
- `test_a_preview_can_read_its_own_assets` — 生成されたものが、自分で作った画像や音声を読めること。
- `test_the_authenticated_artifact_route_stays_closed` — token を介さない経路は緩めない。緩める理由が preview にしか無い。
- `test_an_unchanged_preview_asset_is_not_sent_again` — no-cache は「毎回問い合わせる」であって「毎回受け取り直す」ではない。
- `test_project_delete_removes_the_folder_and_stays_inside_codedev` — 設定メニューからの削除。CodeDEV 配下だけを消し、外の実体には触れない。
- `test_project_delete_refuses_while_running_or_published` — 走っているもの・公開しているものは消さない。先に止める／取り下げてもらう。
- `test_project_delete_permission_is_administrator_only`

### backend/tests/test_project_lab_export.py

目的: プロジェクトの持ち出しで秘密情報が混ざらないことを守る。 ここは画面で1つずつ開く経路と違い、ソース一式が対象になる。allowlistが 効かない代わりに「危ないものを落とす」で選ぶので、落とし漏れは即そのまま 手元のZIPへ入る。落ちること自体をテストで固定する。

- `test_plan_keeps_source_and_drops_secrets`
- `test_plan_reports_why_each_file_was_dropped`
- `test_symlink_is_not_followed` — project の外を指す symlink を辿ると、その先ごと持ち出してしまう。
- `test_archive_has_single_root_and_records_exclusions`
- `test_export_plan_endpoint_lists_what_will_be_dropped`
- `test_archive_endpoint_returns_a_zip_without_secrets`

### backend/tests/test_project_lab_live_config.py

目的: Project Lab の見張りモードの設定（manifest の `watch`）と、実行のメモリの上限（`run_memory_max_mb`）。 目的: - `watch` の paths・ignore・build は command・artifacts と同じ検証（相対パスだけ、`..` 禁止、shell の -c 禁止）。 mode と debounce_ms の範囲。mode の省略値（web は restart、ほかは rerun）。 - watch があって mode が native 以外なら、systemd の unit は command の代わりに見張り役を起こす （spec は 0600・プロジェクトの外）。native は今までどおり command を直接起こす。 - 設定 `run_memory_max_mb`（既定 2048、512〜16384）が unit の MemoryMax に入る。 根拠: 2026-11-21 の設計。C++ のビルドは 2G では足りない。見張り役の指示には鍵付きの {base} が入るので置き場に気をつける。

- `test_watch_accepts_a_full_declaration_and_fills_defaults`
- `test_watch_rejects_unsafe_or_out_of_range_values`
- `test_watch_is_only_for_runnable_profiles`
- `test_run_memory_setting_range_default_and_persistence`
- `test_memory_setting_api_validates_and_applies`
- `test_watch_run_starts_the_supervisor_with_a_private_spec`
- `test_native_watch_and_no_watch_start_the_command_directly`
- `test_memory_max_follows_the_setting`
- `test_start_run_resolves_the_build_executable_and_skips_native` — manifest から起こすとき、build の実行ファイルも許可 SDK だけ。native は見張り役を起こさない。

### backend/tests/test_project_lab_live_preview.py

目的: 見張りモードの画面側: live.js の差し込み（鍵付き入口の HTML だけ）と、WebSocket `__cd/live` の受け口。 目的: - 鍵付き入口の応答が text/html のときだけ、`</head>`（無ければ `</body>`、無ければ末尾）の前へ script を 1 本入れる。 接頭辞が正しい・content-length が合う・圧縮された HTML は圧縮なし（identity）で頼み直す・8 MiB 超と HTML 以外は触らない。`__cd/live.js` は上流へ送らず ControlDeck が答える。 - `__cd/live` は run の鍵で認証する。見張り役の状態を `status` で、restart の再起動を `reload` で、AI の知らせ （notes ファイル）を `note` で画面へ送る。native・見張り無しなら status を送らない。 - 画面から来た記録は `.controldeck/feedback/<run_id>.jsonl` へ追記する。1 件 16KB・1 秒 50 件・1 run 20MB まで。 根拠: 2026-11-21 の設計。記録の入口は誰でも（鍵があれば）叩けるので、ディスクと CPU を食い尽くされない上限が要る。 Vite の HMR など、アプリ自身の WebSocket・script を壊さないため、HTML 以外は今のまま流す。

- `test_html_gets_exactly_one_script_before_head_close`
- `test_script_goes_before_body_close_or_at_the_end`
- `test_non_html_and_oversized_html_pass_through_untouched`
- `test_html_just_under_the_limit_is_still_injected`
- `test_8mib_is_the_real_limit`
- `test_compressed_html_is_requested_again_without_compression`
- `test_live_js_is_answered_by_control_deck_even_while_upstream_is_down`
- `test_live_websocket_rejects_a_wrong_key_and_a_finished_run`
- `test_records_are_appended_with_time_and_source`
- `test_a_record_over_16kb_is_dropped_and_invalid_json_is_refused`
- `test_records_are_limited_to_50_per_second`
- `test_a_run_stops_accepting_records_at_the_total_limit`
- `test_status_reload_and_note_reach_the_page_in_restart_mode`
- `test_rerun_mode_sends_status_but_never_reload`
- `test_native_or_unwatched_runs_get_no_status`
- `test_old_notes_are_not_replayed_on_connect`

### backend/tests/test_project_lab_live_supervisor.py

目的: Project Lab の見張り役 `app.project_lab.live`（見張りモードの本体）。 目的: - 保存のたびに build → command を回す。まとめて 1 回（debounce）、build が失敗したら command を起こさず failed、rerun は最後まで流して exited と exit_code、restart は前の子を止めてから起こす。 - `.controldeck/` などの変更では回らない（状態ファイル自身の書き込みで回り続けない）。 - glob の当たり方（paths・ignore、`/` を含まない pattern はどの深さにも当たる）。 根拠: 2026-11-21 の設計（Vite 以外の Web・Python・C++ でも、保存したら結果をすぐ確かめる）。 状態ファイルへの書き込みを見張ると無限に回るので、常に見張らない場所を決めている。 実際に watchfiles と子プロセスを使う（本物の systemd・podman は使わない）。子は偽の python スクリプト。

- `test_rerun_runs_to_the_end_and_reports_exit_code`
- `test_a_burst_of_saves_runs_build_and_command_once`
- `test_a_failed_build_never_starts_the_command`
- `test_restart_stops_the_previous_child_before_starting_again`
- `test_changes_under_controldeck_and_build_dirs_do_not_loop`
- `test_ignore_patterns_keep_listed_files_from_triggering`
- `test_glob_matching_rules`

### backend/tests/test_project_lab_publish.py

目的: 静的サイト公開で、出してはいけない物が出ないことを守る。 公開は取り消しても索引や cache に残る。ダウンロードなら手元で気づけば済むが、 ここは気づいた時点で手遅れになる。選別が export と同じ規則を使い続けること、 index.html の無い場所を公開しないことをテストで固定する。

- `test_detect_directory_prefers_the_built_site`
- `test_detect_directory_falls_back_to_project_root`
- `test_resolve_target_refuses_to_leave_the_project`
- `test_plan_uses_the_same_exclusions_as_download` — 公開経路にだけ緩い判定を置くと、緩い方から漏れる。
- `test_publish_refuses_without_an_index`
- `test_publish_refuses_when_gh_is_not_logged_in`
- `test_publish_rejects_an_unknown_visibility`
- `test_publish_pushes_force_and_never_puts_a_token_in_the_remote`
- `test_publish_stages_only_selected_files_and_adds_nojekyll` — staging の中身を、git が走る直前に覗いて確かめる。
- `test_unpublish_disables_pages_and_removes_the_branch` — 公開を取り下げると URL が 404 になり、中身も残らないこと。
- `test_unpublish_tolerates_an_already_disabled_site` — 既に止まっているものを止め直しても失敗させない（404 は想定内）。
- `test_unpublish_refuses_when_nothing_is_published`
- `test_unpublish_still_works_when_pages_cannot_be_deactivated` — public repository は API から Pages を止められない。
- `test_unpublish_empties_the_branch_when_it_cannot_be_deleted` — 公開用 branch が既定ブランチだと消せない。
- `test_republish_reuses_where_and_how_far_it_was_published`
- `test_republish_refuses_when_nothing_is_published`
- `test_republish_refuses_an_unreadable_record` — 記録が壊れていたら黙って既定へ倒さない。public で出す事故を作らない。

### backend/tests/test_project_view_websocket.py

目的: Project Lab の preview の鍵の入口と、WebSocket（HMR）の中継。 目的: - preview は sandbox の iframe（生まれが null）で開くので Cookie を送れない。URL の run ごとの鍵で通し、 鍵が違う・終わった run は通さない。ControlDeck の Cookie・Origin は上流へ渡さない。 - WebSocket は上流と双方向に中継し、サブプロトコル（Vite の vite-hmr）・文字・binary・close code を通す。 - command に {base} を渡した run は、接頭辞を付けたまま上流へ送る（Vite の --base で HMR が同じ接頭辞へ来る）。 根拠: 2026-11-21 実測。前の preview では、sandbox の中の Vite は module script が CORS で 1 本も読めず、 HMR はサイトの根元 ws://host/ へ繋ぎに来て 403 だった。

- `test_websocket_relays_messages_subprotocol_and_close`
- `test_websocket_rejects_wrong_token_and_finished_run`
- `test_websocket_keeps_prefix_when_command_has_base`
- `test_token_http_route_needs_no_cookie_and_allows_null_origin`
- `test_launch_fills_base_but_does_not_record_the_token` — {base} は run の番号が決まってから埋める。記録（command_json）には鍵を残さない。

### backend/tests/test_radiance_runtime.py

目的: Radiance runtime: registration, launch command, lifecycle and routing.

- `test_registration_validates_and_lists`
- `test_lifecycle_flags_change_while_running_but_launch_settings_do_not`
- `test_launch_args_are_fixed_and_loopback_only`
- `test_starting_radiance_frees_the_other_large_models`
- `test_gateway_auto_uses_radiance_when_selected`
- `test_requests_to_a_radiance_port_use_the_radiance_provider`
- `test_runtime_policy_and_feature_know_radiance`
- `test_thinking_defaults_reach_the_server`
- `test_starting_strata_frees_a_resident_llama_chat_model` — Strata does not fit beside Qwen3.8-27B on the R9700: its start must free it too.
- `test_long_context_uses_yarn_and_memory_options` — Past the training context YaRN is set; i8 / memory saver / RAM offload reach the file.

### backend/tests/test_rag_db_nodes.py

目的: **未記入**

- `test_chunkers`
- `test_rag_build_and_query` — 埋め込みをモックして build → query のコサイン検索を確認する。
- `test_rag_invalid_collection` — コレクション名でパスを抜けられないこと（保存先・グラフのファイル名に使うため）。
- `test_db_query_sqlite`
- `test_db_query_rejects_non_dml`
- `test_db_query_sqlite_path_must_be_in_root`
- `test_workflow_api_accepts_rag_db_nodes`
- `test_chat_rerank_gives_up_after_its_budget_and_keeps_search_order` — 会話の回答前の再ランクは上限（3 秒）で打ち切り、検索の順のまま返す。打ち切りは自己診断に出る。
- `test_small_conversation_materials_are_given_whole_without_search` — 会話の資料が小さければ探さずに全部、文書の順で渡す（並べ替えも埋め込みも使わない）。保存時に入っていた
- `test_cpu_reranker_is_preferred_and_its_scores_do_not_gate` — CPU の reranker が導入済みなら GPU の reranker より優先し、上位の件数で選ぶ（点数の門で切らない）。
- `test_cpu_reranker_failure_keeps_search_order` — CPU の reranker が失敗しても会話を止めず、検索の順で返す。失敗は自己診断に出る。
- `test_rerank_query_carries_the_previous_topic` — 会話の資料の並べ替えには、直前の発言（後ろ 300 字）を質問の前に添える。最初の発言はそのまま。
- `test_cpu_rerankers_are_installed_and_chosen_in_models` — Models の「Embed / Reranker」で CPU の reranker（速い xsmall・高精度 ruri）を導入・選択する。
- `test_large_conversation_materials_are_searched_only_when_the_message_points_at_them` — 大きい会話の資料は、発言が資料・前に調べたもの・詳しい内容を指すときだけ探す。挨拶・お礼・雑談では探さない。
- `test_rerank_follows_the_selection` — 選んだものを並べ替えに使う: none は並べ替えず検索の順、gpu は CPU が入っていても GPU（llama.cpp）を使う。
- `test_gpu_ruri_is_preferred_and_falls_back_to_cpu` — GPU の ruri（fp16、MIGraphX）が入っていれば、自動では会話にも Knowledge にも使う（精度も速さも最良）。
- `test_install_endpoint_actually_runs_the_job` — 導入の API はジョブを実際に走らせる。根拠: 2026-11-20 本番 v0.87。同期関数で受けていたためイベントループが無く 500
- `test_gpu_preset_uninstall_removes_registration_and_file` — GPU の埋め込み・reranker（llama.cpp のプリセット）のアンインストール: 登録を消し、GGUF も消す。
- `test_gpu_helpers_are_released_before_an_llm_loads` — LLM を GPU に載せる前に、GPU の補助（GPU の ruri・GPU に載った埋め込み / reranker）を止める。CPU だけのものは止めない。
- `test_gpu_reranker_starts_before_embedding_and_checks_vram` — 資料の検索では GPU の ruri を埋め込みより先に起動する（ruri は全部を GPU に載せないと動かないが、bge-m3 は空きに
- `test_cpu_llama_helpers_never_touch_the_gpu` — n_gpu_layers 0（CPU）の llama.cpp には --device none を付ける。付けないと大きな計算を自動で GPU へ回す
- `test_embedding_start_brings_up_the_gpu_reranker_first` — GPU の埋め込みを載せる前に、GPU の reranker（ruri）を先に載せる。LLM を載せる前は両方を止める。

### backend/tests/test_ratelimit.py

目的: **未記入**

- `test_failed_attempt_limiter_bounds_key_count`

### backend/tests/test_release_bundle_feature.py

目的: **未記入**

- `test_release_bundle_installs_side_by_side_and_switches_atomically`
- `test_failed_new_health_rolls_back_current_and_addon`
- `test_unsafe_archive_never_changes_current`
- `test_same_version_reinstall_repairs_service_and_addon`
- `test_provision_runs_before_smoke_with_the_managed_environment`
- `test_lifecycle_environment_does_not_inherit_host_secrets`
- `test_lifecycle_errors_are_normalized`
- `test_failed_provision_does_not_change_current`
- `test_release_bundle_status_uses_live_service_health`
- `test_release_bundle_enable_disable_controls_addon_registry`
- `test_catalog_url_rejects_untrusted_host`
- `test_feature_job_api_rejects_caller_supplied_source`
- `test_a_release_signed_by_the_publisher_needs_no_catalog_edit`
- `test_a_tampered_manifest_is_refused`
- `test_a_signature_from_another_key_is_refused`
- `test_a_correctly_signed_release_cannot_be_passed_off_as_another_version` — 署名だけでは足りない。digest 単体に署名すると、正しく署名された古い版を
- `test_a_signed_release_for_another_feature_is_refused`
- `test_an_entry_with_a_publisher_key_requires_the_release_to_be_signed`
- `test_sonicforge_catalog_uses_the_generic_signed_release_contract`

### backend/tests/test_release_bundle_publisher_hardening.py

目的: **未記入**

- `test_signed_manifest_binds_exact_identity_digest_and_size`
- `test_signed_manifest_rejects_identity_mismatch`
- `test_signed_manifest_rejects_extra_fields_even_when_signature_is_valid`
- `test_signed_manifest_requires_mediaforge_canonical_json`
- `test_signed_manifest_rejects_wrong_publisher_key`
- `test_signed_manifest_rejects_malformed_signature`
- `test_downloaded_artifact_checks_signed_size_before_digest`
- `test_downloaded_artifact_rejects_tamper`
- `test_signed_metadata_is_authenticated_before_large_download`
- `test_downgrade_uses_ordered_versions`
- `test_prerelease_cannot_replace_stable_release`
- `test_unorderable_changed_version_fails_closed`

### backend/tests/test_remote_desktop.py

目的: **未記入**

- `test_encode_instruction`
- `test_instruction_parser_partial_and_multiple`
- `test_parser_multiple_in_one_feed`
- `test_build_guacd_params`
- `test_password_is_encrypted`
- `test_handshake_with_mock_guacd` — ローカルにモック guacd を立て、select→args→connect の流れを検証する。
- `test_connection_crud_api`
- `test_endpoint_available_reports_open_and_closed_tcp_ports`
- `test_remote_requires_permission`

### backend/tests/test_remote_desktop_toggle.py

目的: リモートデスクトップの有効・無効の切り替え（設定、ControlDeck 側の強制、特権補助の enable/disable）を守る。 目的: - 設定 remote_desktop.enabled は settings.manage だけが変えられ、監査に残り、既定は有効（今までどおり）。 - オフのとき、ControlDeck のリモートの API と WebSocket は断られ、/api/v1/meta の enabled_features から "remote-desktop" が消える（ナビ・ホームのアイコン・/remote の画面はこれで出し分ける）。オンで戻る。 - オフ/オンで xrdp 本体と sesman に stop+disable / enable+start を補助経由で送る。補助や一覧が古いときは 設定は保存し、「補助の更新が要ります」を結果に返す（失敗を隠さない）。 - 補助（helper）の system-service が enable/disable を受け付け、一覧に無い id・不正な action を断る。 subprocess はモックで、本物の xrdp は止めない。 根拠: 利用者の依頼（2026-11-21）「リモートデスクトップは一旦停止する。設定に有効・無効のボタンを。オフになったら 通信・サーバーの起動・ホームのアイコンも消す」。特権の操作に関わる（L2）ので、強制の場所ごとに固定する。

- `test_default_is_enabled_and_meta_lists_it` — 既定は有効。meta の enabled_features に remote-desktop が出る。
- `test_disable_stops_server_blocks_api_and_hides_feature_then_enable_restores` — オフで xrdp を stop+disable、リモートの API・WebSocket は 403、meta から消える。オンで戻る。
- `test_old_helper_or_catalog_saves_setting_and_reports_update_needed` — 補助・一覧が古いときは設定を保存し「補助の更新が要ります」を返す。本物の失敗は隠さない。
- `test_permission_and_audit` — 不正な入力の拒否、監査（enable/disable）、未認証の拒否。
- `test_builtin_catalog_defines_both_xrdp_units` — `./deck.sh service` が書く一覧に xrdp と sesman が入る。
- `test_helper_accepts_enable_disable_and_rejects_unknown` — 補助が enable/disable を受け付け、一覧に無い id・不正な action を断る（subprocess はモック）。
- `test_isolated_instance_never_touches_the_machine_service` — 検証用インスタンスは機械の xrdp を操作しない。

### backend/tests/test_research_forums.py

目的: 調査の道具の新しい入口（Reddit の OAuth・Discourse 系のフォーラム・Stack Exchange）と、設定 → API キーの「調査」群のテスト （HTTP はすべてモック。外へは通信しない）。 目的と根拠: 2026-11-23 利用者「Reddit の API を登録するところを作って、入っていたらそれを使って検索できるように。XDA や他の技術的に有望な フォーラムも検索したい」。2026-11-22 の実測で Reddit の未認証の JSON・RSS は拒否された。守ること: キーがあれば OAuth を先に使いトークンを 使い回す・キーが無い／失敗なら今の経路へ・403 は「承認が要る可能性」と平易に言う・キーの値を応答・例外・ログ・監査に出さない・ ドメインは形だけ許す（localhost・IP は断る）・秘密の会話は外へ出ない・Stack Exchange は site 名に直してキーを付ける。

- `test_reddit_oauth_token_is_cached_and_search_uses_the_api`
- `test_reddit_thread_uses_oauth_and_without_keys_the_old_path_is_used`
- `test_reddit_oauth_failure_falls_back_and_403_is_explained`
- `test_keys_never_appear_in_errors_or_logs`
- `test_discourse_search_and_read`
- `test_user_added_forum_is_searched_and_bad_domains_are_refused`
- `test_stackexchange_site_names_key_and_read`
- `test_xda_stays_on_searxng_and_confidential_conversations_do_not_connect`
- `test_settings_group_save_test_and_delete`

### backend/tests/test_research_tools.py

目的: サイト指定の調査・依頼の理解・時の解決・本文での日付の確認のテスト（HTTP・LLM・SearXNG はすべてモック）。 目的と根拠: 2026-11-22 利用者の依頼（Reddit・GIGAZINE・公式ページの中を調べる／『なんとかについて調べて』で関係ないものを選ぶ／ 『今日の横浜の天気』で過去の天気を拾う）。守ること: 検索の件数が足りること（語を広げても site を保つ）、ページは focus に近い段落が上に来て リンクを辿れること、今日がいつかを全部の段へ渡し「今日」を日付に直すこと、日付の合わないページを捨てて探し直すこと、 秘密の会話・社内のアドレスへは通信しないこと。

- `test_now_text_and_relative_dates`
- `test_page_date_is_checked_against_the_requested_period`
- `test_sites_are_normalized_and_queries_widen_while_keeping_the_site`
- `test_search_limit_is_clamped_and_multiple_sites_are_merged`
- `test_search_merge_keeps_distinct_query_urls`
- `test_search_merge_drops_tracking_params_and_ignores_param_order` — 目的: 追跡用の引数（utm_*・fbclid）だけ違う URL・引数の順番だけ違う URL は同じ記事として 1 つにまとめる。
- `test_reddit_search_and_thread_comments`
- `test_reddit_falls_back_to_rss_then_searxng`
- `test_github_and_hacker_news_entrances`
- `test_excerpt_puts_relevant_paragraphs_first_and_links_prefer_same_site`
- `test_page_links_prioritize_relevant_documentation_within_site`
- `test_read_page_reads_html_with_links_and_pdf`
- `test_internal_addresses_and_confidential_conversations_never_connect`
- `test_tools_register_sources_and_wrap_text_as_material`
- `test_chat_web_search_tool_checks_dates_in_the_body` — チャットの web_search も同じ流れ: 「今日の天気」は日付つきの語でも探し、本文の日付が合わないものは捨てる。
- `test_guidance_has_the_current_time_from_the_single_function`
- `test_understanding_builds_short_queries_per_aspect_and_never_the_raw_sentence`
- `test_simple_requests_get_one_query_and_ambiguous_ones_ask_back`
- `test_understanding_falls_back_to_rules_when_the_llm_fails`
- `test_selection_drops_irrelevant_results_with_reranker_or_words`
- `test_investigation_drops_stale_pages_and_searches_again_only_for_gaps`
- `test_investigation_stops_at_the_round_limit_and_says_when_nothing_matches`
- `test_pages_read_per_aspect_follow_depth_setting_and_latest_floor` — 読む件数は深さ（速い 3・ふつう 5・深い 8）で変わり、設定の値が基準になる。時事は速くても最低 5 件。
- `test_source_cap_keeps_matching_pages_first`
- `test_research_limits_are_clamped_and_saved`
- `test_unselected_candidate_is_available_in_later_round` — 目的: 1 回目に選ばなかった候補を 2 回目の調査から排除しない。
- `test_same_page_found_by_two_aspects_is_read_once` — 目的: 2 つの観点の検索で同じページが見つかっても、取得（と本文の取り出し・ブラウザの描画）は 1 回だけ。
- `test_snippet_date_does_not_certify_unread_page` — 目的: 本文が取得できなければ検索要約の日付が合っていても確認済みにしない。
- `test_norm_url_keeps_signed_spa_and_port_but_merges_cosmetic_differences` — 目的: 重複の判定で、見た目だけ違う URL（www・http/https・末尾の /・引数の順番・追跡の引数・節のアンカー）は同じ、

### backend/tests/test_resource_api.py

目的: **未記入**

- `test_resource_api_requires_auth_rbac_and_csrf`
- `test_resource_api_submit_wait_cancel_release_and_audit_without_requirement_payload`
- `test_resource_api_unknown_and_finished_lease_fail_closed`

### backend/tests/test_resource_broker.py

目的: **未記入**

- `test_exclusive_waiter_is_granted_after_release_without_occupying_a_lease`
- `test_waiting_cancel_is_immediate_and_never_granted`
- `test_lease_ttl_expiry_wakes_waiter_and_renew_extends_deadline`
- `test_multi_device_leases_are_independent_and_fixed_device_is_respected`
- `test_shared_safe_leases_coexist_but_exclusive_waits`
- `test_level_zero_capacity_impossible_rejects_immediately_even_when_queue_requested`
- `test_fail_fast_rejects_temporary_contention_and_fit_scheduling_skips_large_waiter`
- `test_request_max_wait_and_owner_cancel_reclaim_all_state`
- `test_waiting_owner_cancel_preserves_active_lease_for_ttl_fail_safe`
- `test_oom_profile_enforces_cooldown_and_raised_reservation_floor`
- `test_release_request_marks_the_holder_without_taking_the_device_away` — LLM は broker の許可なく載る。抱えている相手へ伝える手段が要る。
- `test_release_request_leaves_other_devices_alone`
- `test_a_waiting_request_is_not_cut_off_by_the_clock` — 待っている要求を時計で打ち切らない。打ち切るのは頼んだ側が居なくなったとき。

### backend/tests/test_resource_devices.py

目的: **未記入**

- `test_fake_device_collection_tracks_two_devices_without_double_counting_observed_usage`
- `test_device_collection_rejects_duplicates_and_unknown_updates`
- `test_observed_devices_accept_monitor_float_bytes`
- `test_observed_devices_reuse_selected_provider_when_latest_is_unavailable`
- `test_provider_registry_keeps_reservations_explicit`
- `test_provider_probe_returns_structured_reason_without_ui_text`

### backend/tests/test_resource_exclusive_step_aside.py

目的: device を占有したい要求が、常駐物へ退去を頼めることを守る。 音楽生成は GPU を単独で使う。LLM が載っているだけで待たされ、退いてくれと 言うことすらできないと、利用者側は exclusive を諦めて shared-safe に落とす しかない。そうすると LLM と場所を奪い合い、実際に OOM で落ちていた。 退くかどうかを決めるのは provider 側である。ここで守るのは「頼みが届くこと」と 「同居して構わない要求の扱いは変わらないこと」の2つ。

- `test_exclusive_request_asks_the_resident_llm_to_step_aside` — 空きは足りているのに exclusive というだけで塞がれていた経路。
- `test_exclusive_request_keeps_waiting_when_the_llm_is_in_use` — 退くかどうかは provider が決める。使用中なら待つ。
- `test_shared_requests_are_unaffected_and_do_not_evict_the_llm` — 同居して構わない要求の扱いは変えない。ASR/TTS はここを通る。
- `test_preferred_with_a_floor_shares_instead_of_waiting_for_the_llm_to_leave` — preferred は required ではない。
- `test_preferred_without_a_floor_still_waits_for_the_device` — 下限を言っていない要求は、これまでどおり占有しか受けない。
- `test_a_floor_larger_than_the_free_space_still_waits` — 動けない大きさで通してはいけない。通せば要求側が OOM で落ちる。
- `test_the_cheap_one_is_asked_first_and_the_llm_is_left_alone` — 安く戻せる方から頼む。足りたらそこでやめる。
- `test_the_llm_is_the_last_resort_when_the_cheap_one_is_not_enough` — 安い方を空けても足りないなら、最後に LLM へ頼む。
- `test_the_order_does_not_depend_on_the_provider_name` — 順は名前の並びではなく決めごとで決まる。名前を変えても入れ替わらない。

### backend/tests/test_resource_host_placement.py

目的: VRAMが空いていればVRAM、無ければRAM（host）へ流す配置。 画像生成のような計算律速の処理は、VRAMを奪ってLLMのKVを追い出すより、 RAMへ載せた方が全体として速い。LLMはRAMでは実用にならないので gpu0 に固定する。

- `test_vram_is_used_when_it_fits`
- `test_falls_back_to_host_when_vram_is_full` — LLMがVRAMを埋めていても、画像生成はRAMへ載って共存する。
- `test_preferred_order_wins_over_free_bytes` — RAMの方が空きが大きくても、VRAMが空いていればVRAMを選ぶ。
- `test_host_is_opt_in` — host を挙げていない要求へ黙って割り当てない。
- `test_llm_pins_itself_to_the_gpu` — LLMはメモリ帯域律速で、RAM配置では実用にならない。gpu0 に固定する。
- `test_host_device_is_registered_from_system_ram`
- `test_ram_uses_its_own_figure_not_the_vram_envelope` — 同じモデルでも置き場所で必要量が違う。
- `test_ram_without_its_own_figure_falls_back_to_the_vram_envelope` — 申告が無ければ従来どおり。黙って小さく見積もらない。
- `test_vram_placement_ignores_the_ram_figure` — RAM の数字で VRAM を受理しない。小さい方を使うと OOM する。
- `test_a_ram_figure_without_asking_for_ram_is_refused` — 使われない申告を黙って受け取らない。効かないことに気づけなくなる。
- `test_ram_is_not_lent_to_the_last_byte` — OSと他プロセスのための余白を残す。
- `test_a_machine_with_no_spare_ram_lends_nothing` — 余白を割り込んだら貸さない。負の空きを作らない。
- `test_the_leftover_is_lent_when_the_whole_thing_does_not_fit` — 全常駐が入らなくても、下限を満たせるなら空きぶんを貸す。
- `test_a_budget_below_the_floor_is_refused` — 下限を割る枠では動かない。貸しても OOM で落ちるだけである。
- `test_a_request_without_a_floor_still_needs_the_whole_thing` — 下限を申告しない要求の意味は変えない。黙って小さく貸さない。
- `test_a_full_card_lends_the_whole_requirement_not_more` — 空いていれば全常駐ぶんを貸す。空き全部を予約して他を締め出さない。
- `test_ram_is_never_lent_in_pieces` — RAM は分割して載せる先ではない。全部置ける空きが要る。
- `test_an_idle_llm_steps_aside_when_there_is_nowhere_else` — 他に置き場所が無いときだけ、使っていない LLM に退いてもらう。
- `test_an_llm_in_use_is_never_pulled_out` — 使用中なら降ろさない。実行中の推論を切らないのは provider の責任である。
- `test_a_request_that_already_fits_never_asks_anyone_to_move` — 置ける要求のために誰かを降ろさない。
- `test_stepping_aside_is_not_rate_limited` — 時計で縛らない。

### backend/tests/test_resource_load_profile_persistence.py

目的: **未記入**

- `test_load_origin_classification_is_scoped_windowed_and_one_shot`
- `test_profiles_survive_reconstruction_and_respect_age_maxlen_and_clear`
- `test_corrupt_or_future_profile_file_is_ignored_without_startup_failure`
- `test_atomic_replace_failure_preserves_previous_profile_file`

### backend/tests/test_resource_queue_receipt.py

目的: **未記入**

- `test_queue_returns_owned_receipt_before_provider_drain_finishes`
- `test_fail_fast_preserves_provider_drain_verdict`
- `test_acquire_still_waits_for_grant_after_fast_queue_receipt`

### backend/tests/test_resource_scheduler.py

目的: **未記入**

- `test_resource_request_requires_confidence_and_rejects_ambiguous_devices`
- `test_scheduler_invariant_interactive_precedes_background_under_equal_conditions`
- `test_scheduler_invariant_starved_background_gets_head_evaluation`
- `test_scheduler_invariant_fairness_penalizes_owner_with_existing_grant`
- `test_scheduler_invariant_residency_bonus_cannot_break_class_or_aging_guarantees`
- `test_scheduler_invariant_score_ties_are_fifo`

### backend/tests/test_resource_telemetry.py

目的: **未記入**

- `test_load_profiles_use_bounded_observed_samples_and_nearest_rank_p90`
- `test_load_profiles_reject_estimate_shaped_or_invalid_values`
- `test_oom_profile_counts_incidents_and_never_lowers_requirement_floor`
- `test_first_token_completes_only_the_latest_pending_load_sample`
- `test_reset_removes_events_and_profiles`

### backend/tests/test_route_map.py

目的: 情報の伝達ルートの地図（backend/app/route_map.py）がコードの実際と食い違わないこと。 目的: 新しい AI・道具・通知・外部への出口・tmux への送り込みを足したのに地図へ書き忘れると落とし、 docs/ROUTE-MAP.md が地図のデータと一致していることを守る。地図に無いルートを足すと関門で落ちる。 根拠: 2026-11-27 利用者「情報伝達のルートを調査して明確にし、新たに AI を追加したり機能を開発する際は そのルートマップを保守する方法で開発を進めれば迷わないのではないか」。

- `test_map_data_is_well_formed`
- `test_owner_files_exist_and_ids_are_unique`
- `test_every_gateway_caller_is_on_the_map`
- `test_every_mcp_tool_is_on_the_map`
- `test_sandbox_allowed_tools_are_known_tools`
- `test_every_notification_kind_is_on_the_map`
- `test_every_external_egress_file_is_an_egress_owner`
- `test_every_tmux_injection_file_is_a_tmux_owner`
- `test_route_map_doc_matches_the_data`

### backend/tests/test_runtime_policy.py

目的: **未記入**

- `test_runtime_policy_roundtrip`
- `test_default_model_ref_is_dropped_from_policy` — RuntimePolicy から未使用の default_model_ref を消し、古い設定ファイルも読める。
- `test_apply_exclusive_llama_unloads_ollama`
- `test_apply_exclusive_ollama_stops_llama`
- `test_runtime_policy_rejects_invalid_limits`
- `test_model_output_tokens_uses_ollama_model_config`
- `test_model_output_tokens_uses_llama_instance_and_caps_unlimited`
- `test_preferred_models_are_per_runtime_and_survive_restart`
- `test_preference_save_does_not_start_stop_or_change_gpu`

### backend/tests/test_runtime_provider.py

目的: **未記入**

- `test_openai_stream_normalizes_content_thinking_and_usage`
- `test_ollama_native_stream_normalizes_json_lines`
- `test_ollama_structured_output_uses_native_format_and_disables_thinking`
- `test_ollama_native_request_applies_deep_context_window`
- `test_complete_structured_output_uses_generic_dialect_fallbacks`
- `test_llama_cpp_structured_output_uses_openai_schema_without_backend_dependency`
- `test_structured_dialect_error_does_not_retry_server_failure`
- `test_llama_cpp_provider_selection_is_same_for_vulkan_and_rocm`
- `test_explicit_cancel_and_task_cancel_cleanup_active_registry`
- `test_chat_job_cancel_notifies_runtime_before_generic_cancel`
- `test_local_only_transport_ignores_proxy_and_never_follows_redirect` — A redirect cannot forward a confidential request to another destination.

### backend/tests/test_samplebook_chat.py

目的: サンプルブック API と Chat アシスタント API のテスト。

- `test_all_samples_are_valid` — 全サンプル定義がエンジン検証を通ること（ノード追加/変更時の回帰検知）。
- `test_every_node_has_complete_canonical_documentation`
- `test_catalog_covers_only_known_node_types` — LLM カタログのノード type が実行エンジンに存在すること。
- `test_samples_list_and_install`
- `test_every_sample_is_safe_previewable_and_publishable` — サンプルが見本だけで終わらず、コピー直後に公開できることを保証する。
- `test_complex_order_sample_executes_with_typed_outputs` — 複合サンプルは公開できるだけでなく、外部依存なしで最終出力まで実行できる。
- `test_regression_batch_sample_executes_and_asserts_typed_output`
- `test_time_travel_sample_replays_historical_and_current_versions`
- `test_ai_patch_sample_fails_then_diagnosis_patch_recovers`
- `test_pc_state_recovery_sample_persists_progress`
- `test_install_substitutes_llm_model` — コピー時に base_url/model を渡すとサンプル既定 LLM が差し替わる。
- `test_register_workflow_validates_definition`
- `test_chat_search_rejects_unknown_mode`
- `test_generated_definition_validator`
- `test_workflow_generation_uses_configured_schema_mode` — reasoningを無制限に走らせず、構造化出力を要求する。
- `test_persistent_chat_defaults_to_fast_non_thinking_mode`

### backend/tests/test_sandbox_gateway.py

目的: サンドボックスの LLM 中継（app.maintenance.sandbox_gateway）。 目的と根拠: 箱から本番のモデル切り替え（POST .../models/{m}/load）を鍵なしで呼べた（2026-11-21 実測）。 中継は許した呼び出しだけを通し、Authorization・Cookie を上流へ渡さず、本文・同時実行に上限を持つ。 上流は httpx.MockTransport の偽物。 MCP の中継（P2a、2026-11-21）の目的と根拠: 箱の AI に ControlDeck の道具（Project Lab の実行など）を使わせたいが、 箱へ MCP のトークンは置けない。トークンは箱の外の中継だけが持ち、箱から来た Authorization は捨てて付け替える。 呼べる道具は許可の一覧だけに絞り、一覧（/tools）も同じ範囲にする。

- `test_only_allowed_calls_pass_and_load_needs_allow_load`
- `test_models_response_passes_through_with_vision_and_loaded` — 目的: 中継は /models の応答の中身を変えない（vision・loaded がそのまま箱へ届く）。
- `test_headers_are_replaced_not_forwarded`
- `test_body_over_32_mib_is_413`
- `test_stream_chunks_arrive_separately`
- `test_concurrency_limit_returns_429_without_waiting`
- `test_upstream_down_is_502_and_slot_is_released`
- `test_cloud_models_are_refused_unless_allowed` — 箱の OpenCode はローカル LLM だけ（2026-11-21 利用者）。外部のモデルへの chat は既定で断り、上流へ送らない。
- `test_mcp_paths_need_the_relay_token_and_are_closed_without_it`
- `test_mcp_authorization_is_replaced_with_the_relays_token`
- `test_mcp_call_outside_the_allowlist_is_403_and_never_reaches_upstream`
- `test_mcp_tools_listing_is_narrowed_and_renewed_token_is_not_passed_on`
- `test_mcp_upstream_errors_pass_through_and_token_stays_out_of_logs`
- `test_relay_reads_the_token_from_the_environment_not_argv`

### backend/tests/test_searxng_helper.py

目的: SearXNG 連携ヘルパーのテスト。

- `test_resolve_url_defaults_to_local`
- `test_default_url_autodetects_registered_app` — 管理アプリ「SearXNG」の web_port から既定 URL を自動検出する。
- `test_is_local_detection`
- `test_ensure_running_noop_for_remote_and_unregistered` — リモート URL は何もしない。未登録環境でも例外を出さない（検索側のエラーに任せる）。
- `test_idle_check_stops_regardless_of_starter` — 起動元を問わずアイドル停止する。利用時刻不明時は猶予を仕切り直す。

### backend/tests/test_selection_only_explicit.py

目的: 利用者の既定の選択（selected_runtime）は、要求による載せ替えでは変わらない。 目的: ゲートウェイ・載せ替えの調停役・役割ごとのモデルの要求で別のランタイムが起きても、選択はそのまま。 利用者が画面でロード（各ランタイムの start）したときだけ、選択がそのランタイムへ移る。60 秒ごとの合わせ込みも回さない。 根拠: 2026-11-22 本番。役割で Maya-S を呼ぶたびに選択が Maya-S へ移り、既定（auto）の要求が Maya-S と Strata の間を 行き来した（90 分で 5 回書き換わった）。利用者「モデルを一旦 FlashNext に切り替えるがまた Maya-XS の DS に戻ってる」。

- `test_on_demand_start_does_not_change_the_selection`
- `test_explicit_start_still_follows`
- `test_idle_loop_no_longer_reconciles_the_selection`

### backend/tests/test_session_lifetime.py

目的: **未記入**

- `test_default_persists_after_idle_and_logout_revokes`
- `test_legacy_session_migrates_only_when_still_valid`
- `test_finite_timeout_still_renews_and_expires`
- `test_reenabling_timeout_applies_to_persistent_sessions`
- `test_revocation_and_disabled_user_reject_persistent_session`
- `test_secure_cookie_on_login`
- `test_negative_timeout_rejected`

### backend/tests/test_settings_keys.py

目的: Settings →「API キー」: 外部のサービスのキーを 1 か所で見る・入れる・消す。 目的: - クラウドの AI（OpenRouter など）・特許の公開データ（EPO・USPTO・特許庁）が 1 つの一覧に出ること。 Hugging Face と Workflow のシークレットは出さない（2026-11-21 利用者「モデルのダウンロードと Workflow のシークレットは設定不要」）。 - **今あるキーは置き場を変えずにそのまま見える**（移すときに消さない）。値は返さず、設定済みか・末尾 4 文字だけ。 - ここで入れたキーは元の置き場に入り、元の機能（Models・Patent）がそのまま使えること。空の値は「変えない」。 - 消すのは明示の DELETE だけ。管理の権限が要ること。 根拠: 2026-11-21 利用者「EPO や特許庁の API のキーはどこで設定できる？設定の中にキーをまとめてほしい。OpenRouter のキーも。 今あるキーを全部移動してほしいので、消さないように」。

- `test_existing_keys_show_up_without_moving_or_exposing_them`
- `test_saving_here_writes_to_the_original_store_and_blank_means_unchanged`
- `test_delete_is_explicit`
- `test_opencode_zen_key_is_listed_saved_and_deleted_without_exposing_it` — 目的: 「開発」の組に OpenCode Zen の項目が出て、保存・削除でき、値は返さず末尾 4 文字だけ。

### backend/tests/test_signal_origin.py

目的: 停止の合図の送り主が記録に残ることを、実際に撃って確かめる。 2026-09-12、Control Deck 本体が 40 分止まった。journal に「Stopping」の行が無く systemd が止めたのではないことは分かったが、誰が撃ったのかはどこにも無かった。 後から画面の履歴を辿って、OpenCode が `kill <本体の PID>` を実行していたと分かった。 見張りは signal を block して受け取る。block したまま見張りが死ぬと二度と停止でき なくなるので、「記録が残ること」と同じ重みで「今までどおり止まること」を縛る。

- `test_the_sender_of_a_stop_signal_is_named_in_the_log` — 誰が撃ったかを、PID と系譜で残す。
- `test_the_process_still_stops_the_way_it_did_before` — 記録を取るために止まらなくなっては、直した意味が無い。
- `test_shutdown_is_bounded_so_systemd_never_has_to_sigkill` — 停止は上限付きにする。接続が空くのを待ち続けない。

### backend/tests/test_skill_execution.py

目的: **未記入**

- `test_adapter_exposes_only_director_and_preserves_upstream`
- `test_failed_repair_keeps_files_state_and_disabled_choice`
- `test_old_unadapted_install_requires_update`
- `test_missing_execution_is_not_exposed`
- `test_management_rejects_symlink_escape`
- `test_execution_checks_public_contract`
- `test_skill_config_is_not_called_synchronously_in_async_provider`

### backend/tests/test_skills.py

目的: 推奨スキルの導入・更新・無効化・削除。 スキルは OpenCode が読む手順書で、ControlDeck のデータ配下にだけ置く。利用者の `~/.claude/skills` や `~/.config/opencode` へは書かない——そこは利用者自身のもので、 消し忘れると ControlDeck を使っていない OpenCode の挙動まで変えてしまう。

- `test_every_bundled_skill_ships_a_document` — 同梱したのに SKILL.md が無いと、OpenCode は黙って無視する。
- `test_bundled_documents_declare_a_matching_name` — frontmatter の name は folder 名と一致していないと読まれない。
- `test_install_enable_disable_remove`
- `test_reinstall_keeps_a_deliberate_disable` — 入れ直しで、利用者が切っていたものを勝手に戻さない。
- `test_a_missing_install_is_not_reported_as_installed` — 記録だけ残って実体が消えていたら、導入済みとは言わない。
- `test_unknown_skills_are_refused`
- `test_external_skills_state_what_they_need` — 実行環境が別に要るものは、導入する前にそれが分かること。
- `test_the_runtime_config_only_carries_enabled_skills`
- `test_external_subpaths_exist_in_the_pinned_tree` — 取り出す場所を間違えると、導入そのものが失敗する。

### backend/tests/test_sly_radiance.py

目的: Sly-Radiance（vLLM 改造版）のランタイム: 登録の検証・起動の引数・目録・フォルダの読み取り・共通部品との結びつき。 目的: `vllm serve` の引数が配列で組まれ（shell 不使用）、127.0.0.1 だけに待ち受け、モデル・先読みのフォルダが read-only で渡り、先読み 0 や先読みモデル無しでは speculative を付けず、量子化の切り替え・追加の環境変数が 検証を通ったものだけ渡ること。GPU の使用率が共通の VRAM の余裕を超えないこと。他のランタイム（Radiance）と 同じ扱い（載せ替え・並列の枠・ゲートウェイ・一覧）に入っていること。 根拠: 2026-11-27 利用者「Sly-Radiance を導入し、Radiance と同様のメニューや導入方法・他のランタイムと同様に利用」。 起動の引数は公式 README の Quickstart（2026-11-27 確認）。実機の起動は別に確かめる（ここは全部モック）。

- `test_registration_validates`
- `test_engine_settings_are_validated`
- `test_folder_with_colon_is_refused`
- `test_launch_args_loopback_readonly_and_speculative`
- `test_quantization_switches_and_kv4_and_thinking`
- `test_gpu_utilization_leaves_the_common_margin`
- `test_inspect_folder_suggests_quantization`
- `test_catalog_matches_the_readme_rows`
- `test_release_tag_conversion`
- `test_shared_with_radiance_runtime_groups`
- `test_a_loaded_sly_model_is_freed_for_another_runtime`
- `test_stop_removes_a_container_left_in_removing` — 停止で unit が止まっても container が残っていれば強制で外す（Radiance と共通の部品）。

### backend/tests/test_speech_engines.py

目的: Supertonic 3（TTS）の保管庫・導入・合成と、機能追加の「音声エンジン」の扱いを守る。 根拠: 2026-11-19 利用者「Supertonic 3 を TTS として使いたい。開発中止・サポート終了で更新も消去もありうるので、 リポジトリを丸ごとアーカイブとして ControlDeck に保存し、機能追加から導入できるように。導入時は音声モデルも」。 - 保管庫は一度取ったものを取り直さない（上流が消えた・書き換えられた後に失わない） - アンインストールで消すのは実行環境だけで、保管庫は残す - 機能追加では「更新」が無く、導入＝利用可能（有効化・再読み込みを求めない） 実際の取得・合成は外部と重い計算なのでここでは模擬し、実機の確認は PR に記録する。

- `test_archive_keeps_what_it_has_and_fetches_only_missing`
- `test_uninstall_removes_runtime_but_keeps_archive`
- `test_feature_is_a_speech_engine_without_update_or_reload`
- `test_tts_returns_wav_and_validates_input`
- `test_engine_list_has_both_asr_and_tts`
- `test_synthesis_rejects_out_of_range_before_starting_a_process`
- `test_input_filter_presets_and_custom_values` — 入力フィルター: 初めは推奨。プリセットを選ぶとその値に揃い、細かい値を変えるとカスタムになる。範囲外は受け取らない。
- `test_speech_engines_can_be_uninstalled` — TTS / ASR のアンインストール（2026-11-20 利用者の依頼）。ASR はそのエンジンのモデルだけを消し、sherpa-onnx の venv

### backend/tests/test_standards_license.py

目的: Purpose: keep standards licence decisions fail-closed and explainable. Basis: docs/standards-license-policy.md (moved from ResearchDataHub on 2026-11-19 when it was merged into ControlDeck). ISO/IEC/JSA/ASTM terms checked on 2026-11-19 forbid AI input of purchased standards; JSA also forbids LAN/network use. A wrong "allowed" here would send licensed text to embeddings or LLMs, so every path that can open AI use is pinned by a test.

- `test_common_designations`
- `test_unknown_designation`
- `test_unknown_channel_is_metadata_only`
- `test_unknown_publisher_is_metadata_only`
- `test_purchased_iso_iec_jis_astm_never_reach_ai_or_server`
- `test_record_cannot_exceed_publisher_ceiling`
- `test_free_view_never_stores_text`
- `test_unknown_operation_raises`
- `test_contract_without_reference_is_closed`
- `test_contract_with_reference_grants_recorded_scope`
- `test_stale_review_stops_ai`
- `test_public_law_is_allowed`
- `test_open_publication_stores_but_ai_waits_for_review`
- `test_every_snapshot_is_well_formed`
- `test_no_snapshot_allows_ai_by_default`

### backend/tests/test_strata_catalog.py

目的: Strata の目録が 1 か所に集約され、画面・API・登録のすべてが同じ値を見ることを守る。 確かめること: 全系列に量子化がある・id が重ならない・未知の系列を Pydantic が弾く・ NVIDIA 限定の行を NVIDIA の無い PC で出さない・「載るか」の計算・API の形（同期 endpoint）。 根拠: 2026-11-26 の指示書 M1。以前は目録が gateway・router・model_names・frontend に 二重・三重にあり、0.2.22 の全系列を登録画面に出せなかった。AGENTS.md「テストは目的と根拠を 書いてから足す」の決まりで、足す前に `./deck.sh test-catalog` で同じ観点を探した。

- `test_catalog_is_complete_and_ids_do_not_collide` — 目録の全行に量子化があり、系列 id・量子化 id が重ならない。
- `test_catalog_engine_and_thinking_flags` — Qwen 用引数を渡さない系列と、0.2.22 で思考を持つ系列を間違えない。
- `test_family_validator_uses_catalog_not_a_fixed_literal` — API の系列は目録の id だけを受け付け、固定 Literal に無い新しい系列も通る。
- `test_nvidia_only_family_is_hidden_without_nvidia` — NVIDIA の無い PC では nvfp4 を画面に出さない。目録自体からは消さない。
- `test_fit_compares_memory_estimate_with_ram_plus_vram` — 「載る／ぎりぎり／載らない／不明」が RAM+VRAM と目安の比べで決まる。
- `test_api_payload_shape_and_sync_endpoint` — 目録 API が画面に必要な形を返し、同期 endpoint で event loop を塞がない。

### backend/tests/test_strata_gateway.py

目的: Strata must not download a model or alter the active runtime by default. 加えて、目録（strata_catalog.py）の全系列・全量子化が登録でき、古い登録の設定が壊れないことを守る。 根拠: 2026-11-26 の指示書 M1（目録を 1 か所へ。0.2.22 の Inkling は思考あり）。

- `test_status_parses_upstream_json_even_when_human_lines_follow`
- `test_auto_load_refuses_first_download`
- `test_explicit_start_uses_fixed_cli_args_and_records_activation`
- `test_cannot_register_untrusted_family_or_injected_alias`
- `test_unsloth_flash_next_sizes_are_registrable` — 目的: 目録にある Unsloth の全量子化が登録でき、ほかの系列の量子化は混ぜられない。
- `test_uninstall_never_deletes_models`
- `test_download_only_exit_zero_does_not_activate`
- `test_readiness_requires_loaded_health_and_models`
- `test_local_model_requires_all_shards_and_allowed_realpath`
- `test_settings_update_requires_stopped_model_and_resets_activation`
- `test_addon_target_can_use_activated_local_strata`
- `test_lifecycle_flags_change_while_running_and_keep_activation`
- `test_engine_settings_reach_launch_as_validated_set_flags`
- `test_free_form_chat_leaves_sampling_to_the_model` — The main chat sends no temperature, so each model's own (card / per-model) sampling applies.
- `test_thinking_settings_are_strata_shared_defaults`
- `test_memory_settings_preserve_omitted_clear_auto_and_forward`
- `test_invalid_memory_settings_are_rejected`
- `test_legacy_memory_values_are_not_launch_changes_when_equal`
- `test_strata_refuses_quantizations_it_cannot_run_at_registration` — Strata は Unsloth の UD-*・GSQ-RCO・Q8_0 だけを動かす。ほかの K 量子化（Q4_K_S など）は登録の時点で断り、llama.cpp を案内する。
- `test_inkling_2_mini_keeps_thinking_but_not_qwen_only_arguments` — 目的: Inkling 2 Mini を登録・起動するとき、Qwen 用の引数だけを渡さないことを守る。

### backend/tests/test_strata_memory.py

目的: Memory planning must distinguish disk, pinned host KV and device allocations.

- `test_context_is_per_conversation_and_quantization_changes_capacity`
- `test_unknown_model_or_auto_window_does_not_invent_capacity`
- `test_zero_ram_reserve_is_not_default_four`
- `test_log_categories_and_restart_do_not_double_count`
- `test_ssd_fallback_reports_ram_complement`
- `test_estimate_endpoints_validate_and_do_not_start_models`
- `test_observed_stopped_does_not_read_stale_logs`
- `test_explicit_full_device_kv_does_not_allocate_host_streaming_backing`
- `test_runtime_status_shape_without_child_pid_uses_owned_host_record`

### backend/tests/test_strata_optional.py

目的: Safety checks for the optional Strata Terminal and gateway lifecycle.

- `test_strata_unsupported_platform_fails_without_download`
- `test_strata_registration_is_opt_in_without_model_download`
- `test_strata_rejects_unknown_family_and_port`
- `test_strata_update_refuses_to_stop_active_model`
- `test_strata_uninstall_only_managed_cli_and_engine`

### backend/tests/test_swap_scheduler.py

目的: モデルの載せ替えの調停役（models_mgmt/swap_scheduler.py）の振る舞い。 目的: ローカル LLM の載せ替えを 1 か所で決め、(1) 実行中の要求を切らない、(2) 待たせても 503 にしない、 (3) 載せ替えの回数を到着順より減らす、(4) 待ち時間で優先度が上がり必ず実行される、(5) 二重のロックで 行き詰まらない、ことを守る。 根拠: 2026-11-22、箱の OpenCode が開発（Strata）と調査（Maya）を交互に呼ぶと、要求ごとに別々のモデルが互いを 降ろそうとし、解放の見送り・Strata の操作ロックの取り合いで、待てば通る要求が 300〜660 秒待って 503 だった。 利用者の指示（同日）: 並列は Radiance だけ（登録の数まで）・それ以外は 1 本ずつ。断ると要求が消えるので、 待ち時間で点数が上がる方式にする。 載せ替えはモックの loader（時間を制御）、載っている状態は注入した snapshot で模す。

- `test_loaded_model_runs_at_once_and_other_model_waits_for_running_to_finish` — 載っているモデルは待たずに実行・別のモデルは実行中が終わってから切り替える（実行中は切られない）。
- `test_same_model_runs_one_at_a_time_except_radiance` — 同じモデルの並列は Radiance だけ（登録の数まで）。Strata・Maya・llama.cpp の chat は 1 本ずつ。
- `test_snapshot_gives_parallel_only_to_radiance` — 既定の snapshot: Radiance だけ登録の max_num_seqs（n_parallel）まで。llama.cpp は n_parallel 4 でも 1。
- `test_score_prefers_more_waiting_then_faster_load_then_older` — 点数: 待ちが多いモデルが先・載せ替えが速い（warm）モデルが先・同点は古い順。
- `test_waiting_raises_priority_so_every_request_runs_and_none_gets_503` — 載っているモデルに要求が来続けても、別のモデルの要求は待ち時間で点数が上がり、やがて必ず実行される。
- `test_swap_always_serves_the_request_it_loaded_for` — 載せた目的の要求は、載せ替えの直後に必ず 1 本は通す（載せたモデルを一度も使わず行き来し続けない）。
- `test_requests_after_the_switch_decision_are_queued_not_refused` — 切り替えると決めた後に来た元のモデルの要求も、断らず待ち行列へ入れて後で実行する。
- `test_waiting_requests_are_not_refused_and_a_client_that_leaves_is_dropped` — 待たせても 503 にしない。クライアントが切ったら待ち行列から外れ（499）、載せ替えもしない。
- `test_failed_swap_returns_503_with_reason_then_pauses_after_two_failures` — 載せ替えの失敗は、そのモデルの待ちに理由つきの 503（ログにも出す）。2 回続いたら少し間を置く。
- `test_alternating_models_swap_less_than_arrival_order` — 交互に来る 2 つのモデルの要求（開発と調査）で、載せ替えの回数が到着順（FIFO）より少ない。
- `test_prefetch_only_with_enough_memory_and_failures_are_ignored` — 先読みはメモリが足りないときにしない・失敗しても載せ替えは続く。GGUF の分割も全部読む。
- `test_run_prefetch_skips_unreadable_files` — 読めないファイル・fadvise の失敗は無視して残りを続ける。
- `test_auxiliary_and_unknown_models_are_not_scheduled` — 補助のモデル（embedding）と設定に無いモデルは対象外。載せ替え中でも待たず、実行中に数えない。
- `test_status_reports_queue_next_and_recent_swaps` — swap-queue の中身: モデルごとの待ち数・最古の待ち秒・載っているモデル・次の予定と理由・最近の載せ替え。
- `test_failopen_when_the_scheduler_itself_breaks` — 調停役の不具合で要求を止めない。snapshot が壊れたら、待たせず通す。
- `test_gateway_chat_through_the_scheduler_does_not_deadlock` — gateway_chat → 調停役 → _swap_load（_model_startup・lease・ensure_ready）が二重のロックで止まらない。
- `test_load_endpoint_goes_through_the_scheduler` — /v1/models/{m}/load も調停役に並ぶ。載っていれば載せ替えない・載っていなければ 1 回載せる。
- `test_load_waits_for_running_chat` — /load は、載っているモデルの実行中の要求が終わるまで待つ（実行中を切らない）。
- `test_overload_never_starves_another_model` — 到着が処理より速い過負荷でも、別のモデルの要求は上限の時間内に実行される（待てば必ず実行される）。
- `test_stream_cleanup_runs_even_when_the_relay_never_starts_or_is_cut` — クライアントが relay の始まる前・途中で切れても、lease と調停役の枠が戻る。上流のエラーでも戻る。
- `test_swap_queue_api` — GET /models/swap-queue が調停役の状態を返す。

### backend/tests/test_system_services.py

目的: **未記入**

- `test_installed_catalog_rejects_symlink_and_invalid_definition`
- `test_system_service_api_uses_catalog_id_and_helper_boundary`
- `test_system_service_disallowed_action_is_rejected_before_helper`
- `test_privileged_helper_executes_only_fixed_systemctl_and_catalog_unit`

### backend/tests/test_systemd_unit.py

目的: **未記入**

- `test_basic_unit`
- `test_argument_escaping`
- `test_newline_injection_rejected`
- `test_env_injection_rejected`
- `test_relative_exec_rejected`
- `test_invalid_restart_policy_rejected`
- `test_description_sanitized`

### backend/tests/test_terminal_attachments.py

目的: ターミナルへ渡す一時画像の置き場。溜め込まないことを確かめる。

- `test_stores_an_image_and_returns_a_readable_path`
- `test_rejects_non_images_and_oversized_files`
- `test_content_type_decides_the_suffix_not_the_uploaded_name`
- `test_expired_entries_are_swept`
- `test_oldest_entries_drop_once_the_count_limit_is_passed`
- `test_oldest_entries_drop_once_the_size_limit_is_passed`
- `test_discard_removes_one_entry_and_reports_unknown_ids`
- `test_a_new_store_does_not_inherit_the_previous_one`
- `test_upload_endpoint_returns_a_path_and_rejects_other_types`
- `test_upload_requires_authentication`

### backend/tests/test_terminal_automation.py

目的: **未記入**

- `test_snippet_preview_and_detached_worker_keep_command_out_of_metadata`
- `test_terminal_condition_mismatch_is_skipped_without_injection`
- `test_schedule_crud_uses_durable_timer_and_protects_referenced_snippet`
- `test_schedule_rejects_blind_interactive_session_injection`
- `test_schedule_unit_contains_only_worker_reference_and_absolute_utc_time`
- `test_worker_python_keeps_virtualenv_entrypoint_symlink`

### backend/tests/test_terminals.py

目的: **未記入**

- `test_fallback_pty_lifecycle` — tmux 有無に関わらず、セッション作成→一覧→終了が動作すること。
- `test_terminal_api_requires_permission`
- `test_terminal_api_admin`
- `test_terminal_create_accepts_cwd_for_chat_command_runs` — 目的: チャットの返事のコマンドを「その会話の作業場所」で実行するため、セッション作成に cwd を
- `test_terminal_v2_websocket_rejects_non_lab_session_before_attach`
- `test_terminal_v1_websocket_rejects_v2_lab_session_before_attach`
- `test_terminal_session_metadata_distinguishes_shell_and_program`
- `test_terminal_session_id_and_history_bounds`
- `test_terminal_size_is_bounded_and_duplicate_resize_is_suppressed`
- `test_terminal_write_retries_partial_and_interrupted_writes`
- `test_terminal_write_rejects_zero_progress`
- `test_tmux_history_scroll_uses_bounded_copy_mode_commands`
- `test_tmux_history_scroll_down_exits_at_live_edge`
- `test_tmux_automation_input_uses_stdin_and_exact_target`
- `test_terminal_websocket_resize_ack_tracks_generations`
- `test_output_journal_bounds_and_sequence_lookup`
- `test_terminal_stream_waits_for_complete_output_burst_boundary`
- `test_terminal_websocket_resume_sends_only_sequence_delta`
- `test_terminal_websocket_resume_outside_journal_falls_back_once`
- `test_terminal_websocket_paste_ack_is_exact_and_deduplicated`
- `test_terminal_input_sequence_and_cleanup`
- `test_terminal_stream_rejects_stale_generation_and_cleans_session`
- `test_terminal_stream_journals_disconnected_output_in_order`
- `test_tmux_replays_ten_thousand_lines`

### backend/tests/test_test_catalog.py

目的: テストの目録の決まりを守らせる。 目的: 新しいテストファイルに「何を・なぜ確かめるか」（目的と根拠）を書かせ、docs/test-catalog.md から 重複を探せるようにする。根拠: 2026-11-17 の利用者の決まり（同じ項目を確かめるテストを増やさず、 目録を元に統廃合する）。目的の書かれていない既存のファイルは下の一覧にだけ残し、一覧は減らす一方にする。

- `test_every_new_test_file_states_its_purpose`
- `test_legacy_list_only_shrinks` — 目的を書いた（または消した）ファイルは一覧から外す。一覧に残ったままだと、次に未記入へ戻っても気づけない。
- `test_catalog_renders`

### backend/tests/test_test_catalog_preserve.py

目的: テストの目録の作り直しが、手書きの説明を消さないこと。 目的: `./deck.sh test-catalog`（app.maintenance.test_catalog）で作り直しても、既存の docs/test-catalog.md にある 「目的:」の行と「 — 説明」のうち、コードの docstring に無いものが名前で突き合わせて引き継がれること。docstring があれば そちらが正。消えたテストの説明だけが消える。 根拠: 開発の作業場 1 段目（2026-11-21）の試験で、箱の OpenCode が目録を作り直したら手書きの説明が消えた。

- `test_parse_reads_purpose_and_descriptions_by_name`
- `test_merge_fills_only_what_the_code_leaves_empty`
- `test_render_with_existing_text_keeps_every_description_in_the_real_catalog` — 実物の目録で、作り直しても「 — 」の付いた行が 1 つも減らない（docstring の無いものは引き継ぐ）。

### backend/tests/test_totp.py

目的: **未記入**

- `test_totp_full_lifecycle`
- `test_totp_secret_is_encrypted`

### backend/tests/test_totp_policy.py

目的: **未記入**

- `test_all_users_policy_blocks_rest_and_websocket_until_enrollment`
- `test_administrator_and_legacy_policies_are_role_scoped`

### backend/tests/test_validation.py

目的: ワークフロー意味検証・品質スコアのテスト。

- `test_detects_dangling_reference`
- `test_allows_valid_references`
- `test_detects_missing_required`
- `test_detects_unreachable`
- `test_validates_error_routes_and_node_timeout`
- `test_validates_human_approval_and_merge_contracts`
- `test_quality_score_ranges`
- `test_generate_workflow_validator_includes_semantic`

### backend/tests/test_visual_review.py

目的: 画面の見た目の関門（app.maintenance.visual_review と dev-check の「画面の見た目」）。 目的: (1) モデルは「画像を読めるローカルのモデル」だけが選ばれ、cloud: や外部は選ばれず、無ければ未実施になる (2) モデルの答えを形で検め、読めない答えは失敗にする (3) dev-check の項目が、high で不合格・medium で注意・ 撮影なしで不合格・frontend を変えていなければ未実施・モデルが無ければ未実施になる。LLM はモックで、外へは出ない。 根拠: 2026-11-22 利用者の依頼。OpenCode（GLM は画像を読めない）が作った画面を、Claude が目で見なくても 仕組みで確かめる。画面の撮影には未公開の内容が写りうるので外部のモデルへ送ってはならない。

- `test_pick_model_only_local_vision_and_never_cloud`
- `test_review_skips_without_vision_model`
- `test_review_sends_max_four_images_per_call_with_confidential_mark`
- `test_prepare_images_skips_too_large_and_non_png`
- `test_parse_answer_rejects_malformed`
- `test_parse_answer_accepts_fenced_and_thinking`
- `test_review_unreadable_answer_is_failure`
- `test_check_visual_not_run_for_backend_only_change`
- `test_check_visual_fails_without_screenshots`
- `test_check_visual_high_fails_medium_warns`
- `test_check_visual_no_model_is_skipped_not_failed`
- `test_check_visual_skip_option_and_error`
- `test_review_switches_to_the_next_vision_model_when_the_first_refuses_images`
- `test_review_skips_with_a_clear_message_when_no_model_reads_images`
- `test_other_gateway_errors_still_fail_instead_of_switching`

### backend/tests/test_visual_review_in_box.py

目的: 開発の作業場（箱）の中から、画面の見た目の検査が中継を通って流れること。 目的: 箱の中では本番のゲートウェイへ届かないので、箱の環境の OPENAI_BASE_URL（中継）を使う。ローカル LLM を借りない 作業場（中継が無い）では、不合格にせず未実施にする。明示の CONTROL_DECK_GATEWAY_URL があればそれを優先する。 根拠: 2026-11-22、利用者として作業場から＋メニューの作り直しを頼んだ通しで、関門が「画面の見た目: ゲートウェイへ 届きません（Connection refused）」で不合格になり、画面を変えた作業を取り込めなかった。

- `test_box_uses_the_relay`
- `test_explicit_gateway_wins_and_host_ignores_openai_base_url`
- `test_box_without_the_llm_relay_is_skipped_not_failed`

### backend/tests/test_vram_margin.py

目的: Common headroom survives saves and reaches the native engine interfaces.

- `test_validation_and_rounding`
- `test_margin_save_does_not_stop_or_reconfigure_running_models`
- `test_maya_native_flags_and_decreasing_margin` — Each Maya engine's own VRAM-reserve flag gets the common margin (Maya #1026), in the engine's own unit:
- `test_duplicate_flags_larger_reserve_and_invalid_values`
- `test_radiance_uses_common_minimum_without_changing_model_config`
- `test_llama_fit_margin_and_cpu_only`
- `test_strata_reserve_flag_for_each_engine`
- `test_llama_fixed_layers_fail_instead_of_silently_ignoring_margin`
- `test_llama_persisted_boot_units_receive_margin_without_restart`
- `test_same_displayed_default_in_legacy_policy_refreshes_units`
- `test_maya_duplicate_original_reserves_and_native_edits_are_preserved`

### backend/tests/test_watchdog_liveness.py

目的: systemd watchdog は「プロセスが生きているか」を見る。 健全性の確認を既定の executor で回していたため、`asyncio.to_thread` を使う他の 処理（モデルの起動・停止、重みのハッシュ計算）で埋まると確認が順番待ちになり、 その間 ping が出せなかった。プロセスは生きているのに systemd から見ればハングで、 SIGABRT で落とされる。実測で 3 日に 68 回。落ちれば繋いでいる画面は切れ、走って いた生成も道連れになる。

- `test_the_health_check_does_not_share_the_default_executor` — 共有プールが埋まっても確認は動く。
- `test_a_slow_check_does_not_kill_the_process` — 確認が返らないだけで落とさない。この loop が動けている＝生きている。
- `test_an_unhealthy_answer_still_stops_the_ping` — 本当に駄目なときは従来どおり黙り、systemd の再起動へ委ねる。

### backend/tests/test_web_browser.py

目的: Web 収集エンジン W2（ブラウザの安全）。 目的: - Chrome の全通信が中継（egress の検査）を通り、内部のアドレスへ**届かない**こと。 iframe・Worker・fetch・WebSocket・リダイレクト・IP リテラル・IPv6・DNS rebinding（内部が混ざる DNS）を実 Chrome で確かめる。 「内部」の代わりに 127.0.0.2（ローカルの試験サーバー。中継の外にあり、許可した公開の試験サーバー 127.0.0.1 とは別）を使い、 そこに**1 件も接続が来ない**ことを見る。公開の側（127.0.0.1）には届く（中継が動いている対照）。 - 中継そのもの（CONNECT・http 絶対 URI・認証・上限）が、既定の方針で内部（127.0.0.1・プライベート・リンクローカル・IPv6）を 403 で断ること。 - 使い捨てのプロファイル・Chrome のプロセスが残らないこと・秘密の会話では起動しないこと。 根拠: docs/research-web-collector.md §4、docs/PLAN-web-collector.md §7 W2。外へは出ない（相手はローカルの試験サーバーだけ）。

- `test_browser_never_reaches_internal_targets_from_iframe_worker_fetch_websocket_ipv6_and_rebinding`
- `test_redirect_to_an_internal_host_is_stopped`
- `test_stop_conditions_are_reported_not_bypassed`
- `test_profile_is_disposable_and_no_chrome_process_is_left`
- `test_confidential_conversation_never_starts_a_browser`
- `test_relay_refuses_internal_targets_with_the_default_policy`
- `test_relay_requires_authentication_and_refuses_plain_http_to_internal`
- `test_relay_stops_when_the_size_limit_is_exceeded`
- `test_loopback_is_not_bypassed_by_the_browser` — Chrome は既定でループバックを中継しない。`<-loopback>` で中継に通っているか（許可の無い 127.0.0.1 は断られる）。
- `test_memory_limit_kills_the_browser_and_cleans_up`
- `test_browser_does_not_hide_automation` — 目的: 自動操作であることをサイトから隠さない（--enable-automation を外さない・UA を詐称しない・AutomationControlled を消さない）。

### backend/tests/test_web_collector.py

目的: Web 収集エンジン W1（共通の取得・抽出・SSRF）。 目的: - 内部のアドレス（ループバック・プライベート・リンクローカル＝クラウドのメタデータ・CGNAT・IPv4 射影の IPv6）へ接続しないこと。 - DNS の答えに内部のアドレスが 1 つでも混ざれば拒むこと。リダイレクトの先も各段で検査すること。 - 接続は**検査した IP にそのまま**行うこと（検査の後で別に名前解決しない。DNS rebinding の対策）。Host は元の名前のまま。 - 失敗を status で区別すること（security_rejected / blocked / captcha / too_large / http_error）。 - Trafilatura が無くても、見出し・段落の区切りを残して本文を取り出すこと（以前は 1 行に連結）。 - Deep Research の `_page_text` と Workflow の `web.scrape` が同じ安全な取得を使うこと。 根拠: docs/research-web-collector.md §1・§3、docs/PLAN-web-collector.md W1（2026-11-21 利用者「検索基盤の強化」）。 外部へは出ない（名前の解決は差し替え、相手はローカルの試験サーバー）。

- `test_internal_addresses_are_not_public`
- `test_dns_answers_mixing_an_internal_address_are_rejected`
- `test_collect_refuses_internal_targets_without_connecting`
- `test_connection_is_pinned_to_the_checked_address_and_keeps_the_host_name`
- `test_redirect_to_an_internal_host_is_checked_at_each_hop`
- `test_failures_are_classified`
- `test_fallback_extraction_keeps_headings_and_paragraphs`
- `test_japanese_legacy_encodings_are_decoded`
- `test_deep_research_and_workflow_scrape_share_the_guarded_fetch`
- `test_trafilatura_worker_extracts_markdown_in_a_separate_process` — 本物の Trafilatura を専用 venv の子プロセスで動かす。表・見出しを Markdown で残し、メニュー・フッターを落とす。
- `test_knowledge_and_patent_url_import_use_the_body_not_the_whole_page` — ナレッジ・Patent の URL 取り込みと Workflow のページ解析（scrape_tools）も共通の安全な取得を使い、
- `test_bibliographic_meta_tags_are_kept_for_patents_and_papers` — Google Patents・論文のページは出願人・発明者・DOI を本文ではなく meta（DC.* / citation_*）に持つ。拾って本文の先頭に置く。
- `test_web_documents_are_split_by_headings_without_changing_the_collection` — Web から取り込む文書は見出しで分ける（2026-11-21 本番の評価で検索の当たり 19→23/26）。コレクションの設定は変えない。

### backend/tests/test_web_data_api.py

目的: 公開データ API の直接利用（data_api）。 目的: - URL の形で GitHub（repo・blob・issue・PR）・Wikipedia・OpenAlex（works・authors）・arXiv（abs）だけを判定し、それ以外は対象にしないこと。 - 固定の応答の見本から本文へ変換できること（README・issue のコメントの上位と件数の上限・Wikipedia の見出しと脚注の除去・ OpenAlex の抄録の再構成・arXiv の Atom）。 - 上限（コメント件数・応答の大きさ）、失敗の扱い（403/429 は止めて理由を返し HTML へ戻らない／404 は HTML へ戻る／ 内部アドレスは止める／XML の宣言は読まない）。 - `fetch.collect` が前段で data_api を使い（fetch_method="data_api"）、`data_api=False` や対象外の URL では使わないこと。 根拠: docs/HANDOFF-web-collector.md「W2 で最初にやること」（GitHub README の見出し・OpenAlex の 403・Wikipedia REST）。外へは通信しない。

- `test_match_supported`
- `test_match_rejects_others`
- `test_github_repo_readme_keeps_headings_and_meta`
- `test_github_issue_comments_are_top_and_capped`
- `test_github_blob_binary_and_truncation`
- `test_rate_limit_and_not_found_are_distinguished`
- `test_openalex_work_rebuilds_abstract`
- `test_openalex_author_survives_works_failure_but_stops_on_429`
- `test_http_get_rejects_internal_address_and_foreign_redirect`
- `test_collect_uses_data_api_before_html`
- `test_collect_stops_on_blocked_and_never_falls_back`

### backend/tests/test_web_push.py

目的: Web Push（端末への通知）。 目的: - 暗号化（RFC 8291 aes128gcm）が規格の例と 1 バイト違わず一致し、受け手の鍵で復号できる。 - VAPID（RFC 8292）の JWT が公開鍵で検証でき、aud は push service の origin、sub は https。 - 端末が渡す endpoint は既知の push service だけ受ける（内側の URL へ POST させない）。 - 404・410 の端末は消す。届いたら最後に届いた時刻を残す。endpoint は一覧に出さない。 - Project Run が終わったら起動した人へ送る。取り消しでは送らない。同じ終わりは 1 回だけ。 - 通知チャンネル push は、監視を見られる人（system.view）の端末へ送る。 - VAPID の JWT は 1 時間以上使い回す（Apple の文書の求め）。Urgency は high（すぐ届ける）。 - 端末の Service Worker が受け取ったと返すと記録し、画面で「届いたか」を確かめられる （2026-11-21 利用者「通知が届いていない気がする」。Apple は 201 を返していて、サーバーからは区別できなかった）。 根拠: 2026-11-21 利用者の依頼（通知の片方向を塞ぐ）。本番は通知先が 0 件で、自動更新にも気づけなかった。

- `test_encryption_matches_rfc8291_example`
- `test_encryption_round_trips_with_a_fresh_receiver`
- `test_vapid_token_verifies_with_the_public_key`
- `test_vapid_token_is_reused_for_an_hour_or_more` — Apple は JWT を 1 時間に 1 回より頻繁に作り直さないよう求める。期限の 1 時間前までは使い回す。
- `test_only_known_push_services_are_accepted`
- `test_subscription_api_rejects_internal_endpoints_and_hides_secrets`
- `test_send_removes_gone_devices_and_records_success`
- `test_send_refuses_offsite_urls_in_payload`
- `test_finished_run_notifies_its_starter_once_and_not_on_cancel`
- `test_push_channel_sends_to_monitoring_viewers`
- `test_push_channel_needs_no_url`

### backend/tests/test_workflow_artifacts.py

目的: **未記入**

- `test_large_output_is_offloaded_downloaded_and_deleted`
- `test_artifact_path_rejects_escape_symlink_and_checksum_mismatch`
- `test_output_hard_limit_is_enforced`

### backend/tests/test_workflow_cache.py

目的: **未記入**

- `test_durable_cache_set_get_overwrite_expire_scope_and_workflow_cleanup`
- `test_cache_delete_size_secret_safety_and_bounded_validation`
- `test_concurrent_cache_set_keeps_one_valid_entry`

### backend/tests/test_workflow_dry_run.py

目的: 副作用なしdry-runとnode metadata契約。

- `test_dry_run_never_calls_executor_or_leaks_secret`
- `test_dry_run_reports_errors_cycle_and_unreachable`
- `test_node_metadata_matches_executor_catalog_and_frontend`
- `test_node_metadata_guided_configuration_is_safe_and_consistent`
- `test_dry_run_api_does_not_create_execution`
- `test_redaction_masks_sensitive_values_when_copied_to_other_fields`

### backend/tests/test_workflow_events.py

目的: **未記入**

- `test_execution_events_are_ordered_replayable_and_redacted`
- `test_execution_event_endpoints_require_authentication`
- `test_business_event_contract_rejects_unsafe_names_and_payloads`
- `test_business_event_delivers_published_contract_and_redacts_payload`
- `test_business_event_retry_is_bounded_error_safe_and_restart_recoverable`

### backend/tests/test_workflow_flow_control.py

目的: **未記入**

- `test_disabled_node_is_skipped_without_losing_graph_route`
- `test_flow_control_executors_are_typed_and_deterministic`
- `test_typed_error_routes_once_and_return_is_leaf_only`
- `test_flow_return_public_runner_contract`

### backend/tests/test_workflow_intelligence.py

目的: **未記入**

- `test_operation_patch_is_versioned_safe_and_atomic`
- `test_runtime_route_scores_live_state_and_tolerates_na`
- `test_ai_route_output_templates_into_llm_node`
- `test_intelligence_preview_apply_conflict_auto_test_and_audit`
- `test_ai_diagnosis_sends_only_redacted_minimal_context`

### backend/tests/test_workflow_llm_autoload.py

目的: **未記入**

- `test_managed_ollama_model_is_loaded_before_generation`
- `test_external_endpoint_is_not_started_or_loaded`
- `test_llm_node_reports_managed_runtime_start_failure`

### backend/tests/test_workflow_node_catalog.py

目的: **未記入**

- `test_parallel_loop_isolates_iteration_context`
- `test_node_progress_is_recorded_without_cross_task_state`
- `test_data_transform_json_schema_and_csv`
- `test_deterministic_template_filter_and_aggregate_nodes`
- `test_deterministic_data_nodes_reject_unsafe_or_invalid_inputs`
- `test_file_glob_filters_symlink_escape`
- `test_ai_utility_normalizes_operations`

### backend/tests/test_workflow_nodes_v2.py

目的: **未記入**

- `test_string_ops`
- `test_variable_and_template_chain`
- `test_markdown`
- `test_file_read_write`
- `test_file_write_outside_root_rejected`
- `test_wol_packet_validation`
- `test_git_subcommand_allowlist`
- `test_ssh_host_validation`
- `test_python_exec_disabled_by_default`
- `test_loop_foreach_execution`
- `test_loop_count_and_done_branch`
- `test_scrape_selector` — web.scrape は Web 収集エンジンの共通の取得（fetch.collect）を使う。取得を差し替えてセレクタの抜き出しを確かめる。
- `test_workflow_api_accepts_v2_nodes`

### backend/tests/test_workflow_pauses.py

目的: **未記入**

- `test_approval_pause_is_db_backed_and_resumes_with_typed_response`
- `test_human_form_is_durable_and_exposes_schema_validated_response`
- `test_waiting_execution_can_be_canceled_without_an_in_memory_task`
- `test_durable_delay_survives_task_loss_and_resumes_without_rerunning_upstream`

### backend/tests/test_workflow_queue.py

目的: **未記入**

- `test_durable_queue_is_fifo_workflow_scoped_and_deleted_with_workflow`
- `test_queue_redacts_secrets_and_concurrent_dequeue_never_duplicates`

### backend/tests/test_workflow_resilience.py

目的: **未記入**

- `test_batch_node_preserves_order_and_validates_bounds`
- `test_rate_limit_is_atomic_durable_and_secret_safe`
- `test_rate_limit_node_rejects_without_retry`
- `test_circuit_breaker_transitions_and_allows_only_one_half_open_probe`
- `test_circuit_check_routes_allowed_and_blocked_in_real_execution`

### backend/tests/test_workflow_runner.py

目的: **未記入**

- `test_runner_exposes_only_published_contract_and_runs_immutable_version`
- `test_runner_rejects_unpublished_workflow`
- `test_runner_exposes_and_resolves_typed_approval`
- `test_run_only_operator_uses_runner_without_definition_access`

### backend/tests/test_workflow_state.py

目的: **未記入**

- `test_durable_state_is_typed_versioned_scoped_and_deleted_with_workflow`
- `test_state_conflict_type_lock_delete_recreate_and_secret_safety`
- `test_concurrent_state_increment_is_atomic_and_expected_version_allows_one_writer`

### backend/tests/test_workflow_subflow_map.py

目的: **未記入**

- `test_subflow_map_is_bounded_version_pinned_ordered_and_secret_safe`
- `test_subflow_map_stop_policy_stops_after_failed_batch_and_cycle_is_preflight`
- `test_subflow_map_collects_real_published_children_and_audits_without_payload`

### backend/tests/test_workflow_try_control.py

目的: **未記入**

- `test_control_try_routes_published_subflow_success_and_typed_error`

### backend/tests/test_workflows.py

目的: **未記入**

- `test_validate_rejects_bad_definitions`
- `test_template_rendering`
- `test_condition_and_wait_graph` — trigger → wait → condition → (true) file.exists のグラフを直接実行する。
- `test_workflow_api_crud_and_run`
- `test_preview_test_outputs_and_historical_inputs_are_integrated_and_redacted`
- `test_execution_snapshot_node_runs_and_current_or_historical_retry`
- `test_node_test_pinned_data_and_resume_from_cached_upstream`
- `test_workflow_regression_test_cases_batch_and_assertions`
- `test_publish_separates_production_from_draft_and_blocks_pins`
- `test_workflow_update_rejects_stale_editor_revision`
- `test_workflow_groups_round_trip_and_reject_invalid_membership`
- `test_validate_publish_run_publishes_only_changed_draft_and_returns_diagnostics`
- `test_publish_check_explains_missing_output`
- `test_output_render_typed_contract_and_sensitive_redaction`
- `test_viewer_cannot_run_workflows`
- `test_schedule_judgement`

### backend/tests/test_workspace_lab.py

目的: 作業場（箱）の中でアプリを動かして preview する（U12）。 目的: - lab_start は利用者の command を文字列へ埋め込まず、箱の中の bash へ位置引数（$1）で渡す。{port}・{host}・{base} だけを 置き換え、環境変数 PORT・HOST も渡す。ControlDeck の複製が動いているときは同じ公開ポートを使うので断る。 - API は持ち主だけ（他人は 404 相当の 422）・command は 1〜500 文字・監査に command を 200 文字に切って残す。 - preview の入口は URL の鍵（起こすたびに変わる）で通す。鍵違いは 404・止まっていれば 409・待ち受け前は 425。 ControlDeck の Cookie は上流へ渡さない。{base} を渡した command は接頭辞を付けたまま上流へ送る。 根拠: ROADMAP U12。箱の隔離（pod の公開ポートだけが外へ出る）を保ったまま、作った画面を見られるようにする。 podman は呼ばない（モック）。上流は小さな HTTP サーバー。

- `test_lab_start_passes_command_as_positional_argument`
- `test_lab_start_stops_the_previous_one_and_host_never_writes_the_log` — 前の lab は止める。ログを空にするのは箱の中（argv の `: >"$2"`）で、ホストは box.path の中のログを書かない。
- `test_box_file_tail_does_not_follow_symlinked_folder` — lab/ そのものを symlink にされても辿らない（ミラーの server.log も同じ読み方）。
- `test_lab_start_refuses_while_mirror_runs_and_checks_length`
- `test_lab_state_reports_listening_and_log_tail`
- `test_lab_api_owner_only_validation_and_audit`
- `test_lab_api_refuses_while_mirror_runs`
- `test_workspace_preview_key_state_cookie_and_base`

### backend/tests/test_workspace_media.py

目的: 作業場の画面の記録（撮影・操作テストの動画）の一覧・配信・削除（ROADMAP U28・U34）。 目的: 箱の AI が書ける repo の中の記録を、ホストが安全に一覧・配信・削除できること。 - 一覧は `.controldeck/media` と `test-results` の画像・動画だけ、新しい順。symlink（ファイルも階層も）は辿らない。 - 配信は一覧に出た相対パスだけ（`..`・絶対パス・置き場の外・許さない拡張子は断る）。content-type は拡張子で決め、 nosniff・CSP sandbox を付ける。Range に応じる。大きすぎるファイルは返さない。 - 削除は 1 件・作業場ぶん・全作業場ぶん。持ち主だけ。監査に件数と大きさを残す。取り込み・マージの後に自動で消す設定。 - ターンの終わりに、この回に撮ったものを meta.media へ。provider の Playwright MCP の引数に caps と output-dir が入る。 根拠: 2026-11-23 利用者「スクリーンショットを撮って表示する・操作テストの動画をチャットに出す・容量をとるので後で個別・一斉に消せる」。 podman は呼ばない（モック）。

- `test_list_is_newest_first_and_only_media_in_the_two_roots`
- `test_symlinks_are_neither_listed_nor_followed`
- `test_paths_outside_the_list_are_refused`
- `test_big_file_is_not_served`
- `test_api_serves_with_safe_headers_and_range`
- `test_api_delete_one_all_and_audit`
- `test_all_workspaces_summary_and_bulk_delete_only_mine`
- `test_auto_delete_after_adopt_only_when_enabled`
- `test_opencode_config_writes_media_dir_with_gitignore` — 出力先は repo/.controldeck/media。git に混ざらない（.gitignore の * ）。ui-verifier に動画の道具を渡す caps が入る。
- `test_turn_meta_media_lists_only_this_turns_files_up_to_12` — この回に撮った記録（ターンの始めより新しいもの）だけを最大 12 件、回答の meta.media に載せる。

## E2E（frontend/e2e）

### frontend/e2e/a11y.spec.ts

目的: 主な画面のアクセシビリティを axe-core で測る（WCAG 2.1 A / AA）。 落とすのは impact が critical のものだけ。serious 以下は結果に添付して数を出す—— 既存の画面には直していない指摘があり、全部を一度に落とすと検査そのものが使われなく なる。直したら閾値を上げる（docs/PLAN-opencode-dev-environment.md 第 1 段階 D）。 検証用インスタンスで走らせる: ./deck.sh e2e a11y.spec.ts */

- a11y ${width}px ${path}

### frontend/e2e/access-management.spec.ts

目的: **未記入**

- creates a custom role and manages a user at mobile and desktop widths

### frontend/e2e/addon-locale.spec.ts

目的: Run against an isolated Vite dev server, never the installed API. navigator.language + languagechange are browser-input fixtures, not a claim that browser Settings were operated. The production React bridge is real.

- embedded locale changes preserve the opaque frame at ${width}px

### frontend/e2e/addon-update.spec.ts

目的: **未記入**

- updates the installed OpenCode addon from the settings tab

### frontend/e2e/addon-v2-embedded.spec.ts

目的: **未記入**

- embedded Add-on Bridge preserves host authority and lifecycle UX

### frontend/e2e/addon-v2-execution.spec.ts

目的: **未記入**

- executes remote Workflow, Agent, and scoped Context contributions

### frontend/e2e/addon-v2-ui.spec.ts

目的: **未記入**

- renders v2 contributions and state without a host reload

### frontend/e2e/alert-log-error.spec.ts

目的: **未記入**

- creates a durable application log ERROR alert at mobile and desktop widths

### frontend/e2e/api-rate-limit.spec.ts

目的: **未記入**

- normal API and metrics WebSocket remain available at mobile and desktop widths

### frontend/e2e/app-health-command.spec.ts

目的: **未記入**

- shows only catalogued health commands without accepting argv

### frontend/e2e/app-long-running-layout.spec.ts

目的: **未記入**

- long-running app remains readable without status or action overlap in dark mode

### frontend/e2e/app-system-service.spec.ts

目的: **未記入**

- system scope service can only be selected from the installed catalog

### frontend/e2e/application-builder-auto-app.spec.ts

目的: **未記入**

- auto-composes a runnable Workflow app before AI or manual refinement

### frontend/e2e/application-builder-build.spec.ts

目的: **未記入**

- App Studio builds and tests generated source in an isolated systemd user unit

### frontend/e2e/application-builder-phase-a.spec.ts

目的: **未記入**

- creates and validates a Phase A project from Workflow without fake build UI

### frontend/e2e/application-builder-workspace.spec.ts

目的: **未記入**

- App Builder uses a focused responsive workspace without losing draft edits
- E7 source preview keeps Secret names opaque and blocks unauthenticated side effects

### frontend/e2e/application-project-delete.spec.ts

目的: **未記入**

- App Studio deletes a project through the secondary menu with confirmation

### frontend/e2e/assistant-chat-list-workspace-badge.spec.ts

目的: 目的: 会話の一覧に、作業場を使う OpenCode の会話の状態の小さな印（作業中・質問待ち・完了・中断）が出る。 作業場を使わない会話には印が無い。320px でも題が読め（横にはみ出さない）、ライト・ダークで印が読める。 根拠: 2026-11-23 ROADMAP「AI 開発環境の UX 改善」U9。Project Lab の作業場の一覧（#560）と同じ API （/opencode/workspaces/progress）・中断は #561 の interrupted を使う。API は page.route で模擬する。

- conversation list shows workspace state marks (${width}px, ${scheme})

### frontend/e2e/assistant-claude-layout.spec.ts

目的: **未記入**

- history list: groups, search, open, rename, delete, new chat (${width}px)
- mode chip menu in the composer (${width}px)
- a chat can be handed off to OpenCode from the plus menu in the same conversation
- an archive is attached for OpenCode and is sent once OpenCode is chosen
- auto never hands work to OpenCode by itself, even when asked to implement it
- a question that only mentions web apps stays in chat
- in an OpenCode conversation, auto still does not choose OpenCode (1)
- in an OpenCode conversation, auto still does not choose OpenCode (2)
- in an OpenCode conversation, an image goes to the chat that can see it
- a long thinking trace opens from its heading and closes by tapping inside it at ${width}px
- an OpenCode answer shows thinking split around its tools and reopening keeps the work folder
- failed and canceled answers show their status, the error and a retry
- standalone iPhone: top bar and composer stay inside the screen (${viewport.width}px)
- drawer and settings are flat on a phone
- standalone iPhone: shell fits the shortened web view and drops the bottom safe area
- history rows swipe right to archive and left to delete, with undo
- standalone iPhone: with the status bar outside the page the bottom safe area stays

### frontend/e2e/assistant-code-copy.spec.ts

目的: 会話のコードブロックの右上のコピーボタンが、コード全体（``` の行を除く）をクリップボードへ写し、「コピーしました」を出すこと。押せる範囲が 44px 程度で、横スクロールしても動かず、コードに重ならないこと。 根拠: 2026-11-21 利用者「コードブロックの右上ぐらいにコピーボタンを付けて、押すと全体がコピーできるように」。

- code block copy button copies the whole code (${width}px)

### frontend/e2e/assistant-code-run.spec.ts

目的: 目的: 返事の中のコマンド（bash などのシェルの言語・言語なしでコマンドに見えるブロック）に「▶ 実行」が出て、 確認のシート（内容・場所・危険な内容は赤字で 2 段）を通して ControlDeck の Terminal へ渡ることを守る。 python などシェルでない言語には出ない。場所はその会話の作業場（無ければホーム）。 根拠: 2026-11-27 利用者「返事の中のコマンドを、ボタン 1 つでコンソール（Terminal）で実行する」。 API は既存の POST /terminals（cwd を足した）だけ。新しい危ない API は作っていない。

- code blocks show run button only for shell commands (${width}px)
- dangerous command needs a red note and two steps, then runs in the workspace

### frontend/e2e/assistant-dev-targets.spec.ts

目的: 目的: ＋メニューの「開発対象」（普通の開発／ローカル LLM を使うアプリ／ControlDeck 本体）と機能の一覧を確かめる。 作業場を作るときは選べて（作る API に target・capabilities が渡る）、作業場を選んでいるときは表示だけ。 「詳しく」で機能の一覧（gpu は「準備中」で押せない）。モードと権限は半分の幅で左右に並べた 1 行で、既存の動き（PUT permission-mode）は同じ。 メニューはコンパクト（320px で画面の高さの 60% 以下・押せる範囲 44px 以上・はみ出さない）。作業場の札に開発対象が出る。 根拠: 2026-11-22 利用者「開発対象を＋ボタンのところに記載して選択できるように。プラン/ビルド・権限もトグルで、 表示面積を小さくして＋メニューをコンパクトに」。API は page.route で模擬する（箱・OpenCode は動かさない）。 同日の追加依頼: モード・権限はフラットアイコンのみ（svg がある・aria-label が残る）、メニュー幅 288px（≤300px で確かめる）、 機能タイルはアイコンとラベルの横並び 1 行（中心 y が同じ）。

- plus menu shows the dev target, details and compact toggles (${width}px ${scheme})
- target controldeck and mirror cannot be chosen when the source is a CodeDEV project

### frontend/e2e/assistant-dev-workspace-diff-merge.spec.ts

目的: 目的: 作業場の結果のカードの「変更」で、ファイルの一覧 → 行ごとの差分（追加は緑・削除は赤・行番号・横スクロール・打ち切りとバイナリの表示）が見え、 PR のリンクの横の「マージ」で前提の確認（関門・区分・マージできるか）→ 確認 → マージの結果が出る。L2 は強い警告と確認の文言の入力が要る。 前提を満たさないときは理由を出して押せない。320px でも横にはみ出さない。 根拠: 2026-11-22 利用者「マージの判断を Claude に頼らず、画面で行えるように」（開発の作業場 4 段目・L2）。API は page.route で模擬する。実際には何もマージしない。

- workspace diff: file list then line diff, binary and truncation (${width}px)
- workspace merge: L1 confirms then merges without a phrase (${width}px)
- workspace merge: L2 shows a strong warning and is approved by a tap (${width}px)
- workspace merge: unmet preconditions show the reason and cannot be pressed (dark, 320px)

### frontend/e2e/assistant-dev-workspace-result.spec.ts

目的: 目的: 作業場の会話に「作業場の結果」のカードが出る（変更の数と増減・開くと一覧・未コミットの印・確認の結果と主の操作）。 確認（テスト・旧「関門」）の状態は 1 つの札（未確認・確認中…・合格・不合格（n 件））で、 主の操作は状態で 1 つだけ: 未確認→確認してから取り込む（合格なら取り込みの確認へ）・ 不合格→直させる（落ちた内容を同じ会話へ）。「それでも取り込む」は細かい表示（エキスパート）の中で管理者だけ。 320px でも横にはみ出さない。 根拠: 2026-11-22 利用者「作業場の会話に結果のカードと取り込むを足す」（開発の作業場 3 段目）。 2026-11-27 利用者「関門がわかりにくい。確認（テスト）にそろえて」。API は page.route で模擬する。

- workspace result card: check then adopt creates a PR (${width}px)
- workspace result card: failed check offers fixing, adopt-anyway is expert (${width}px)
- workspace result card: visual-only failure can be skipped after a human check (320px)

### frontend/e2e/assistant-dev-workspace.spec.ts

目的: 目的: AI アシスタントの OpenCode で「開発の作業場」を選べること。一覧に自分の作業場が出て、選ぶと作業場の印が出る。 「新しい作業場」で元を選んで作ると選ばれる。送ると作業場の作業ツリーのパスが code_project_path で渡る。 消す操作は確認つき（未保存の変更があれば警告し、確認の印つきで消す）。320px でも横にはみ出さない。 根拠: 2026-11-22 利用者「Claude なしで、AI アシスタントに要望を出せば OpenCode が開発を進められる状態に」 （開発の作業場 2 段目）。API と WebSocket は模擬する（箱・OpenCode を動かさない）。

- dev workspaces can be listed, created, used and deleted (${width}px)

### frontend/e2e/assistant-diff-ask-review.spec.ts

目的: 目的: (U7) 差分の行（会話の「変更」・作業場の結果の「変更」）を押すと小さな入力が開き、送ると 「<ファイル>:<行> の <その行の内容> について: <文>」が同じ会話へビルド（code_agent: build）で送られること。 完了カードと作業場の結果のカードの「レビューして」が短い指示（reviewer の係・問題があれば直す）を同じ会話へ送ること。 生成中は行も「レビューして」も出ないこと。320・1280、ライト・ダークで横にはみ出さないこと。 根拠: 2026-11-23 利用者の通し（評価 U7）。差分を見て気づいた所を、文でファイルと行を説明して頼んでいた。 送る口は「推奨どおりで進める」と同じ send。API は page.route で模擬する（OpenCode と LLM を動かさない）。

- unifiedLineNumbers / askMessage: new-side numbers, deleted lines use old numbers, long content is cut
- changes list: ask about a line, review from the done card (${width}px ${scheme})
- workspace result card: ask about a diff line, review (${width}px ${scheme})
- no ask rows or review button while the assistant is generating

### frontend/e2e/assistant-live-status.spec.ts

目的: 目的: OpenCode の生成中に「いま: 作業中の項目 · 経過 · 段」が 1 行で出て 320px でも横にあふれないこと、道具の羅列が 段ごとに畳まれること、子エージェントの行に係・モデル・経過が出て 10 分動きが無ければ止める入口が出ること、 完了したら要約・ToDo の完了数・変更の数のカードが本文の下に出ること。 根拠: 2026-11-23 利用者の通し（評価 U4・U5・U6）。道具の羅列が長く今の作業が分からない、子が 1 時間近く動いても様子が 見えない、完了の要約が無く長い本文を読まないと分からなかった。

- now line, folded tools and subagent row while generating (${width}px ${scheme})
- done card after an OpenCode turn (${width}px ${scheme})

### frontend/e2e/assistant-message-actions.spec.ts

目的: **未記入**

- message menu forks, deletes and edits (${width}px)
- attachment chip shows upload progress
- the menu of the last message stays above the composer and its items can be pressed (${width}px)

### frontend/e2e/assistant-mobile.spec.ts

目的: **未記入**

- assistant input stays flush in standalone iPhone viewports
- shows compact conversation references and inserts one into the prompt

### frontend/e2e/assistant-model-picker.spec.ts

目的: 目的: AI アシスタントのモデルの札が 1 つの選び手で、ふつうのチャットも OpenCode も同じ一覧（種類ごとのまとまり: ローカル・ OpenRouter・OpenCode（Zen）・Claude）から選べること。各モデルに一言が付き、細かい値（モデル名）はエキスパートのときだけ出ること。 会話で選んだモデルは、OpenCode に切り替えても変わらず（札も送る値も同じ）、会話ごとに戻ること。OpenCode で使えないモデル （チャット専用の Claude）を選んでいたら、切り替えのときに 1 行出して Models の既定へ替えること。 Models の「フロンティアモデル」の「OpenCode のモデル」で既定を選んで保存できること。320px・1280px・ライト・ダーク。 根拠: 2026-11-27 利用者「AI アシスタントで Qwen で会話を始めたが、OpenCode に切り替えた後は GLM が選択されていた。 OpenCode のモデルは Models のフロンティアモデルのところで設定できるようにして。AI アシスタントなどで、他のモデルとローカル LLM を選べるように」。 API は page.route で模擬する（OpenCode・LLM・外部のモデルを動かさない）。

- model picker: grouped list, same model across modes, per conversation (${width}px ${scheme})
- Models frontier: OpenCode model is chosen and saved here (${width}px ${scheme})

### frontend/e2e/assistant-offline-research.spec.ts

目的: 目的: AI アシスタントの「この会話は秘密」（会話ごと・一方通行）のボタン・確認・印を守る（PC 1280・モバイル 320）。 押す前に確認が出る（解除できないと書く）→ 押すと入力欄に印が出る・横にはみ出さない・再読み込み後も残る・ 解除の操作がどこにも無い。新しい会話を「秘密で始める」切り替えもある。 根拠: 2026-11-21 利用者「途中から秘密の話をしたくなって押すことはよくある。一度押した会話の文脈には秘密が入っているので、 解除して外部へ調査に出られてはいけない」。撮影: E2E_SHOT_DIR（無ければ playwright の出力先）。

- secret conversation button, confirm and pill (${label})

### frontend/e2e/assistant-opencode-progress.spec.ts

目的: 目的: OpenCode の作業中に、段階（プリフィル・思考・道具など）と時間が入力欄の下に出ること。アクションリストはある ときだけ浮かび、タップで作業中・完了・時刻が見えること。道具を開くと開始・終了の時刻が見えること。 2026-11-27: コンテキストの使用量（使っている量/全体）が行の右端に「数値＋その右の棒」で出て、 終わった後も次の送信まで残ること。320px でも棒は消えず（40px に縮む）、95% を超えたら赤になること。 根拠: 2026-11-18 利用者「ずっと応答待ちと記載されている」「いつ終わるのかわからん」「項目の詳細に時間があれば」。 2026-11-27 利用者「下の統計の行の右端に 5K/20K で常に出す。数値の右に棒。80% 琥珀・95% 赤」。

- an action list that was not updated says so
- changes open as a diff and the latest turn can be reverted (${width}px)
- text stays between the work steps and steps share one card (${width}px)
- progress, action list and tool times are shown (${width}px)
- context usage sits at the right end and stays after the turn (${width}px)
- context usage turns red past 95% with the bar kept on a narrow screen

### frontend/e2e/assistant-plan.spec.ts

目的: 目的: OpenCode のプラン（計画だけで何も変えない）とビルドを切り替えられ、プラン中はビルドでないことがはっきり分かること。 Shift+Tab は Auto → 確認 → プラン → Auto。プラン中は入力欄の上に帯・札「プラン」・枠の色・案内文。 プランで送ると code_agent=plan が渡り、回答に「プラン」の印と「このプランで作る」が出て、押すとビルドで送る。 根拠: 2026-11-18 利用者「プランの切り替えを追加し、プランの際はビルドモードではないことが明確にわかること」。 API と WebSocket は模擬する（OpenCode と LLM を動かさない）。

- plan is clearly shown, sent as plan and can be built afterwards (${width}px)
- the plus menu switches between build and plan, and Shift+Tab leaves plan back to Auto
- auto stays auto, and a fixed mode survives reopening per conversation
- research with plan goes to OpenCode: investigate first, then plan
- plan with auto plans in OpenCode, and leaving plan stops going to OpenCode
- the plus menu stays on a short screen, scrolls inside and follows the arrow keys
- with OpenCode chosen, a question stays in OpenCode
- in auto, when the model hands the work to OpenCode the answer becomes an OpenCode answer
- with OpenCode chosen, an attached image is sent to OpenCode, and a model that cannot read images says so

### frontend/e2e/assistant-plus-menu.spec.ts

目的: 目的: ＋のメニューが押した「＋」の近く（入力欄の真上）に出ることを守る。画面からはみ出さない・ 中身が長いときはメニューの中だけがスクロールする・キーボードで殻が縮んでいる間も同じ位置。 根拠: 2026-11-27 利用者「＋のメニューが飛ぶ（iPhone。メニューが画面の上の方に出て、下の入力欄の＋から 離れる）」。原因は window.innerHeight からの位置計算で、iOS の fixed の基準とずれるため。 PlusMenu は fixed の探り要素で基準を実測する直しをした（ここは Chromium で置き方を確かめる。 低い画面ではみ出さない・中だけスクロールは assistant-plan.spec.ts の short screen の試験が見る）。

- plus menu opens right above the button (${width}px)
- plus menu stays inside the keyboard-shrunk area

### frontend/e2e/assistant-question-buttons-model-pill.spec.ts

目的: 目的: (U13) OpenCode の回の本文に「決めてもらいたいこと」の質問があれば、本文の下に答えの画面（選択肢のラジオ／チェックボックス・ 「推奨」の札・推奨は最初から選択・「その他」の自由入力・全体の補足）が出て、「この答えで作る」はビルドで、 「答えてプランを直す」はプランのまま、答えを付けて送ること。答えの無い質問があれば押せないこと。 質問があるプランでは、答え無しの単独「このプランで作る」を出さないこと。 抜き出しは、新しい書き方・古い書き方（→ 推奨：○○）・複数可・選択肢の無い質問・見出しの無い本文を扱い、LLM は使わない。 (U11) ヘッダの札が「auto（実際のモデル名）」になり、320px でも札が切れて崩れず（長ければ省略・title に全体）、 OpenCode の会話では設定に「役割ごとのモデル」の一覧が出ること。 根拠: 2026-11-23 利用者の通し（評価 U13・U11）。「Claude みたいにチェックボックスと自由入力で答えたい。選択肢にも推奨が出てほしい。 推奨どおりに進めると『このプランで作る』が同時に出ていて、答え無しで始めてよいのか分からない」。 API は page.route で模擬する（OpenCode と LLM を動かさない）。

- extractPlanQuestions: new format / old format / multi / free-only / no heading
- proceed-with-recommendations button and model pill (${width}px ${scheme})
- the primary button builds with the answers, a free answer replaces the choice, old-style recommendation still works
- the recommended answers are sent as they are, and in a build turn only one button is shown
- no answer form when there are no questions, and the plain build button stays

### frontend/e2e/assistant-queue-voice.spec.ts

目的: 目的: 生成中に送った要望が「送信待ち」に入り、編集・削除（元に戻す）・すべて取り消すができ、回答が終わって サーバーが送ったら会話に出ること。音声入力は波形と時間が出て、黙っても止まらず、区切りごとに入力欄へ 文字が入り（送信はしない）、✓ で終わり、✕ でこの回の文字を戻すこと。320px と PC 幅ではみ出さないこと。 根拠: 2026-11-18 利用者「実行中に要望を送信したい。サーバー側で保存し、適切なタイミングで LLM に送る。複数の送信・ 削除・編集・取り消し」「Claude のように波形が出て、確かめながら文字入力したい」「すぐ止まった。長時間を想定して」。 API・WebSocket・マイクは模擬する（LLM と音声認識を動かさない）。

- requests sent during a run wait, can be edited, removed and restored, then arrive (${width}px)
- voice input shows a waveform, keeps listening through silence and types each phrase (${width}px)

### frontend/e2e/assistant-restore-checkpoint.spec.ts

目的: **未記入**

- restore the code to the checkpoint of an autosaved turn

### frontend/e2e/assistant-secret-lock-topbar.spec.ts

目的: 目的: AI アシスタントの上の行（「auto」のボタンの右）にある「秘密の会話」の鍵のボタンを守る。 新しい会話で押す→確認→OK で閉じた鍵・入力欄の 🔒・送ると confidential:true で作られる／既存の会話で押すと API が呼ばれ閉じた鍵／ キャンセルは何も変えない／閉じた鍵は説明だけで解除の操作が出ない／320px で上の行の要素が重ならずはみ出さない（ライト・ダーク）。 根拠: 2026-11-21 利用者「Web 検索などの通信ロックは重要なので、分かりやすいところに置きたい。右上の『auto』の右側に、鍵が開いているマークを」。 撮影: E2E_SHOT_DIR（無ければ playwright の出力先）。

- secret lock button in the top bar (${label}, ${scheme})

### frontend/e2e/assistant-usb-devices.spec.ts

目的: 目的: OpenCode の会話から USB 機器（M5Stack など）をプロジェクトの箱へ渡せること。機器が 1 台以上つながっているときだけ 作業フォルダの横に「USB」の札が出て、押すとシート（機器の一覧・切／自動／選ぶ・道具の有無）が開く。 既定は切で、「自動」を押すと PUT が飛ぶ。返事に Permission denied が出たら、その下に案内が出て同じシートが開く。 つながっている機器が無ければ札は出ない。390px・1280px とも横にはみ出さない。 根拠: 2026-11-27 利用者「OpenCode で開発したソフトが USB 経由で書き込めない（M5Paper）」「M5 や iOS、Android の開発」。 API と WebSocket は模擬する（実機・箱を動かさない）。

- USB chip, sheet and error hint (${width}px)
- no USB chip when no device is connected

### frontend/e2e/assistant-voice-modes.spec.ts

目的: 目的: AI アシスタントの TTS 返答（回答の主要な文言を声で読む）と会話モード（話して黙ると送り、回答を読み、 また聞く。停止まで交互に続く）。読む文言からコード・表・URL・出典を除くこと。読み上げ中はマイクの音を 送らないこと。320px と PC 幅ではみ出さないこと。 根拠: 2026-11-19 利用者「TTS 返答モードではチャットの主要な文言を音声で回答」「会話モードは一定の入力停止を 検知して LLM が回答し、停止ボタンを押すまで交互に会話が続く」。 API・WebSocket・マイク・音声合成は模擬する（LLM・音声認識・TTS を動かさない）。

- speakable text keeps the main words and drops code, tables, links and sources
- conversation mode sends after a pause, speaks the reply and listens again until stopped (${width}px)
- TTS reply mode reads the finished answer and is remembered
- a reply is read once by the tab that sent it, not by another tab watching the same chat
- a streaming answer is cut at sentence ends and every part is read once
- TTS reply mode starts reading while the answer is still being written
- leading and trailing silence of each synthesized piece is cut so pieces join without gaps
- a long answer is played in order with a sentence-sized pause between pieces
- after reading aloud, the microphone still opens on an iPhone-like audio session
- a streamed answer with markdown is read without skipping or reading symbols
- a finished answer that differs from the streamed one is not read twice nor skipped
- progress lines are not read, and a rewritten head does not make the answer read again
- turning reading off mid-answer silences it, and the rest of the stream does not restart it

### frontend/e2e/assistant-workspace-header-and-plan-card.spec.ts

目的: 目的: (1) 作業場を選んだ OpenCode のヘッダの札は、会話のモデル（auto）ではなく作業場のモデルの並びの先頭 （opencode-go/glm-5.3 → 「GLM-5.3（作業場）」）を出すこと。(2) ファイルを変えていない回（プラン）の完了カードには 「レビューして」を出さず、ファイルを変えた回には出すこと。 根拠: 2026-11-23 の評価（ヘッダが auto のままで、どのモデルが動くか分からない・プランの回にレビューが出る）、ROADMAP U22。 API は page.route で模擬する（箱・OpenCode・LLM を動かさない）。

- header shows the workspace model; plan turn has no review button (${width}px)

### frontend/e2e/assistant-workspace-media.spec.ts

目的: 目的: 作業場の画面の記録（撮影・操作テストの動画）が、(1) チャットの完了カードの下に縮小画像と動画で出る（押すと大きく・消された記録は「消しました」）、 (2) 作業場の結果のカードの「画面の記録」で一覧され、1 件ずつ消せる・まとめて消せる（確認つき）、 (3) 設定 → 開発（作業場）で全作業場の合計が見え、一斉に消せる（確認つき）・自動で消す設定が保存される。320px でも横にはみ出さない。 根拠: 2026-11-23 利用者「スクリーンショットや操作テストの動画をチャットに出し、容量をとるので後で個別・一斉に消せるように」（ROADMAP U28・U34）。 API は page.route で模擬する。画像は data URI の 1px の PNG、動画は 200 を返すだけ（再生内容は問わない）。

- workspace media in chat and result card (${width}px)
- workspace media settings bulk delete (${width}px)

### frontend/e2e/assistant-workspace-resume.spec.ts

目的: 目的: 作業場の「保存と中断・再開」の画面（2026-11-22）。 中断・止めた OpenCode の回のメニューに「続きから」が出て、押すと resume API が呼ばれる。 結果のカードに main の進み・ディスクの空きの注意・ぶつかったときの「OpenCode に直させる」ボタンが出る。 作業中の作業場を消すときは確認の文言が強くなる。320px でも横にはみ出さない。 根拠: 2026-11-22 利用者「作業場のコードはどういうタイミングで実際に保存されるのか。途中で停電や再起動した 場合はどうなるのか。中断して再開できるのか。…問題がないように実装してほしい」。API は page.route で模擬する。

- interrupted workspace turn has a resume action that posts the message id (${width}px)
- workspace result card warns about main progress, disk and conflicts (${width}px)
- conflicts show the files and ask OpenCode to fix them (dark, 320px)
- a resumed turn does not offer resume again (1280px)
- deleting a working workspace asks to stop it first (1280px)

### frontend/e2e/background-return.spec.ts

目的: 携帯で背面に回して戻すと「再接続中」が居座っていた。 戻ったときは socket の readyState を信用せず、必ず繋ぎ直す。 */

- returning from the background reconnects quickly

### frontend/e2e/blank-screen.spec.ts

目的: **未記入**

- settings loads repeatedly without blanking
- a throwing page shows a message instead of a blank screen
- a feature turning off redirects instead of rebuilding the router

### frontend/e2e/button-sweep.spec.ts

目的: Patent と Knowledge のボタンを、入力を入れて押し、結果まで確かめる（検証用インスタンス・1280px）。 根拠: 2026-11-21 利用者「各ボタンなどの機能動作も確認して」。 検証用インスタンスにはローカルのモデル・埋め込みが無いので、AI のボタンは「押せない理由が出ること」までを確かめる （AI の実行結果は本番の評価 ~/ControlDeck-評価/ で確かめる）。外部へ送るボタン（取得する・いま取得）は押さない。 押したボタンと結果の一覧は test-results/button-sweep.json に書く。 */

- Patent と Knowledge のボタンの動作（1280px）

### frontend/e2e/chat-memory.spec.ts

目的: 目的: AI の記憶の画面（設定の一覧・編集・削除・守る・すべて消す）と、答えの下の「記憶を n 件使いました」、 AI 設定の「この会話で記憶を使わない／この会話を記憶に残さない」の切り替えを守る（PC 1280・モバイル 320）。 守りの要点: 守られた記憶は印つきで出る・秘密の会話・特許から作った記憶は「守りを外す」が押せない・ 秘密でない会話の答えの下の記憶には守られた記憶が出ない（件数もサーバーが数えた normal だけ）。 根拠: 2026-11-23 利用者「保護された会話や特許のアイデア関連の記憶はインターネット検索を保護されていない会話には露呈できない」。 API は page.route で差し替える（LLM・記憶の保存先を動かさない）。撮影は test-results/ へ。

- memory settings: list, edit, protect, delete, clear (${width}px)
- answer shows the memories used; toggles are per conversation (${width}px)

### frontend/e2e/client-id.spec.ts

目的: **未記入**

- uses randomUUID when available
- uses getRandomValues when randomUUID is unavailable
- works without crypto and satisfies the backend contract
- generates 1000 unique identifiers without crypto

### frontend/e2e/commander-team.spec.ts

目的: Nexarch の AI チーム（/commander?view=team）の E2E。API と端末の WebSocket はモック。 目的（docs/PLAN-nexarch-team.md T1）: チームを作る → メンバーに役割を付ける → タスクを作る → 担当へ送る → 報告を取り込む → 合格、の流れと、担当ファイルの重なりの警告・報告ありの印を確かめる。 T2a: 未検証の印・合格の確認ダイアログ・止まった／上限の印・通知の切り替えも確かめる。 T2b: チームの前提（brief）の編集・送る前のリセットの切り替え・確認のコマンドの入力と結果（✓／✗・出力）・ 「自動で直せず」（blocked）の印も確かめる。 T3a: ヘッドレスのメンバーを足す・実行中の表示（最後の行・経過・使用量）と止める・上限の方針（引き継ぐ／待つ）の切り替え・ 候補の順番の並べ替え・同時並列の設定・板の上の「同時に動いている数／上限」・作業場所と履歴（runs）の表示も確かめる。 T3b: 目標からチームを作る（チームリーダーの応答はモック）・役割の欄（自動の振り分け）・担い手の選び手（コンソールとモデルが別・ 考えるレベル）・引き継ぎ先を選ぶ（タスクのカードとコンソールのカード）・チームの既定（チームリーダー・保存先を一度だけ）。 利用者の決まり（2026-11-27）「不要な表示は隠し、必要ならエキスパートで開く」: 切（既定）と入の両方を撮る。 既存の T1〜T3a の流れはエキスパートを入にして通す（beforeEach）。タイトルに「エキスパート切」を含む試験だけ切のまま。 320px と 1280px で撮影し、横はみ出しが無いこと（モバイルは列が縦に並ぶ）を見る。

- AI チーム ${label}px: 作る → メンバー → タスク → 送る → 取り込む → 合格
- URL の ?view=team でチームが開き、コンソールの切り替えで戻れる
- AI チーム ${label}px: 未検証の印と合格の確認・止まった／上限の印・通知の切り替え
- AI チーム ${label}px: チームの前提・リセットの切り替え・確認のコマンドと結果・自動で直せず
- AI チーム ${label}px: ヘッドレス（実行中の表示・止める）・上限の方針・候補の順番・同時並列
- AI チーム ${label}px: ヘッドレスのメンバーを足す
- AI チーム ${label}px: エキスパート切で目標からチームを作り、板は題・状態・担当だけ → 入で詳細（自動の振り分け）
- AI チーム ${label}px: 担い手の選び手（コンソールとモデルが別・考えるレベル）と役割の保存
- AI チーム ${label}px: エキスパート切でも引き継ぎ先を選べる（タスクのカードとコンソールのカード）
- AI チーム ${label}px: チームの既定（チームリーダー）と保存先を一度だけ選ぶ
- AI チーム ${label}px: エキスパート切で一文の依頼 → 時系列（計画・進み具合・質問に答える・結果・取り込む）
- AI チーム ${label}px: エキスパート入で板・MCP の呼び出しの履歴・使用量が出る（止まったら再開）

### frontend/e2e/commander.spec.ts

目的: AI Commander（/commander）の E2E。API と端末の WebSocket はモック（実 CLI を動かさない）。 目的（c1-spec.md「E2E」の項目）: - ＋ コンソールのシートの流れ（未導入の CLI は選べない・アカウント・作業フォルダ・個数・表示名） - タイル（1280px）とモバイル 320px の 1 つずつ（リスト → 全画面） - アカウントを作る・消す（確認ダイアログ） - 320 と 1280 のライト・ダークの撮影

- モバイル 320px: 一覧から 1 個ずつ開き、閉じる・削除も確認できる
- ＋ コンソール: 未導入は選べない・アカウント・フォルダ・個数・表示名を選んで開ける（320px）
- ＋ コンソール: 「新しい作業場所（自動の名前）」が既定で、名前を入れて開ける（${width}px）
- アカウントの管理: 一覧・作る・消す（確認ダイアログ）（320px）
- PC 1280px: タイル 2 列で並べ、押すと大きく開く（ライト・ダークの撮影も）
- 設定: AI の CLI（Nexarch）の分類に 3 つの CLI が出る（320px）

### frontend/e2e/dashboard-fans.spec.ts

目的: 目的: ホームのシステムサマリーで、CPU・GPU のファン回転（rpm）が数値で表示され、 320px（iPhone の最小幅）と PC 幅のどちらでも横にはみ出さないこと。 根拠: ファン回転をサマリーのカードに入れたとき（モバイルで横スクロールが出た）。2026-11-17 のファン制御 v2 で 「FAN 1,500 RPM」の文字表記がアイコン＋「1500 RPM」になっていたのに合わせて書き直した。 ファンの値は実機のセンサー次第（無ければ出さない）なので、出ている分の形式と幅だけを確かめる。

- dashboard shows CPU and GPU fan values without mobile overflow

### frontend/e2e/dev-workspace-settings.spec.ts

目的: 目的: 設定 → 開発（作業場）に、役割の要約・隔離・ディスクの下限が出て、編集が既存の API（workflow・settings）へ届くこと。 根拠: 開発の設定が OpenCode 画面・AI アシスタントに散らばっていたので 1 か所に集めた（ROADMAP U10）。新しい設定値は持たない。

- dev workspace settings at ${width}px

### frontend/e2e/diagnostics.spec.ts

目的: 目的: 診断の画面が実データで開き、要の欄が出て、レポートに秘密（メールアドレス）が出ないこと。 根拠: 2026-11-17 診断の追加。同日、携帯でカードが右へはみ出した（ページの横幅だけ見ていて見逃した）。

- diagnostics page shows self-test, restarts and a redacted report (${width}px)

### frontend/e2e/fan-temperature-margin.spec.ts

目的: 目的: ファン制御 v2 の設定画面で、利用者が決める最小限の値（上限温度・マージン）を編集して保存でき、 方式の選択・温度マップ・プリセットが無く（PID だけ）、推移に各部の温度とファンの回転数（rpm）が出て、 自動調整を確認してから始められること。iPhone 幅（320px）でも横にはみ出さないこと。 根拠: 2026-11-17 利用者の依頼「設定が多すぎて分からない・入れ子・プリセットが謎」で画面を作り直し、 2026-11-18 実機の比較で PID だけを残した（「いい方式のみ採用」「調整していないプリセットは消す。 デフォルトは残す」「回転数と各部の温度のグラフを出す」）。旧画面の「プリセットの保存・削除」と 「温度追従のマージン」の確認はこのファイルで置き換えた。 API は page.route で模擬する（実機の GPU とファンを動かさない）。

- fan limits and margin are edited and saved at ${width}px
- only the PID control is offered and the history shows each temperature and the fan rpm
- deviations from the log are shown and the controller is rebuilt from the log in one click
- auto tuning asks for confirmation, then shows its stage and a cancel button

### frontend/e2e/feature-auto-update.spec.ts

目的: 目的: 設定 → 機能と拡張 → オプション機能で、毎日の自動更新のオン・時刻を保存でき、「今すぐすべて更新」で 一括更新の Job が始まり、終わると前回の結果（更新・失敗）が出ること。320px でも横にはみ出さないこと。 根拠: 2026-11-20 利用者「オプション機能を 1 日 1 回ぐらい時間を決めて自動でバージョンアップ。ボタンと時間設定を」。 API は模擬する（実際の更新は走らせない）。

- feature auto update settings and run now (${width}px)

### frontend/e2e/feature-boot-restore.spec.ts

目的: **未記入**

- restored ${feature.id} settings and library (${width}px)

### frontend/e2e/file-archive-preview.spec.ts

目的: **未記入**

- archives safely and previews PDF/audio/video in responsive surfaces

### frontend/e2e/frontier-models.spec.ts

目的: Models の「フロンティアモデル」: キーを保存 → 有効 → モデルを選ぶ → 外部への送信を許可、を 320px で通す。 API は page.route で差し替える。本物の設定には書かず、外部へも送らない。 */

- キーを保存して有効にし、モデルを選び、確認のうえで外部への送信を許可できる（320px）
- OpenRouter の費用・入力の扱い・残高が見え、並べ替えと絞り込みができる（320px）
- 賢さ・コンテキスト・価格・対応機能・提供元を組み合わせて絞り込める（${width}px）
- 一覧を取り直せて、アカウントの設定で除外されたモデルが見える（${width}px）

### frontend/e2e/generated-blazor-gui.spec.ts

目的: **未記入**

- renders generated Blazor Entity UI at mobile and desktop widths

### frontend/e2e/generated-blazor-navigation.spec.ts

目的: **未記入**

- uses generated fixed success and error page navigation at 320px

### frontend/e2e/generated-blazor-query.spec.ts

目的: **未記入**

- runs typed entity and API queries with safe filtering and pagination

### frontend/e2e/generated-blazor-state.spec.ts

目的: **未記入**

- keeps generated typed client state in memory and updates safe consumers

### frontend/e2e/gpu-settings.spec.ts

目的: 目的: 設定の「GPU 設定」で、周波数と電圧を別々に指定でき、試験設定は入力欄を埋めるだけで「適用」を押すまで書かないこと。 設定値と実測を分けて見られ、Models の共通設定から電力・静音が移り、移動ボタンが消えていること。 根拠: 2026-11-18 利用者「電力制限 210 W のままホットスポット温度を下げたい」。API は模擬する（実機へ書かない）。

- GPU settings apply only on request (${width}px)

### frontend/e2e/home-compact.spec.ts

目的: 目的: モバイル（390 / 320px）のホームを詰め、状態のカード群が 1 画面に収まること。 - CPU+RAM・GPU+VRAM がそれぞれ 1 枚（2 本の線）で、横 1 段。履歴の選択の行（履歴・生データ・n 点）は無い。 - 電力の 4 項目（PSU 総出力・今回の起動中・今日・今月）が 1 行。 - LLM カードは見出しの行に「ローカル · 未ロード」。 - 一番上に通知の 1 行（最も重要な 1 件の札・本文・ほか n 件）。色だけでなく札の文字がある。PC（1280px）にも出る。 - どの幅でも横にはみ出さない。撮影: test-results/home-compact-{390,320,1280}.png（ライト）と -dark.png。 根拠: 2026-11-23 利用者「モバイルのホーム画面は 1 画面で今の情報とニュースを表示したい…履歴の 1 行は不要… 最終的な通知の内容を一行で表示して、重要度で題名と色を変えて」。API は模擬する（metrics は実体）。

- home compact (${width}px, ${scheme})
- home notice scrolls and flips (390px)
- home notice with a single item does not flip (390px)

### frontend/e2e/home-cpu-fan-colors.spec.ts

目的: 目的: ホームのモバイルのカードで、(1) 利用率と RAM・VRAM の色が違う（computed style・ライト/ダーク）、 (2) RAM・VRAM の容量が補足の行の右端、(3) CPU のカードの補足の行に CPU ファンの回転数が出る（PWM は出さない） （API はモック）、(4) どの幅でも横にはみ出さない。撮影: test-results/home-fan-colors-{390,320,1280}[-dark].png。 根拠: 2026-11-24 利用者「CPU のところに回転数の表示を追加して・RAM の容量はグラフの右下に・利用率と VRAM 使用率の色が同じなので変えて」。

- home cpu fan, colors, capacity (${width}px, ${scheme})
- home without a readable CPU fan shows nothing for it (390px)

### frontend/e2e/home-marquee-decouple.spec.ts

目的: ホームの 3 秒ごとの更新（メトリクス）があっても、テロップ・通知の一行の部品は描き直されない。API・WebSocket はモック。 目的: モバイル（390x844・CPU 4 倍遅く）で 15 秒の間、①テロップ・通知・ホームの殻の React の描き直しが 0 回 ②テロップの DOM が変わらない ③流れの animation を付け直さない ④いっぽうメトリクスの部品は 3 秒ごとに描き直される（計測が効いている証拠）。 根拠: 2026-11-25 利用者「3 秒ごとぐらいに更新されている。画面の更新は 60fps、データの取得は波形は 3 秒ごと、ニュースは更新のときに」。 数え方: 部品の本体で countRender（window.__renders）、DOM は MutationObserver。結果は test-results/home-marquee-decouple.json。 */

- 3 秒ごとの更新があってもテロップ・通知の部品は描き直されない（モバイル・CPU 4 倍遅く・15 秒）

### frontend/e2e/home-marquee-flags.spec.ts

目的: 切り分け用の目印（?marquee=css・?live=off）の E2E。下は計測の spec と同じモックを使う。 元の説明: ホームのテロップを「実機のモバイルに近い条件」（390x844・DPR 3・タッチ・CPU を遅く）で 15 秒測る。API・WebSocket はモック。 目的: 3 秒ごとのメトリクス更新と重なっても、①テロップの DOM が 1 件も作り直されない（mutation 0・animate の付け直し 0） ②長いコマ（> 33ms）の数と最大が小さい ③transform が流れの途中で巻き戻らない。結果は test-results/home-marquee-mobile-*.json。 根拠: 2026-11-25 利用者「まだ 3 秒ごとっていうのが直ってない気がする」（モバイルでだけカクつく）。 */

- 目印なしは WAAPI のまま。?marquee=css で CSS の animation になり、?live=off で 3 秒ごとの更新が止まり、設定から解除できる

### frontend/e2e/home-marquee-flip.spec.ts

目的: iPhone で「すごい速さでテロップが変わる」不具合の再発防止。ResizeObserver が何度も発火し、Animation の onfinish が早く来る（模擬）条件でも、 1 項目は「流し終わるまでの時間か holdMs の長いほう」より早くめくれない。?marquee=debug の表示も確かめる。 根拠: 2026-11-26 利用者「iPhone でテロップが変わるのがすごい速さ」（?marquee=css・?live=off でも同じ）。 */

- ResizeObserver が繰り返し発火し、onfinish が早く来ても、項目は下限より早くめくれない
- 収まる項目は読む時間（全角 30 文字で 8 秒）見せ、デバッグの表示はテロップを隠さない

### frontend/e2e/home-marquee-mobile.spec.ts

目的: ホームのテロップを「実機のモバイルに近い条件」（390x844・DPR 3・タッチ・CPU を遅く）で 15 秒測る。API・WebSocket はモック。 目的: 3 秒ごとのメトリクス更新と重なっても、①テロップの DOM が 1 件も作り直されない（mutation 0・animate の付け直し 0） ②長いコマ（> 33ms）の数と最大が小さい ③transform が流れの途中で巻き戻らない。結果は test-results/home-marquee-mobile-*.json。 根拠: 2026-11-25 利用者「まだ 3 秒ごとっていうのが直ってない気がする」（モバイルでだけカクつく）。 */

- ホームのテロップはモバイル条件（CPU ${rate} 倍遅く${multi ? 

### frontend/e2e/home-marquee-smooth.spec.ts

目的: ホームの流れる文字（ニュースのテロップ・通知の 1 行）が、3 秒ごとのメトリクス更新の間も滑らかに流れ続ける。API・WebSocket はモック。 目的: ①10 秒の間 rAF で transform の X を毎フレーム集め、流れている間は巻き戻らない ②3 秒ごとの境目で 50ms 以上の止まりが無い ③1 秒あたりのフレームが 50 以上（headless の目安）。計測の値は test-results/home-marquee-smooth.json に残す。 根拠: 2026-11-24 利用者「ホーム画面が更新が 3 秒ごとになっている気がする。ニュースの右から左に移動する文字は 60fps くらい滑らかに。 ブラウザ側で一度情報を受信したらスムーズに動かせないか」。 */

- ホームの流れる文字（${name}）は 3 秒ごとの更新の間も止まらない

### frontend/e2e/ios-keyboard-field.spec.ts

目的: iPhone: キーボードが出ても、触った入力欄が見える範囲に出る（AI アシスタント以外の画面でも）。 根拠: 2026-11-21 利用者「設定の API キーの入力欄をタップするとキーボードが上がって入力欄が消え、貼り付けられない」。 実機（iOS 18.7、設定の画面）: innerHeight 932 のまま、visual viewport だけ 517 に縮み、殻は 932 のままだった。 殻を見える範囲に合わせる useKeyboardViewportFit を AI アシスタントだけが呼んでいたため。 Chromium はキーボードで visual viewport だけを縮められないので、visualViewport を偽物にして iOS と同じ値を作る。 */

- iPhone: キーボードで visual viewport が縮むと、設定の入力欄が見える範囲に出る
- iPhone: キーボードが出ると、シートが見える範囲に収まり入力欄が見える（Nexarch のアカウントの管理）

### frontend/e2e/knowledge-essence-cross.spec.ts

目的: 知識の横断（K2）: Knowledge の「知識の横断」タブ。 目的: - 検索の欄と種類の絞り込みで、承認済みの知識エッセンスを横断して探せること。 結果のカードには文献の題名（押すと文献の画面が開く）・種類・要約・引用（折りたたみ）・ 承認の日時が出ること。 - 「比較に加える」で別の文献 2〜3 件を選び、「比較する」で種類ごとの表が見えること。 比較は並べて見せるだけ（AI の判断は足さないことが画面にも書いてある）。 - ページ送り（next_cursor があるとき「もっと見る」）で続きが取れること。 - 何も見つからないとき、文献の画面で知識を抽出・承認するよう案内すること。 - 1280 と 320 で横に崩れず（比較の表は横スクロール）、ライト・ダークで撮影すること。 根拠: 2026-11-21 利用者「PatentBuddy の開いている PR は閉じてよい。開発した内容を ControlDeck にルールに従って反映して」（K2 の作り直し。docs/HANDOFF-knowledge-platform.md の K2 の行。backend は cross.py として main 済み）。検証用インスタンスには文献も承認済みの 知識も無いので、API は page.route で差し替えて確かめる（本物の LLM は呼ばない）。 */ ---- 差し替える応答（本物の形: backend/app/knowledge/cross.py の search・compare） ----

- Knowledge: 知識の横断で探して種類ごとに比較する（${width}px）
- Knowledge: 知識の横断で何も見つからないときは案内する（${width}px）
- Knowledge: 検索の結果が1画面に収まらないとき「もっと見る」で続きを取る（1280px）

### frontend/e2e/knowledge-essence.spec.ts

目的: 知識エッセンス（K1b）: 文献の画面（DocumentSheet）の「知識エッセンス」の節。 目的: - ローカル専用の AI が抜き出した知識（種類 8 つ・引用つき・AI の推論・未確認）を、種類ごとのカードで 見せられること。抽出の開始から Job の進み具合を経て一覧が読み直されること。 - カードの引用が折りたたみで、開くと原文からの引用が出ること。 - 承認・却下・差し戻し（メモは任意）を押したら API が呼ばれ、カードの状態が変わること。 - 原文の版が変わった行（stale）には「原文が変わりました（前の承認は効きません）」の印と、 検めで捨てた件数（形違い・上限）が出ること。 - ローカルのモデルが無い（503）・AI の利用が禁止の文献（422）は、返ってきた理由がそのまま見えること。 - 見るだけの利用者（workflows.edit が無い）には抽出もレビューのボタンが出ないこと。 - 1280 と 320 で横に崩れず、ライト・ダークで撮影すること。 根拠: 2026-11-21 利用者「PatentBuddy の開いている PR は閉じてよい。開発した内容は ControlDeck に ルールに従って反映して」（知識エッセンス K1 の作り直し。docs/HANDOFF-knowledge-platform.md の K1 の行。 backend は K1a として main 済み）。検証用インスタンスにはローカルのモデルも文献も無いので、 API は page.route で差し替えて確かめる（本物の LLM は呼ばない）。 */ ---- 差し替える応答（本物の形: backend/app/knowledge/essence.py の list_essences と router.py） ----

- Knowledge: 知識エッセンスを抽出して承認・却下・差し戻しで確かめる（${width}px）
- Knowledge: 知識の抽出が始められないときは理由を出す（${width}px）
- Knowledge: 見るだけの利用者に抽出もレビューのボタンが無い（1280px）

### frontend/e2e/knowledge-map.spec.ts

目的: 知識マップ（K3）: 文献の画面（DocumentSheet）の「知識マップ」と「知識のつながり」。 目的: - 知識マップ: 1 つの文献を中心に 1〜2 hop の文献と承認済みの知識を SVG の放射図で見ること。 hop の切り替えで広がりが変わり、上限に達したら打ち切りの印が出ること。 ノード（四角=文献・円=知識）を押すとその文献を開くこと。 - 技術仮説のリンク: 承認済みのエッセンスのカードから「別の文献の知識と結ぶ」で 相手を検索して選び、理由を書いて結ぶこと。作っただけでは未確認で、 「知識のつながり」の一覧に承認・却下の別操作が出ること。 - 1280 と 320 で横に崩れず、ライト・ダークで撮影すること。 根拠: 2026-11-21 利用者「PatentBuddy の開いている PR は閉じてよい。開発した内容は ControlDeck にルールに従って反映して」（K3 の作り直し。docs/HANDOFF-knowledge-platform.md の K3 の行。backend は map.py・links.py）。検証用インスタンスには文献も承認済みの 知識も無いので、API は page.route で差し替えて確かめる（本物の LLM は呼ばない）。 */ ---- 差し替える応答（本物の形: backend/app/knowledge/map.py・links.py・essence.py） ----

- Knowledge: 知識マップで関係を見て、技術仮説のリンクを結んで承認する（${width}px）

### frontend/e2e/knowledge.spec.ts

目的: Knowledge: 探す・資料・規格のタブ。規格は入手経路から保存・AI 利用の判定を見せ、購入品は番号と題名だけで登録する。 「資料の管理」の「作り直し」は、実行する前に何を何件作り直すかを見せる。 根拠: 2026-11-21 利用者「規格検索の RAG」「利用者が知らなくていい情報は隠し、必要なら開けるように」、同日 R3-2（ナレッジの作り直し）。 */

- Knowledge: 規格を登録して番号で探す（${width}px）
- Knowledge: 審査基準のコレクションを作る（詳細設定は既定で隠す）

### frontend/e2e/llama-runtime-settings.spec.ts

目的: **未記入**

- llama.cpp instance save excludes read-only status fields
- Ollama running state updates the indicator and lifecycle button

### frontend/e2e/log-redaction.spec.ts

目的: **未記入**

- never renders application log secrets at mobile or desktop widths

### frontend/e2e/maya-ds4-vision.spec.ts

目的: 目的: DeepSeek（DS-XS）の画像の読み取りが、モデル一覧の「VISION」の印と、個別設定の入／切に出ること。 画像の部品（vision tower）が無いモデルでは入／切を選べず、案内が出ること。320px と PC 幅で横にはみ出さないこと。 根拠: 2026-11-18 利用者「vision の設定 ON/OFF は ControlDeck の設定として入るようになっているか」 「（モデル画面で）Vision 表示になっていない」。API は page.route で模擬する（実機のモデルを動かさない）。

- DS-XS shows the vision badge and its read-images switch at ${width}px
- a DeepSeek model without the vision tower cannot switch images on

### frontend/e2e/maya-register.spec.ts

目的: **未記入**

- Maya のモデルを手元のファイルから登録できる（${width}px）
- Maya のモデルをダウンロードして登録できる・一覧に準備カードを出さない（${width}px）

### frontend/e2e/mobile-reconnect.spec.ts

目的: 携帯で別のアプリへ移って戻る動き。OS が経路を切っても socket は OPEN の まま残るので、readyState を信用すると復帰が何十秒も遅れる。 */

- returning from the background reconnects within seconds
- a brief blip does not flash the badge

### frontend/e2e/mobile-viewport.spec.ts

目的: **未記入**

- reload and text focus keep the ${viewport.width}px viewport contained

### frontend/e2e/model-display-name.spec.ts

目的: モデルの表示名: Models の個別設定で名前を付けると、一覧の名前が変わる。ゲートウェイでの名前（alias）は変えない。 根拠: 2026-11-21 利用者「モデルが同じ名前になるので分かりにくい。任意の名前に変えれるようにしたい」。 検証用インスタンスには Strata のモデルが無いので、一覧と登録の応答だけ差し替える（表示名の保存は本物の API）。 */

- Models: 表示名を付ける

### frontend/e2e/model-provider-common.spec.ts

目的: **未記入**

- uses the common provider routes for pull and model configuration

### frontend/e2e/models-swap-queue.spec.ts

目的: Models 画面の「切り替え待ち」（ゲートウェイの載せ替えの調停役 /models/swap-queue）。 目的: 別のモデルの要求が待っているときだけ 1 行で出て、待ちが無ければ出ないこと。320px で横にはみ出さないこと。 根拠: 2026-11-22 利用者「載せ替えをインテリジェントに」。調停役は要求を断らず待たせるので、待っている理由を画面で見せる。 */

- 切り替え待ちの表示 (${width}px ${scheme})

### frontend/e2e/navigation-layout.spec.ts

目的: **未記入**

- uses one page-title layout without horizontal overflow
- keeps Workflows below the logo with an iPhone standalone safe area
- migrates the saved Play destination to Workflows
- pins up to six favorites at the top of the drawer
- sidebar has no 

### frontend/e2e/navigation-visibility.spec.ts

目的: 設定 → 表示の「メニューの表示」: メニュー（PC のサイドバー・携帯のドロワー）の項目を 1 つずつ隠せる。 隠した項目は「よく使う」からも消える。Settings は隠せない（隠すと戻れない）。選択は端末に残る。 Add-on の項目も同じ一覧から選べる（effective addons を差し替えて確かめる）。 根拠: 2026-11-23 利用者「設定で表示っていう項目があれば、そこの中にメインメニューの項目を任意に 表示、非表示の選択ができるようにしてほしい。新しく導入されたプラグインや今後導入される機能、今開発中の パテントなども含めて…汎用的に開発すること」。 */

- メニューの項目を隠せる（${width}px）

### frontend/e2e/news-digest-bubbles.spec.ts

目的: ニュースの画面の「今朝のまとめ」「今週のまとめ」のバブル。API は page.route でモック（外へ通信しない）。 目的: ①どの幅でも 1 行に 2 つの小さなバブルが並ぶ（カルーセル・点は無い） ②タップで 1 つが横幅いっぱいに広がり、中身が開く ③開いた中身をタップすると小さく戻る。ただし出典のリンク・話題のボタン・出典の開閉では戻らない ④主な技術・製品の語（出典の記事の話題と一致）と見出し（チャンネル名）をタップすると、新着の一覧がその話題・チャンネルで絞り込まれる。 根拠: 2026-11-24 利用者「今週のまとめと今朝のまとめは、1 行に横並びで 2 つバブルみたいのを出してタップすると、左右横幅フルまで拡大して展開される。 記事の中をタップすると最小化される。気になったトピックスをタップして関連するトピックスに移動できるか」。 撮影: test-results/news-bubbles-{closed,open,topic}-{390,320,1280}[-dark].png（NEWS_SHOTS_DIR があればそこにも写す）。 */

- ニュース: まとめのバブル（${width}px・${scheme}）
- ニュース: まとめの話題・見出しをタップすると新着が絞り込まれる（${width}px）
- ニュース: 動きを減らす設定ではバブルの開閉も開く（短い動き）

### frontend/e2e/news-prep.spec.ts

目的: ニュース: 設定に「今日の下ごしらえの呼び出し n 回」と上限が出ること、ON AIR がチャンネルに合う記事であること。API は page.route で模擬する。 根拠: 2026-11-24 利用者「ある程度のまとまりを送って構造的に個別に返してもらう」（無料のモデルの 1 日の上限 50 回に気づけるように）、 本番で ON AIR とホームの 1 位が Bored.com（HN のトップ）でチャンネルに合わなかった。 撮影は test-results/news-prep-settings-{320,1280}.png・news-prep-onair-{320,1280}.png。 */

- ニュース設定: 今日の下ごしらえの呼び出し回数と上限（${width}px）
- ニュース: ON AIR はチャンネルに合う記事（${width}px）

### frontend/e2e/news-reader.spec.ts

目的: ニュースの「あなたの興味」の × とニュースリーダー。API は page.route でモック（外へ通信しない）。 目的: ①興味の × を押すと消え、読み直しても戻らず、「外した語」から戻せる ②記事を押すと外へ飛ばずリーダーが開き、 要約 → 元の記事のリンク → 本文の順に並び、取り出した本文がすぐ出て、訳の途中（訳 n/m）→ 完了へ順に差し替わる ③取り出せないときは元の記事へ移り、一覧は直リンク ④release は今まで通り外へ ⑤携帯（320・390）・PC（1280）・ライト・ダークで崩れない。 根拠: 2026-11-24 利用者「あなたの興味…それ × をしても消えない」「記事を押すと元ページへ飛ぶ…本文を取得して、英語なら日本語に翻訳して、リーダーみたいな形で 表示…冒頭にその全文の要約、その下に元リンク先のアドレス、翻訳文をその下から」「LLM で変換が終わったものから順次変えていけば、見れない不具合は発生しない」。 */

- ニュース: 興味の × で語が消え、読み直しても戻らず、「外した語」から戻せる
- ニュース: 記事を押すとリーダーが開き、要約 → 元の記事 → 本文の順で、訳が段落ごとに差し替わる
- ニュース: リーダーの途中の状態の撮影（320・390・1280、ライト・ダーク）
- ニュース: 本文を取り出せないときは失敗の画面を挟まず元の記事へ移り、一度失敗した記事は一覧から直リンク。release も外へ

### frontend/e2e/news-sources-images.spec.ts

目的: ニュース: 画像の無い記事のアクセントライン・ホームのカード（テロップ・まとめの概要・表示数）・取得の失敗の件数を出さないこと。API は page.route で差し替える。 根拠: 2026-11-23 利用者「無意味なアイコンを貼ったデカデカとしたエリア…アクセントラインだけにして、表示を小さくする」「取れなかった総数が1件ありますと表示されてますけれども、ニュースのところで表示しないでほしい」 「ニュースのソースの右側にプリセットボタン」「ホームのニュースに、今日（無ければ昨日）のまとめの概要と、記事を何個か。何個かは設定できるように」。 撮影は test-results/news-noimg-*.png・news-home-digest-{today,yesterday,none}-{390,1280}.png・news-presets-{320,1280}.png。 */

- ニュース: 画像の無い記事は大きな箱でなく細いアクセントラインで、取得の失敗の件数は出ない
- ニュース: ホームのカード（まとめ: ${name}）はテロップ・まとめの概要・記事 5 件
- ニュース: テロップ（共通の Marquee）は複数の項目をめくり、触れると止まり、動きを減らす設定では省略表示になる
- ニュース: 設定でホームに出す記事の数を変えると表示が変わる

### frontend/e2e/news-sources-manage.spec.ts

目的: ニュース: 取得先の管理（/news/sources）。一覧・絞り込み・詳細と履歴・通信確認・間隔の変更・有効/無効・削除・一括・目録（おすすめをまとめて足す）・フィードを作る手伝い・サイトを追加。API は page.route で差し替える。 根拠: 2026-11-24 利用者「設定の中にボタンを置き、別の管理ページに飛ばして、疎通確認だったりとか、これまでの取得実績、最終取得日時などを確認、サイトごとに確認できたりとか、新しいサイトの追加、削除などができるように」 「これらを元に、情報収集の蓄積、ナレッジ化を効率的に実施してほしい」（目録・分野ごとの取得の間隔・フィードを作る手伝い）。 撮影は test-results/news-sources-{list,detail,add}-{320,1280}-{light,dark}.png。 */

- 取得先の管理: 一覧・絞り込み・詳細と履歴・通信確認・間隔と有効/無効・削除・一括
- 取得先の管理: おすすめをまとめて足す・分野で足す・フィードを作る手伝い（通信確認つき）・サイトを追加
- 設定 → ニュース: ソースは要点と「取得先を管理」への入口だけ

### frontend/e2e/news-swipe.spec.ts

目的: ニュースの画面の「指でずらす」操作・流れる文字・上の部分。API は page.route でモック（外へ通信しない）。 目的: ①ON AIR のチャンネルを左右の指でずらして変えられる（少しだけなら戻る・縦ならスクロールでチャンネルは変わらない） ②（まとめはバブルに変えたので news-digest-bubbles.spec.ts へ移した）③Marquee がデータの読み直し（項目の配列が新しく作られる）で位置を巻き戻さない ④上の部分（取り直す・設定・表示・検索）が押せて、狭い幅でも低い。撮影は test-results/ と scratchpad の shots4 に残す。 根拠: 2026-11-24 利用者「CH の上で左右にフリックすると CH が変わるようにして」「今週のまとめの上で左右にフリックすると今朝のまとめ」 「流れる文字・フリップのアニメーションはスムーズに」「取り直すボタンがある行から今週のまとめまでの表示がやぼったい」。 */

- ニュース: ON AIR を左右に指でずらすとチャンネルが変わり、少しなら戻り、縦ならスクロールでチャンネルは変わらない
- ニュース: 新着の一覧を左右に指でずらすと前後のチャンネルになる
- ニュース: Marquee は項目の配列が作り直されても（読み直し）位置を巻き戻さず、要素も作り直さない
- ニュース: 上の部分（取り直す・設定・表示・検索）は押せて低い（${design} ${width}px）

### frontend/e2e/news.spec.ts

目的: ニュースの専門ページ（/news）: 放送局のデザイン・チャンネル・設定。API は page.route で差し替える（外へ通信せず、本物の記事にも依存しない）。 画像はテストの中で作る SVG の data URI（外部の画像の URL は使わない）。 守ること: ON AIR・チャンネルの切り替え・テロップ・NOW/TODAY/THIS WEEK・チャンネルの区画・数字が出る／チャンネルを押すと ON AIR が変わる／ 設定でデザインを変えると見た目が変わる／チャンネルを足せる／携帯（390・320px）で 1 列・横にあふれない／ 新着・アーカイブ・検索・👍・隠すは 1 段目のまま使える。撮影は test-results/ に残す。 根拠: 2026-11-23 利用者「OZY のようなページのデザインを参考に。設定で検索テーマや表示のデザインなど、ニュースを便利に使うための設定を詳細にできるように」。 */ 画面の中で縦に動く領域（アプリの殻）の全体が写るよう、撮影の間だけ viewport を伸ばす（fullPage は殻の内側までは写さない）。 */

- ニュース（放送局）: ON AIR・テロップ・区画・数字・チャンネルの切り替え（${width}px）
- ニュース設定: 未保存で別のタブへ移ると確認が出て、戻ると入力が残っている
- ニュース: 設定でデザイン・表示を変えると見た目が変わり、チャンネルを足せる
- ニュース: 5 つのデザインを順に選ぶ（並び・特徴の要素・保存・撮影）
- ニュース: ダークでも 5 つのデザインが読める（背景と文字が違う色）
- ニュース: 保存済みの「紙面」「コンパクト」は経済紙・テキスト一覧として扱う
- ニュース: 動きを減らす設定でテロップが止まる
- ニュース: 新着・チャンネル・絞り込み・👍・隠す・アーカイブ・検索（${width}px）
- ニュース: ホームのカード（${design}）: まとめ・画像のカード・見出し・見た目
- ニュース: ホームのカード・見出しは新しいタブで open を記録・まとめが今日の分でなければ出さない・メニュー・設定の入口
- ニュース: 記事が無いとき「今すぐ取得する」が出る
- ニュース: 今朝のまとめ・今週のまとめのカードと本文・出典（${width}px）
- ニュース設定: 更新のタイミング・LLM（無料の印・許可が無い案内）・RAG（${width}px・許可${egress ? 
- ニュース: 検索を「意味で探す（RAG）」に切り替えられる（消えた古い記事は操作なしで出る）
- ニュース: まとめは話題ごとの傾向・主な技術・注目点と「3 行」、英語の概要は「訳」の印、設定に重点の欄（${width}px）

### frontend/e2e/notification-settings.spec.ts

目的: 目的: - 設定 → 通知 の「通知の種類」で、種類ごとのスイッチの保存（再読み込みしても残る）と、既定（調査の完了は有効・ ふつうの会話は無効）が出る。 - 「お休みモード」の開始・終了の時刻が保存され、表示に出る。今の状態（お休み中かどうか）が出る。 - 320px と 1280px で横にあふれず、ライト・ダークで読める（撮影を test-results に残す）。 根拠: 2026-11-23 利用者の依頼（お休みモード・通知の種類の拡充）。実の API（検証用インスタンス）で確かめる。

- notification kinds and quiet hours are saved and shown

### frontend/e2e/opencode-focus.spec.ts

目的: 画面を開いただけで software keyboard が上がらないこと。 以前は入力欄に autoFocus が付いていて、OpenCode を開くだけで keyboard が せり上がり、プロジェクト一覧も開始ボタンも隠れていた。v1/v2 は同じ コンポーネントなので両方の route で縛る。 */

- ${path}: 開いただけでは入力欄へフォーカスが入らず、タップで入る

### frontend/e2e/opencode-resume.spec.ts

目的: **未記入**

- closed OpenCode session can be resumed (${width}px)

### frontend/e2e/opencode-roles.spec.ts

目的: OpenCode 設定の「役割とモデル」。 目的: 役割（計画・開発・調査・簡易検証・最終検証・評価・相談）ごとに自由にモデルを選べ（全部違っても保存できる）、 同じ段に別のローカルがあればお知らせだけ出ること。最終検証＝開発のお知らせ。320・1280、ライト・ダークで読めること。 根拠: 2026-11-22 利用者「個別にモデルを設定できるようにして。全部同じも全部違うも。変な制限は設けなくて良い」。 検査はサーバー（/opencode/roles/check）が持つので、ここは検証用インスタンスの本物の API を通す。 */

- 役割とモデル: どの組み合わせでも保存できる (${width}px ${scheme})

### frontend/e2e/opencode-scroll.spec.ts

目的: **未記入**

- scrolling: ${label}

### frontend/e2e/passkeys.spec.ts

目的: **未記入**

- password-only remembered account and passkey lifecycle ${width}

### frontend/e2e/password-settings.spec.ts

目的: **未記入**

- changes password and revokes the current session without viewport overflow

### frontend/e2e/patents.spec.ts

目的: Patent（特許調査・検討）画面。案件の作成 → 案件のページ（/patents/:id）→ 概要 → ヒアリング → 期限 → 請求項 → AI 支援 → 分析、 一覧の「分析」（グラフ）と「公報」を、PC 幅と 320px で通す。 根拠: 2026-11-21 利用者「PatentBuddy のアドオンは不要。Patent 機能をつけて」、同日「新しい特許の開発フローがフローティング ウィンドウになっている。コントロールデックと統合してほしい」（案件はシートではなくページで開く・戻るで一覧へ）と 「特許の分析をグラフで（推移・書誌・請求項・類似・バブル）」。 検証用インスタンスにはローカルのモデルも公報も無いので、AI 支援は「外部へ送らない」案内、分析は空の案内が出ることを確かめ、 グラフの見た目は分析の API の応答を差し替えて撮る。 ウォッチ・公開データの取得（R3-2）: 検索語は外部へ送られるが、確認のチェックは無く（2026-11-22 に自動の検査へ置き換え）、案件の未公開の文章を 25 文字以上そのまま写した検索語だけをサーバーが止めて言い換えの案を出すこと、 鍵の無い取得先（EPO）は選べないことを、本物の API の取得先一覧で確かめる。外部へは実際には送らない（「いま取得」は押さない）。 AI クレームチャートの判定（R3-3）: 判定の結果（○ △ × ?、引用を確かめられず ? にした行、古い判定の注意）の見え方を、 判定の一覧の応答を差し替えて確かめる。検証用インスタンスにはローカルのモデルが無いので「判定」は押せないこと。 */

- Patent: 案件のページで期限と請求項を扱う（${width}px）
- Patent: 分析のグラフ（応答を差し替え・${width}px）
- Patent: ウォッチは確認のチェックが無く、写した検索語だけ止まる（${width}px）
- Patent: AI クレームチャートの判定の表示（応答を差し替え・${width}px）
- Patent: 公報が無いときの分析と公報の画面（1280px）

### frontend/e2e/persistent-login.spec.ts

目的: **未記入**

- persistent login survives restored browser context (${width}px)

### frontend/e2e/project-lab-rename.spec.ts

目的: 目的: Project Lab のプロジェクト情報で、表示名をその場で書き換えられる（✏️ 名前を変える）。 自動で作られた作業場所には「どこから作られたか」の小さな印（Nexarch・チーム・AI アシスタント）が出る。 根拠: 2026-11-27 利用者「作業場所は既定の作業場所に自動で名前を振ってもらって、後で書き換えられるようにし、 そこを Project Lab からも見られればよくないか？」。API は模擬する。320・1280 で横にはみ出さない。

- project lab: 表示名を書き換えられ、作成元の印が出る（${width}px）

### frontend/e2e/project-lab-web.spec.ts

目的: **未記入**

- starts, previews, and stops an isolated Project Lab Web profile

### frontend/e2e/project-lab-workspaces.spec.ts

目的: 目的: Project Lab の一覧の上の「プロジェクト｜作業場」の切り替えと、作業場のカード（元・開発対象の札・ 進み具合・ToDo・最後に動いた時刻・会話を開く）が正しく出る。AI アシスタントの作業フォルダの選択欄は 「CodeDEV のプロジェクト」と「作業場（一時）」に分かれ、「🧪 ＋ 新しい作業場（ControlDeck を直す・試す）」は選択欄の中の「➕ 新規作成」のすぐ下に出る（利用者の決定。選択欄の外の別のボタンは作らない）。 根拠: 2026-11-22 利用者「Project Lab で現在の進捗とかって確認できないんだったっけ…参照先がテンポラリーな フォルダなら、今あるドロップダウンの一覧とは別にした方がいい」「入口が選択欄のいちばん下に隠れて分からない」。 API は模擬する（箱・OpenCode を動かさない）。320・1280、ライト・ダークの撮影もこの中で行う。

- project lab switches to workspaces and shows progress (${width}px ${scheme})
- assistant folder select separates codedev and workspaces (${width}px ${scheme})
- workspace lab runs, listens and stops (${width}px)

### frontend/e2e/project-lab.spec.ts

目的: **未記入**

- previews artifacts and runs a source file without page scrolling

### frontend/e2e/projectlab-audio.spec.ts

目的: 音声の成果物は再生器を出す。 以前は Centered（文言用の grid）に入れており、place-items-center が中身を内容幅 まで縮めていた。audio は内容幅を持たないので幅 0 になり、携帯では押せなかった。 読み込みは成功していたぶん、見た目だけでは気づけない壊れ方だった。

- an audio artifact gets a player wide enough to press

### frontend/e2e/release-bundle-settings.spec.ts

目的: **未記入**

- manages an installed release bundle and generates through its Add-on

### frontend/e2e/remote-desktop-toggle.spec.ts

目的: 目的: 設定のリモートデスクトップの切り替えで、ナビの「Remote」が消え、/remote に無効の表示が出て、 オンで戻ること（PC 幅 1280 と 320px）。画面の撮影も残す。 根拠: 利用者の依頼（2026-11-21）「リモートデスクトップは一旦停止する。設定に有効・無効のボタンを。 オフになったらホームのアイコンも消す」。検証用インスタンスは xrdp を操作しない（toggle.py の isolated）。

- remote desktop toggle hides and restores the feature at ${viewport.width}px

### frontend/e2e/resource-load-profile-ui.spec.ts

目的: 目的: LLM 共通設定の「Add-on への GPU の譲り方」パネル（方針の説明と連動、載せ直し制御、 譲り合いの目安）がモバイル（320px・横溢れなし）と PC の両幅で出ることを確かめる。 根拠: 退避(yield)機構は 2026-09-04 の 4deb560「画像生成をRAMへ載せてLLMと共存させ、退避(yield)機構を廃止する」で 廃止され、この spec が確かめていた cold/warm 実測・今すぐ退避の UI は GPU Broker パネルごと画面から無くなった。 2026-11-22 の監査で本 spec が main で落ちていることを確認（HANDOFF-opencode-dev-environment 不具合と課題 14）。 保証していた「退避の根拠の説明」は対象の機能が製品から削除されたため立て直せず、後継パネルの説明に読み替えた。

- explains the add-on GPU policy and reload controls at mobile and desktop widths

### frontend/e2e/runtime-preferred-model.spec.ts

目的: **未記入**

- runtime startup preferences can be selected and saved at ${width}px

### frontend/e2e/settings-android-sdk.spec.ts

目的: 目的: 設定の機能と拡張に「Android SDK（コマンドライン）」の行があり、ライセンスへの同意にチェックを入れるまで「導入」を押せず、 押すと同意つきで install-jobs へ送られること（取得は利用者の「導入」のときだけ）。入っていれば入っている部品が出る。 機器のシートでは、Android の機器を渡す設定でホストの adb が動いていれば「ホストの adb が機器を使っています」と 「ホストの adb を止める」が出て、押すと POST が飛ぶ。Android の道具が無ければ「設定で入れる」リンクが出る。 390px・1280px とも横にはみ出さない。 根拠: 2026-11-27 利用者「Android とかにも対応してね」（M5・iOS・Android の開発を OpenCode で）。API は page.route で模擬する （Google から何も取得しない・adb を止めない）。

- 機能と拡張: Android SDK（${width}px）
- 機能と拡張: Android SDK が入っているとき
- 機器のシート: ホストの adb の案内と設定で入れるリンク（${width}px）

### frontend/e2e/settings-keys-research.spec.ts

目的: Settings →「API キー」の「調査（フォーラム・検索）」群: Reddit のキーを保存し、「つながりを確かめる」の結果が 1 行で出る。 根拠: 2026-11-23 利用者「Reddit の API を登録するところを作って、入っていたらそれを使って検索できるように。API の登録のところにまとめて」。 守ること: 群が出る・値は画面に出ない・Reddit に断られたときは「承認」と平易に言う・320px で横にはみ出さない。 API は page.route でモックする（Reddit へは繋がない・本番のキーも変えない）。 */

- Settings: 調査のキー（${width}px）

### frontend/e2e/settings-keys.spec.ts

目的: Settings →「API キー」: 外部のサービスのキーを 1 か所で。値は出さず、設定済みか・末尾の数文字だけ。消すのは確認つき。 根拠: 2026-11-21 利用者「EPO や特許庁の API のキーはどこで設定できる？設定の中にキーをまとめて。OpenRouter のキーも。消さないように」。 */

- Settings: API キー（${width}px）

### frontend/e2e/settings-news-cpu-llm.spec.ts

目的: 設定 → ニュースの「下ごしらえの使うモデル」（既定は CPU の Gemma 4 E2B）と、機能と拡張の「ニュースの下ごしらえ（CPU の LLM）」。API は page.route で模擬する（モデルは取得しない）。 根拠: 2026-11-24 利用者「基本的に CPU で動かした Gemma の小を使う。生成クオリティの問題なかったということなので、それを既定に設定する」。 撮影は test-results/settings-news-cpu-llm-{320,1280}.png・settings-news-cpu-llm-feature-{320,1280}.png。 */

- ニュースの下ごしらえ: 既定は CPU の Gemma（${width}px）
- 機能と拡張: ニュースの下ごしらえ（CPU の LLM）の導入（${width}px）

### frontend/e2e/settings-news-hub.spec.ts

目的: 設定 → ニュースの「QNAP ニュースハブとつなぐ」: 出どころ・つなぐ（指紋の見比べ）・鍵を渡す・診断・つなぎを切る。API は page.route で模擬する（ハブへは通信しない）。 根拠: 2026-11-25 利用者「ControlDeck に QNAP ニュースハブとつなぐ機能」。指紋を利用者が見比べるまで使い始めない／トークン・鍵を画面に出さない／320 で崩れない。 */

- ニュースハブ: つなぐ → 指紋の確認 → 診断（${width}px）
- ニュースハブ: 同期の失敗が診断に出る・違う指紋は破棄できる

### frontend/e2e/settings-news-imagegen-tiers.spec.ts

目的: 設定 → ニュースの「モデルの精度」（小・中・大）と、機能と拡張の段ごとの導入ボタン。API は page.route で模擬する（モデルは取得しない）。 根拠: 2026-11-23 利用者「もうちょい精度の高いモデル使えないか？…このモデルを残しておいて、小中大みたいな感じで、選べるようにしてほしい、設定で」。 */

- ニュースの画像生成: 精度の選択（${width}px）
- ニュースの画像生成: 機能と拡張で段ごとに導入（${width}px）

### frontend/e2e/settings-playwright-browsers.spec.ts

目的: 機能と拡張: 「Playwright のブラウザ」の項目と導入ボタン、OS のパッケージ（sudo が要る）はコピーできるコマンドだけ。 API は page.route で模擬する（実際には Playwright のブラウザを取得しない・sudo を実行しない）。 根拠: 2026-11-23 利用者「設定の機能の追加のところに導入ボタンを」「追加で入れないといけないものについては導入ボタンを」。 */

- 機能と拡張: Playwright のブラウザ（${width}px）

### frontend/e2e/settings-search-tools.spec.ts

目的: 機能と拡張の「検索と収集」: Web 本文抽出（Trafilatura）の導入ボタン、取得先の設定（特許庁 API を含む）への入口、 分類ごとの「まとめて導入」。導入・まとめて導入は PyPI / npm から取得するので、ここでは押さない（見える・押せることまで）。 根拠: 2026-11-21 利用者「必要なコードやプラグインは、機能と拡張に導入ボタンと更新ボタンを付けて、個別管理・一括導入・更新」 「特許庁などからデータをダウンロードする API の設定はどこ？見当たらない」。 */

- 機能と拡張: 検索と収集（${width}px）

### frontend/e2e/sly-radiance.spec.ts

目的: 目的: Models の「モデル登録」に Sly-Radiance が出て、目録から取得（HF の取得 Job）→ 取得したフォルダがフォームへ入る、 フォルダの中身（safetensors の数・量子化）が見えて量子化の切り替えを提案する、登録の本文が API の形（同時に答える数 1〜8・ 先読み・prefill chunk・追加の環境変数）で送られる、ことを守る。320px と 1280px で横にあふれない。API はすべてモック。 根拠: 2026-11-27 利用者「Sly-Radiance を Radiance と同様のメニュー・導入方法で、目録からの取得・フォルダ選択・並列の設定も」。 AGENTS.md「不具合・診断・テストの決まり」（目的と根拠）。

- Sly-Radiance の登録: 目録から取得してフォルダを入れ、設定を送る（${width}px）
- Sly-Radiance は Radiance のカードの中のスイッチ（320px）
- Sly-Radiance の個別設定を保存できる（表示用の項目を送らない）

### frontend/e2e/speech-engines.spec.ts

目的: Models の「TTS / ASR」タブと、設定の機能追加の「音声（TTS / ASR）」。 目的: ASR と TTS が 1 つのタブに並び、使うエンジンを選べ、320px でも横にはみ出さないこと。 Supertonic 3 が導入済みなら、試し聞きで本物の WAV が返ること。 根拠: 2026-11-19 利用者「TTS と ASR は同じ項目として追加。各 TTS / ASR の利用するエンジンを選択できるように」。 */

- TTS / ASR tab lists engines and switches them at ${width}px
- Supertonic 3 is offered under 音声 in the feature list
- CPU rerankers are installed and chosen in Models > Embed / Reranker

### frontend/e2e/strata-catalog.spec.ts

目的: Strata のモデル登録画面が backend の目録 API（GET /api/v1/models/strata/catalog）から 全系列・全量子化を読み、1280px と 320px の両方で崩れずに選べることを確かめる。 量子化の選択肢には id に加えてダウンロード目安・メモリ目安・「載る／ぎりぎり／載らない／不明」が出る。 手作業が要る orca は選べない案内表示になり、Inkling では 0.2.22 の思考欄が残る。 目録の取得失敗時は alert を出して保存ボタンを無効にする。 根拠: 2026-11-26 の指示書 M1（目録を backend の 1 か所に置き、画面は API から読む。Strata 0.2.22 の 全系列・全量子化を登録画面に出す）。AGENTS.md「不具合・診断・テストの決まり」（目的と根拠を書いてから足す） と UI ルール（320px・ダーク/ライト・等幅数字・日本語）。 API は page.route でモックし、外部（HF・npm・GitHub）へ取りに行かない。

- Strata catalog shows all families and quantizations at ${width}px
- Strata catalog failure shows an alert and disables save at 1280px

### frontend/e2e/strata-memory-settings.spec.ts

目的: **未記入**

- Strata memory planning and settings at ${width}px
- Strata registration offers Inkling 2 Mini with thinking and without Qwen-only memory settings

### frontend/e2e/terminal-attachment.spec.ts

目的: **未記入**

- the image button sits between → and ^C and inserts the stored path

### frontend/e2e/terminal-automation.spec.ts

目的: **未記入**

- snippet review, detached run, durable schedule, and per-session entry stay usable

### frontend/e2e/terminal-copy.spec.ts

目的: **未記入**

- terminal text can be selected and copied (${width}px)

### frontend/e2e/terminal-ime.spec.ts

目的: **未記入**

- mounts over HTTP when crypto.randomUUID is unavailable
- initial terminal connection reveals one stable frame without viewport racing
- shows the close action at the right edge with a 44px touch target
- pastes directly, swipes up to Copy, and helper keys keep the keyboard closed
- swipes the live terminal body through real tmux scrollback
- keeps the active input line above the resized mobile viewport
- echoes and deletes mobile keystrokes without per-key rendering stalls
- reconnects a large existing tmux history without revealing intermediate scroll frames
- jumps and drags with the overlay history bar without opening terminal input
- delivers 100KB, 300KB and UTF-8 paste without loss
- coalesces keyboard geometry event bursts
- keyboard open and close ten times keeps one websocket and no history replay
- reconnect resumes only missing journal output without resetting scrollback
- journal range miss falls back to one bounded reset
- full page reload creates a new client and performs initial replay
- server reload keeps replay hidden and stable after the final frame
- session switch never mixes the previous terminal history
- keeps cell geometry locked while the shell follows the keyboard during composition
- holds FIFO input until matching ACK and local resize commit
- requeues pending resize input on connection generation reset
- does not create a resize barrier for position-only or unchanged geometry
- keeps one placeholder in matching buffer and DOM rows
- keeps terminal screen above the single-line helper bar
- keeps writes and controller resources bounded across ten keyboard cycles
- optional ten-minute IME and remount soak
- desktop copy and remount keep one terminal instance

### frontend/e2e/terminal-input.spec.ts

目的: **未記入**

- keeps and ACKs a 300KB UTF-8 paste without queue limits
- reconnect resends the same unACKed sequence and ignores stale ACK
- matches xterm newline and bracketed paste semantics

### frontend/e2e/terminal-scrollback.spec.ts

目的: **未記入**

- normal terminal retains long scrollback after reload (${width}px)

### frontend/e2e/terminal-session-overview.spec.ts

目的: **未記入**

- Terminal overview identifies the foreground program, directory, and live state

### frontend/e2e/terminal-v2-lab.spec.ts

目的: **未記入**

- uses V2 only for this tab

### frontend/e2e/totp-policy.spec.ts

目的: **未記入**

- required TOTP policy confines navigation until enrollment

### frontend/e2e/update-history.spec.ts

目的: 目的: 本体の自動更新が動いたことに気づけること。何から何へ更新するのかを Release の名前で出すこと。 - 設定 → 更新とバックアップに履歴（時刻・自動/手動・版・成否）と、待っている理由が出る - 自動で更新した後、次に画面を開いたら一度だけ知らせ、もう一度開いても出さない 320px でも横にはみ出さない。 根拠: 2026-11-20 利用者「自動更新が動いていない気がする。気づいてないだけ？」。実際は自動 38 回動いていたが、 通知先が無く画面にも出ないので気づけなかった。API は模擬する。

- update history and one-time auto update notice (${width}px)

### frontend/e2e/ux-swipe-and-metrics-gap.spec.ts

目的: 目的: - 携帯幅で、左から右へのフリックでメニューが開き、右から左で閉じる。設定 → 操作性で切ると働かない。 縦のスクロール・横にスクロールできる所（設定のカテゴリの並び）で始めた指では開かない。 - 題名を押すと本文の先頭へ戻る。 - ホームのグラフ: 受け取りが途切れたら（画面を閉じていた間）、サーバーの履歴を読み直して埋める。 v0.98 の後: 真ん中からゆっくり引いても指に付いて開く。左右の端の指は iPhone の「戻る」「進む」にさせない。 根拠: 2026-11-20 利用者「左から右へフリックでメニューを出し、右から左で閉じたい。オンオフは設定で」 v0.98 の後「Claude は真ん中から右へ引いても出る。いまは左端からだと戻る、右端からだと進むになる」 「ブラウザを閉じてまた開くと、閉じていた間のグラフが更新されない」。

- flick opens and closes the menu, and can be turned off
- tapping the title scrolls back to the top
- home graph reloads server history after the stream was interrupted

### frontend/e2e/vram-margin.spec.ts

目的: **未記入**

- common VRAM margin persists at ${width}px

### frontend/e2e/web-push.spec.ts

目的: 目的: - 設定 → 通知 の「この端末で受け取る」で、ブラウザの購読をサーバーへ登録し、一覧に端末が出る。止めると消える。 - Service Worker が push を受けたら通知を出し、押したら中の url（実行結果など）を開く。 push service は外にあるので、購読は差し替え、push は Chromium の DevTools から直接 Service Worker へ届ける。 - push を受けたら、番号を添えて「受け取った」をサーバーへ返す（2026-11-21「届いていない気がする」）。 根拠: 2026-11-21 利用者の依頼（通知の片方向を塞ぐ）。本番は通知先が 0 件で、何も届いていなかった。

- subscribes this device, shows it, and the service worker shows pushed notifications
- opening a notification link selects the run in Project Lab

### frontend/e2e/workflow-business-event.spec.ts

目的: **未記入**

- publishes a durable workflow event and receives its typed payload at desktop and 320px

### frontend/e2e/workflow-control-nodes.spec.ts

目的: **未記入**

- merges branches and resumes a typed human approval at mobile width
- edits durable delay, try branches, and a system trigger at 320px

### frontend/e2e/workflow-data-nodes.spec.ts

目的: **未記入**

- deterministic data nodes execute and appear in the mobile library

### frontend/e2e/workflow-durable-cache.spec.ts

目的: **未記入**

- durable cache is shared by published runs and configurable on mobile

### frontend/e2e/workflow-durable-pause.spec.ts

目的: **未記入**

- public runner edits and resumes a durable approval on mobile
- public runner submits a durable typed human form

### frontend/e2e/workflow-durable-queue.spec.ts

目的: **未記入**

- durable queue survives published runs and remains usable on mobile

### frontend/e2e/workflow-durable-state.spec.ts

目的: **未記入**

- durable typed state is shared by published runs and configurable on mobile

### frontend/e2e/workflow-event-stream.spec.ts

目的: **未記入**

- Execution Debugger follows the durable event stream at mobile and desktop widths

### frontend/e2e/workflow-guided-config.spec.ts

目的: **未記入**

- uses recommended settings and inserts a typed upstream variable at the cursor on mobile

### frontend/e2e/workflow-intelligence.spec.ts

目的: **未記入**

- diagnoses, previews and selectively applies a workflow patch at 320 and 1280px

### frontend/e2e/workflow-large-flow.spec.ts

目的: **未記入**

- groups, searches, lays out, undoes, and autosaves a 100-node flow
- opens and navigates a 500-node definition without rendering the full canvas
- stops autosave and offers recovery when another editor wins

### frontend/e2e/workflow-preview.spec.ts

目的: **未記入**

- shows the exact publish blocker instead of a generic 409
- integrates trigger input, safe preview, test result, and past input at 320px

### frontend/e2e/workflow-resilience.spec.ts

目的: **未記入**

- batches input and enforces durable rate and circuit control at desktop and 320px

### frontend/e2e/workflow-runner.spec.ts

目的: **未記入**

- runs a published workflow without exposing its canvas at mobile and desktop widths
- approves a published workflow from the mobile public app

### frontend/e2e/workflow-samplebook-phase6.spec.ts

目的: **未記入**

- previews a runnable differentiator sample and exposes complete node docs

### frontend/e2e/workflow-subflow-map.spec.ts

目的: **未記入**

- maps typed items through one pinned published subflow at desktop and 320px

