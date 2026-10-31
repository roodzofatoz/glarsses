import { definePlugin, toaster } from "@decky/api";
import {
  ButtonItem,
  DropdownItem,
  Field,
  PanelSection,
  PanelSectionRow,
  SliderField,
  ToggleField,
  staticClasses,
} from "@decky/ui";
import { useCallback, useEffect, useRef, useState } from "react";
import { FaGlasses } from "react-icons/fa";
import {
  DisplayMode,
  DriverChoice,
  DriverConfig,
  INDEP_MODE_LABELS,
  SavedProfile,
  applyProfile,
  captureProfile,
  getDriverChoice,
  setDriverChoice,
  DriverStatus,
  Store,
  configForMode,
  deleteGameProfile,
  deletePreset,
  getConfig,
  getStatus,
  getStore,
  modeFromConfig,
  restartDriver,
  saveGameProfile,
  savePreset,
  sendControl,
  setConfig,
  setPerGameEnabled,
} from "./backend";
import { IndependentSections } from "./independent";
import { currentGame, startProfileWatcher, stopProfileWatcher } from "./profiles";

const SIZE_MIN = 0.1;
const SIZE_MAX = 2.5;
const PRESET_SLOTS = ["Preset 1", "Preset 2", "Preset 3"];

const MODE_OPTIONS: { data: DisplayMode; label: string; description: string }[] = [
  { data: "off", label: "Off", description: "Glasses act as a normal mirrored screen." },
  { data: "anchored", label: "Anchored", description: "Screen stays fixed in place in the room; look around it." },
  { data: "anchored_recenter", label: "Anchored + auto-recenter", description: "Fixed in place, but recenters if you turn away for a while." },
  { data: "follow", label: "Smooth follow", description: "Screen gently glides after your head movement." },
  { data: "head_locked", label: "Head-locked", description: "Screen moves rigidly with your head (no tracking)." },
];

const POSITION_OPTIONS = [
  ["middle_center", "Center"],
  ["top_left", "Top left"],
  ["top_center", "Top center"],
  ["top_right", "Top right"],
  ["middle_left", "Middle left"],
  ["middle_right", "Middle right"],
  ["bottom_left", "Bottom left"],
  ["bottom_center", "Bottom center"],
  ["bottom_right", "Bottom right"],
].map(([data, label]) => ({ data, label }));

const clamp = (v: number, min: number, max: number) => Math.min(Math.max(v, min), max);

function Content() {
  const [config, setLocalConfig] = useState<DriverConfig | null>(null);
  const [status, setStatus] = useState<DriverStatus | null>(null);
  const [store, setStoreState] = useState<Store | null>(null);
  const [game, setGame] = useState(currentGame());
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [driver, setDriver] = useState<DriverChoice | null>(null);
  const [switching, setSwitching] = useState(false);
  const [indepKey, setIndepKey] = useState(0);
  const [presetSlot, setPresetSlot] = useState(PRESET_SLOTS[0]);
  const pending = useRef<Partial<DriverConfig>>({});
  const flushTimer = useRef<number | undefined>(undefined);

  // ---- data loading / polling
  useEffect(() => {
    getConfig().then(setLocalConfig);
    getStore().then(setStoreState);
    getDriverChoice().then(setDriver);
    const poll = () => {
      getStatus().then(setStatus).catch(() => undefined);
      setGame(currentGame());
    };
    poll();
    const id = window.setInterval(poll, 1500);
    return () => window.clearInterval(id);
  }, []);

  // ---- debounced config writes (sliders fire many onChange events)
  const update = useCallback((changes: Partial<DriverConfig>, immediate = false) => {
    setLocalConfig((c) => (c ? { ...c, ...changes } : c));
    pending.current = { ...pending.current, ...changes };
    if (flushTimer.current !== undefined) window.clearTimeout(flushTimer.current);
    const flush = async () => {
      const batch = pending.current;
      pending.current = {};
      const result = await setConfig(batch);
      if (!result) toaster.toast({ title: "glARsses", body: "Couldn't write driver settings" });
    };
    if (immediate) flush();
    else flushTimer.current = window.setTimeout(flush, 200);
  }, []);

  if (!config || !status || !driver) {
    return (
      <PanelSection>
        <PanelSectionRow>
          <Field label="Loading..." />
        </PanelSectionRow>
      </PanelSection>
    );
  }

  const mode = modeFromConfig(config);
  const isAnchored = mode === "anchored" || mode === "anchored_recenter";
  const isSideview = mode === "follow" || mode === "head_locked";
  const smoothFollow = status.features["smooth_follow"];
  const sbsFeature = status.features["sbs"];

  // Like XR Gaming: in anchored mode with depth (3D/SBS or 6DoF), a farther display must be
  // scaled up to keep the same apparent size, so stored size = intended size * distance.
  const sizeTiedToDistance = isAnchored && (status.connected_device_pose_has_position || status.sbs_mode_enabled);
  const distance = clamp(config.display_distance || 1, SIZE_MIN, SIZE_MAX);
  const sizeMax = mode === "head_locked" ? 1.0 : SIZE_MAX;
  const intendedSize = clamp(config.display_size / (sizeTiedToDistance ? distance : 1), SIZE_MIN, sizeMax);
  const fullDistanceCm = status.connected_device_full_distance_cm;
  const distanceLabel = fullDistanceCm > 0 ? ` (~${Math.round(distance * fullDistanceCm)} cm)` : "";

  const setSize = (value: number) =>
    update({ display_size: +(value * (sizeTiedToDistance ? distance : 1)).toFixed(3) });
  const setDistance = (value: number) =>
    update({
      display_distance: value,
      display_size: sizeTiedToDistance ? +(intendedSize * value).toFixed(3) : config.display_size,
    });

  const applyValues = async (values: SavedProfile, label: string) => {
    await applyProfile(values);
    const { indep: _ignored, ...xr } = values;
    if (driver === "xr_gaming") setLocalConfig((c) => (c ? { ...c, ...xr } : c));
    setIndepKey((k) => k + 1); // remount the glARsses driver panel so it reloads
    toaster.toast({ title: "glARsses", body: label });
  };

  const presetLabel = (saved: SavedProfile) =>
    driver === "independent"
      ? saved.indep?.mode ? INDEP_MODE_LABELS[saved.indep.mode] : "Saved"
      : MODE_OPTIONS.find((o) => o.data === modeFromConfig({ ...config, ...saved } as DriverConfig))?.label;

  const switchDriver = async (choice: DriverChoice) => {
    setSwitching(true);
    const result = await setDriverChoice(choice);
    setDriver(result);
    setSwitching(false);
    if (result !== choice)
      toaster.toast({
        title: "glARsses",
        body: choice === "xr_gaming" ? "XR Gaming's driver isn't installed - install the XR Gaming plugin first" : "Couldn't switch driver - see Decky logs",
      });
    if (result === "xr_gaming") getConfig().then(setLocalConfig);
  };

  const xr = driver === "xr_gaming";

  const glassesName = status.connected_device_model
    ? `${status.connected_device_brand ?? ""} ${status.connected_device_model}`.trim()
    : "Not connected";

  return (
    <>
      {/* ---------------------------------------------------------- driver */}
      <PanelSection title="Driver">
        <PanelSectionRow>
          <DropdownItem
            disabled={switching}
            rgOptions={[
              { data: "xr_gaming", label: "XR Gaming (Breezy)" },
              { data: "independent", label: "glARsses (free)" },
            ]}
            selectedOption={driver}
            description={
              switching
                ? "Switching..."
                : xr
                ? "Uses the XR Gaming plugin's driver. Smooth follow needs its supporter licence."
                : "Uses the glARsses driver. All modes are free."
            }
            onChange={(o) => switchDriver(o.data as DriverChoice)}
          />
        </PanelSectionRow>
      </PanelSection>

      {!xr && <IndependentSections key={indepKey} />}

      {xr && (<>
      {/* ---------------------------------------------------------- status */}
      <PanelSection title="Status">
        <PanelSectionRow>
          <Field label="Glasses" description={status.driver_running ? "Driver running" : "Driver not running"}>
            {glassesName}
          </Field>
        </PanelSectionRow>
        {!status.breezy_installed && (
          <PanelSectionRow>
            <Field
              label="Breezy Vulkan not found"
              description="Install it once from the XR Gaming plugin. This plugin uses the same driver."
            />
          </PanelSectionRow>
        )}
        {status.calibration_state === "CALIBRATING" && (
          <PanelSectionRow>
            <Field label="Calibrating..." description="Keep the glasses still on a flat surface." />
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => sendControl({ recenter_screen: true })} disabled={mode === "off"}>
            Recenter display
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      {/* ------------------------------------------------------------ mode */}
      <PanelSection title="Display mode">
        <PanelSectionRow>
          <DropdownItem
            rgOptions={MODE_OPTIONS.map((o) => ({ data: o.data, label: o.label }))}
            selectedOption={mode === "other" ? undefined : mode}
            strDefaultLabel={mode === "other" ? "VR-Lite (set in XR Gaming)" : undefined}
            description={MODE_OPTIONS.find((o) => o.data === mode)?.description}
            onChange={(opt) => update(configForMode(opt.data as DisplayMode), true)}
          />
        </PanelSectionRow>
        {(mode === "follow" || mode === "anchored_recenter") && smoothFollow && !smoothFollow.enabled && (
          <PanelSectionRow>
            <Field
              label="Smooth follow is locked"
              description="The driver requires an XR Gaming supporter licence for this feature. Open XR Gaming to manage it."
            />
          </PanelSectionRow>
        )}
        {(mode === "follow" || mode === "anchored_recenter") && smoothFollow?.trial && smoothFollow.days_left !== null && (
          <PanelSectionRow>
            <Field label="Smooth follow trial" description={`${smoothFollow.days_left} days left`} />
          </PanelSectionRow>
        )}
      </PanelSection>

      {/* -------------------------------------------------- follow tuning */}
      {mode === "follow" && (
        <PanelSection title="Smooth follow">
          <PanelSectionRow>
            <SliderField
              label="Follow threshold (degrees)"
              description="How far you can look away before the screen starts following. Lower = tighter follow."
              value={config.sideview_follow_threshold}
              min={0.5}
              max={45}
              step={0.5}
              editableValue
              showValue
              onChange={(v) => update({ sideview_follow_threshold: v })}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ToggleField
              label="Follow left/right"
              checked={config.smooth_follow_track_yaw}
              onChange={(v) => update({ smooth_follow_track_yaw: v }, true)}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ToggleField
              label="Follow up/down"
              checked={config.smooth_follow_track_pitch}
              onChange={(v) => update({ smooth_follow_track_pitch: v }, true)}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ToggleField
              label="Follow head tilt"
              checked={config.smooth_follow_track_roll}
              onChange={(v) => update({ smooth_follow_track_roll: v }, true)}
            />
          </PanelSectionRow>
        </PanelSection>
      )}

      {/* ------------------------------------------------- size / distance */}
      {mode !== "off" && mode !== "other" && (
        <PanelSection title="Size & distance">
          <PanelSectionRow>
            <SliderField
              label="Display size"
              value={+intendedSize.toFixed(2)}
              min={SIZE_MIN}
              max={sizeMax}
              step={0.01}
              editableValue
              showValue
              notchCount={3}
              notchTicksVisible
              notchLabels={[
                { notchIndex: 0, label: "Small" },
                { notchIndex: 1, label: "" },
                { notchIndex: 2, label: "Large" },
              ]}
              onChange={setSize}
            />
          </PanelSectionRow>
          {isAnchored && (
            <PanelSectionRow>
              <SliderField
                label={`Display distance${distanceLabel}`}
                description={
                  status.connected_device_pose_has_position || status.sbs_mode_enabled
                    ? "Moves the screen nearer or farther. Size is kept the same."
                    : "Your glasses track rotation only, so distance shows as depth only in 3D (SBS) mode."
                }
                value={+distance.toFixed(2)}
                min={SIZE_MIN}
                max={SIZE_MAX}
                step={0.01}
                editableValue
                showValue
                onChange={setDistance}
              />
            </PanelSectionRow>
          )}
          {isSideview && (
            <PanelSectionRow>
              <DropdownItem
                label="Screen position"
                rgOptions={POSITION_OPTIONS}
                selectedOption={config.sideview_position}
                onChange={(opt) => update({ sideview_position: opt.data }, true)}
              />
            </PanelSectionRow>
          )}
          {isAnchored && (
            <PanelSectionRow>
              <ToggleField
                label="Curved display"
                description="Wrap the screen around your field of view."
                checked={config.curved_display}
                onChange={(v) => update({ curved_display: v }, true)}
              />
            </PanelSectionRow>
          )}
          {status.sbs_mode_supported && (
            <PanelSectionRow>
              <ToggleField
                label="3D / side-by-side mode"
                description={
                  sbsFeature && !sbsFeature.enabled
                    ? "Requires an XR Gaming supporter licence."
                    : "Switch the glasses to 3D mode (needed for real depth)."
                }
                checked={status.sbs_mode_enabled}
                onChange={(v) => sendControl({ sbs_mode: v ? "enable" : "disable" })}
              />
            </PanelSectionRow>
          )}
        </PanelSection>
      )}

      {/* ------------------------------------------------- comfort / extra */}
      {mode !== "off" && (
        <PanelSection title="Comfort & tracking">
          <PanelSectionRow>
            <ToggleField label="Show advanced options" checked={showAdvanced} onChange={setShowAdvanced} />
          </PanelSectionRow>
          {showAdvanced && (
            <>
              <PanelSectionRow>
                <SliderField
                  label="Dead zone (degrees)"
                  description="Ignore small head movements to keep the screen steady."
                  value={config.dead_zone_threshold_deg}
                  min={0}
                  max={5}
                  step={0.1}
                  editableValue
                  showValue
                  onChange={(v) => update({ dead_zone_threshold_deg: v })}
                />
              </PanelSectionRow>
              <PanelSectionRow>
                <SliderField
                  label="Movement look-ahead"
                  description="0 = driver default. Raise only if the screen lags behind your movements."
                  value={config.look_ahead}
                  min={0}
                  max={45}
                  step={3}
                  showValue
                  onChange={(v) => update({ look_ahead: v })}
                />
              </PanelSectionRow>
              <PanelSectionRow>
                <SliderField
                  label="Neck-saver horizontal"
                  description="Amplify left/right head turns."
                  value={config.neck_saver_horizontal_multiplier}
                  min={1}
                  max={2.5}
                  step={0.1}
                  showValue
                  onChange={(v) => update({ neck_saver_horizontal_multiplier: v })}
                />
              </PanelSectionRow>
              <PanelSectionRow>
                <SliderField
                  label="Neck-saver vertical"
                  description="Amplify up/down head movement."
                  value={config.neck_saver_vertical_multiplier}
                  min={1}
                  max={2.5}
                  step={0.1}
                  showValue
                  onChange={(v) => update({ neck_saver_vertical_multiplier: v })}
                />
              </PanelSectionRow>
              <PanelSectionRow>
                <ToggleField
                  label="Double-tap glasses to recenter"
                  checked={config.multi_tap_enabled}
                  onChange={(v) => update({ multi_tap_enabled: v }, true)}
                />
              </PanelSectionRow>
            </>
          )}
        </PanelSection>
      )}

      </>)}

      {/* --------------------------------------------------------- presets */}
      {store && (
        <PanelSection title="Presets">
          <PanelSectionRow>
            <DropdownItem
              label="Preset slot"
              rgOptions={PRESET_SLOTS.map((slot) => {
                const saved = store.presets[slot];
                return { data: slot, label: `${slot}: ${saved ? presetLabel(saved as SavedProfile) ?? "Saved" : "Empty"}` };
              })}
              selectedOption={presetSlot}
              onChange={(o) => setPresetSlot(o.data as string)}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={!store.presets[presetSlot]}
              onClick={() => {
                const saved = store.presets[presetSlot];
                if (saved) applyValues(saved as SavedProfile, `Loaded ${presetSlot}`);
              }}
            >
              Load {presetSlot}
            </ButtonItem>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              onClick={async () => {
                setStoreState(await savePreset(presetSlot, await captureProfile()));
                toaster.toast({ title: "glARsses", body: `Saved current settings to ${presetSlot}` });
              }}
            >
              Save current settings to {presetSlot}
            </ButtonItem>
          </PanelSectionRow>
          {store.presets[presetSlot] && (
            <PanelSectionRow>
              <ButtonItem layout="below" onClick={async () => setStoreState(await deletePreset(presetSlot))}>
                Clear {presetSlot}
              </ButtonItem>
            </PanelSectionRow>
          )}
        </PanelSection>
      )}

      {/* ----------------------------------------------- per-game profiles */}
      {store && (
        <PanelSection title="Per-game profiles">
          <PanelSectionRow>
            <ToggleField
              label="Auto-apply game profiles"
              description="Switch to a game's saved settings when it starts, and restore yours when it closes."
              checked={store.per_game_enabled}
              onChange={async (v) => setStoreState(await setPerGameEnabled(v))}
            />
          </PanelSectionRow>
          {game ? (
            <>
              <PanelSectionRow>
                <ButtonItem
                  layout="below"
                  onClick={async () => {
                    setStoreState(await saveGameProfile(game.appid, game.name, await captureProfile()));
                    toaster.toast({ title: "glARsses", body: `Saved profile for ${game.name}` });
                  }}
                >
                  Save current settings for {game.name}
                </ButtonItem>
              </PanelSectionRow>
              {store.game_profiles[game.appid] && (
                <PanelSectionRow>
                  <ButtonItem layout="below" onClick={async () => setStoreState(await deleteGameProfile(game.appid))}>
                    Remove profile for {game.name}
                  </ButtonItem>
                </PanelSectionRow>
              )}
            </>
          ) : (
            <PanelSectionRow>
              <Field description="Start a game to save a profile for it." />
            </PanelSectionRow>
          )}
          {Object.keys(store.game_profiles).length > 0 && (
            <PanelSectionRow>
              <Field
                label="Saved profiles"
                description={Object.values(store.game_profiles)
                  .map((p) => p.name)
                  .join(", ")}
              />
            </PanelSectionRow>
          )}
        </PanelSection>
      )}

      {/* -------------------------------------------------- troubleshooting */}
      {xr && <PanelSection title="Troubleshooting">
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            description="Leave the glasses on a flat surface while it runs."
            onClick={() => sendControl({ recalibrate: true })}
          >
            Recalibrate tracking
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={async () =>
              toaster.toast({ title: "glARsses", body: (await restartDriver()) ? "Driver restarted" : "Driver restart failed" })
            }
          >
            Restart driver
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>}
    </>
  );
}

export default definePlugin(() => {
  startProfileWatcher();
  return {
    name: "glARsses",
    titleView: <div className={staticClasses.Title}>glARsses</div>,
    content: <Content />,
    icon: <FaGlasses />,
    onDismount() {
      stopProfileWatcher();
    },
  };
});
