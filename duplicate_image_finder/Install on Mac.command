#!/bin/bash
# Double-click this file in Finder to install Duplicate Image Finder.
# (If macOS says it can't be opened, right-click it and choose Open.)
cd "$(dirname "$0")" || exit 1

PY=""
for candidate in \
    /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
    /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys, tkinter; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
    PY="$candidate"; break
  fi
done

if [ -z "$PY" ]; then
  echo "Python 3.9 or newer (with Tkinter) is needed."
  echo "Opening python.org: install Python, then double-click this file again."
  open "https://www.python.org/downloads/macos/"
else
  "$PY" installer.py "$@"
fi
echo
read -r -n 1 -p "Press any key to close this window..."
