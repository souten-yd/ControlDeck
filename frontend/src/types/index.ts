export interface UserInfo {
  id: number;
  username: string;
  display_name: string;
  role: string;
  permissions: string[];
  totp_enabled: boolean;
}

export type AppStatus =
  | "STOPPED"
  | "STARTING"
  | "RUNNING"
  | "STOPPING"
  | "RESTARTING"
  | "FAILED"
  | "DEGRADED"
  | "UNKNOWN";

export interface AppRuntime {
  status: AppStatus;
  pid: number | null;
  uptime_seconds: number | null;
  started_at: string | null;
  restart_count: number;
  cpu_percent: number | null;
  memory_bytes: number | null;
}

export interface ManagedApp {
  id: number;
  name: string;
  description: string;
  application_type: string;
  icon_path: string | null;
  working_directory: string | null;
  executable_path: string | null;
  script_path: string | null;
  python_path: string | null;
  arguments: string[];
  environment_masked: Record<string, string>;
  auto_start: boolean;
  restart_policy: string;
  stop_timeout_seconds: number;
  systemd_unit_name: string;
  created_at: string;
  updated_at: string;
  runtime: AppRuntime;
  env_warnings: string[];
}

export interface MetricsSnapshot {
  timestamp: string;
  cpu: {
    percent: number;
    per_cpu: number[];
    load: number[];
    freq_mhz: number | null;
    temperature_c: number | null;
    cores: number;
  };
  memory: {
    total: number;
    used: number;
    available: number;
    percent: number;
    swap_total: number;
    swap_used: number;
    swap_percent: number;
  };
  gpu: {
    name: string;
    utilization_percent: number | null;
    vram_used_bytes: number | null;
    vram_total_bytes: number | null;
    temperature_c: number | null;
    hotspot_c: number | null;
    power_watts: number | null;
    power_cap_watts: number | null;
  } | null;
  io: {
    disk_read_bps: number;
    disk_write_bps: number;
    net_rx_bps: number;
    net_tx_bps: number;
  };
  power: {
    cpu_watts_estimated: number | null;
    gpu_watts: number | null;
    total_watts_estimated: number | null;
    is_estimate: boolean;
  };
  uptime_seconds: number;
}

export interface HostInfo {
  hostname: string;
  os: string;
  kernel: string;
  boot_time: string;
  uptime_seconds: number;
  time: string;
  timezone: string;
}

export interface Meta {
  app_name: string;
  accent_color: string;
  default_theme: string;
  metric_refresh_seconds: number;
}
