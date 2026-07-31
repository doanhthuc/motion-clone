# ALD 10/06/2026 - bg-remover (rembg). Python/glibc vì onnxruntime KHÔNG có wheel cho Alpine (image api).
FROM python:3.11-slim
WORKDIR /app
# libgl1/libglib2.0-0 cho opencv (dep rembg); libgomp1 cho onnxruntime.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py ./
# Bake model (u2net + u2net_human_seg, ~350MB) vào image → khỏi tải runtime (container ephemeral).
RUN python -c "from rembg import new_session; new_session('u2net'); new_session('u2net_human_seg')"
EXPOSE 8000
CMD ["python", "app.py"]
