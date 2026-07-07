#!/usr/bin/env zsh
XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
target="${XDG_DATA_HOME}/run_kuranne"

rm -rf $target || echo "Failed to rm"
cp -r $(pwd) $target || echo "Failed to cp"
cd $target
./setup.sh || echo "Failed to setup"

echo "Done!"
