"""config 模块的映射与校验测试。"""

from __future__ import annotations

import pytest

from cad2image.config import (
    RenderOptions,
    build_drawing_configuration,
    get_oda_converter_path,
    parse_resolution,
)


def test_get_oda_converter_path_returns_default() -> None:
    """未设置环境变量时返回默认路径。"""
    path = get_oda_converter_path()
    assert path.name == "ODAFileConverter.exe"


def test_build_configuration_defaults() -> None:
    """默认参数能成功构建配置。"""
    config = build_drawing_configuration(RenderOptions())
    assert config.background_policy is not None
    assert config.color_policy is not None


@pytest.mark.parametrize("background", ["white", "black", "off", "default"])
def test_valid_background_policies(background: str) -> None:
    """合法背景策略不抛异常。"""
    build_drawing_configuration(RenderOptions(background=background))


@pytest.mark.parametrize("color", ["color", "monochrome", "grayscale", "black", "white"])
def test_valid_color_policies(color: str) -> None:
    """合法颜色策略不抛异常。"""
    build_drawing_configuration(RenderOptions(color_policy=color))


def test_invalid_background_raises() -> None:
    """非法背景策略 fail-fast 抛出 ValueError。"""
    with pytest.raises(ValueError, match="背景策略"):
        build_drawing_configuration(RenderOptions(background="transparent"))


def test_invalid_color_raises() -> None:
    """非法颜色策略抛出 ValueError。"""
    with pytest.raises(ValueError, match="颜色策略"):
        build_drawing_configuration(RenderOptions(color_policy="rainbow"))


def test_invalid_dpi_raises() -> None:
    """非正 DPI 抛出 ValueError。"""
    with pytest.raises(ValueError, match="DPI"):
        build_drawing_configuration(RenderOptions(dpi=0))


def test_invalid_lineweight_scaling_raises() -> None:
    """非正线宽缩放系数抛出 ValueError。"""
    with pytest.raises(ValueError, match="线宽缩放"):
        build_drawing_configuration(RenderOptions(lineweight_scaling=-1.0))


def test_invalid_min_lineweight_raises() -> None:
    """非正最小线宽抛出 ValueError。"""
    with pytest.raises(ValueError, match="最小线宽"):
        build_drawing_configuration(RenderOptions(min_lineweight=-1.0))


def test_min_lineweight_maps_to_config() -> None:
    """最小线宽正确映射到 Configuration。"""
    config = build_drawing_configuration(RenderOptions(min_lineweight=0.2))
    assert config.min_lineweight == pytest.approx(0.2)


def test_invalid_relative_max_stroke_width_raises() -> None:
    """非正相对线宽最粗比例抛出 ValueError。"""
    with pytest.raises(ValueError, match="最粗"):
        build_drawing_configuration(RenderOptions(relative_max_stroke_width=0.0))


def test_invalid_relative_min_stroke_width_raises() -> None:
    """非正相对线宽最细比例抛出 ValueError。"""
    with pytest.raises(ValueError, match="最细"):
        build_drawing_configuration(RenderOptions(relative_min_stroke_width=-1.0))


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1k", 1024), ("2k", 2048), ("3k", 3072), ("4k", 4096), ("2K", 2048)],
)
def test_parse_resolution_presets(value: str, expected: int) -> None:
    """--resolution 预设短名（大小写不敏感）映射为长边像素。"""
    assert parse_resolution(value) == expected


def test_parse_resolution_custom_pixels() -> None:
    """--resolution 支持纯数字自定义长边像素。"""
    assert parse_resolution("3000") == 3000
    assert parse_resolution(" 2048 ") == 2048


def test_parse_resolution_none_means_dpi() -> None:
    """未指定 --resolution 时返回 None（回退 --dpi）。"""
    assert parse_resolution(None) is None


def test_parse_resolution_invalid_raises() -> None:
    """非法 --resolution 取值 fail-fast 抛出 ValueError。"""
    with pytest.raises(ValueError, match="resolution"):
        parse_resolution("abc")
    with pytest.raises(ValueError, match="resolution"):
        parse_resolution("0")


def test_invalid_resolution_raises() -> None:
    """非正目标分辨率抛出 ValueError。"""
    with pytest.raises(ValueError, match="目标分辨率"):
        build_drawing_configuration(RenderOptions(resolution=0))
