#!/usr/bin/env bash
# The CEO app (app/), built into a Mac .app in one command, on the Mac that will run it. From the kernel's folder:
#
#     tools/app.sh          # asks on the terminal before it installs Rust, when cargo is missing
#     tools/app.sh --yes    # installs Rust, when missing, without asking
#
# It installs tauri-cli at the one version below when `cargo tauri --version` says another, makes the bundle's
# icons from app/icon.svg, and builds with `cargo tauri build`. When this Mac's keychain holds a "Developer ID
# Application" identity (CEO_APP_IDENTITY names one when there are several), the bundler signs with it, with the
# hardened runtime; when notarytool credentials are stored as a keychain profile (`xcrun notarytool
# store-credentials ceo-app`, or the name in CEO_APP_NOTARY_PROFILE), it notarizes the app and staples the ticket.
# Without an identity the build is unsigned and local. It ends by printing the .app's path; nothing is published.
set -euo pipefail

TAURI_CLI=2.12.4  # the one tauri-cli this app is built with
RUSTUP="curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y"
root=$(cd "$(dirname "$0")/.." && pwd)
answer=
for arg in "$@"; do
  case $arg in
    --yes) answer=yes ;;
    *) echo "tools/app.sh takes only --yes, not $arg" >&2; exit 2 ;;
  esac
done

if [ "$(uname -s)" != Darwin ]; then
  echo "tools/app.sh builds the CEO app on macOS only, and this system is $(uname -s)" >&2
  exit 1
fi

# Rust, by its official rustup installer, only once the owner says so.
cargo_env="${CARGO_HOME:-$HOME/.cargo}/env"
if ! command -v cargo >/dev/null 2>&1 && [ -f "$cargo_env" ]; then
  . "$cargo_env"
fi
if ! command -v cargo >/dev/null 2>&1; then
  if [ "$answer" != yes ]; then
    if [ ! -t 0 ]; then
      echo "tools/app.sh needs Rust and has no terminal to ask on: install it with $RUSTUP, or run tools/app.sh --yes" >&2
      exit 1
    fi
    read -r -p "cargo is missing. Install Rust now with the official rustup installer (https://sh.rustup.rs)? [y/N] " answer
    case $answer in
      [yY]*) ;;
      *) echo "Nothing was installed. Rust installs with: $RUSTUP" >&2; exit 1 ;;
    esac
  fi
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
  . "$cargo_env"
fi

if [ "$(cargo tauri --version 2>/dev/null || true)" != "tauri-cli $TAURI_CLI" ]; then
  cargo install tauri-cli --version "=$TAURI_CLI" --locked
fi

cd "$root/app"
cargo tauri icon icon.svg --output src-tauri/icons

# The signing identity: a "Developer ID Application" one of this keychain, or the one CEO_APP_IDENTITY names.
identity=${CEO_APP_IDENTITY:-}
if [ -z "$identity" ]; then
  found=$(security find-identity -v -p codesigning 2>/dev/null \
    | sed -n 's/.*"\(Developer ID Application: [^"]*\)".*/\1/p' | sort -u)
  if [ "$(printf '%s' "$found" | grep -c .)" -gt 1 ]; then
    echo "This keychain holds several Developer ID Application identities; name one in CEO_APP_IDENTITY:" >&2
    printf '%s\n' "$found" >&2
    exit 1
  fi
  identity=$found
fi
profile=${CEO_APP_NOTARY_PROFILE:-ceo-app}

# Tauri's bundler signs with APPLE_SIGNING_IDENTITY and would notarize with its own credentials, which are unset
# here: this script notarizes with the keychain profile instead.
unset APPLE_SIGNING_IDENTITY APPLE_CERTIFICATE APPLE_CERTIFICATE_PASSWORD APPLE_ID APPLE_PASSWORD APPLE_TEAM_ID \
  APPLE_API_ISSUER APPLE_API_KEY APPLE_API_KEY_PATH
if [ -n "$identity" ]; then
  APPLE_SIGNING_IDENTITY=$identity cargo tauri build --bundles app
else
  cargo tauri build --bundles app --no-sign
fi
app=$(ls -d "$root"/app/src-tauri/target/release/bundle/macos/*.app | head -1)

local_note="a locally built app carries no quarantine and opens on the Mac that built it"
if [ -z "$identity" ]; then
  echo "An unsigned local build: this keychain holds no Developer ID Application identity; $local_note."
else
  codesign --verify --deep --strict "$app"
  if xcrun notarytool history --keychain-profile "$profile" >/dev/null 2>&1; then
    zipped="$(mktemp -d)/app.zip"
    ditto -c -k --keepParent "$app" "$zipped"
    xcrun notarytool submit "$zipped" --keychain-profile "$profile" --wait
    xcrun stapler staple "$app"
    echo "Signed as $identity, with the hardened runtime, notarized and stapled."
  else
    echo "Signed as $identity, not notarized: no notarytool keychain profile $profile" \
      "(xcrun notarytool store-credentials $profile); $local_note."
  fi
fi
echo "$app"
