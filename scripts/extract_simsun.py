"""从本机 Windows 的 ``simsun.ttc`` 提取 SimSun / NSimSun 单文件 TTF。

背景：ezdxf 的字体管理器**不索引 .ttc 集合**（只识别单文件 TTF），而 ODA 转换
中文图纸时文字样式常引用 ``SimSun.ttf`` / ``NSimSun.ttf``，缺少对应单文件会导致
中文回退到无中文字形的内置 ``arial.ttf`` 而显示为方框。

SimSun / NSimSun 是微软专有字体，因此**不随仓库分发**。本脚本在你自己的 Windows
许可范围内从本机字体提取，仅供本地渲染使用；提取产物已被 ``.gitignore`` 排除，
不会进入版本库。

用法：

    python scripts/extract_simsun.py

依赖：fontTools（已包含在 ``pip install -e ".[dev]"`` 的 dev 依赖中）。
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from fontTools.ttLib import TTFont, TTCollection
except ImportError:  # pragma: no cover
    print("缺少依赖 fontTools，请先执行：pip install fonttools", file=sys.stderr)
    sys.exit(1)

# 目标字体族名 → 输出文件名（小写）。simsun.ttc 同时含 SimSun 与 NSimSun。
_TARGET_FAMILIES = {"SimSun": "simsun.ttf", "NSimSun": "nsimsun.ttf"}

_WINDOWS_FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/simsun.ttc"),
    Path("C:/Windows/Fonts/simsun.ttf"),
)


def _find_simsun() -> Path:
    """定位本机 Windows 的 SimSun 字体文件，找不到则 fail-fast。"""
    for candidate in _WINDOWS_FONT_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("未找到 simsun.ttc / simsun.ttf，请确认本机 Windows 已安装 SimSun 字体。")


def _family_name(font: TTFont) -> str:
    """返回字体族名（优先 typographic family，回退 legacy family）。"""
    return font["name"].getBestFamilyName()


def main() -> int:
    source = _find_simsun()
    dest = Path(__file__).resolve().parent.parent / "src" / "cad2image" / "fonts"
    dest.mkdir(parents=True, exist_ok=True)

    # 极少数环境直接提供单文件 TTF（非集合），此时直接复制为 simsun.ttf。
    if source.suffix.lower() != ".ttc":
        target = dest / "simsun.ttf"
        target.write_bytes(source.read_bytes())
        print(f"复制 {source.name} → {target}")
        return 0

    extracted = 0
    collection = TTCollection(str(source))
    for font in collection.fonts:
        filename = _TARGET_FAMILIES.get(_family_name(font))
        if filename is None:
            continue
        target = dest / filename
        font.save(str(target))
        print(f"提取 {_family_name(font)} → {target}")
        extracted += 1

    if extracted == 0:
        print("警告：未能从 simsun.ttc 中识别出 SimSun / NSimSun 字体。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
