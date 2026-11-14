#!/usr/bin/env bash
# Builds the Debian package (Debian, Ubuntu, Mint...) from the PyInstaller bundle:
#   pyinstaller packaging/adv-tg.spec --noconfirm
#   ./packaging/linux/build_deb.sh [version]
# Needs dpkg-deb (Debian/Ubuntu: dpkg; Fedora/RHEL: dnf install dpkg, from EPEL on RHEL).
# Same layout as the RPM: /opt/adv-tg, /usr/bin/adv-tg, desktop entry and icon.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

VERSION="${1:-$(grep -E '^__version__\s*=' src/adv_tg/__init__.py | cut -d'"' -f2)}"
case "$(uname -m)" in
    x86_64) ARCH=amd64 ;;
    aarch64) ARCH=arm64 ;;
    *) echo "ERROR: unsupported architecture $(uname -m)" >&2; exit 1 ;;
esac

if [ ! -x dist/adv-tg/adv-tg ]; then
    echo "ERROR: dist/adv-tg/adv-tg not found; run PyInstaller first." >&2
    exit 1
fi
command -v dpkg-deb >/dev/null || { echo "ERROR: dpkg-deb is not installed." >&2; exit 1; }

STAGE="${ROOT_DIR}/build/deb/adv-tg_${VERSION}_${ARCH}"
rm -rf "${ROOT_DIR}/build/deb"
mkdir -p "$STAGE"/{DEBIAN,opt,usr/bin,usr/share/applications,usr/share/pixmaps} \
    "$STAGE/usr/share/icons/hicolor/scalable/apps" "$STAGE/usr/share/doc/adv-tg"

cp -a dist/adv-tg "$STAGE/opt/adv-tg"
ln -s /opt/adv-tg/adv-tg "$STAGE/usr/bin/adv-tg"
install -m 644 packaging/linux/adv-tg.desktop "$STAGE/usr/share/applications/adv-tg.desktop"
install -m 644 src/adv_tg/ui/assets/adv-tg.svg \
    "$STAGE/usr/share/icons/hicolor/scalable/apps/adv-tg.svg"
install -m 644 src/adv_tg/ui/assets/adv-tg.svg "$STAGE/usr/share/pixmaps/adv-tg.svg"
install -m 644 LICENSE "$STAGE/usr/share/doc/adv-tg/copyright"
# dpkg wants directories 755 and no group/other write bits
chmod -R u+rwX,go+rX,go-w "$STAGE"

SIZE_KB="$(du -sk --exclude=DEBIAN "$STAGE" | cut -f1)"

# The bundle carries Python, Qt, numpy, PortAudio and libsndfile; these come from the system
# (see section 5 of packaging/adv-tg.spec). Built on EL9: glibc >= 2.34 (Debian 12,
# Ubuntu 22.04). The "t64" alternatives are the names of Ubuntu 24.04 / Debian 13.
cat > "$STAGE/DEBIAN/control" <<CONTROL
Package: adv-tg
Version: ${VERSION}
Section: science
Priority: optional
Architecture: ${ARCH}
Installed-Size: ${SIZE_KB}
Maintainer: Alberto Pico Lara <albertopicolara@gmail.com>
Homepage: https://github.com/SPQR14/advanced_timegrapher
Depends: libc6 (>= 2.34), libstdc++6, libgcc-s1, libasound2t64 | libasound2,
 libglib2.0-0t64 | libglib2.0-0, libfontconfig1, libfreetype6, libx11-6, libx11-xcb1,
 libxcb1, libgl1, libegl1, libwayland-client0, libwayland-cursor0, libwayland-egl1
Description: Software timegrapher for mechanical and quartz watches
 Advanced Timegrapher measures rate, beat error and amplitude of a watch from
 the sound of its escapement, with an ordinary microphone or a contact pickup.
 .
 Self-contained build: Python, Qt, numpy, PortAudio and libsndfile are included.
CONTROL

cat > "$STAGE/DEBIAN/postinst" <<'SCRIPT'
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -f -t /usr/share/icons/hicolor || true
fi
SCRIPT
cp "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"
chmod 755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"

OUT="dist/adv-tg_${VERSION}_${ARCH}.deb"
rm -f "$OUT"
# xz: readable by every dpkg in use (zstd is not, before Debian 12)
dpkg-deb --root-owner-group -Zxz --build "$STAGE" "$OUT"
echo "Debian package: $OUT ($(du -m "$OUT" | cut -f1) MB)"
