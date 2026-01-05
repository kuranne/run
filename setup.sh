#--- Python Setup ---#
# Colors
GREEN='\033[0;32m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

echo "${CYAN}Checking for python3...${NC}"
[[ $(command -v python3) ]] || { echo "${RED}Can't execute python3${NC}"; exit 1; }

# Check Python Version (>= 3.11)
python3 -c "import sys; exit(0) if sys.version_info >= (3, 11) else exit(1)" || { echo "${RED}Python 3.11+ required${NC}"; exit 1; }

echo "${CYAN}Setting up virtual environment...${NC}"
python3 -m venv .venv
source ./.venv/bin/activate

#--- Create Wrapper Script ---#
echo "${CYAN}Creating runner script...${NC}"
CURRENT_DIR=$(pwd)
RUN_SCRIPT="${CURRENT_DIR}/run"

cat <<EOF > "$RUN_SCRIPT"
#!/bin/bash
exec "${CURRENT_DIR}/.venv/bin/python" "${CURRENT_DIR}/source/main.py" "\$@"
EOF

chmod +x "$RUN_SCRIPT"

#--- Symlink ---#
echo "${CYAN}Symlinking to ~/bin...${NC}"
mkdir -p ${HOME}/bin
ln -sf "$RUN_SCRIPT" ${HOME}/bin/run

#--- Clear & Clean ---#
echo "${GREEN}Setup complete!${NC}"
echo "You can now use 'run' command."