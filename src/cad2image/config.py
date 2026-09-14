"""配置解析与渲染参数映射。

提供两部分能力：

1. ODA File Converter 可执行文件路径的解析（环境变量优先，默认安装路径兜底）。
2. 将面向用户的 ``RenderOptions`` 映射为 ezdxf ``Configuration``，
   集中处理 Acme CAD Converter 参数语义到 ezdxf 的对应关系。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ezdxf.addons.drawing import config as draw_config

# ODA File Converter 的默认安装路径（Windows）。允许通过环境变量覆盖。
_DEFAULT_ODA_PATH = Path(r"D:\ODA\ODAFileConverter_title 21.5.0\ODAFileConverter.exe")

# 覆盖 ODA 安装路径的环境变量名。
_ODA_PATH_ENV = "ODA_FILE_CONVERTER_PATH"

# 合法的背景、颜色与线宽策略取值，用于 fail-fast 校验。
_BACKGROUND_CHOICES = {"default", "white", "black", "off", "none"}
_COLOR_POLICY_CHOICES = {"color", "monochrome", "grayscale", "black", "white"}
_LINEWEIGHT_POLICY_CHOICES = {"absolute", "relative"}


def get_oda_converter_path() -> Path:
    """返回 ODA File Converter 可执行文件的路径。

    优先读取环境变量 ``ODA_FILE_CONVERTER_PATH``，未设置时回退到默认安装路径。

    Returns:
        ``ODAFileConverter.exe`` 的绝对路径。
    """
    env_path = os.getenv(_ODA_PATH_ENV)
    if env_path:
        return Path(env_path).expanduser()
    return _DEFAULT_ODA_PATH


@dataclass
class RenderOptions:
    """面向用户的渲染参数。

    Attributes:
        dpi: 输出分辨率（PNG）。默认 300。
        background: 背景策略，``default/white/black/off``。默认 ``white``。
        color_policy: 颜色策略，``color/monochrome/grayscale/black/white``。默认 ``color``。
        lineweight_policy: 线宽策略，``absolute/relative``。默认 ``absolute``。
        lineweight_scaling: 线宽整体缩放系数，乘到每条线的线宽上（仅绝对线宽策略生效）。默认 1.0。
        min_lineweight: 最小打印线宽（mm），``None`` 表示不设下限。
        relative_max_stroke_width: 相对线宽策略下，最粗线宽（2.11mm）占页面较小边的比例。默认 0.001（0.1%）。
        relative_min_stroke_width: 相对线宽策略下，最细线宽（0.05mm）占最粗线宽的比例。默认 0.05（5%）。
        ctb: CTB 打印样式表路径，``""`` 表示不使用。
        font_dir: 附加的 SHX/TTF 字体目录，``""`` 表示仅用系统字体。
        layout_name: 要渲染的布局名，``None`` 表示模型空间。默认 ``None``。
        width_mm: 显式页面宽度（mm），``None`` 表示按布局/范围自适应。
        height_mm: 显式页面高度（mm），``None`` 表示按布局/范围自适应。
        fit_to_extents: 是否按模型空间内容范围自适应页面尺寸。默认 ``False``。
        margin: 内容范围自适应时的四周余量，按内容较小边的百分比（0–100）。默认 3.0。
    """

    dpi: int = 300
    background: str = "white"
    color_policy: str = "color"
    lineweight_policy: str = "absolute"
    lineweight_scaling: float = 1.0
    min_lineweight: float | None = None
    relative_max_stroke_width: float = 0.001
    relative_min_stroke_width: float = 0.05
    ctb: str = ""
    font_dir: str = ""
    layout_name: str | None = None
    width_mm: float | None = None
    height_mm: float | None = None
    fit_to_extents: bool = False
    margin: float = 3.0


def build_drawing_configuration(options: RenderOptions) -> draw_config.Configuration:
    """将 ``RenderOptions`` 映射为 ezdxf ``Configuration``。

    在边界处完成校验（fail-fast），非法的策略取值抛出 ``ValueError``。

    Args:
        options: 面向用户的渲染参数。

    Returns:
        可直接传给 ``ezdxf.addons.drawing.Frontend`` 的配置对象。

    Raises:
        ValueError: 当背景/颜色/线宽策略或 DPI 取值非法时。
    """
    if options.dpi <= 0:
        raise ValueError(f"DPI 必须为正整数，得到 {options.dpi}")
    if options.lineweight_scaling <= 0:
        raise ValueError(f"线宽缩放系数必须为正数，得到 {options.lineweight_scaling}")
    if options.min_lineweight is not None and options.min_lineweight <= 0:
        raise ValueError(f"最小线宽必须为正数，得到 {options.min_lineweight}")
    if options.relative_max_stroke_width <= 0:
        raise ValueError(f"相对线宽最粗比例必须为正数，得到 {options.relative_max_stroke_width}")
    if options.relative_min_stroke_width <= 0:
        raise ValueError(f"相对线宽最细比例必须为正数，得到 {options.relative_min_stroke_width}")
    if options.margin < 0:
        raise ValueError(f"页面余量百分比必须为非负数，得到 {options.margin}")

    background = _map_background_policy(options.background)
    color_policy = _map_color_policy(options.color_policy)
    lineweight_policy = _map_lineweight_policy(options.lineweight_policy)

    return draw_config.Configuration(
        background_policy=background,
        color_policy=color_policy,
        lineweight_policy=lineweight_policy,
        lineweight_scaling=options.lineweight_scaling,
        min_lineweight=options.min_lineweight,
    )


def _map_background_policy(value: str) -> draw_config.BackgroundPolicy:
    """把背景策略字符串映射为 ``BackgroundPolicy`` 枚举。"""
    normalized = value.strip().lower()
    if normalized not in _BACKGROUND_CHOICES:
        raise ValueError(f"非法的背景策略 '{value}'，可选：{', '.join(sorted(_BACKGROUND_CHOICES))}")
    return {
        "default": draw_config.BackgroundPolicy.DEFAULT,
        "white": draw_config.BackgroundPolicy.WHITE,
        "black": draw_config.BackgroundPolicy.BLACK,
        "off": draw_config.BackgroundPolicy.OFF,
        "none": draw_config.BackgroundPolicy.OFF,
    }[normalized]


def _map_color_policy(value: str) -> draw_config.ColorPolicy:
    """把颜色策略字符串映射为 ``ColorPolicy`` 枚举。

    Note:
        ezdxf 1.1.x 无独立的灰度策略，``grayscale`` 暂以 ``MONOCHROME`` 近似，
        待 Phase 2 通过像素级后处理实现真正的 256 级灰度。
    """
    normalized = value.strip().lower()
    if normalized not in _COLOR_POLICY_CHOICES:
        raise ValueError(f"非法的颜色策略 '{value}'，可选：{', '.join(sorted(_COLOR_POLICY_CHOICES))}")
    return {
        "color": draw_config.ColorPolicy.COLOR,
        "monochrome": draw_config.ColorPolicy.MONOCHROME,
        "grayscale": draw_config.ColorPolicy.MONOCHROME,
        "black": draw_config.ColorPolicy.BLACK,
        "white": draw_config.ColorPolicy.WHITE,
    }[normalized]


def _map_lineweight_policy(value: str) -> draw_config.LineweightPolicy:
    """把线宽策略字符串映射为 ``LineweightPolicy`` 枚举。"""
    normalized = value.strip().lower()
    if normalized not in _LINEWEIGHT_POLICY_CHOICES:
        raise ValueError(f"非法的线宽策略 '{value}'，可选：{', '.join(sorted(_LINEWEIGHT_POLICY_CHOICES))}")
    return {
        "absolute": draw_config.LineweightPolicy.ABSOLUTE,
        "relative": draw_config.LineweightPolicy.RELATIVE,
    }[normalized]
