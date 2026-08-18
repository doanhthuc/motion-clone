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
    if any(c.isspace() for c in domain):
        raise ConfigError(
            f"DOMAIN trong .env có khoảng trắng: {repr(domain)}\n"
            "  Thường do một trong hai: khoảng trắng thừa cuối dòng, hoặc DOMAIN bị khai hai lần.\n"
            "  (Makefile:30 nối các dòng trùng khoá bằng dấu cách, nên `make gpu-up` cũng đang hỏng\n"
            "   vì đúng lý do này.)\n"
            "  Sửa .env rồi chạy lại."
        )
    api_key = env_get(root / "motions" / ".env", "NUXT_MOTION_API_KEY")
    if not api_key:
        raise ConfigError(
            "Thiếu NUXT_MOTION_API_KEY trong motions/.env.\n"
            "  Chạy: make gpu-bootstrap   (nó tự dán key của pod vào file đó)"
        )
    if any(c.isspace() for c in api_key):
        raise ConfigError(
            f"NUXT_MOTION_API_KEY trong motions/.env có khoảng trắng: {repr(api_key)}\n"
            "  Thường do một trong hai: khoảng trắng thừa cuối dòng, hoặc NUXT_MOTION_API_KEY bị khai hai lần.\n"
            "  (Makefile:30 nối các dòng trùng khoá bằng dấu cách, nên `make gpu-up` cũng đang hỏng\n"
            "   vì đúng lý do này.)\n"
            "  Sửa motions/.env rồi chạy lại."
        )
    return Settings(
        domain=domain,
        api_key=api_key,
        instance_id=env_get(root / ".env", "GPU_INSTANCE_ID"),
    )
