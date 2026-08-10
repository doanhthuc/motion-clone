"""RAM THẬT của box = trần cgroup, KHÔNG phải /proc/meminfo của host.

ALD 10/08/2026 - RunPod cho container thấy /proc/meminfo của HOST chứ không thấy trần
cgroup. Trên pod motion:

    /sys/fs/cgroup/memory.max   59999997952   trần THẬT của container (55,9 GiB)
    /proc/meminfo MemTotal      129429860 kB  RAM host (123 GiB)

`os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")` đọc con số 123 GiB kia. Nơi
dùng nó trong linux.py là trần frame của preset drv-Ns:

    _fcap = 601 if _ram_gb >= 120 else 481

nên trên pod này nó luôn chọn 601 — dù container chỉ có 55,9 GiB. Job motion 453 frame
đã bị cgroup OOM-kill hai lần vì đúng nhóm nguyên nhân này (commit 82c9e58); 601 frame
là mốc còn nặng hơn mà chưa ai đo.

Đây là lần thứ ba con bug "tin RAM host" xuất hiện, hai lần trước đã trị ở:
  • scripts/pod-fe.sh              — heap V8 của Node lúc build FE
  • scripts/pysite/sitecustomize.py — psutil.virtual_memory() của ComfyUI

sitecustomize.py chỉ nạp cho ComfyUI qua PYTHONPATH nên KHÔNG che được worker, và nó kẹp
psutil chứ không kẹp os.sysconf. Vì vậy worker cần bản riêng này.

Cùng thuật toán với sitecustomize.py `_detect_cgroup` và api/src/box-ram.js — giữ ĐỒNG BỘ
khi sửa một trong ba.
"""
import os

# cgroup v1 ghi "không giới hạn" bằng một số khổng lồ (~2^63) thay vì chuỗi "max".
_UNLIMITED = 1 << 62

_LIMIT_PATHS = (
    "/sys/fs/cgroup/memory.max",                    # cgroup v2
    "/sys/fs/cgroup/memory/memory.limit_in_bytes",  # cgroup v1
)


def _read_limit_bytes(path):
    try:
        with open(path) as f:
            raw = f.read().strip()
    except Exception:
        return 0
    if not raw or raw == "max":
        return 0
    try:
        value = int(raw)
    except ValueError:
        return 0
    return 0 if value <= 0 or value >= _UNLIMITED else value


def host_ram_bytes():
    """RAM của HOST theo /proc/meminfo. Trên container RunPod đây là con số SAI để tính ngân sách."""
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return 0


def box_ram_bytes(host_bytes=None, paths=_LIMIT_PATHS):
    """Trần RAM thật (bytes). Trả trần cgroup khi nó CHẶT HƠN RAM host; ngược lại trả RAM host.

    `host_bytes`/`paths` chỉ để test — production gọi không tham số.
    """
    host = host_ram_bytes() if host_bytes is None else host_bytes
    for path in paths:
        limit = _read_limit_bytes(path)
        # host = 0 nghĩa là không đọc được RAM host → tin trần cgroup.
        if limit > 0 and (host <= 0 or limit < host):
            return limit
    return host


def box_ram_gb(host_bytes=None, paths=_LIMIT_PATHS):
    return box_ram_bytes(host_bytes=host_bytes, paths=paths) / (1024 ** 3)
