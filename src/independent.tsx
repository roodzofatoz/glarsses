import { toaster } from "@decky/api";
import { ButtonItem, DropdownItem, Field, PanelSection, PanelSectionRow, SliderField, ToggleField } from "@decky/ui";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  INDEP_MODE_LABELS,
  IndepConfig,
  IndepMode,
  IndepStatus,
  getIndepConfig,
  getIndepStatus,
  indepRecenter,
  setIndepConfig,
} from "./backend";

const MODE_DESCRIPTIONS: Record<IndepMode, string> = {
  off: "Glasses show the normal screen.",
  anchored: "Screen stays fixed in place in the room.",
  follow: "Screen stays put until you look away, then glides after you.",
  head_locked: "Screen moves rigidly with your head.",
};

/** Settings for the glARsses driver. */
export function IndependentSections() {
  const [cfg, setCfg] = useState<IndepConfig | null>(null);
  const [status, setStatus] = useState<IndepStatus | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const pending = useRef<Partial<IndepConfig>>({});
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    getIndepConfig().then(setCfg);
    const poll = () => getIndepStatus().then(setStatus).catch(() => undefined);
    poll();
    const id = window.setInterval(poll, 1000);
    return () => window.clearInterval(id);
  }, []);

  const update = useCallback((changes: Partial<IndepConfig>, immediate = false) => {
    setCfg((c) => (c ? { ...c, ...changes } : c));
    pending.current = { ...pending.current, ...changes };
    if (timer.current !== undefined) window.clearTimeout(timer.current);
    const flush = async () => {
      const batch = pending.current;
      pending.current = {};
      await setIndepConfig(batch);
    };
    if (immediate) flush();
    else timer.current = window.setTimeout(flush, 150);
  }, []);

  if (!cfg) {
    return (
      <PanelSection>
        <PanelSectionRow>
          <Field label="Loading..." />
        </PanelSectionRow>
      </PanelSection>
    );
  }

  let statusText = "Starting...";
  if (status?.running) {
    if (!status.gamescope) statusText = "Waiting for Gaming Mode";
    else if (!status.glasses) statusText = "Glasses not connected";
    else if (status.effect) statusText = status.following ? "Active (following)" : "Active";
    else statusText = "Connected";
  } else if (status && !status.installed) {
    statusText = "Not installed";
  } else if (status) {
    statusText = "Driver not running";
  }

  const apparent = cfg.size / Math.max(0.1, cfg.distance);

  return (
    <>
      <PanelSection title="Status">
        <PanelSectionRow>
          <Field label="glARsses driver">{statusText}</Field>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={cfg.mode === "off"}
            description="Look where you want the screen, level your head, press, then hold still for a second (this also corrects drift)."
            onClick={async () => {
              await indepRecenter();
              toaster.toast({ title: "glARsses", body: "Recentered" });
            }}
          >
            Recenter display
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Display mode">
        <PanelSectionRow>
          <DropdownItem
            rgOptions={(Object.keys(INDEP_MODE_LABELS) as IndepMode[]).map((m) => ({ data: m, label: INDEP_MODE_LABELS[m] }))}
            selectedOption={cfg.mode}
            description={MODE_DESCRIPTIONS[cfg.mode]}
            onChange={(o) => update({ mode: o.data as IndepMode }, true)}
          />
        </PanelSectionRow>
      </PanelSection>

      {cfg.mode === "follow" && (
        <PanelSection title="Smooth follow">
          <PanelSectionRow>
            <SliderField
              label="Follow threshold (degrees)"
              description="How far you can look away before the screen starts to follow."
              value={cfg.follow_threshold_deg}
              min={1}
              max={45}
              step={1}
              showValue
              editableValue
              onChange={(v) => update({ follow_threshold_deg: v })}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <SliderField
              label="Follow speed"
              description="How quickly the screen catches up once it starts moving."
              value={cfg.follow_speed}
              min={0.5}
              max={10}
              step={0.5}
              showValue
              onChange={(v) => update({ follow_speed: v })}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ToggleField
              label="Gentle centering"
              description="When you hold your head still, the screen slowly settles back in front of you. Also cancels slow drift."
              checked={cfg.gentle_centering}
              onChange={(v) => update({ gentle_centering: v }, true)}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ToggleField label="Follow left/right" checked={cfg.follow_yaw} onChange={(v) => update({ follow_yaw: v }, true)} />
          </PanelSectionRow>
          <PanelSectionRow>
            <ToggleField label="Follow up/down" checked={cfg.follow_pitch} onChange={(v) => update({ follow_pitch: v }, true)} />
          </PanelSectionRow>
        </PanelSection>
      )}

      {cfg.mode !== "off" && (
        <PanelSection title="Size & distance">
          <PanelSectionRow>
            <SliderField
              label="Display size"
              value={cfg.size}
              min={0.3}
              max={1.5}
              step={0.01}
              showValue
              editableValue
              onChange={(v) => update({ size: v })}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <SliderField
              label="Display distance"
              description={`Farther pushes the screen back (it looks ${Math.round(apparent * 100)}% of your view's size). Your glasses track rotation only, so distance shows as scale.`}
              value={cfg.distance}
              min={0.5}
              max={2.5}
              step={0.05}
              showValue
              editableValue
              onChange={(v) => update({ distance: v })}
            />
          </PanelSectionRow>
          {cfg.mode !== "head_locked" && (
            <PanelSectionRow>
              <ToggleField
                label="Curved display"
                description="Wrap the screen around you."
                checked={cfg.curved}
                onChange={(v) => update({ curved: v }, true)}
              />
            </PanelSectionRow>
          )}
          <PanelSectionRow>
            <SliderField
              label="Tilt correction (degrees)"
              description="Rotate the screen if it looks tilted. Positive turns it clockwise."
              value={cfg.roll_trim_deg}
              min={-10}
              max={10}
              step={0.5}
              showValue
              editableValue
              onChange={(v) => update({ roll_trim_deg: v })}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ToggleField
              label="Ambient light"
              description="Soft glow around the screen in the colours of the picture's edges."
              checked={cfg.ambient_light}
              onChange={(v) => update({ ambient_light: v }, true)}
            />
          </PanelSectionRow>
          {cfg.ambient_light && (
            <>
              <PanelSectionRow>
                <SliderField
                  label="Glow intensity"
                  value={cfg.ambient_intensity}
                  min={0.1}
                  max={1}
                  step={0.05}
                  showValue
                  onChange={(v) => update({ ambient_intensity: v })}
                />
              </PanelSectionRow>
              <PanelSectionRow>
                <SliderField
                  label="Glow size"
                  description="How far the glow reaches, in screen heights."
                  value={cfg.ambient_size}
                  min={0.1}
                  max={1.0}
                  step={0.05}
                  showValue
                  onChange={(v) => update({ ambient_size: v })}
                />
              </PanelSectionRow>
            </>
          )}
          <PanelSectionRow>
            <ToggleField label="Show advanced options" checked={showAdvanced} onChange={setShowAdvanced} />
          </PanelSectionRow>
          {showAdvanced && (
            <>
              <PanelSectionRow>
                <SliderField
                  label="Motion prediction (ms)"
                  description="Hides display lag. Lower it if the screen overshoots when you stop moving."
                  value={cfg.look_ahead_ms}
                  min={0}
                  max={40}
                  step={1}
                  showValue
                  onChange={(v) => update({ look_ahead_ms: v })}
                />
              </PanelSectionRow>
              <PanelSectionRow>
                <ToggleField
                  label="Show screen outline"
                  description="Draws a yellow frame around the virtual screen."
                  checked={cfg.debug_outline}
                  onChange={(v) => update({ debug_outline: v }, true)}
                />
              </PanelSectionRow>
            </>
          )}
        </PanelSection>
      )}
    </>
  );
}
