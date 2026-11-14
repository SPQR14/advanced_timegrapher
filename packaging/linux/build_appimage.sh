#!/usr/bin/env bash
# Builds the AppImage from the PyInstaller bundle:
#   pyinstaller packaging/adv-tg.spec --noconfirm
#   ./packaging/linux/build_appimage.sh [version]
# appimagetool and the AppImage runtime are downloaded at pinned versions and verified by
# SHA-256 (set APPIMAGETOOL / APPIMAGE_RUNTIME to use local copies instead).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

VERSION="${1:-$(grep -E '^__version__\s*=' src/adv_tg/__init__.py | cut -d'"' -f2)}"
ARCH="$(uname -m)"
[ "$ARCH" = x86_64 ] || { echo "ERROR: only x86_64 is pinned here (got $ARCH)" >&2; exit 1; }

TOOL_URL="https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage"
TOOL_SHA256="ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"
RUNTIME_URL="https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-x86_64"
RUNTIME_SHA256="2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d"

if [ ! -x dist/adv-tg/adv-tg ]; then
    echo "ERROR: dist/adv-tg/adv-tg not found; run PyInstaller first." >&2
    exit 1
fi

WORK="${ROOT_DIR}/build/appimage"
rm -rf "$WORK"
mkdir -p "$WORK/tools"

fetch() { # url sha256 destination
    curl --fail --silent --show-error --location --retry 3 -o "$3" "$1"
    echo "$2  $3" | sha256sum --check --quiet -
}
TOOL="${APPIMAGETOOL:-}"
if [ -z "$TOOL" ]; then
    TOOL="$WORK/tools/appimagetool"
    fetch "$TOOL_URL" "$TOOL_SHA256" "$TOOL"
    chmod +x "$TOOL"
fi
RUNTIME="${APPIMAGE_RUNTIME:-}"
if [ -z "$RUNTIME" ]; then
    RUNTIME="$WORK/tools/runtime-x86_64"
    fetch "$RUNTIME_URL" "$RUNTIME_SHA256" "$RUNTIME"
fi

APPDIR="$WORK/AdvancedTimegrapher.AppDir"
mkdir -p "$APPDIR/opt" "$APPDIR/usr/share/applications" \
    "$APPDIR/usr/share/icons/hicolor/scalable/apps"
cp -a dist/adv-tg "$APPDIR/opt/adv-tg"
install -m 644 packaging/linux/adv-tg.desktop "$APPDIR/usr/share/applications/adv-tg.desktop"
install -m 644 packaging/linux/adv-tg.desktop "$APPDIR/adv-tg.desktop"
install -m 644 src/adv_tg/ui/assets/adv-tg.svg \
    "$APPDIR/usr/share/icons/hicolor/scalable/apps/adv-tg.svg"
install -m 644 src/adv_tg/ui/assets/adv-tg.svg "$APPDIR/adv-tg.svg"
ln -s adv-tg.svg "$APPDIR/.DirIcon"
cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/opt/adv-tg/adv-tg" "$@"
APPRUN
chmod 755 "$APPDIR/AppRun"

OUT="dist/adv-tg-${VERSION}-linux-x86_64.AppImage"
rm -f "$OUT"
# --appimage-extract-and-run: appimagetool is itself an AppImage and CI containers have no FUSE
ARCH=x86_64 VERSION="$VERSION" "$TOOL" --appimage-extract-and-run \
    --no-appstream --runtime-file "$RUNTIME" "$APPDIR" "$OUT"
chmod +x "$OUT"
echo "AppImage: $OUT ($(du -m "$OUT" | cut -f1) MB)"
