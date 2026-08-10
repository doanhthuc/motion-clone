"""Kẹp psutil.virtual_memory() theo trần cgroup thay vì RAM của HOST.

ALD 10/08/2026 - TRỊ "ComfyUI tự khởi động lại giữa job, log KHÔNG có traceback".

Triệu chứng: job motion 453 frame đang sampling (Frames 0-81, 0/4 bước) thì 15 giây
sau ComfyUI boot lại từ đầu. Không exception, không "CUDA out of memory" — vì đây
không phải lỗi Python mà là SIGKILL từ cgroup OOM killer:

    /sys/fs/cgroup/memory.max       59999997952   (55,9 GiB — trần THẬT của container)
    /sys/fs/cgroup/memory.peak      60000010240   (đỉnh chạm ĐÚNG trần)
    /sys/fs/cgroup/memory.events    max 18494 / oom_kill 1
    /proc/meminfo MemTotal          129429860 kB  (123 GiB — RAM của HOST)

Nguyên nhân: RunPod cho container thấy /proc/meminfo của host chứ không thấy trần
cgroup. ComfyUI đọc RAM qua psutil nên tự cho mình gấp đôi lượng RAM thật sự có:

  • comfy/model_management.py  MAX_PINNED_MEMORY = ram * 0.90   → 111 GiB trần pinned
  • main.py                    cache inactive = min(128, total) → 123 GiB
  • comfy_execution/caching.py RAMPressureCache so với psutil.virtual_memory().available
                               → thấy host còn 108 GiB rảnh trong khi cgroup đã sát 56 GB

Hệ quả nặng nhất là cái thứ ba: RAM pressure cache KHÔNG BAO GIỜ kích hoạt, nên nó
giữ nguyên umt5 encoder (11,4 GB), CLIP-vision, và tensor ảnh 453 frame của mọi node
(VHS_LoadVideo + 3 nhánh DWPose + face crop ≈ 15 GB). Cộng 12,17 GB block-swap nằm
pinned (page-locked, kernel KHÔNG đòi lại được) → vượt 56 GB → bị giết.

Đây đúng con bug scripts/pod-fe.sh đã trị cho heap Node (xem #cgroup ở file đó);
lần này nó cắn ComfyUI. Cách chữa cũng vậy: lấy số từ cgroup, đừng tin RAM host.

Nạp qua PYTHONPATH trong comfy-start-native.sh — CHỈ ComfyUI dính, không đụng python
khác trên pod. Mọi thứ bọc try/except: hỏng thì im lặng bỏ qua, không chặn Python khởi động.
"""

import sys


def _read_int(path):
    try:
        with open(path) as f:
            v = f.read().strip()
        return None if v in ("", "max") else int(v)
    except Exception:
        return None


def _read_reclaimable(stat_path, keys):
    """Page cache sạch + slab tái thu hồi được — kernel đòi lại được, nên tính là 'còn rảnh'.

    Không cộng phần này thì lúc đọc file model 18 GB, page cache đẩy memory.current lên cao
    và ComfyUI tưởng hết RAM → nhả sạch cache rồi nạp lại liên tục (thrash).
    """
    total = 0
    try:
        with open(stat_path) as f:
            for line in f:
                k, _, v = line.partition(" ")
                if k in keys:
                    total += int(v)
    except Exception:
        return 0
    return total


def _detect_cgroup():
    """→ (limit, current_path, stat_path, reclaimable_keys) hoặc None nếu không có trần."""
    for limit_p, cur_p, stat_p, keys in (
        ("/sys/fs/cgroup/memory.max",
         "/sys/fs/cgroup/memory.current",
         "/sys/fs/cgroup/memory.stat",
         ("inactive_file", "slab_reclaimable")),
        ("/sys/fs/cgroup/memory/memory.limit_in_bytes",
         "/sys/fs/cgroup/memory/memory.usage_in_bytes",
         "/sys/fs/cgroup/memory/memory.stat",
         ("total_inactive_file", "total_slab_reclaimable")),
    ):
        limit = _read_int(limit_p)
        # cgroup v1 ghi "không giới hạn" bằng một số khổng lồ (~2^63) thay vì chuỗi "max".
        if limit is None or limit <= 0 or limit >= (1 << 62):
            continue
        return limit, cur_p, stat_p, keys
    return None


def _install():
    cg = _detect_cgroup()
    if cg is None:
        return
    limit, cur_p, stat_p, keys = cg

    import psutil

    _orig = psutil.virtual_memory
    if limit >= _orig().total:
        return  # trần cgroup không chặt hơn RAM host (máy thật / pod không giới hạn) → khỏi kẹp

    def virtual_memory():
        m = _orig()
        try:
            cur = _read_int(cur_p) or 0
            avail = max(0, min(limit, limit - cur + _read_reclaimable(stat_path=stat_p, keys=keys)))
        except Exception:
            return m
        used = limit - avail
        return m._replace(total=limit, available=avail, used=used,
                          free=min(m.free, avail),
                          percent=round(100.0 * used / limit, 1))

    psutil.virtual_memory = virtual_memory
    print("[sitecustomize] RAM kẹp theo cgroup: {:.1f} GiB (RAM host {:.1f} GiB — KHÔNG dùng)"
          .format(limit / 1024 ** 3, _orig().total / 1024 ** 3), file=sys.stderr)


try:
    _install()
except Exception as e:  # không bao giờ được phép chặn Python khởi động
    print("[sitecustomize] bỏ qua kẹp RAM theo cgroup: {}".format(e), file=sys.stderr)
