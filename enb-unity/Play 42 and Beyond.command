#!/bin/zsh
set -eu
cd -- "${0:A:h}"
exec /usr/bin/python3 Tools/install_mac.py --launch-vm
