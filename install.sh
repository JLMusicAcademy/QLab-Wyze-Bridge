#!/usr/bin/env bash
#
# install.sh — one-shot installer for the QLab -> Wyze bridge on macOS.
#
# It will:
#   1. Find a compatible Python (3.10 or 3.11), offering to install 3.11
#      via Homebrew if needed.
#   2. Create a clean virtualenv in ./.venv.
#   3. Install all Python dependencies (in the right order for the legacy
#      Wyze SDK build dependencies).
#   4. Set up your .env credentials file (optionally entering them now).
#   5. Verify the install and test the Wyze connection.
#
# Re-runnable: running it again rebuilds the virtualenv from scratch but
# leaves your existing .env in place.
#
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO"

# Python versions we accept. 3.10/3.11 ship distutils and work with the Wyze
# SDK's legacy dependencies; 3.12+ removed distutils and is unreliable here.
PREFERRED="3.11"
ACCEPTED=("3.11" "3.10")

bold()  { printf "\033[1m%s\033[0m\n" "$*"; }
info()  { printf "\033[1;34m==>\033[0m %s\n" "$*"; }
ok()    { printf "\033[1;32m✓\033[0m %s\n" "$*"; }
warn()  { printf "\033[1;33m!\033[0m  %s\n" "$*"; }
fail()  { printf "\033[1;31mError:\033[0m %s\n" "$*" >&2; }

# --- 1. Locate a compatible Python -----------------------------------------
py_version() { "$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true; }

is_accepted() {
  local v="$1" a
  for a in "${ACCEPTED[@]}"; do [ "$v" = "$a" ] && return 0; done
  return 1
}

find_python() {
  # Try preferred first, then any accepted version, across common locations.
  local cand
  for cand in \
      "python$PREFERRED" \
      /opt/homebrew/bin/python$PREFERRED /usr/local/bin/python$PREFERRED \
      python3.10 /opt/homebrew/bin/python3.10 /usr/local/bin/python3.10 \
      python3 python; do
    command -v "$cand" >/dev/null 2>&1 || continue
    is_accepted "$(py_version "$cand")" && { command -v "$cand"; return 0; }
  done
  return 1
}

bold "QLab -> Wyze Bridge installer"
echo

info "Looking for a compatible Python ($(IFS=/; echo "${ACCEPTED[*]}"))..."
PYTHON="$(find_python || true)"

if [ -z "$PYTHON" ]; then
  warn "No compatible Python found (need ${ACCEPTED[*]}; newer versions don't work with the Wyze SDK's dependencies)."
  if command -v brew >/dev/null 2>&1; then
    printf "Install Python %s with Homebrew now? [y/N] " "$PREFERRED"
    read -r ans || ans=""
    case "$ans" in
      y|Y)
        info "Installing python@$PREFERRED via Homebrew..."
        brew install "python@$PREFERRED"
        PYTHON="$(find_python || true)"
        ;;
    esac
  else
    fail "Homebrew isn't installed. Install Python $PREFERRED from"
    fail "https://www.python.org/downloads/release/python-3119/ (or install Homebrew), then re-run."
  fi
fi

if [ -z "$PYTHON" ]; then
  fail "Could not find or install a compatible Python. Aborting."
  exit 1
fi
ok "Using Python $(py_version "$PYTHON")  ($PYTHON)"

# --- 2. Virtualenv ----------------------------------------------------------
if [ -d "$REPO/.venv" ]; then
  info "Removing existing .venv for a clean install..."
  rm -rf "$REPO/.venv"
fi
info "Creating virtualenv (.venv)..."
"$PYTHON" -m venv "$REPO/.venv"
VENV_PY="$REPO/.venv/bin/python"

# --- 3. Dependencies --------------------------------------------------------
info "Upgrading pip..."
"$VENV_PY" -m pip install --quiet --upgrade pip
# The Wyze SDK pulls in blackboxprotobuf, whose legacy setup.py needs an
# older setuptools to build. Install that first, then the rest.
info "Installing build tools (setuptools<66, wheel)..."
"$VENV_PY" -m pip install --quiet "setuptools<66" wheel
info "Installing dependencies (this can take a minute)..."
"$VENV_PY" -m pip install --quiet -r "$REPO/requirements.txt"
ok "Dependencies installed."

# --- 4. Credentials (.env) --------------------------------------------------
env_has_real_creds() {
  [ -f "$REPO/.env" ] && grep -q '^WYZE_EMAIL=' "$REPO/.env" \
    && ! grep -q 'you@example.com' "$REPO/.env"
}

write_env() {
  # Values are single-quoted so $ # etc. are taken literally (no interpolation).
  cat >"$REPO/.env" <<EOF
WYZE_EMAIL='$1'
WYZE_PASSWORD='$2'
WYZE_KEY_ID='$3'
WYZE_API_KEY='$4'
EOF
  chmod 600 "$REPO/.env"
}

has_single_quote() { case "$1" in *\'*) return 0 ;; *) return 1 ;; esac; }

if env_has_real_creds; then
  ok "Existing .env with credentials found — leaving it untouched."
else
  echo
  info "Wyze credentials are needed. Get an API key at https://developer-api-console.wyze.com/"
  printf "Enter them now? (No = create a blank .env to edit later) [Y/n] "
  read -r ans || ans=""
  case "$ans" in
    n|N)
      [ -f "$REPO/.env" ] || cp "$REPO/.env.example" "$REPO/.env"
      warn "Created .env from template — edit it with your credentials before starting."
      ;;
    *)
      printf "  Wyze email: ";        read -r c_email || c_email=""
      printf "  Wyze password: ";     read -rs c_pass || c_pass=""; echo
      printf "  Wyze API Key ID: ";   read -r c_keyid || c_keyid=""
      printf "  Wyze API Key: ";      read -r c_apikey || c_apikey=""
      if [ -z "$c_email" ] || [ -z "$c_pass" ]; then
        [ -f "$REPO/.env" ] || cp "$REPO/.env.example" "$REPO/.env"
        warn "Email/password left blank — created .env from template to edit later."
      elif has_single_quote "$c_email$c_pass$c_keyid$c_apikey"; then
        cp "$REPO/.env.example" "$REPO/.env"
        warn "A value contained a single quote, which this prompt can't write safely."
        warn "Created .env from template — please edit it by hand."
      else
        write_env "$c_email" "$c_pass" "$c_keyid" "$c_apikey"
        ok "Saved credentials to .env (permissions set to 600)."
      fi
      ;;
  esac
fi

# --- 5. Make helper scripts executable & verify -----------------------------
chmod +x "$REPO/bridgectl" "$REPO/Start QLab-Wyze Bridge.command" \
         "$REPO/Stop QLab-Wyze Bridge.command" 2>/dev/null || true

info "Verifying installation..."
"$VENV_PY" -c "import qlab_wyze_bridge, wyze_sdk, pythonosc, yaml, dotenv, truststore" \
  && ok "All modules import correctly."

if env_has_real_creds; then
  echo
  info "Testing the Wyze connection (logs in and lists your bulbs)..."
  if "$VENV_PY" -m qlab_wyze_bridge --list-devices; then
    ok "Wyze connection works."
  else
    warn "Couldn't list devices. Double-check the credentials in .env, then run: ./bridgectl start"
  fi
fi

# --- Done -------------------------------------------------------------------
echo
bold "Installation complete."
echo
echo "Start the bridge:"
echo "    ./bridgectl start          (or double-click 'Start QLab-Wyze Bridge.command')"
echo "Check status / logs / stop:"
echo "    ./bridgectl status | logs | stop"
echo "Run automatically at every login:"
echo "    ./bridgectl install"
echo
echo "Then point a QLab Network (OSC) patch at this Mac on port 9000 and send"
echo "messages like  /wyze/all/on  or  /wyze/all/color ff0000"
