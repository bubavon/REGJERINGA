#!/bin/sh
# Install BC-250 RAM Monitor for the current user (no root needed).
#   ./install.sh            install to ~/.local
#   ./install.sh --deps     also install GTK4 deps via pacman (asks for sudo)
#   ./install.sh --uninstall
set -e
cd "$(dirname "$0")"
SHARE="$HOME/.local/share/bc250-ram-monitor"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"

if [ "$1" = "--uninstall" ]; then
    rm -rf "$SHARE" "$BIN/bc250-ram-monitor" "$APPS/bc250-ram-monitor.desktop"
    echo "Removed."; exit 0
fi
if [ "$1" = "--deps" ]; then
    sudo pacman -S --needed python python-gobject python-cairo gtk4
fi

mkdir -p "$SHARE" "$BIN" "$APPS"
install -m755 bc250-ram-monitor "$SHARE/bc250-ram-monitor"
install -m644 bc250_core.py "$SHARE/bc250_core.py"
ln -sf "$SHARE/bc250-ram-monitor" "$BIN/bc250-ram-monitor"
sed "s|^Exec=.*|Exec=$BIN/bc250-ram-monitor|" bc250-ram-monitor.desktop > "$APPS/bc250-ram-monitor.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
echo "Installed. Launch 'BC-250 RAM Monitor' from your app menu, or run: bc250-ram-monitor"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "Note: add $BIN to your PATH to run it from a terminal.";; esac
