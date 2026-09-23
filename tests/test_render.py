"""render 模块的渲染与错误路径测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from cad2image.config import RenderOptions
from cad2image.render import _bundled_font_dir, render_dxf


def test_render_to_png(sample_dxf: Path, tmp_path: Path) -> None:
    """合成 DXF 渲染为 PNG，文件非空。"""
    output = tmp_path / "out.png"
    result = render_dxf(sample_dxf, output, RenderOptions(dpi=150))
    assert result == output
    assert output.is_file()
    assert output.stat().st_size > 0


def test_render_to_svg(sample_dxf: Path, tmp_path: Path) -> None:
    """合成 DXF 渲染为 SVG，内容为合法 XML 且编码声明为 utf-8。"""
    output = tmp_path / "out.svg"
    result = render_dxf(sample_dxf, output, RenderOptions())
    assert result == output
    content = output.read_text(encoding="utf-8")
    assert content.startswith("<?xml")
    assert "encoding='utf-8'" in content
    assert "<svg" in content


def test_render_missing_file_raises(tmp_path: Path) -> None:
    """源 DXF 不存在时抛出 FileNotFoundError。"""
    with pytest.raises(FileNotFoundError):
        render_dxf(tmp_path / "missing.dxf", tmp_path / "out.png", RenderOptions())


def test_render_unsupported_format_raises(sample_dxf: Path, tmp_path: Path) -> None:
    """不支持的输出格式抛出 ValueError。"""
    with pytest.raises(ValueError, match="不支持的输出格式"):
        render_dxf(sample_dxf, tmp_path / "out.jpg", RenderOptions())


def test_render_invalid_layout_raises(sample_dxf: Path, tmp_path: Path) -> None:
    """不存在的布局名抛出 ValueError。"""
    with pytest.raises(ValueError, match="布局"):
        render_dxf(sample_dxf, tmp_path / "out.png", RenderOptions(layout_name="Nope"))


def test_render_empty_layout_raises(empty_dxf: Path, tmp_path: Path) -> None:
    """空模型空间渲染时抛出 RuntimeError。"""
    with pytest.raises(RuntimeError, match="为空|无效"):
        render_dxf(empty_dxf, tmp_path / "out.png", RenderOptions())


def test_render_explicit_page_size(sample_dxf: Path, tmp_path: Path) -> None:
    """显式页面尺寸渲染不抛异常。"""
    output = tmp_path / "out.png"
    render_dxf(sample_dxf, output, RenderOptions(width_mm=100.0, height_mm=50.0))
    assert output.is_file()


def test_render_solid_entity_no_crash(solid_dxf: Path, tmp_path: Path) -> None:
    """含 SOLID（填充多边形）的 DXF 渲染不崩溃。

    回归 PyMuPDF 1.24.11 无法解析 ezdxf Vec2 的 bug。
    """
    output = tmp_path / "out.png"
    result = render_dxf(solid_dxf, output, RenderOptions(dpi=100))
    assert result.is_file()
    assert output.stat().st_size > 0


def test_defer_content_wrap_restores_on_exception() -> None:
    """渲染加速用的 wrap_contents 禁用必须异常安全，finally 恢复原实现。"""
    import fitz

    from cad2image.render import _defer_content_wrap

    original = fitz.Page.wrap_contents
    with pytest.raises(RuntimeError), _defer_content_wrap():
        assert fitz.Page.wrap_contents is not original
        raise RuntimeError("boom")
    assert fitz.Page.wrap_contents is original


def test_defer_content_wrap_is_thread_safe() -> None:
    """并发进入 _defer_content_wrap 共享同一补丁，最后一个退出才恢复原实现。"""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    import fitz

    from cad2image.render import _defer_content_wrap

    original = fitz.Page.wrap_contents
    barrier = threading.Barrier(4)

    def worker(_: int) -> bool:
        with _defer_content_wrap():
            barrier.wait()  # 确保 4 线程同时处于补丁生效区间
            return fitz.Page.wrap_contents is not original

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(worker, range(4)))

    assert all(results)
    assert fitz.Page.wrap_contents is original


def test_concurrent_render_no_cross_contamination(sample_dxf: Path, tmp_path: Path) -> None:
    """多线程并发渲染同一 DXF 应全部成功且输出一致（无全局状态交叉污染）。"""
    from concurrent.futures import ThreadPoolExecutor

    def render(i: int) -> int:
        out = tmp_path / f"out_{i}.png"
        render_dxf(sample_dxf, out, RenderOptions(dpi=100))
        return out.stat().st_size

    with ThreadPoolExecutor(max_workers=4) as pool:
        sizes = list(pool.map(render, range(8)))

    assert all(s > 0 for s in sizes)
    assert len(set(sizes)) == 1, f"并发渲染输出尺寸不一致：{sizes}"


def test_decode_multibyte_escapes_oda_format() -> None:
    """ODA 的多字节转义 \\M+5XXXX（5 为前缀，XXXX 是 GBK 双字节）应解码为汉字。"""
    from cad2image.render import _decode_multibyte_escapes

    assert _decode_multibyte_escapes("\\M+5B7C0\\M+5B7B4\\M+5BFD7") == "防反孔"


def test_decode_multibyte_escapes_standard_format() -> None:
    """标准 \\M+XXXX（4 hex）也应解码为汉字。"""
    from cad2image.render import _decode_multibyte_escapes

    assert _decode_multibyte_escapes("\\M+B7C0") == "防"


def test_decode_multibyte_escapes_keeps_invalid_and_plain() -> None:
    """无法解码的转义与普通文本原样保留。"""
    from cad2image.render import _decode_multibyte_escapes

    assert _decode_multibyte_escapes("\\M+ZZZZ") == "\\M+ZZZZ"
    assert _decode_multibyte_escapes("ABC123") == "ABC123"
    assert _decode_multibyte_escapes("") == ""


def test_render_single_dimension_raises(sample_dxf: Path, tmp_path: Path) -> None:
    """仅指定宽度或高度之一时抛出 ValueError。"""
    with pytest.raises(ValueError, match="必须同时指定"):
        render_dxf(sample_dxf, tmp_path / "out.png", RenderOptions(width_mm=100.0))


def test_render_negative_page_size_raises(sample_dxf: Path, tmp_path: Path) -> None:
    """页面尺寸非正数时抛出 ValueError。"""
    with pytest.raises(ValueError, match="必须为正数"):
        render_dxf(sample_dxf, tmp_path / "out.png", RenderOptions(width_mm=100.0, height_mm=-5.0))


def test_render_font_dir_missing_raises(sample_dxf: Path, tmp_path: Path) -> None:
    """字体目录不存在时抛出 FileNotFoundError。"""
    with pytest.raises(FileNotFoundError, match="字体目录"):
        render_dxf(sample_dxf, tmp_path / "out.png", RenderOptions(font_dir=str(tmp_path / "no-fonts")))


def test_render_with_font_dir(sample_dxf: Path, tmp_path: Path) -> None:
    """空字体目录（存在但无字体）渲染不抛异常。"""
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    output = tmp_path / "out.png"
    render_dxf(sample_dxf, output, RenderOptions(font_dir=str(font_dir), dpi=100))
    assert output.is_file()


def test_render_negative_margin_raises(sample_dxf: Path, tmp_path: Path) -> None:
    """负页面余量抛出 ValueError。"""
    with pytest.raises(ValueError, match="余量"):
        render_dxf(sample_dxf, tmp_path / "out.png", RenderOptions(margin=-1.0))


def test_render_margin_increases_page(sample_dxf: Path, tmp_path: Path) -> None:
    """余量增大时页面（PNG 像素尺寸）相应变大。"""
    from PIL import Image

    small = tmp_path / "small.png"
    large = tmp_path / "large.png"
    render_dxf(sample_dxf, small, RenderOptions(dpi=100, margin=0.0))
    render_dxf(sample_dxf, large, RenderOptions(dpi=100, margin=20.0))
    small_size = Image.open(small).size
    large_size = Image.open(large).size
    assert large_size[0] > small_size[0]
    assert large_size[1] > small_size[1]


def test_render_chinese_text_uses_cjk_font(tmp_path: Path) -> None:
    """中文字形应渲染为真实笔画（细横条），而非 ``.notdef`` 方框。

    回归：字体必须扫描在 bbox 测量之前；且引用 ``NSimSun.ttf`` 的中文样式会被
    自动重写为内置开源字体 ``SourceHanSerifSC-Regular.ttf``，避免中文变成方框。
    """
    import ezdxf
    import numpy as np
    from PIL import Image

    doc = ezdxf.new("R2018")
    doc.styles.get("Standard").dxf.font = "NSimSun.ttf"
    doc.modelspace().add_text("一", dxfattribs={"height": 10}).set_placement((0, 0))
    dxf = tmp_path / "cjk.dxf"
    png = tmp_path / "out.png"
    doc.saveas(dxf)
    render_dxf(dxf, png, RenderOptions(dpi=100))

    arr = np.array(Image.open(png).convert("L"))
    ys, xs = np.where(arr < 128)
    assert len(xs) > 0, "应渲染出文字"
    width = xs.max() - xs.min() + 1
    height = ys.max() - ys.min() + 1
    # "一" 是细横条：宽远大于高；若退化成 .notdef 方框则宽高接近。
    assert width > height * 3, f"中文字形异常：{width}x{height}，疑似 .notdef 方框"


def test_map_font_name_empty_and_chinese_names() -> None:
    """空字体名与含中文的字体名应映射到内置中文字体，而非等宽或原样。"""
    from cad2image.render import _map_font_name

    assert _map_font_name("") == "SourceHanSerifSC-Regular.ttf"
    assert _map_font_name("   ") == "SourceHanSerifSC-Regular.ttf"
    assert _map_font_name("仿宋_GB2312") == "SourceHanSerifSC-Regular.ttf"
    assert _map_font_name("黑体") == "SourceHanSerifSC-Regular.ttf"
    # 非中文字体名不受影响
    assert _map_font_name("romans.shx") == "NotoSansMono-Regular.ttf"
    assert _map_font_name("Arial.ttf") == "Arial.ttf"


def test_remap_mtext_inline_fonts() -> None:
    """MTEXT 内联 \\fXXX 覆盖应被移除，文本回落到样式字体，其余内容保留。"""
    import ezdxf

    from cad2image.render import _remap_mtext_inline_fonts

    doc = ezdxf.new("R2018")
    doc.modelspace().add_mtext(r"{\fNSimSun|b0|i0|c134|p49;\W1;Y}")
    _remap_mtext_inline_fonts(doc)

    text = list(doc.modelspace())[0].dxf.text
    assert text == r"{\W1;Y}"  # \f 覆盖被移除，宽度因子与文字保留


def test_remap_mtext_inline_fonts_strips_any_font() -> None:
    """内联字体名无论是否专有（Arial 也一样）都应被移除，统一回落样式字体。"""
    import ezdxf

    from cad2image.render import _remap_mtext_inline_fonts

    doc = ezdxf.new("R2018")
    doc.modelspace().add_mtext(r"{\fArial.ttf;ABC}")
    _remap_mtext_inline_fonts(doc)
    assert list(doc.modelspace())[0].dxf.text == r"{ABC}"


def test_remap_missing_glyphs_diameter() -> None:
    """直径符号 U+2300 应替换为 U+00D8（内置宋体缺前者、有后者）。"""
    import ezdxf

    from cad2image.render import _remap_missing_glyphs

    doc = ezdxf.new("R2018")
    doc.modelspace().add_text(chr(0x2300) + "10", dxfattribs={"height": 10})
    _remap_missing_glyphs(doc)
    assert list(doc.modelspace())[0].dxf.text == chr(0x00D8) + "10"


def test_expand_autocad_control_codes() -> None:
    """AutoCAD 控制码 %%c/%%d/%%p 应展开为 Ø/°/±，%%% 展开为字面 %。

    回归：CAD 直径/度/正负符号常以 %%c/%%d/%%p 控制码存于文字，ezdxf 不解析控制码，
    会原样渲染成 "%c" 或（ODA 展开成 U+2300 时）字体缺字形 → 方框。
    """
    import ezdxf

    from cad2image.render import _expand_autocad_control_codes

    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_mtext(r"{\Fdim|c0;%%C}2.6%%P0.1")
    msp.add_text(r"%%dC 100%%%", dxfattribs={"height": 10})

    _expand_autocad_control_codes(doc)

    entities = list(msp)
    assert entities[0].dxf.text == r"{\Fdim|c0;Ø}2.6±0.1"
    assert entities[1].dxf.text == r"°C 100%"


def test_expand_autocad_control_codes_keeps_plain_text() -> None:
    """无控制码的普通文本应原样保留（含单个 % 不误伤）。"""
    import ezdxf

    from cad2image.render import _expand_autocad_control_codes

    doc = ezdxf.new("R2018")
    doc.modelspace().add_text("50% A3", dxfattribs={"height": 10})
    _expand_autocad_control_codes(doc)
    assert list(doc.modelspace())[0].dxf.text == "50% A3"


def test_expand_autocad_control_codes_in_dimension() -> None:
    """DIMENSION 的 text 覆盖（组码 1）里的 %%c/%%p 也应展开，直径尺寸标注最常在此。

    回归：直径尺寸标注的 %%c 存于 DIMENSION 实体的 text 覆盖而非 TEXT/MTEXT，
    此前 _TEXT_ENTITIES 不含 DIMENSION，导致直径符号不展开、渲染成方框。
    """
    import ezdxf

    from cad2image.render import _expand_autocad_control_codes, _remap_mtext_inline_fonts

    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_linear_dim(base=(0, 0), p1=(0, 0), p2=(20, 0), angle=0)
    dim = list(msp.query("DIMENSION"))[0]
    dim.dxf.text = r"{\Fdim|c0;%%C}2.6%%P0.1"

    _expand_autocad_control_codes(doc)
    _remap_mtext_inline_fonts(doc)

    assert dim.dxf.text == r"{Ø}2.6±0.1"


def test_render_empty_font_name_uses_cjk_font(tmp_path: Path) -> None:
    """文字样式 font 为空时也应渲染中文，而非方框（回归部分 ODA 版本转出空字体名）。"""
    import ezdxf
    import numpy as np
    from PIL import Image

    doc = ezdxf.new("R2018")
    doc.styles.get("Standard").dxf.font = ""  # 空字体名
    doc.modelspace().add_text("一", dxfattribs={"height": 10}).set_placement((0, 0))
    dxf = tmp_path / "empty_font.dxf"
    png = tmp_path / "out.png"
    doc.saveas(dxf)
    render_dxf(dxf, png, RenderOptions(dpi=100))

    arr = np.array(Image.open(png).convert("L"))
    ys, xs = np.where(arr < 128)
    assert len(xs) > 0, "应渲染出文字"
    width = xs.max() - xs.min() + 1
    height = ys.max() - ys.min() + 1
    assert width > height * 3, f"空字体名未映射到中文字体：{width}x{height}"


def test_bundled_font_contours_use_opposite_winding() -> None:
    """内置中文字体的封闭字形（如"口"）应外轮廓、内孔轮廓方向相反。

    回归：变量字体实例化出的静态字体会出现所有轮廓同向（非规范），配合 PyMuPDF
    后端的 even-odd 填充，笔画交叠处会被镂空成"空洞"。经 removeOverlaps 处理后，
    外轮廓应为顺时针、内孔轮廓应为逆时针。
    """
    from fontTools.pens.recordingPen import RecordingPen
    from fontTools.ttLib import TTFont

    font_path = _bundled_font_dir() / "SourceHanSerifSC-Regular.ttf"
    font = TTFont(font_path)
    glyph_name = font.getBestCmap()[ord("口")]  # "口"
    pen = RecordingPen()
    font.getGlyphSet()[glyph_name].draw(pen)

    contours: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for op, args in pen.value:
        if op == "moveTo":
            if current:
                contours.append(current)
            current = [args[0]]
        elif op in ("lineTo", "curveTo", "qCurveTo"):
            current.extend(args[1:] if op in ("curveTo", "qCurveTo") else [args[0]])
        elif op == "closePath":
            if current:
                contours.append(current)
                current = []
    if current:
        contours.append(current)

    areas = []
    for contour in contours:
        area = 0.0
        for i in range(len(contour)):
            x1, y1 = contour[i]
            x2, y2 = contour[(i + 1) % len(contour)]
            area += x1 * y2 - x2 * y1
        areas.append(area)

    assert any(a > 0 for a in areas) and any(a < 0 for a in areas), (
        f"内置字体 '口' 字形轮廓方向应相反（外 CW、内 CCW），实际面积：{areas}"
    )


def test_relative_stroke_width_uses_smaller_dimension() -> None:
    """相对线宽基准按页面较小边，避免极扁/极长图纸线过粗。"""
    from ezdxf.addons.drawing import layout as lay

    from cad2image.render import _SafeRenderBackend

    page = lay.Page(2000.0, 100.0, lay.Units.mm, margins=lay.Margins.all(0))
    settings = lay.Settings(max_stroke_width=0.001, min_stroke_width=0.05)
    backend = _SafeRenderBackend(page, settings)
    # 较小边 100mm × 72/25.4 × 0.001 ≈ 0.28pt，远小于按较大边 2000mm 算的 5.7pt
    assert backend.max_stroke_width < 1.0


def test_render_margin_is_symmetric(tmp_path: Path) -> None:
    """内容自适应页面的四周余量应均匀对称，而非某一边贴边、另一边留白。"""
    import ezdxf
    import numpy as np
    from PIL import Image

    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_line((0, 0), (100, 0))
    msp.add_text("测试文字 ABC", dxfattribs={"height": 10}).set_placement((10, 20))
    dxf = tmp_path / "t.dxf"
    png = tmp_path / "out.png"
    doc.saveas(dxf)
    render_dxf(dxf, png, RenderOptions(dpi=100, margin=5.0))

    arr = np.array(Image.open(png).convert("L"))
    ys, xs = np.where(arr < 128)
    height, width = arr.shape
    left = xs.min()
    right = width - 1 - xs.max()
    top = ys.min()
    bottom = height - 1 - ys.max()
    margins = [left, right, top, bottom]
    assert max(margins) - min(margins) <= 3, f"余量不对称：左{left} 右{right} 上{top} 下{bottom}"
