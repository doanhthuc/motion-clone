"""Đọc cấu hình từ .env gốc và motions/.env. Không làm gì khác.

env_get() CỐ Ý sao chép y hệt hàm `env` ở Makefile:30 — kể cả nhược điểm của nó
(cắt từ dấu '#' đầu tiên, xoá MỌI dấu nháy kép ở mọi vị trí). Sửa "cho đúng hơn"
ở đây sẽ tạo ra tình huống cùng một .env mà `make gpu-up` và `make batch` đọc ra
hai giá trị khác nhau — đúng loại lệch âm thầm mà scripts/check-job-types.mjs
tồn tại để chặn.

Hành vi được đo trên GNU Make thực tế ngày 18/08/2026:
- Khoảng trắng đầu dòng và cuối dòng được giữ nguyên
- Nếu một khóa xuất hiện nhiều lần, các giá trị nối với nhau bằng một dấu cách
  (vì $(shell) nối các dòng output bằng dấu cách)
- Bất kỳ thay đổi nào tới Makefile:30 phải cập nhật cả hai chỗ.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class ConfigError(Exception):
    """Cấu hình thiếu hoặc sai. Thông báo phải nói làm gì tiếp theo."""


def _reject_whitespace(value: str, key: str, where: str, *, secret: bool = False) -> None:
    """Kiểm tra giá trị có khoảng trắng, reject nếu có.

    env_get() CỐ Ý sao chép hành vi Make (kể cả nhược điểm), nên nó trả về
    giá trị malformed nguyên vẹn. Lớp này là chỗ chặn chúng — nếu không có,
    một dấu cách cuối dòng sẽ nổi lên thành "backend không trả lời" và người
    dùng sẽ tìm kiếm trong pod, tunnel, Cloudflare tìm một lỗi một ký tự.

    Args:
        value: Giá trị từ .env
        key: Tên khóa (DOMAIN, NUXT_MOTION_API_KEY, ...)
        where: Đường dẫn file (.env, motions/.env, ...)
        secret: Nếu True, che giấu giá trị nhưng giữ khoảng trắng hiển thị
    """
    if not any(c.isspace() for c in value):
        return

    if secret:
        # Che giấu các ký tự không khoảng trắng bằng '•', giữ khoảng trắng
        masked = "".join("•" if not c.isspace() else c for c in value)
        display_value = repr(masked)
    else:
        # Không phải secret — hiển thị giá trị thực
        display_value = repr(value)

    raise ConfigError(
        f"{key} trong {where} có khoảng trắng: {display_value}\n"
        "  Thường do một trong hai: khoảng trắng thừa cuối dòng, hoặc {key} bị khai hai lần.\n"
        "  (Makefile:30 nối các dòng trùng khoá bằng dấu cách, nên `make gpu-up` cũng đang hỏng\n"
        "   vì đúng lý do này.)\n"
        "  Sửa {where} rồi chạy lại."
        .format(key=key, where=where)
    )


def env_get(path: Path, key: str) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    values = []
    for line in text.splitlines():
        if not line.startswith(f"{key}="):
            continue
        value = line.split("=", 1)[1]
        value = re.sub(r"\s*#.*$", "", value)
        value = value.replace('"', "")
        values.append(value)
    return " ".join(values)


@dataclass(frozen=True)
class Settings:
    domain: str
    api_key: str
    instance_id: str

    @property
    def base_url(self) -> str:
        return f"https://{self.domain}"


def load_settings(root: Path = ROOT) -> Settings:
    domain = env_get(root / ".env", "DOMAIN")
    if not domain:
        raise ConfigError(
            "Thiếu DOMAIN trong .env.\n"
            "  Chạy: make gpu-preflight   (nó liệt kê mọi biến còn thiếu)"
        )
    _reject_whitespace(domain, "DOMAIN", ".env", secret=False)

    api_key = env_get(root / "motions" / ".env", "NUXT_MOTION_API_KEY")
    if not api_key:
        raise ConfigError(
            "Thiếu NUXT_MOTION_API_KEY trong motions/.env.\n"
            "  Chạy: make gpu-bootstrap   (nó tự dán key của pod vào file đó)"
        )
    _reject_whitespace(api_key, "NUXT_MOTION_API_KEY", "motions/.env", secret=True)

    return Settings(
        domain=domain,
        api_key=api_key,
        instance_id=env_get(root / ".env", "GPU_INSTANCE_ID"),
    )
