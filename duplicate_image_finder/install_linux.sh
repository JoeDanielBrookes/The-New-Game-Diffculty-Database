#!/bin/sh
# Install Duplicate Image Finder for the current user:  sh install_linux.sh
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is needed, e.g.: sudo apt install python3 python3-tk python3-venv"
  exit 1
fi
exec python3 installer.py "$@"
