#!/bin/sh
set -eu
for notes_python in "${CONVERSATION_NOTES_PYTHON:-}" "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3" /usr/local/bin/python3 /usr/bin/python3; do
  if [ -n "$notes_python" ] && [ -x "$notes_python" ]; then
    if "$notes_python" -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then
      exec "$notes_python" "$@"
    fi
  fi
done
if command -v python3 >/dev/null 2>&1; then exec python3 "$@"; fi
echo '未找到 Python 3.9 或以上。请先安装并打开 Codex 桌面应用。' >&2
exit 1
