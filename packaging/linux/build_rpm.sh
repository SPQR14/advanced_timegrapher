#!/usr/bin/env bash
# Script para empaquetar Advanced Timegrapher en formato RPM
# Requiere que se haya ejecutado previamente PyInstaller:
#   pyinstaller packaging/adv-tg.spec --noconfirm

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

# 1. Obtener versión (argumento o desde código fuente / pyproject.toml)
VERSION="${1:-}"
if [ -z "$VERSION" ]; then
    VERSION=$(python3 -c "import tomllib, pathlib; print(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['version'])" 2>/dev/null || true)
fi
if [ -z "$VERSION" ]; then
    VERSION=$(grep -E '^__version__\s*=' src/adv_tg/__init__.py | cut -d'"' -f2 2>/dev/null || true)
fi
if [ -z "$VERSION" ]; then
    VERSION="0.6.3"
fi

echo "=========================================================="
echo " Construyendo paquete RPM para Advanced Timegrapher ${VERSION}"
echo "=========================================================="

# 2. Validar que la compilación de PyInstaller exista
if [ ! -d "dist/adv-tg" ] || [ ! -f "dist/adv-tg/adv-tg" ]; then
    echo "ERROR: No se encontró 'dist/adv-tg/adv-tg'."
    echo "Primero genere la distribución standalone con PyInstaller:"
    echo "  pyinstaller packaging/adv-tg.spec --noconfirm"
    exit 1
fi

# 3. Validar disponibilidad de rpmbuild
if ! command -v rpmbuild &> /dev/null; then
    echo "ERROR: 'rpmbuild' no está instalado en el sistema."
    echo "Instalación según distribución:"
    echo "  - Fedora / RHEL / CentOS: sudo dnf install rpm-build"
    echo "  - openSUSE:              sudo zypper install rpm-build"
    echo "  - Debian / Ubuntu:        sudo apt-get install rpm"
    exit 1
fi

# 4. Preparar árbol de construcción aislado para rpmbuild
RPM_TOPDIR="${ROOT_DIR}/build/rpmbuild"
rm -rf "$RPM_TOPDIR"
mkdir -p "${RPM_TOPDIR}"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}

ARCH="$(uname -m)"
echo "==> Arquitectura detectada: ${ARCH}"
echo "==> Directorio temporal RPM: ${RPM_TOPDIR}"

# 5. Ejecutar rpmbuild
rpmbuild -bb \
    --define "_topdir ${RPM_TOPDIR}" \
    --define "_sourcedir ${ROOT_DIR}" \
    --define "version ${VERSION}" \
    --target "${ARCH}" \
    "${ROOT_DIR}/packaging/linux/adv-tg.rpm.spec"

# 6. Copiar el RPM generado a dist/
RPM_FILE=$(find "${RPM_TOPDIR}/RPMS" -type f -name "*.rpm" | head -n 1)

if [ -n "$RPM_FILE" ] && [ -f "$RPM_FILE" ]; then
    RPM_BASENAME=$(basename "$RPM_FILE")
    cp "$RPM_FILE" "dist/${RPM_BASENAME}"
    echo "=========================================================="
    echo " Paquete RPM generado exitosamente:"
    echo "   dist/${RPM_BASENAME}"
    echo "=========================================================="
else
    echo "ERROR: No se encontró el archivo .rpm generado en ${RPM_TOPDIR}/RPMS"
    exit 1
fi
