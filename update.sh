#!/usr/bin/env zsh
target="${HOME}/.local/share/run_kuranne"

rm -rf $target || echo "Failed to rm"
cp -r $(pwd) $target || echo "Failed to cp"
cd $target
./setup.sh || echo "Failed to setup"

echo "Done!"
