#!/usr/bin/env bash
# ALD 16/06/2026 - Launcher cho app Python chạy dưới PM2 qua interpreter=bash.
# LÝ DO: PM2 6.x + Node ≥24 bị bug khi interpreter là đường dẫn Python tuyệt đối — PM2 chạy bootstrap JS
# (ProcessContainerForkBun.js) BẰNG Python → Python parse JS → "SyntaxError: unterminated string literal" →
# crashloop. comfyui (interpreter=bash) không dính, nên ta bọc Python y hệt: PM2 chạy `bash pm2-python.sh`,
# script này exec venv Python. Env (JOB_TYPES, WORKER_TOKEN, PORT…) PM2 set sẵn → inherit xuống Python.
#
# PM2 đặt cwd = thư mục app (worker/ hoặc bg-remover/) nên ./venv/bin/python trỏ đúng venv của app.
# PM2_PY_ENTRY = file Python cần chạy (worker.py | app.py).
# ALD 24/06/2026 - PM2_PY_VENV: override đường dẫn venv. Mặc định ./venv.
set -e
VENV="${PM2_PY_VENV:-./venv}"
exec "$VENV/bin/python" "${PM2_PY_ENTRY:?PM2_PY_ENTRY chưa set trong ecosystem env}"
