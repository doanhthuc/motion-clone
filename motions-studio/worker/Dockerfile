FROM python:3.11-slim
# ffmpeg → montage/thay nhạc; wget → tải voice Piper
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg wget ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# Piper TTS voice tiếng Việt (offline, ổn định — thay edge-tts hay bị Microsoft chặn)
RUN mkdir -p /app/piper-voice && \
    wget -qO /app/piper-voice/vi_VN-vais1000-medium.onnx      https://huggingface.co/rhasspy/piper-voices/resolve/main/vi/vi_VN/vais1000/medium/vi_VN-vais1000-medium.onnx && \
    wget -qO /app/piper-voice/vi_VN-vais1000-medium.onnx.json https://huggingface.co/rhasspy/piper-voices/resolve/main/vi/vi_VN/vais1000/medium/vi_VN-vais1000-medium.onnx.json
# worker.py = Linux/CUDA compatibility entrypoint. worker_macos.py = macOS/MPS wrapper
# (native ngoài Docker). worker_runtime/ giữ implementation lớn; subworkers/ là entrypoint nhỏ
# theo nhóm job, ví dụ WORKER_SCRIPT=subworkers/linux/image.py.
COPY . ./
# WORKER_SCRIPT chọn script (mặc định worker.py). Native macOS: xem README, KHÔNG chạy qua image này.
CMD ["sh", "-c", "exec python -u ${WORKER_SCRIPT:-worker.py}"]
