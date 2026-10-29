#!/usr/bin/env bash
# Script para empaquetar Advanced Timegrapher en formato DMG (macOS)
# Crea una imagen de disco con enlace de arrastrar y soltar a /Applications.
# Soporta tanto 'create-dmg' (con diseño visual) como 'hdiutil' (nativo y headless-safe).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

APP_PATH="${1:-dist/Advanced Timegrapher.app}"
DMG_PATH="${2:-dist/adv-tg-macos-arm64.dmg}"
VOL_NAME="${3:-Advanced Timegrapher}"

if [ ! -d "$APP_PATH" ]; then
    echo "ERROR: No se encontró el bundle de aplicación en '$APP_PATH'."
    echo "Primero genere la distribución con PyInstaller:"
    echo "  pyinstaller packaging/adv-tg.spec --noconfirm"
    exit 1
fi

mkdir -p "$(dirname "$DMG_PATH")"
rm -f "$DMG_PATH"

echo "=========================================================="
echo " Creando DMG de instalación para macOS"
echo "   Origen:  ${APP_PATH}"
echo "   Destino: ${DMG_PATH}"
echo "=========================================================="

DMG_SUCCESS=0

# Opción A: Probar con create-dmg si está instalado
if command -v create-dmg &> /dev/null; then
    echo "==> Herramienta create-dmg detectada. Intentando generación visual..."
    set +e
    create-dmg \
        --volname "$VOL_NAME" \
        --window-pos 200 120 \
        --window-size 600 400 \
        --icon-size 100 \
        --icon "Advanced Timegrapher.app" 175 190 \
        --hide-extension "Advanced Timegrapher.app" \
        --app-drop-link 425 190 \
        --no-strip \
        "$DMG_PATH" \
        "$APP_PATH"
    EXIT_CODE=$?
    set -e

    if [ $EXIT_CODE -eq 0 ] && [ -f "$DMG_PATH" ]; then
        DMG_SUCCESS=1
        echo "==> DMG generado exitosamente con create-dmg."
    else
        echo "==> create-dmg no pudo completar la operación (código $EXIT_CODE; típico en entornos headless de CI). Aplicando fallback hdiutil..."
        rm -f "$DMG_PATH"
    fi
fi

# Opción B: Fallback robusto usando hdiutil nativo (sin depender de WindowServer / AppleScript)
if [ "$DMG_SUCCESS" -ne 1 ]; then
    echo "==> Generando DMG con hdiutil nativo con enlace a /Applications..."
    STAGING_DIR="$(mktemp -d -t adv-tg-dmg-XXXXXX)"
    cp -R "$APP_PATH" "$STAGING_DIR/"
    ln -s /Applications "$STAGING_DIR/Applications"

    hdiutil create \
        -volname "$VOL_NAME" \
        -srcfolder "$STAGING_DIR" \
        -ov \
        -format UDZO \
        "$DMG_PATH"

    rm -rf "$STAGING_DIR"
    DMG_SUCCESS=1
fi

if [ -f "$DMG_PATH" ]; then
    SIZE_MB=$(du -m "$DMG_PATH" | cut -f1)
    echo "=========================================================="
    echo " DMG generado con éxito (${SIZE_MB} MB):"
    echo "   ${DMG_PATH}"
    echo "=========================================================="
else
    echo "ERROR: Falló la creación del archivo DMG."
    exit 1
fi
