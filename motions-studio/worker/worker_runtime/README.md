# Worker Runtime

Runtime chỉ còn Linux/CUDA (macOS/MPS đã bỏ):

- `linux.py`: Linux/CUDA runtime và pipeline implementation đầy đủ.
- `runner.py`: vòng lặp worker dùng chung: heartbeat, claim job, dispatch pipeline, bắt lỗi, retry network.

Khi thêm hoặc sửa pipeline:

1. Sửa implementation trong `linux.py`.
2. Đăng ký handler vào `PIPELINES`.
3. Nếu muốn chạy riêng pipeline/nhóm pipeline, thêm entrypoint trong `../subworkers/`.

Các callback platform-specific nên để trong runtime:

- Linux: guard RAM/swap, pre-run RAM/VRAM log.

## Wan Animate multi-window (Linux)

Motion Transfer đã quay về phương pháp ban đầu `autoregressive`: AnimateEmbeds tự chạy từng window 81 frame,
không gắn `WanVideoContextOptions`, không overlap và không blend giữa hai dự đoán. Cách này ưu tiên loại bỏ hoàn
toàn vùng nhòe/flash tại điểm giao; đổi lại clip dài có thể tích lũy identity/pose drift ở các window sau.
Window 81 tạo 80 frame mới sau 1 frame ref, nên clip 5/10/15/20/30s @16fps được chia đều, không còn
tail window rất ngắn ở cuối clip. Màu giữ RAW/disabled; không match histogram toàn frame vì có thể làm mặt đậm.

Timeline output tách khỏi lưới temporal `4k+1` của Wan: runtime ceil số frame Wan để phủ đủ rồi trim/pad master
theo số frame phát hành. Ví dụ 15s/30fps render nội bộ 453 frame nhưng MP4 cuối phải đúng 450 frame = 15.000s.

Motion Transfer chỉ dùng profile Fast: LightX2V strength `1`, `4` bước, CFG `1`, scheduler `dpm++_sde`.
Các field legacy `renderProfile=natural`, `hq=true`, `steps=20` và preset `8s-720p-max` đều tự hạ về Fast.

Các tham số job hỗ trợ:

- `windowMode`/`window_mode` luôn được chuẩn hóa thành `autoregressive` cho job Motion Transfer.
- `frame_window_size` được chuẩn hóa thành `81` cho Motion Transfer.
- `windowColormatch`/`window_colormatch` mặc định `disabled`; chỉ bật tường minh để A/B.
- Các field `contextFrames`, `contextOverlap`, `contextStride`, `contextSchedule`, `contextFuseMethod` và
  `contextFreeNoise` không còn tham gia đường chạy Motion Transfer.
