import { toaster } from "@decky/api";
import { Router } from "@decky/ui";
import { applyProfile, captureProfile, getStore, SavedProfile } from "./backend";

/**
 * Background watcher (runs while Decky is loaded, even when the panel is closed):
 * when a game with a saved profile starts, apply it; when the game exits, restore
 * the settings that were active before.
 */
let timer: number | undefined;
let lastAppId: string | null = null;
let restoreValues: SavedProfile | null = null;

export function currentGame(): { appid: string; name: string } | null {
  const app = Router.MainRunningApp;
  if (!app) return null;
  return { appid: String(app.appid), name: app.display_name };
}

async function tick() {
  const game = currentGame();
  const appid = game?.appid ?? null;
  if (appid === lastAppId) return;
  lastAppId = appid;

  try {
    if (restoreValues) {
      // leaving a game that had a profile applied
      await applyProfile(restoreValues);
      restoreValues = null;
      if (!appid) toaster.toast({ title: "glARsses", body: "Restored default display settings" });
    }

    const store = await getStore();
    if (store.per_game_enabled && appid && store.game_profiles[appid]) {
      restoreValues = await captureProfile();
      await applyProfile(store.game_profiles[appid].values as SavedProfile);
      toaster.toast({ title: "glARsses", body: `Applied profile for ${store.game_profiles[appid].name}` });
    }
  } catch (e) {
    console.error("glARsses profile watcher", e);
  }
}

export function startProfileWatcher() {
  stopProfileWatcher();
  lastAppId = null; // so a game that is already running gets its profile on load
  timer = window.setInterval(tick, 3000);
}

export function stopProfileWatcher() {
  if (timer !== undefined) window.clearInterval(timer);
  timer = undefined;
}
