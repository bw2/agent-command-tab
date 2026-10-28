#!/bin/bash
# Signs AgentCommandTab.app (built by build_app.sh) with a Developer ID certificate, packs it into a
# disk image, has Apple notarize the disk image and staples Apple's ticket to it, so that macOS
# opens the downloaded app without a warning. The one-time setup is in docs/RELEASING.md.
#
# Usage: sign_and_notarize.sh "Developer ID Application: <name> (<team ID>)" <notary profile>
#   <notary profile>: the name given to `xcrun notarytool store-credentials`.
set -euo pipefail

if [ $# -ne 2 ]; then
    sed -n 2,7p "$0"
    exit 1
fi
SIGNING_IDENTITY="$1"
NOTARY_PROFILE="$2"
PACKAGING_FOLDER="$(cd "$(dirname "$0")" && pwd)"
APP="$PACKAGING_FOLDER/dist/AgentCommandTab.app"
ENTITLEMENTS="$PACKAGING_FOLDER/entitlements.plist"
VERSION="$(/usr/libexec/PlistBuddy -c "Print CFBundleShortVersionString" "$APP/Contents/Info.plist")"
DISK_IMAGE="$PACKAGING_FOLDER/dist/AgentCommandTab-$VERSION.dmg"

sign() {
    codesign --force --timestamp --options runtime --sign "$SIGNING_IDENTITY" "$@"
}

# Inside out: the libraries first, then the Python framework, then the two programs. Both programs
# get the entitlements: the app's own runs the part that restarts the app, and python runs the app.
find "$APP/Contents" -type f \( -name "*.so" -o -name "*.dylib" \) -print0 | while IFS= read -r -d "" library; do
    sign "$library"
done
sign "$APP/Contents/Frameworks/Python.framework"
sign --entitlements "$ENTITLEMENTS" "$APP/Contents/MacOS/python"
sign --entitlements "$ENTITLEMENTS" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

DISK_IMAGE_FOLDER="$(mktemp -d)"
ditto "$APP" "$DISK_IMAGE_FOLDER/AgentCommandTab.app"
ln -s /Applications "$DISK_IMAGE_FOLDER/Applications"  # so the app can be dragged into Applications
rm -f "$DISK_IMAGE"
hdiutil create -volname "AgentCommandTab" -srcfolder "$DISK_IMAGE_FOLDER" -format UDZO "$DISK_IMAGE"
rm -rf "$DISK_IMAGE_FOLDER"
codesign --force --timestamp --sign "$SIGNING_IDENTITY" "$DISK_IMAGE"

NOTARY_RESULT="$(mktemp)"
xcrun notarytool submit "$DISK_IMAGE" --keychain-profile "$NOTARY_PROFILE" --wait --output-format json > "$NOTARY_RESULT"
if [ "$(plutil -extract status raw -o - "$NOTARY_RESULT")" != "Accepted" ]; then
    cat "$NOTARY_RESULT"
    echo
    echo "Notarization failed. Apple's log: xcrun notarytool log $(plutil -extract id raw -o - "$NOTARY_RESULT") --keychain-profile $NOTARY_PROFILE"
    exit 1
fi
rm -f "$NOTARY_RESULT"
xcrun stapler staple "$DISK_IMAGE"
spctl --assess --type open --context context:primary-signature --verbose "$DISK_IMAGE"
echo "SHA-256 for the Homebrew cask:"
shasum -a 256 "$DISK_IMAGE"
