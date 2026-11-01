import { callable } from "@decky/api";

export type DisplayMode = "off" | "anchored" | "anchored_recenter" | "follow" | "head_locked" | "other";

export interface DriverConfig {
  disabled: boolean;
  output_mode: string;
  external_mode: string;
  display_size: number;
  display_distance: number;
  sideview_position: string;
  virtual_display_smooth_follow_enabled: boolean;
  sideview_smooth_follow_enabled: boolean;
  sideview_follow_threshold: number;
  curved_display: boolean;
  multi_tap_enabled: boolean;
  smooth_follow_track_roll: boolean;
  smooth_follow_track_pitch: boolean;
  smooth_follow_track_yaw: boolean;
  neck_saver_horizontal_multiplier: number;
  neck_saver_vertical_multiplier: number;
  dead_zone_threshold_deg: number;
  look_ahead: number;
  sbs_content: boolean;
  sbs_mode_stretched: boolean;
}

export interface FeatureStatus {
  enabled: boolean;
  trial: boolean;
  days_left: number | null;
}

export interface DriverStatus {
  driver_running: boolean;
  breezy_installed: boolean;
  config_exists: boolean;
  connected_device_brand: string | null;
  connected_device_model: string | null;
  connected_device_full_distance_cm: number;
  connected_device_full_size_cm: number;
  connected_device_pose_has_position: boolean;
  calibration_state: string | null;
  sbs_mode_supported: boolean;
  sbs_mode_enabled: boolean;
  firmware_update_recommended: boolean;
  features: Record<string, FeatureStatus>;
}

export type ProfileValues = Partial<DriverConfig>;

export interface Store {
  presets: Record<string, ProfileValues & { indep?: Record<string, unknown> }>;
  game_profiles: Record<string, { name: string; values: ProfileValues & { indep?: Record<string, unknown> } }>;
  per_game_enabled: boolean;
}

export const getStatus = callable<[], DriverStatus>("get_status");
export const getConfig = callable<[], DriverConfig>("get_config");
export const setConfig = callable<[changes: Partial<DriverConfig>], DriverConfig | null>("set_config");
export const sendControl = callable<[flags: Record<string, unknown>], boolean>("send_control");
export const restartDriver = callable<[], boolean>("restart_driver");
export const getStore = callable<[], Store>("get_store");
export const savePreset = callable<[name: string, values: ProfileValues & { indep?: object }], Store>("save_preset");
export const deletePreset = callable<[name: string], Store>("delete_preset");
export const saveGameProfile = callable<[appid: string, name: string, values: ProfileValues & { indep?: object }], Store>("save_game_profile");
export const deleteGameProfile = callable<[appid: string], Store>("delete_game_profile");
export const setPerGameEnabled = callable<[enabled: boolean], Store>("set_per_game_enabled");

/** Keys captured in presets / per-game profiles. */
export const PROFILE_KEYS: (keyof DriverConfig)[] = [
  "disabled",
  "output_mode",
  "external_mode",
  "display_size",
  "display_distance",
  "sideview_position",
  "virtual_display_smooth_follow_enabled",
  "sideview_smooth_follow_enabled",
  "sideview_follow_threshold",
  "curved_display",
  "smooth_follow_track_roll",
  "smooth_follow_track_pitch",
  "smooth_follow_track_yaw",
  "neck_saver_horizontal_multiplier",
  "neck_saver_vertical_multiplier",
  "dead_zone_threshold_deg",
  "look_ahead",
];

export function snapshot(config: DriverConfig): ProfileValues {
  const out: ProfileValues = {};
  for (const key of PROFILE_KEYS) {
    (out as Record<string, unknown>)[key] = config[key];
  }
  return out;
}

export function modeFromConfig(c: DriverConfig | null): DisplayMode {
  if (!c || c.disabled) return "off";
  if (c.output_mode !== "external_only") return "other";
  if (c.external_mode === "virtual_display") {
    return c.virtual_display_smooth_follow_enabled ? "anchored_recenter" : "anchored";
  }
  if (c.external_mode === "sideview") {
    return c.sideview_smooth_follow_enabled ? "follow" : "head_locked";
  }
  return "off";
}

export function configForMode(mode: DisplayMode): Partial<DriverConfig> {
  switch (mode) {
    case "off":
      return { disabled: true };
    case "anchored":
      return { disabled: false, output_mode: "external_only", external_mode: "virtual_display", virtual_display_smooth_follow_enabled: false };
    case "anchored_recenter":
      return { disabled: false, output_mode: "external_only", external_mode: "virtual_display", virtual_display_smooth_follow_enabled: true };
    case "follow":
      return { disabled: false, output_mode: "external_only", external_mode: "sideview", sideview_smooth_follow_enabled: true };
    case "head_locked":
      return { disabled: false, output_mode: "external_only", external_mode: "sideview", sideview_smooth_follow_enabled: false };
    default:
      return {};
  }
}

// ------------------------------------------------------------ independent driver
export type DriverChoice = "xr_gaming" | "independent";
export type IndepMode = "off" | "anchored" | "follow" | "head_locked";

export interface IndepConfig {
  mode: IndepMode;
  size: number;
  distance: number;
  curved: boolean;
  follow_threshold_deg: number;
  follow_speed: number;
  follow_yaw: boolean;
  follow_pitch: boolean;
  look_ahead_ms: number;
  debug_outline: boolean;
  roll_trim_deg: number;
  gentle_centering: boolean;
  ambient_light: boolean;
  ambient_intensity: number;
  ambient_size: number;
}

export interface IndepStatus {
  running: boolean;
  installed: boolean;
  glasses?: boolean;
  gamescope?: boolean;
  effect?: boolean;
  following?: boolean;
  mode?: IndepMode;
}

export const getDriverChoice = callable<[], DriverChoice>("get_driver_choice");
export const setDriverChoice = callable<[choice: DriverChoice], DriverChoice>("set_driver_choice");
export const getIndepConfig = callable<[], IndepConfig>("get_indep_config");
export const setIndepConfig = callable<[changes: Partial<IndepConfig>], IndepConfig>("set_indep_config");
export const indepRecenter = callable<[], boolean>("indep_recenter");
export const getIndepStatus = callable<[], IndepStatus>("get_indep_status");

/** Presets / game profiles hold XR Gaming settings plus an `indep` block for the independent driver. */
export type SavedProfile = ProfileValues & { indep?: Partial<IndepConfig> };

export async function captureProfile(): Promise<SavedProfile> {
  const [xr, indep] = await Promise.all([getConfig(), getIndepConfig()]);
  return { ...snapshot(xr), indep };
}

export async function applyProfile(values: SavedProfile): Promise<void> {
  const { indep, ...xr } = values;
  const driver = await getDriverChoice();
  if (driver === "independent") {
    if (indep) await setIndepConfig(indep);
  } else if (Object.keys(xr).length) {
    await setConfig(xr);
  }
}

export const INDEP_MODE_LABELS: Record<IndepMode, string> = {
  off: "Off",
  anchored: "Anchored",
  follow: "Smooth follow",
  head_locked: "Head-locked",
};
