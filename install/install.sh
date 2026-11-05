#!/usr/bin/env bash
# docconvert - deployment for Linux / macOS
#
# Online  : ./install.sh
# Offline : put packages into ./vendor/ (see README) then ./install.sh --offline
#
# After deployment the tool runs fully offline - no network at conversion time.

set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OFFLINE=0
SKIP_ENGINES=0
CHECK=0

for a in "$@"; do
  case "$a" in
    --offline) OFFLINE=1 ;;
    --skip-engines) SKIP_ENGINES=1 ;;
    --check) CHECK=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
  esac
done

green() { printf "\033[32m  [OK]   %s\033[0m\n" "$1"; }
warn()  { printf "\033[33m  [WARN] %s\033[0m\n" "$1"; }
bad()   { printf "\033[31m  [FAIL] %s\033[0m\n" "$1"; }
head_() { printf "\n\033[36m== %s\033[0m\n" "$1"; }

printf "\ndocconvert deployment\nroot: %s\n" "$ROOT"

# ---------------------------------------------------------------- python
head_ "1. Python"
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then
    if "$c" -c 'import sys;raise SystemExit(0 if sys.version_info>=(3,8) else 1)' 2>/dev/null; then
      PY="$c"; break
    fi
  fi
done
if [ -z "$PY" ]; then
  bad "Python 3.8+ not found"
  [ "$CHECK" = 0 ] && exit 1
else
  green "$($PY -V 2>&1)  ($PY)"
fi

# ---------------------------------------------------------------- pip
head_ "2. Python packages"
PKGS="pymupdf python-docx openpyxl pillow pyyaml python-pptx"
if [ "$CHECK" = 1 ]; then
  for p in fitz docx openpyxl PIL yaml pptx; do
    if $PY -c "import importlib.util as u,sys;sys.exit(0 if u.find_spec('$p') else 1)" 2>/dev/null; then
      green "$p"
    else
      warn "$p missing"
    fi
  done
elif [ "$OFFLINE" = 1 ]; then
  WHL_DIR="$ROOT/install/vendor/wheels"
  if [ -d "$WHL_DIR" ]; then
    "$PY" -m pip install --no-index --find-links "$WHL_DIR" $PKGS && green "packages installed from local wheels"
  else
    warn "offline mode but no $WHL_DIR - skipping pip"
  fi
else
  "$PY" -m pip install --upgrade pip --quiet 2>/dev/null
  # shellcheck disable=SC2086
  "$PY" -m pip install $PKGS && green "python packages ready" || warn "pip had errors"
fi

# ---------------------------------------------------------------- engines
if [ "$SKIP_ENGINES" = 1 ]; then
  head_ "3. Engines - skipped"
else
  head_ "3. Conversion engines"
  PM=""
  command -v apt-get >/dev/null 2>&1 && PM="apt"
  command -v dnf     >/dev/null 2>&1 && PM="dnf"
  command -v pacman  >/dev/null 2>&1 && PM="pacman"
  command -v brew    >/dev/null 2>&1 && PM="brew"

  install_pkg() {
    local pkg="$1"; shift
    if [ "$CHECK" = 1 ]; then
      command -v "$1" >/dev/null 2>&1 && green "$pkg: found" || warn "$pkg: missing"
      return
    fi
    if [ "$OFFLINE" = 1 ]; then
      warn "$pkg: offline mode, install manually from install/vendor/"
      return
    fi
    case "$PM" in
      apt)    sudo apt-get install -y "$pkg" ;;
      dnf)    sudo dnf install -y "$pkg" ;;
      pacman) sudo pacman -S --noconfirm "$pkg" ;;
      brew)   brew install "$pkg" ;;
      *)      warn "no package manager detected, install $pkg manually" ;;
    esac
  }

  command -v soffice >/dev/null 2>&1 && green "LibreOffice: $(command -v soffice)" \
    || install_pkg libreoffice soffice
  command -v pandoc >/dev/null 2>&1 && green "pandoc: $(command -v pandoc)" \
    || install_pkg pandoc pandoc
  command -v tesseract >/dev/null 2>&1 && green "tesseract: $(command -v tesseract)" \
    || install_pkg tesseract-ocr tesseract
  command -v ffmpeg >/dev/null 2>&1 && green "ffmpeg: $(command -v ffmpeg)" \
    || install_pkg ffmpeg ffmpeg

  warn "Chinese OCR: install tesseract-ocr-chi-sim (apt) / tesseract-lang (brew)"
fi

# ---------------------------------------------------------------- verify
head_ "4. Verification"
"$PY" "$ROOT/docconvert.py" doctor
printf "\n"
"$PY" "$ROOT/tests/selftest.py"
printf "\nDone. Quick start:\n  %s %s/docconvert.py convert a.docx b.pdf\n  %s %s/docconvert.py doctor\n" \
  "$PY" "$ROOT" "$PY" "$ROOT"
