# Motion Worker Layout

Worker được tách thành 3 lớp:

- `worker.py`: entrypoint tương thích cũ cho Linux/CUDA.
- `worker_runtime/`: implementation lớn đang giữ nguyên logic pipeline hiện tại.
- `subworkers/`: entrypoint nhỏ theo nhóm job, dùng để chạy nhiều worker độc lập hoặc debug từng nhóm.

## Linux/CUDA Sub-Workers

Chạy trong Docker bằng biến `WORKER_SCRIPT`:

```sh
WORKER_SCRIPT=subworkers/linux/motion.py
WORKER_SCRIPT=subworkers/linux/image.py
WORKER_SCRIPT=subworkers/linux/video.py
WORKER_SCRIPT=subworkers/linux/talk.py
```

Nhóm mặc định:

- `motion.py`: `motion`
- `image.py`: `tryon,create-image,product-overlay`
- `video.py`: `teaser,video,bds,concat`
- `talk.py`: `talk,face-motion,story-film`

Nếu cần override nhóm job, set `JOB_TYPES` trước khi chạy. Các sub-worker chỉ set mặc định khi `JOB_TYPES` chưa có.
