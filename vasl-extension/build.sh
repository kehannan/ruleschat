#!/usr/bin/env bash
# Build AskRuleschat-<version>.vmdx — a VASL module extension.
#
# Compiles against the locally installed VASSAL's own engine jar so the
# bytecode is checked against exactly the API the app will run it with.
set -euo pipefail
cd "$(dirname "$0")"

JAVAC="${JAVAC:-/opt/homebrew/opt/openjdk@21/bin/javac}"
VASSAL_LIB="${VASSAL_LIB:-/Applications/VASSAL.app/Contents/Resources/Java}"
VASL_MODULE="${VASL_MODULE:-$HOME/vasl/vasl-6.7.3.vmod}"
VERSION="$(sed -n 's:.*<version>\(.*\)</version>.*:\1:p' extensiondata | head -n 1)"
if [[ -z "$VERSION" ]]; then
  echo "Could not read extension version from extensiondata" >&2
  exit 1
fi
OUT="AskRuleschat-${VERSION}.vmdx"

rm -rf build dist
mkdir -p build/classes dist

"$JAVAC" --release 11 -Xlint:deprecation \
  -cp "$VASSAL_LIB/*:$VASL_MODULE" \
  -d build/classes \
  $(find src -name '*.java')

cp buildFile.xml build/classes/buildFile.xml
cp extensiondata build/classes/extensiondata
(cd build/classes && zip -q -r "../../dist/${OUT}" .)
cp "dist/${OUT}" dist/AskRuleschat.vmdx
echo "Built dist/${OUT}"
echo "Also wrote dist/AskRuleschat.vmdx"
unzip -l "dist/${OUT}"
