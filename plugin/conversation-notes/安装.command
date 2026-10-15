#!/bin/sh
set -eu
notes_package_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec /bin/sh "$notes_package_dir/scripts/run-python.sh" "$notes_package_dir/scripts/install.py"
