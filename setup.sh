echo "-- Checking for python3..."
command -v python3 >/dev/null 2>&1 || { echo "[ ERROR ] Can't execute python3"; exit 1; }

# Check Python Version (>= 3.11)
python3 -c "import sys; exit(0) if sys.version_info >= (3, 11) else exit(1)" || { echo "[ ERROR ] Python 3.11+ required"; exit 1; }

echo "-- Setting up virtual environment..."
if [[ ! -d ./.venv ]]; then
    python3 -m venv .venv
else
    rm -rf .venv
    python3 -m venv .venv
fi
source ./.venv/bin/activate

# --- Install Dependencies --- #
echo "-- Installing dependencies..."
pip install .

# --- Create Wrapper Script --- #
echo "-- Creating runner script..."
CURRENT_DIR=$(pwd)
RUN_SCRIPT="${CURRENT_DIR}/run"

cat <<EOF > "$RUN_SCRIPT"
#!/usr/bin/env bash
exec "${CURRENT_DIR}/.venv/bin/python" "${CURRENT_DIR}/src/main.py" "\$@"
EOF

chmod +x "$RUN_SCRIPT"

#--- Symlink ---#
echo "-- Installing script into bin"

DEFAULT_BIN_PATH="$HOME/.local/bin"
if [[ ! -z $XDG_BIN_HOME ]]; then
    DEFAULT_BIN_PATH=$XDG_BIN_HOME
fi
mkdir -p "$DEFAULT_BIN_PATH"
ln -sf "$RUN_SCRIPT" "$DEFAULT_BIN_PATH/run"

#--- Clear & Clean ---#
echo "[ SUCCESS ] Setup complete!"
echo "You can now use 'run' command."