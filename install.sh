#!/bin/bash
# glARsses installer for Steam Deck: Decky Loader plugin + free AR glasses driver.
#
# Install / update (run in Desktop Mode, Konsole):
#   curl -fsSL https://raw.githubusercontent.com/roodzofatoz/glarsses/main/install.sh | bash
# Install a specific version:
#   curl -fsSL https://raw.githubusercontent.com/roodzofatoz/glarsses/main/install.sh | bash -s -- --version v0.3.2
# Uninstall (keeps your settings; add --purge to remove them too):
#   curl -fsSL https://raw.githubusercontent.com/roodzofatoz/glarsses/main/install.sh | bash -s -- --uninstall
set -euo pipefail

REPO="roodzofatoz/glarsses"
ASSET="decky-glarsses.zip"
PLUGINS="$HOME/homebrew/plugins"
DEST="$PLUGINS/decky-glarsses"
OLD_DEST="$PLUGINS/decky-ar-glasses"   # name used before v0.3.0

VERSION=""
ACTION="install"
PURGE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="${2:-}"; shift 2 ;;
    --uninstall) ACTION="uninstall"; shift ;;
    --purge) PURGE=1; shift ;;
    -h|--help) sed -n '2,10p' "$0" 2>/dev/null || true; exit 0 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "run this as your normal user (deck), not with sudo."

# sudo: use the terminal if there is one, otherwise a KDE password pop-up
SUDO=(sudo)
if ! ( : </dev/tty ) 2>/dev/null && [ -x /usr/bin/ksshaskpass ]; then
  export SUDO_ASKPASS=/usr/bin/ksshaskpass
  SUDO=(sudo -A)
fi

user_systemctl() { systemctl --user "$@" >/dev/null 2>&1 || true; }

uninstall() {
  say "Stopping the glARsses driver"
  user_systemctl disable --now glarsses
  user_systemctl disable --now xr-independent
  rm -f "$HOME/.config/systemd/user/glarsses.service" "$HOME/.config/systemd/user/xr-independent.service"
  user_systemctl daemon-reload
  rm -rf "$HOME/.local/share/glarsses" "$HOME/.local/share/xr-independent"
  rm -f "$HOME/.local/share/gamescope/reshade/Shaders/"Glarsses*.fx \
        "$HOME/.local/share/gamescope/reshade/Shaders/XRIndependent.fx"
  if [ "$PURGE" -eq 1 ]; then
    say "Removing settings"
    rm -rf "$HOME/.config/glarsses"
    "${SUDO[@]}" rm -rf "$HOME/homebrew/settings/decky-glarsses" "$HOME/homebrew/settings/decky-ar-glasses"
  fi
  say "Removing the Decky plugin (needs your sudo password)"
  "${SUDO[@]}" rm -rf "$DEST" "$OLD_DEST"
  "${SUDO[@]}" systemctl restart plugin_loader || true
  say "glARsses has been uninstalled."
}

install() {
  [ -d "$PLUGINS" ] || die "Decky Loader not found ($PLUGINS). Install Decky Loader first: https://decky.xyz"
  command -v curl >/dev/null || die "curl is required"
  command -v python3 >/dev/null || die "python3 is required"

  if [ -n "$VERSION" ]; then
    URL="https://github.com/$REPO/releases/download/$VERSION/$ASSET"
  else
    URL="https://github.com/$REPO/releases/latest/download/$ASSET"
  fi

  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  say "Downloading ${VERSION:-latest release} from github.com/$REPO"
  curl -fL --progress-bar -o "$TMP/$ASSET" "$URL" || die "download failed: $URL"

  mkdir "$TMP/x"
  python3 -I -m zipfile -e "$TMP/$ASSET" "$TMP/x" || die "could not unpack $ASSET"
  [ -f "$TMP/x/decky-glarsses/plugin.json" ] && [ -f "$TMP/x/decky-glarsses/dist/index.js" ] \
    || die "downloaded archive does not look like glARsses"
  NEW_VERSION="$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$TMP/x/decky-glarsses/package.json" 2>/dev/null || echo "?")"

  say "Installing glARsses $NEW_VERSION into Decky (needs your sudo password)"
  "${SUDO[@]}" rm -rf "$DEST" "$OLD_DEST"
  "${SUDO[@]}" cp -r "$TMP/x/decky-glarsses" "$DEST"
  "${SUDO[@]}" chown -R root:root "$DEST"
  say "Restarting Decky Loader"
  "${SUDO[@]}" systemctl restart plugin_loader

  cat <<EOF

glARsses $NEW_VERSION is installed.

Next steps:
  1. Plug in your glasses and switch to Gaming Mode.
  2. Press the ... (Quick Access) button and open glARsses.
  3. Driver: "glARsses (free)" is selected automatically unless the XR Gaming plugin is installed.
  4. Pick a display mode (e.g. Smooth follow), look straight ahead and press Recenter.

EOF
}

"$ACTION"
