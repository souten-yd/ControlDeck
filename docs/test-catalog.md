# テストの目録

`./deck.sh test-catalog` が作る（手で書き換えない）。テストを足す前にここを検索し、同じ観点のテストがあれば
足さずにそれを直す・広げる。決まりは `AGENTS.md` の「不具合・診断・テストの決まり」。

- backend: 169 ファイル、1478 テスト（目的の未記入 93 ファイル）
- E2E: 79 spec、146 テスト

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
- `test_the_cpu_fan_is_only_reported_when_something_names_it`
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

### backend/tests/test_assistant_planner.py

目的: **未記入**

- `test_rule_plan_combines_web_and_academic`
- `test_llm_plan_uses_structured_json`
- `test_invalid_llm_plan_falls_back_to_chat`
- `test_route_api_returns_validated_plan`
- `test_send_rejects_mismatched_plan`
- `test_research_combines_sources_and_deduplicates`
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

### backend/tests/test_chat_references.py

目的: 会話内文献ID、解決API、選択的コンテキスト注入のテスト。

- `test_reference_registry_uses_short_base36_ids_and_deduplicates`
- `test_send_expands_only_explicitly_requested_references`
- `test_reference_parser_accepts_compact_user_notation`

### backend/tests/test_chatflow.py

目的: **未記入**

- `test_trigger_injects_chat_input`
- `test_signal_display_node`
- `test_chatflow_end_to_end` — trigger(chat) -> signal.display で入力メッセージが表示信号に流れる。

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

### backend/tests/test_code_research.py

目的: OpenCode に渡す前に ControlDeck の調査で調べる（chat_persist._research_for_code）。 目的: 2026-11-18 利用者の依頼「『調べて OpenCode に渡す』をボタンを押さずに」。OpenCode の会話で「〜を調べて反映して」と 送ると、同じ 1 回の送信の中で調べ、出典つきの根拠を OpenCode への依頼に付ける。 - 調べた根拠は見出しを付けて返し、回答の meta は OpenCode のまま（道具の一覧は同じもの）。出典と調べた経過は残す - 量は上限まで（引き継ぎと合わせて OpenCode のコンテキストを超えない） - 調べられなくても OpenCode の作業は止めない（空を返し、meta を元に戻す）

- `test_research_for_code_returns_evidence_and_keeps_the_opencode_answer` — 「調べて反映して」: 調べた根拠を OpenCode への依頼に付け、回答の meta は OpenCode のまま（出典は残す）。

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

### backend/tests/test_dev_sandbox.py

目的: 開発用の軽量サンドボックス（./deck.sh sandbox）。podman は呼ばず、引数と後始末を確かめる。

- `test_create_clones_without_touching_the_source`
- `test_names_are_validated`
- `test_podman_mounts_only_the_box_writable`
- `test_opencode_plugins_visible_readonly_without_exposing_other_features`
- `test_github_config_only_when_allowed`
- `test_destroy_stays_inside_the_sandbox_root`
- `test_mirror_config_points_inside_the_box`
- `test_opencode_cannot_stop_production_through_deck_sh`

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

### backend/tests/test_fan_two_stage.py

目的: 実機の記録に当てはめた 2 段の熱の模型で、ファン制御がマージン 5°C で緊急の最大回転に入らないことを確かめる。 目的: 2026-11-18 利用者「温度がオーバーした」「模擬で確かめてマージン 5 度でゲイン調整できると良い」。 本番（v0.68）では、負荷が止まると回転を急に下げ、再開で 95°C に届いて緊急の最大回転に入り、その後に上乗せが 膨らんで回り続けた。hotspot の読みは全力中でも 1 秒だけ +3〜+7°C 跳ねる。 fan_core の簡単な模型（瞬間の跳ね＋1 段）は直す前の版でも最高 90.7°C で本番を再現できなかったので、 本番の 1 秒ごとの記録に「放熱板（遅い・回転で冷え方が変わる）＋ダイ（速い）」の模型を当てはめた （hotspot の誤差 1.3°C。tests/fixtures/fan_r9700_two_stage.json）。この模型と実際の揺れの分布で、直す前の版は 本番と同じく最高 98°C・緊急 5 回を再現し、直した版は 0 回。 - 記録した実際の電力の流れ・全力と 20〜60 秒の休みの繰り返し・休みから全力を、外気温そのまま／+10°C で流す - 上乗せは本番と同じく育った状態から（全力 15 分の慣らし） - 緊急の最大回転 0 回、全力中の平均が目標 90°C の ±2.5°C

- `test_margin_five_holds_without_emergency_on_the_fitted_r9700_model`

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

### backend/tests/test_jobs_stream_cleanup.py

目的: **未記入**

- `test_cancelled_stream_joins_update_and_disconnect_waiters`

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
- `test_think_normalize_and_config` — think の正規化と個別設定の保存/クリア。
- `test_native_base_helper`

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
- `test_code_mode_records_tools_and_permissions_and_answers_them`
- `test_permission_mode_api_saves_and_applies_to_running_servers`
- `test_run_turn_passes_child_permissions_and_survives_a_reconnect`
- `test_running_servers_are_not_reaped`
- `test_code_mode_rejects_a_second_message_while_working_and_never_stays_generating`
- `test_japanese_project_directory_is_sent_url_encoded` — 日本語のプロジェクト名でも、ヘッダーを送れる（ASCII だけ）。OpenCode は decodeURIComponent で戻す。

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

### backend/tests/test_passkeys.py

目的: **未記入**

- `test_real_signature_login_single_use`
- `test_invalid_proof`
- `test_registration_password_uv_session_replay`
- `test_origin_csrf_limit_delete`
- `test_totp_enrollment_cannot_be_bypassed`
- `test_another_user_cannot_list_or_delete_key`

### backend/tests/test_paths.py

目的: **未記入**

- `test_inside_allowed`
- `test_traversal_rejected`
- `test_symlink_escape_rejected`
- `test_prefix_sibling_rejected`
- `test_secret_masking`

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
- `test_rag_invalid_collection`
- `test_db_query_sqlite`
- `test_db_query_rejects_non_dml`
- `test_db_query_sqlite_path_must_be_in_root`
- `test_workflow_api_accepts_rag_db_nodes`

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

### backend/tests/test_runtime_policy.py

目的: **未記入**

- `test_runtime_policy_roundtrip`
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

### backend/tests/test_searxng_helper.py

目的: SearXNG 連携ヘルパーのテスト。

- `test_resolve_url_defaults_to_local`
- `test_default_url_autodetects_registered_app` — 管理アプリ「SearXNG」の web_port から既定 URL を自動検出する。
- `test_is_local_detection`
- `test_ensure_running_noop_for_remote_and_unregistered` — リモート URL は何もしない。未登録環境でも例外を出さない（検索側のエラーに任せる）。
- `test_idle_check_stops_regardless_of_starter` — 起動元を問わずアイドル停止する。利用時刻不明時は猶予を仕切り直す。

### backend/tests/test_session_lifetime.py

目的: **未記入**

- `test_default_persists_after_idle_and_logout_revokes`
- `test_legacy_session_migrates_only_when_still_valid`
- `test_finite_timeout_still_renews_and_expires`
- `test_reenabling_timeout_applies_to_persistent_sessions`
- `test_revocation_and_disabled_user_reject_persistent_session`
- `test_secure_cookie_on_login`
- `test_negative_timeout_rejected`

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

### backend/tests/test_strata_gateway.py

目的: Strata must not download a model or alter the active runtime by default.

- `test_status_parses_upstream_json_even_when_human_lines_follow`
- `test_auto_load_refuses_first_download`
- `test_explicit_start_uses_fixed_cli_args_and_records_activation`
- `test_cannot_register_untrusted_family_or_injected_alias`
- `test_unsloth_flash_next_sizes_are_registrable`
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

### backend/tests/test_vram_margin.py

目的: Common headroom survives saves and reaches the native engine interfaces.

- `test_validation_and_rounding`
- `test_margin_save_does_not_stop_or_reconfigure_running_models`
- `test_maya_native_flags_and_decreasing_margin`
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
- `test_scrape_selector`
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

### frontend/e2e/assistant-claude-layout.spec.ts

目的: **未記入**

- history list: groups, search, open, rename, delete, new chat (${width}px)
- mode chip menu in the composer (${width}px)
- a chat can be handed off to OpenCode from the plus menu in the same conversation
- an archive is attached for OpenCode, switches to OpenCode and is sent with the message
- a chat answer is handed to OpenCode by just asking to implement it
- a question that only mentions web apps stays in chat
- in an OpenCode conversation, asking to look something up and apply it researches first
- in an OpenCode conversation, an ordinary request stays with OpenCode
- in an OpenCode conversation, a plain question is answered in chat
- in an OpenCode conversation, an image goes to the chat that can see it
- failed and canceled answers show their status, the error and a retry
- standalone iPhone: top bar and composer stay inside the screen (${viewport.width}px)
- drawer and settings are flat on a phone
- standalone iPhone: shell fits the shortened web view and drops the bottom safe area
- history rows swipe right to archive and left to delete, with undo
- standalone iPhone: with the status bar outside the page the bottom safe area stays

### frontend/e2e/assistant-message-actions.spec.ts

目的: **未記入**

- message menu forks, deletes and edits (${width}px)
- attachment chip shows upload progress

### frontend/e2e/assistant-mobile.spec.ts

目的: **未記入**

- assistant input stays flush in standalone iPhone viewports
- shows compact conversation references and inserts one into the prompt

### frontend/e2e/background-return.spec.ts

目的: 携帯で背面に回して戻すと「再接続中」が居座っていた。 戻ったときは socket の readyState を信用せず、必ず繋ぎ直す。 */

- returning from the background reconnects quickly

### frontend/e2e/blank-screen.spec.ts

目的: **未記入**

- settings loads repeatedly without blanking
- a throwing page shows a message instead of a blank screen
- a feature turning off redirects instead of rebuilding the router

### frontend/e2e/client-id.spec.ts

目的: **未記入**

- uses randomUUID when available
- uses getRandomValues when randomUUID is unavailable
- works without crypto and satisfies the backend contract
- generates 1000 unique identifiers without crypto

### frontend/e2e/dashboard-fans.spec.ts

目的: 目的: ホームのシステムサマリーで、CPU・GPU のファン回転（rpm）が数値で表示され、 320px（iPhone の最小幅）と PC 幅のどちらでも横にはみ出さないこと。 根拠: ファン回転をサマリーのカードに入れたとき（モバイルで横スクロールが出た）。2026-11-17 のファン制御 v2 で 「FAN 1,500 RPM」の文字表記がアイコン＋「1500 RPM」になっていたのに合わせて書き直した。 ファンの値は実機のセンサー次第（無ければ出さない）なので、出ている分の形式と幅だけを確かめる。

- dashboard shows CPU and GPU fan values without mobile overflow

### frontend/e2e/diagnostics.spec.ts

目的: 目的: 診断の画面が実データで開き、要の欄が出て、レポートに秘密（メールアドレス）が出ないこと。 根拠: 2026-11-17 診断の追加。同日、携帯でカードが右へはみ出した（ページの横幅だけ見ていて見逃した）。

- diagnostics page shows self-test, restarts and a redacted report (${width}px)

### frontend/e2e/fan-temperature-margin.spec.ts

目的: 目的: ファン制御 v2 の設定画面で、利用者が決める最小限の値（上限温度・マージン）を編集して保存でき、 方式の選択・温度マップ・プリセットが無く（PID だけ）、推移に各部の温度とファンの回転数（rpm）が出て、 自動調整を確認してから始められること。iPhone 幅（320px）でも横にはみ出さないこと。 根拠: 2026-11-17 利用者の依頼「設定が多すぎて分からない・入れ子・プリセットが謎」で画面を作り直し、 2026-11-18 実機の比較で PID だけを残した（「いい方式のみ採用」「調整していないプリセットは消す。 デフォルトは残す」「回転数と各部の温度のグラフを出す」）。旧画面の「プリセットの保存・削除」と 「温度追従のマージン」の確認はこのファイルで置き換えた。 API は page.route で模擬する（実機の GPU とファンを動かさない）。

- fan limits and margin are edited and saved at ${width}px
- only the PID control is offered and the history shows each temperature and the fan rpm
- deviations from the log are shown and the controller is rebuilt from the log in one click
- auto tuning asks for confirmation, then shows its stage and a cancel button

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

### frontend/e2e/llama-runtime-settings.spec.ts

目的: **未記入**

- llama.cpp instance save excludes read-only status fields
- Ollama running state updates the indicator and lifecycle button

### frontend/e2e/log-redaction.spec.ts

目的: **未記入**

- never renders application log secrets at mobile or desktop widths

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

### frontend/e2e/model-provider-common.spec.ts

目的: **未記入**

- uses the common provider routes for pull and model configuration

### frontend/e2e/navigation-layout.spec.ts

目的: **未記入**

- uses one page-title layout without horizontal overflow
- keeps Workflows below the logo with an iPhone standalone safe area
- migrates the saved Play destination to Workflows
- pins up to six favorites at the top of the drawer

### frontend/e2e/opencode-focus.spec.ts

目的: 画面を開いただけで software keyboard が上がらないこと。 以前は入力欄に autoFocus が付いていて、OpenCode を開くだけで keyboard が せり上がり、プロジェクト一覧も開始ボタンも隠れていた。v1/v2 は同じ コンポーネントなので両方の route で縛る。 */

- ${path}: 開いただけでは入力欄へフォーカスが入らず、タップで入る

### frontend/e2e/opencode-resume.spec.ts

目的: **未記入**

- closed OpenCode session can be resumed (${width}px)

### frontend/e2e/opencode-scroll.spec.ts

目的: **未記入**

- scrolling: ${label}

### frontend/e2e/passkeys.spec.ts

目的: **未記入**

- password-only remembered account and passkey lifecycle ${width}

### frontend/e2e/password-settings.spec.ts

目的: **未記入**

- changes password and revokes the current session without viewport overflow

### frontend/e2e/persistent-login.spec.ts

目的: **未記入**

- persistent login survives restored browser context (${width}px)

### frontend/e2e/project-lab-web.spec.ts

目的: **未記入**

- starts, previews, and stops an isolated Project Lab Web profile

### frontend/e2e/project-lab.spec.ts

目的: **未記入**

- previews artifacts and runs a source file without page scrolling

### frontend/e2e/projectlab-audio.spec.ts

目的: 音声の成果物は再生器を出す。 以前は Centered（文言用の grid）に入れており、place-items-center が中身を内容幅 まで縮めていた。audio は内容幅を持たないので幅 0 になり、携帯では押せなかった。 読み込みは成功していたぶん、見た目だけでは気づけない壊れ方だった。

- an audio artifact gets a player wide enough to press

### frontend/e2e/release-bundle-settings.spec.ts

目的: **未記入**

- manages an installed release bundle and generates through its Add-on

### frontend/e2e/resource-load-profile-ui.spec.ts

目的: **未記入**

- explains cold bootstrap, warm threshold, and suppression at mobile and desktop widths

### frontend/e2e/runtime-preferred-model.spec.ts

目的: **未記入**

- runtime startup preferences can be selected and saved at ${width}px

### frontend/e2e/strata-memory-settings.spec.ts

目的: **未記入**

- Strata memory planning and settings at ${width}px

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

### frontend/e2e/vram-margin.spec.ts

目的: **未記入**

- common VRAM margin persists at ${width}px

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

