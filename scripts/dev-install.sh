#!/bin/bash
# Installs the glARsses Decky plugin into Decky Loader (requires sudo).
set -e
cd "$(dirname "$0")/.."
[ -f dist/index.js ] || { echo "Run 'npm run build' first"; exit 1; }
DEST="$HOME/homebrew/plugins/decky-glarsses"
OLD_DEST="$HOME/homebrew/plugins/decky-ar-glasses"  # install from before the rename
# No terminal (e.g. run from Claude Code with "!")? Ask for the password with a KDE pop-up.
if [ ! -t 0 ] && [ -x /usr/bin/ksshaskpass ]; then
  export SUDO_ASKPASS=/usr/bin/ksshaskpass
  sudo() { command sudo -A "$@"; }
  sudo -v || { echo "sudo password was not entered"; exit 1; }
fi
sudo rm -rf "$DEST" "$OLD_DEST"
sudo mkdir -p "$DEST/dist"
sudo cp dist/index.js "$DEST/dist/"
[ -f dist/index.js.map ] && sudo cp dist/index.js.map "$DEST/dist/"
sudo cp main.py plugin.json package.json "$DEST/"
sudo cp -r driver "$DEST/"
sudo systemctl restart plugin_loader
echo "Installed to $DEST and restarted Decky Loader."
