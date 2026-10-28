#!/bin/bash
# Builds AgentCommandTab.app, unsigned, into packaging/macos_app/dist/ with py2app, from a fresh
# build environment so the app holds only the app's own dependencies. sign_and_notarize.sh signs it.
# See docs/RELEASING.md.
#
# PYTHON picks the Python the app bundles. It must be a framework build (Homebrew's and python.org's
# are). Homebrew's runs on Apple silicon only; python.org's universal2 installer would also cover
# Intel Macs.
set -euo pipefail

PACKAGING_FOLDER="$(cd "$(dirname "$0")" && pwd)"
REPOSITORY_FOLDER="$(cd "$PACKAGING_FOLDER/../.." && pwd)"
PYTHON="${PYTHON:-/opt/homebrew/bin/python3.13}"
PY2APP_VERSION="0.28.10"

rm -rf "$PACKAGING_FOLDER/build" "$PACKAGING_FOLDER/dist"
mkdir -p "$PACKAGING_FOLDER/build"
"$PYTHON" -m venv "$PACKAGING_FOLDER/build/venv"
# PYTHONPATH is cleared so packages from the user's own environment can't slip into the app.
env -u PYTHONPATH "$PACKAGING_FOLDER/build/venv/bin/pip" install --quiet "$REPOSITORY_FOLDER" "py2app==$PY2APP_VERSION"
cd "$PACKAGING_FOLDER"
env -u PYTHONPATH build/venv/bin/python make_app_icon_file.py build/AgentCommandTab.icns
env -u PYTHONPATH build/venv/bin/python setup_py2app.py py2app
# The source of the bundled iterm2 version, which the GPL asks to offer next to the app (NOTICE.txt).
ITERM2_VERSION="$(build/venv/bin/python -c 'import importlib.metadata; print(importlib.metadata.version("iterm2"))')"
env -u PYTHONPATH build/venv/bin/pip download --quiet --no-binary :all: --no-deps -d dist "iterm2==$ITERM2_VERSION"
echo "built $PACKAGING_FOLDER/dist/AgentCommandTab.app and $PACKAGING_FOLDER/dist/iterm2-$ITERM2_VERSION.tar.gz"
