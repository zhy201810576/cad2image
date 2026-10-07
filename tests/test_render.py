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


def test_map_font_name_defaults_to_cjk_font() -> None:
    """字体名默认映射到思源宋体；仅明确的西文单线字体才映射到等宽。

    回归：中文字体名无法穷举（SimHei/FangSong/KaiTi/微软雅黑…），且不少是纯英文、
    不含汉字、不以 .shx 结尾，旧的中文白名单 + 汉字启发式会漏判 → 误入等宽字体 →
    中文方框。改为"默认落思源宋体"，任何中文字体名都不会方框。
    """
    from cad2image.render import _map_font_name

    cjk = "SourceHanSerifSC-Regular.ttf"
    mono = "NotoSansMono-Regular.ttf"

    # 空字体名 → 中文兜底
    assert _map_font_name("") == cjk
    assert _map_font_name("   ") == cjk

    # 含汉字的字体名 → 中文
    assert _map_font_name("仿宋_GB2312") == cjk
    assert _map_font_name("黑体") == cjk

    # 旧中文白名单里的名字 → 中文
    assert _map_font_name("宋体") == cjk
    assert _map_font_name("SimSun") == cjk
    assert _map_font_name("NSimSun.ttf") == cjk

    # 纯英文/拼音的中文字体名（旧逻辑会误判成等宽 → 方框）→ 中文
    assert _map_font_name("SimHei") == cjk
    assert _map_font_name("FangSong_GB2312") == cjk
    assert _map_font_name("KaiTi") == cjk
    assert _map_font_name("Microsoft YaHei") == cjk

    # 中文 bigfont SHX → 中文（不再是等宽）
    assert _map_font_name("hztxt.shx") == cjk
    assert _map_font_name("gbcbig.shx") == cjk

    # 西文单线 SHX → 等宽
    assert _map_font_name("romans.shx") == mono
    assert _map_font_name("txt") == mono
    assert _map_font_name("simplex.shx") == mono
    assert _map_font_name("isocp.shx") == mono

    # Arial（西文无衬线）→ 等宽
    assert _map_font_name("Arial.ttf") == mono


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


def test_remap_mtext_inline_fonts_in_block() -> None:
    """块定义内的 MTEXT 内联 \\f 覆盖也应被移除。

    回归：标题栏/图框/工艺单等文字通常放在块定义里，INSERT 引用后 ezdxf 会递归渲染
    块内文字。这些实体不在 ``doc.layouts`` 中，若只遍历布局会漏掉，内联字体名
    （如 ``\\f仿宋_GB2312``）无法命中扫描目录 → 回退默认字体 → 中文变方框。
    """
    import ezdxf

    from cad2image.render import _remap_mtext_inline_fonts

    doc = ezdxf.new("R2018")
    blk = doc.blocks.new("图框")
    blk.add_mtext(r"\f仿宋_GB2312|b0|i0|p34;借（通）用")
    blk.add_mtext(r"{\fNSimSun|b0|i0|c134|p49;\W1;Y}")
    doc.modelspace().add_blockref("图框", (0, 0))

    _remap_mtext_inline_fonts(doc)

    texts = [e.dxf.text for e in blk if e.dxftype() == "MTEXT"]
    assert texts == ["借（通）用", r"{\W1;Y}"], texts


def test_remap_missing_glyphs_diameter() -> None:
    """直径符号 U+2300 应替换为 U+00D8（内置宋体缺前者、有后者）。"""
    import ezdxf

    from cad2image.render import _remap_missing_glyphs

    doc = ezdxf.new("R2018")
    doc.modelspace().add_text(chr(0x2300) + "10", dxfattribs={"height": 10})
    _remap_missing_glyphs(doc)
    assert list(doc.modelspace())[0].dxf.text == chr(0x00D8) + "10"


def test_remap_missing_glyphs_empty_set_diameter() -> None:
    """直径符号 U+2205（∅）应替换为 U+00D8（宋体全宽字形导致尺寸文字膨胀）。"""
    import ezdxf

    from cad2image.render import _remap_missing_glyphs

    doc = ezdxf.new("R2018")
    doc.modelspace().add_text(chr(0x2205) + "62-0.011", dxfattribs={"height": 10})
    _remap_missing_glyphs(doc)
    assert list(doc.modelspace())[0].dxf.text == chr(0x00D8) + "62-0.011"


def test_scale_text_heights_scales_all_text() -> None:
    """全局文字缩放：TEXT 高度与 MTEXT char_height 按系数缩放，factor=1.0 不改变。"""
    import ezdxf

    from cad2image.render import _scale_text_heights

    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_text("A", dxfattribs={"height": 10.0})
    msp.add_mtext("B", dxfattribs={"char_height": 25.0})

    _scale_text_heights(doc, 0.8)

    entities = list(msp)
    assert entities[0].dxf.height == pytest.approx(8.0)
    assert entities[1].dxf.char_height == pytest.approx(20.0)

    _scale_text_heights(doc, 1.0)
    assert entities[0].dxf.height == pytest.approx(8.0)
    assert entities[1].dxf.char_height == pytest.approx(20.0)


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


def test_expand_autocad_control_codes_decodes_unicode_escape() -> None:
    """\\U+XXXX 转义应解码为对应 Unicode 字符（如 \\U+00B0 → °）。

    回归：ODA 把部分非 ASCII 字符（角度符号 ° 等）转成 AutoCAD 的 Unicode 转义
    ``\\U+XXXX``，ezdxf 不解析、会原样渲染成字面 "\\U+00B0"。
    """
    import ezdxf

    from cad2image.render import _expand_autocad_control_codes

    doc = ezdxf.new("R2018")
    doc.modelspace().add_text(r"135\U+00B0", dxfattribs={"height": 10})

    _expand_autocad_control_codes(doc)

    assert list(doc.modelspace())[0].dxf.text == "135°"


def test_expand_autocad_control_codes_keeps_plain_text() -> None:
    """无控制码的普通文本应原样保留（含单个 % 不误伤）。"""
    import ezdxf

    from cad2image.render import _expand_autocad_control_codes

    doc = ezdxf.new("R2018")
    doc.modelspace().add_text("50% A3", dxfattribs={"height": 10})
    _expand_autocad_control_codes(doc)
    assert list(doc.modelspace())[0].dxf.text == "50% A3"


def test_remap_dimension_geometry_texts() -> None:
    """DIMENSION 的渲染文本在几何块内，%%c/内联字体必须改块内文本而非 dxf.text。

    回归：ezdxf 渲染 DIMENSION 读的是关联匿名几何块里的 MTEXT（含 %%c 与 \\f 内联
    字体），而非 dxf.text 覆盖，故只改 dxf.text 对直径符号无效 → 方框。
    """
    import ezdxf

    from cad2image.render import _remap_dimension_geometry_texts

    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_linear_dim(base=(0, 0), p1=(0, 0), p2=(20, 0), angle=0)
    dim = list(msp.query("DIMENSION"))[0]
    dim.dxf.text = r"{\Fdim|c0;%%C}2.6%%P0.1"
    dim.render()  # 生成匿名几何块（内含含 %%c 的 MTEXT）
    block = dim.get_geometry_block()
    assert block is not None

    before = [e.dxf.get("text", "") for e in block if e.dxftype() == "MTEXT"]
    assert any("%%C" in t for t in before), before

    _remap_dimension_geometry_texts(doc)

    after = [e.dxf.get("text", "") for e in block if e.dxftype() == "MTEXT"]
    assert any("Ø" in t and "%%C" not in t and "\\F" not in t for t in after), after


def test_remap_dimension_properties() -> None:
    """DIMENSION 几何块子实体的 ByBlock 颜色/线宽应改为标注实体自身的值。

    回归：ODA 转出的 DIMENSION 几何块子实体颜色/线宽为 ByBlock，ezdxf 渲染时把虚拟实体
    的 ByBlock 解析成布局默认值（颜色 ACI 7、线宽 0.25mm），而非标注实体/图层的值，导致
    CTB"使用对象颜色"不生效、线宽不认图层设置。这里应把子实体改回标注实体的值。
    """
    import ezdxf

    from cad2image.render import _remap_dimension_properties

    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_linear_dim(base=(0, 0), p1=(0, 0), p2=(20, 0), angle=0)
    dim = list(msp.query("DIMENSION"))[0]
    dim.render()
    block = dim.get_geometry_block()
    assert block is not None

    # 模拟 ODA 输出：几何块子实体颜色/线宽为 ByBlock，标注实体为显式绿色(3)、线宽 0.5mm(50)
    dim.dxf.color = 3
    dim.dxf.lineweight = 50
    for e in block:
        if e.dxftype() == "POINT":
            continue
        e.dxf.color = 0
        e.dxf.lineweight = -2  # BYBLOCK

    _remap_dimension_properties(doc)

    for e in block:
        if e.dxftype() == "POINT":
            continue
        assert e.dxf.color == 3, (e.dxftype(), e.dxf.color)
        assert e.dxf.lineweight == 50, (e.dxftype(), e.dxf.lineweight)


def test_remap_mleader_properties() -> None:
    """MULTILEADER 引线/箭头/块内容的 ByBlock 颜色/线宽应改回引线实体自身值。

    回归：ODA 转出的 MULTILEADER 引线颜色/线宽为 ByBlock，ezdxf 渲染时把虚拟实体解析成
    布局默认值（颜色 ACI 7、线宽 0.25mm），而非引线实体/图层的值。这里应把 ByBlock 改回
    引线实体的 dxf.color / dxf.lineweight。
    """
    import ezdxf
    from ezdxf.colors import BY_BLOCK_RAW_VALUE
    from ezdxf.math import Vec2
    from ezdxf.render.mleader import ConnectionSide, decode_raw_color

    from cad2image.render import _remap_mleader_properties

    doc = ezdxf.new("R2018")
    builder = doc.modelspace().add_multileader_mtext()
    builder.add_leader_line(ConnectionSide.right, [Vec2(0, 0), Vec2(5, 5)])
    builder.set_content("测试")
    builder.build(Vec2(10, 10))
    mleader = builder.multileader
    mleader.dxf.color = 3  # 显式绿色
    mleader.dxf.lineweight = 50  # 显式 0.5mm
    mleader.dxf.leader_lineweight = -2  # BYBLOCK

    line = mleader.context.leaders[0].lines[0]
    assert line.color == BY_BLOCK_RAW_VALUE

    _remap_mleader_properties(doc)

    assert decode_raw_color(line.color)[0] == 3, decode_raw_color(line.color)
    assert mleader.dxf.leader_lineweight == 50, mleader.dxf.leader_lineweight


def test_clear_mleader_proxy_graphics() -> None:
    """MULTILEADER 的代理图形应被清除，避免 ODA 硬编码的 .ttc 字体导致中文方框。

    回归：ODA 转出的 MULTILEADER 代理图形把字体硬编码为 Windows 的 ``simsun.ttc``，
    ezdxf 渲染 MULTILEADER 时无论全局代理图形策略如何都优先用代理图形，解析出的样式名
    ``.ttc`` 无法命中字体管理器 → 回退默认字体 → 中文变方框。清除后走原生 RenderEngine，
    文本回落已 remap 的样式字体。
    """
    import ezdxf

    from cad2image.render import _clear_mleader_proxy_graphics

    doc = ezdxf.new("R2018")
    mleader = doc.modelspace().add_multileader_mtext().multileader
    mleader.proxy_graphic = b"\x00\x01\x02"

    _clear_mleader_proxy_graphics(doc)

    assert mleader.proxy_graphic is None


def test_remap_cjk_text_styles() -> None:
    """含中文但样式为空/西文字体的文字实体，应重指到内置中文字体样式。

    回归：ODA 转出的部分 MTEXT（尤其 DIMENSION 几何块内）样式名为空，ezdxf 渲染时
    回退到 ``Standard``（ODA 常写为 ``arial.ttf``，无中文字形）→ 中文方框。只重写
    样式表无效——这些实体的样式名本身是空的。这里按文字内容判中文并重指样式。
    """
    import ezdxf

    from cad2image.render import _remap_cjk_text_styles, _remap_text_style_fonts

    doc = ezdxf.new("R2018")
    doc.styles.get("Standard").dxf.font = "arial.ttf"
    msp = doc.modelspace()
    # 空样式 + 中文 → 应被重指
    msp.add_mtext("拉直＜0.02")  # add_mtext 默认 style 为空
    # 显式 Standard（arial.ttf）+ 中文 → 应被重指
    msp.add_mtext("此腹板找平", dxfattribs={"style": "Standard"})
    # 纯西文，不应被误改
    msp.add_mtext("123.45")

    _remap_text_style_fonts(doc)
    _remap_cjk_text_styles(doc)

    texts = list(msp)
    # 前两个中文实体应被重指到中文字体样式（非空、且其字体为内置中文）
    for entity in texts[:2]:
        style_name = entity.dxf.get("style", "")
        assert style_name != "", "中文实体样式名不应为空"
        assert doc.styles.get(style_name).dxf.font == "SourceHanSerifSC-Regular.ttf"
    # 纯西文实体样式应保持空（回退 Standard）
    assert texts[2].dxf.get("style", "") == ""


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


def test_parse_tolerance_content_maps_gdt_symbols() -> None:
    """TOLERANCE 内容应展开 GDT 符号、丢弃空单元格，得到框格文本序列。

    回归：ezdxf 1.1.3 不支持 TOLERANCE 渲染，形位公差框被整体丢弃。这里校验
    ``{\\Fgdt;r}``（同心度）→ ◎、``{\\Fgdt;n}``（直径）→ Ø 的映射。
    """
    from cad2image.render import _parse_tolerance_content

    cells = _parse_tolerance_content(r"{\Fgdt;r}%%v{\Fgdt;n}0.03%%v%%vA%%v%%v")
    assert cells == ["◎", "Ø0.03", "A"]

    # 纯基准框（只有基准字母）
    cells = _parse_tolerance_content(r"%%v%%v%%vA%%v%%v")
    assert cells == ["A"]


def test_parse_tolerance_content_falls_back_missing_glyphs() -> None:
    """缺字形的 GDT 符号应替换为视觉等价的有字形符号（对称度 ⌯ → ≡ 等）。

    回归：思源宋体缺对称度 U+232F 等 5 个形位公差符号字形，渲染成 .notdef 方框。
    """
    from cad2image.render import _parse_tolerance_content

    # {\Fgdt;i} = 对称度 U+232F → ≡ (U+2261)
    cells = _parse_tolerance_content(r"{\Fgdt;i}%%v0.1%%v%%vA%%v%%v")
    assert cells == ["≡", "0.1", "A"]

    # 圆柱度 U+232D → ⊘、位置度 U+2316 → ⊕
    assert _parse_tolerance_content(r"{\Fgdt;g}%%v{\Fgdt;j}") == ["⊘", "⊕"]


def test_split_tolerance_cell_gdt_keeps_gdt_letters() -> None:
    """GDT 字体优先时单元格应拆成 (text, is_gdt) 片段，保留 ``\\Fgdt;X`` 的字母 X。

    标准 GDT 字体（GDT.shx / GDT.ttf）按 ObjectARX AcDbFcf::setText 符号表编码，小写字母
    字符码即形位公差符号；普通文字（公差值/基准字母）作为非 GDT 片段。
    """
    from cad2image.render import _split_tolerance_cell_gdt

    assert _split_tolerance_cell_gdt(r"{\Fgdt;r}") == [("r", True)]
    assert _split_tolerance_cell_gdt(r"{\Fgdt;n}0.03") == [("n", True), ("0.03", False)]
    assert _split_tolerance_cell_gdt("A") == [("A", False)]
    # 大小写不敏感 + 符号后紧跟基准字母
    assert _split_tolerance_cell_gdt(r"{\fgdt;m}A") == [("m", True), ("A", False)]


def test_find_gdt_font_finds_in_font_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """GDT 字体应从附加字体目录按文件名（大小写不敏感）找到。"""
    from cad2image import render as render_mod
    from cad2image.render import _find_gdt_font

    monkeypatch.setattr(render_mod, "_bundled_font_dir", lambda: tmp_path / "bundled")
    (tmp_path / "bundled").mkdir()
    font_dir = tmp_path / "extra"
    font_dir.mkdir()
    (font_dir / "GDT.ttf").write_bytes(b"x")

    assert _find_gdt_font(str(font_dir)) == "GDT.ttf"


def test_find_gdt_font_returns_none_when_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """无 GDT 字体时应返回 None（回退到 Unicode 展开）。"""
    from cad2image import render as render_mod
    from cad2image.render import _find_gdt_font

    monkeypatch.setattr(render_mod, "_bundled_font_dir", lambda: tmp_path / "bundled")
    (tmp_path / "bundled").mkdir()

    assert _find_gdt_font("") is None


def test_remap_mleader_text_expands_control_codes_and_inline_font() -> None:
    """MULTILEADER 内容 MTEXT 应展开 %%c 并剥离内联字体，避免直径符号缺失/中文方框。

    回归：ODA 转出的 MULTILEADER 内容带内联字体（``\\fISOCPEUR``/``\\fFangSong``）与
    ``%%c`` 控制码，而文字级重映射函数只处理 TEXT/MTEXT/ATTRIB/ATTDEF，不覆盖引线内容
    ——直径符号不展开、中文内联字体命中失败 → 方框。
    """
    import ezdxf
    from ezdxf.math import Vec2
    from ezdxf.render.mleader import ConnectionSide

    from cad2image.render import _remap_mleader_text

    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    builder = msp.add_multileader_mtext()
    builder.add_leader_line(ConnectionSide.right, [Vec2(0, 0), Vec2(5, 5)])
    builder.set_content(r"\A1;（{\fISOCPEUR|b0|i0|c134|p34;%%C}434外圆）")
    builder.build(Vec2(10, 10))

    _remap_mleader_text(doc)

    content = builder.multileader.context.mtext.default_content
    assert "%%c" not in content.lower()
    assert "Ø" in content
    assert "外圆" in content


def test_remap_mleader_text_remaps_cjk_style() -> None:
    """含中文的 MULTILEADER 内容应把样式句柄重指到内置中文字体样式，避免中文方框。"""
    import ezdxf
    from ezdxf.math import Vec2
    from ezdxf.render.mleader import ConnectionSide

    from cad2image.render import _remap_mleader_text, _remap_text_style_fonts

    doc = ezdxf.new("R2018")
    doc.styles.get("Standard").dxf.font = "arial.ttf"
    msp = doc.modelspace()
    builder = msp.add_multileader_mtext()
    builder.add_leader_line(ConnectionSide.right, [Vec2(0, 0), Vec2(5, 5)])
    builder.set_content(r"{\fFangSong|b0|i0|c134|p49;螺纹试铣块材料}")
    builder.build(Vec2(10, 10))

    _remap_text_style_fonts(doc)
    _remap_mleader_text(doc)

    style = doc.entitydb.get(builder.multileader.context.mtext.style_handle)
    assert style.dxf.font == "SourceHanSerifSC-Regular.ttf"


def test_remap_tolerance_to_graphics_creates_insert_block() -> None:
    """TOLERANCE 实体应被转换为含框线/文字的块引用，而非被 ezdxf 丢弃。"""
    import ezdxf

    from cad2image.render import _remap_tolerance_to_graphics

    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.new_entity(
        "TOLERANCE",
        dxfattribs={
            "insert": (0, 0),
            "content": r"{\Fgdt;r}%%v{\Fgdt;n}0.03%%v%%vA%%v%%v",
            "dimstyle": "Standard",
        },
    )

    _remap_tolerance_to_graphics(doc)

    # TOLERANCE 应已被移除，代之以 INSERT
    assert "TOLERANCE" not in [e.dxftype() for e in msp]
    inserts = [e for e in msp if e.dxftype() == "INSERT"]
    assert len(inserts) == 1
    block = doc.blocks.get(inserts[0].dxf.name)
    assert block is not None
    # 块内应有框线（LINE）与文字（TEXT），文字含展开后的符号
    texts = [e.dxf.text for e in block if e.dxftype() == "TEXT"]
    assert any("◎" in t for t in texts)
    assert any("Ø0.03" in t for t in texts)
    assert any(t == "A" for t in texts)
    assert sum(1 for e in block if e.dxftype() == "LINE") >= 4


def test_remap_tolerance_to_graphics_uses_gdt_font_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """检测到 GDT 字体时应保留 ``\\Fgdt;X`` 字母用 GDT 样式渲染，而非展开 Unicode。

    用内置等宽字体充当"GDT 字体"验证分段渲染路径：GDT 符号字母（r/n）用 gdt 样式、
    普通文字（0.03/A）用 cjk 样式。
    """
    import ezdxf

    from cad2image import render as render_mod
    from cad2image.render import _configure_fonts, _remap_tolerance_to_graphics

    gdt_font = "NotoSansMono-Regular.ttf"
    _configure_fonts("")  # 扫描 bundled fonts/，让 get_font_face 命中
    monkeypatch.setattr(render_mod, "_find_gdt_font", lambda _font_dir: gdt_font)

    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.new_entity(
        "TOLERANCE",
        dxfattribs={
            "insert": (0, 0),
            "content": r"{\Fgdt;r}%%v{\Fgdt;n}0.03%%v%%vA%%v%%v",
            "dimstyle": "Standard",
        },
    )

    _remap_tolerance_to_graphics(doc)

    inserts = [e for e in msp if e.dxftype() == "INSERT"]
    assert len(inserts) == 1
    block = doc.blocks.get(inserts[0].dxf.name)
    texts = [(e.dxf.text, e.dxf.style) for e in block if e.dxftype() == "TEXT"]
    assert ("r", "_cad2image_gdt") in texts  # 同心度字母保留，用 gdt 样式
    assert ("n", "_cad2image_gdt") in texts  # 直径字母保留，用 gdt 样式
    assert ("0.03", "_cad2image_cjk") in texts  # 公差值用 cjk 样式
    assert ("A", "_cad2image_cjk") in texts  # 基准字母用 cjk 样式


def test_tolerance_text_height_reads_xdata_dimtxt_override() -> None:
    """TOLERANCE 文字高度应优先取实体 XDATA 里 DSTYLE 覆盖的 DIMTXT（组码 140）。

    回归：AutoCAD 的 TOLERANCE（AcDbFcf）把创建时的文字高度存在实体 XDATA（应用名
    ``ACAD``、字符串 ``DSTYLE``）的组码 140 里，而实体 ``dimstyle`` 指向的样式 DIMTXT
    只是默认值。图纸整体放大（如 6:1）时两者相差数倍，只读样式 DIMTXT 会把形位公差框
    渲染得过小。
    """
    import ezdxf

    from cad2image.render import _tolerance_text_height

    doc = ezdxf.new("R2018")
    doc.dimstyles.get("Standard").dxf.dimtxt = 2.5
    msp = doc.modelspace()
    entity = msp.new_entity(
        "TOLERANCE",
        dxfattribs={"insert": (0, 0), "content": "A", "dimstyle": "Standard"},
    )
    entity.set_xdata(
        "ACAD",
        [(1000, "DSTYLE"), (1002, "{"), (1070, 140), (1040, 10.5), (1002, "}")],
    )

    assert _tolerance_text_height(doc, entity) == 10.5


def test_tolerance_text_height_falls_back_to_dimstyle() -> None:
    """无 XDATA 覆盖时，TOLERANCE 文字高度回退到样式 DIMTXT。"""
    import ezdxf

    from cad2image.render import _tolerance_text_height

    doc = ezdxf.new("R2018")
    doc.dimstyles.get("Standard").dxf.dimtxt = 2.5
    msp = doc.modelspace()
    entity = msp.new_entity(
        "TOLERANCE",
        dxfattribs={"insert": (0, 0), "content": "A", "dimstyle": "Standard"},
    )

    assert _tolerance_text_height(doc, entity) == 2.5


def test_remap_cjk_text_width_mtext_prefix() -> None:
    r"""含中文的 MTEXT 应加 ``\W0.734;`` 前缀压窄，纯西文不动。

    回归：ezdxf 不支持 SHX 中文大字体，中文用 TrueType 思源宋体替代后中文字符宽度是
    字高的 1.36 倍，比 SHX 原图宽 36%，会挤到相邻的序号圈/尺寸线。这里对含中文的
    MTEXT 加宽度因子前缀，把中文压窄回 1.0 字高。
    """
    import ezdxf

    from cad2image.render import _remap_cjk_text_width

    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_mtext("螺纹收尾1.27-2.54")
    msp.add_mtext("123.45")

    _remap_cjk_text_width(doc)

    texts = list(msp)
    assert texts[0].text.startswith("\\W0.734;"), texts[0].text
    assert "\\W" not in texts[1].text, texts[1].text


def test_remap_cjk_text_width_text_factor() -> None:
    """含中文的 TEXT 实体应把宽度因子设为 0.734，纯西文保持原值。"""
    import ezdxf

    from cad2image.render import _remap_cjk_text_width

    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_text("全部", dxfattribs={"height": 2.5})
    msp.add_text("3.2", dxfattribs={"height": 2.5})

    _remap_cjk_text_width(doc)

    texts = list(msp)
    assert abs(texts[0].dxf.width - 0.734) < 1e-6, texts[0].dxf.width
    assert texts[1].dxf.width == 1.0, texts[1].dxf.width


def test_looks_like_image_formats() -> None:
    """识别 BMP/PNG/JPEG/GIF 魔数，拒绝非图片与过短的 BMP。"""
    from cad2image.render import _looks_like_image

    assert _looks_like_image(b"BM" + b"\x00" * 30)
    assert _looks_like_image(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)
    assert _looks_like_image(b"\xff\xd8\xff\xe0" + b"\x00" * 10)
    assert _looks_like_image(b"GIF89a" + b"\x00" * 10)
    assert not _looks_like_image(b"NOT_AN_IMAGE" + b"\x00" * 20)
    assert not _looks_like_image(b"BM" + b"\x00" * 5)


def test_find_ole_image_stream_prefers_contents() -> None:
    """优先命中常见流名 CONTENTS，返回图片字节。"""
    from cad2image.render import _find_ole_image_stream

    bmp = b"BM" + b"\x00" * 30

    class _FakeStream:
        def read(self) -> bytes:
            return bmp

    class _FakeOle:
        def exists(self, name: str) -> bool:
            return name == "CONTENTS"

        def openstream(self, entry: object) -> _FakeStream:
            return _FakeStream()

        def listdir(self) -> list[object]:
            return []

    assert _find_ole_image_stream(_FakeOle()) == bmp


def test_find_ole_image_stream_falls_back_to_all_streams() -> None:
    """常见流名未命中时遍历全部流按魔数判断。"""
    from cad2image.render import _find_ole_image_stream

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10

    class _FakeStream:
        def read(self) -> bytes:
            return png

    class _FakeOle:
        def exists(self, name: str) -> bool:
            return False

        def openstream(self, entry: object) -> _FakeStream:
            return _FakeStream()

        def listdir(self) -> list[object]:
            return [["ObjectPool"], ["CONTENTS"]]

    assert _find_ole_image_stream(_FakeOle()) == png


def test_find_ole_image_stream_no_image_returns_none() -> None:
    """所有流都不是图片时返回 None。"""
    from cad2image.render import _find_ole_image_stream

    class _FakeStream:
        def read(self) -> bytes:
            return b"\x00" * 64

    class _FakeOle:
        def exists(self, name: str) -> bool:
            return True

        def openstream(self, entry: object) -> _FakeStream:
            return _FakeStream()

        def listdir(self) -> list[object]:
            return []

    assert _find_ole_image_stream(_FakeOle()) is None


def test_extract_ole_images_empty_layout() -> None:
    """无 OLE2FRAME 的布局返回空列表。"""
    import ezdxf

    from cad2image.render import _extract_ole_images

    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 10))

    assert _extract_ole_images(msp) == []


def test_extract_ole_images_ignores_invalid_ole_data() -> None:
    """OLE2FRAME 二进制数据无 OLE 复合文档魔数时跳过，不崩溃。"""
    import ezdxf
    from ezdxf.entities import OLE2Frame
    from ezdxf.lldxf.tags import Tags
    from ezdxf.lldxf.types import DXFTag

    from cad2image.render import _extract_ole_images

    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    ole = OLE2Frame.new(dxfattribs={"layer": "0"}, doc=doc)
    ole.acdb_ole2frame = Tags(
        [
            DXFTag(10, (0.0, 0.0, 0.0)),
            DXFTag(11, (100.0, 100.0, 0.0)),
            DXFTag(310, b"\x00\x01\x02"),  # 无 OLE 复合文档魔数
        ]
    )
    msp.add_entity(ole)

    assert _extract_ole_images(msp) == []


def test_wrap_text_to_width_cjk() -> None:
    """按宽度折行：中文逐字拆，超过 max_width 换行。"""
    from cad2image.render import _wrap_text_to_width

    def measure(t: str) -> float:
        return float(len(t))
    assert _wrap_text_to_width("工步一二三四", 3.0, measure) == ["工步一", "二三四"]


def test_wrap_text_to_width_keeps_ascii_run() -> None:
    """西文/数字连续段作为整体不拆分（"AB" 保持一个 token 不拆成 A、B）。"""
    from cad2image.render import _wrap_text_to_width

    def measure(t: str) -> float:
        return float(len(t))
    assert _wrap_text_to_width("AB余量", 3.0, measure) == ["AB余", "量"]


def test_remap_cjk_text_width_wrap_wraps_long_text() -> None:
    r"""含中文、框宽>0 且超宽的 MTEXT 应插入 \P 折行。"""
    import ezdxf

    from cad2image.render import _configure_fonts, _remap_cjk_text_width_wrap

    _configure_fonts("")
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_mtext(
        "工步一：以B面为基准磨C面，C面磨削余量0.03以内",
        dxfattribs={"width": 40.0, "char_height": 2.5},
    )

    _remap_cjk_text_width_wrap(doc)

    mtext = list(msp)[0]
    assert r"\P" in mtext.text, mtext.text


def test_remap_cjk_text_width_wrap_skips_short_or_wide() -> None:
    """短文本、无框宽（width<=0）、纯西文不折行。"""
    import ezdxf

    from cad2image.render import _configure_fonts, _remap_cjk_text_width_wrap

    _configure_fonts("")
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_mtext("短文本", dxfattribs={"width": 100.0, "char_height": 2.5})
    msp.add_mtext("无框宽的长中文文本", dxfattribs={"width": 0.0, "char_height": 2.5})
    msp.add_mtext("ASCII only text", dxfattribs={"width": 5.0, "char_height": 2.5})

    _remap_cjk_text_width_wrap(doc)

    texts = [e.text for e in msp]
    assert all(r"\P" not in t for t in texts), texts


def test_wrap_text_to_width_kinsoku_start_forbidden() -> None:
    """折行后闭标点（，）不应落在行首，应连带前一字一起换到下一行。"""
    from cad2image.render import _wrap_text_to_width

    def measure(t: str) -> float:
        return float(len(t))

    lines = _wrap_text_to_width("螺纹收尾，去毛刺", 4.0, measure)
    assert lines == ["螺纹收", "尾，去毛刺"], lines


def test_wrap_text_to_width_kinsoku_end_forbidden() -> None:
    """折行后开标点（（）不应落在行尾，应移到下一行开头。"""
    from cad2image.render import _wrap_text_to_width

    def measure(t: str) -> float:
        return float(len(t))

    lines = _wrap_text_to_width("完成（余量", 3.0, measure)
    assert lines == ["完成", "（余量"], lines


def test_remap_cjk_text_width_wrap_keeps_attachment_point() -> None:
    """折行只改 text、不改 attachment_point，多行对齐由 ezdxf 按锚点处理。"""
    import ezdxf

    from cad2image.render import _configure_fonts, _remap_cjk_text_width_wrap

    _configure_fonts("")
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    # attachment_point=4（Middle left）：多行文字从 insert 点垂直居中堆叠。
    msp.add_mtext(
        "工步一：以B面为基准磨C面，C面磨削余量0.03以内",
        dxfattribs={"width": 40.0, "char_height": 2.5, "attachment_point": 4},
    )

    _remap_cjk_text_width_wrap(doc)

    mtext = list(msp)[0]
    assert r"\P" in mtext.text, mtext.text
    assert mtext.dxf.attachment_point == 4


def test_remap_cjk_text_width_wrap_skips_width_equals_text_width() -> None:
    """无框宽约束（width≈文字实际宽度）的 MTEXT 不应被浮点误差误折行。"""
    import ezdxf

    from cad2image.render import _configure_fonts, _remap_cjk_text_width_wrap

    _configure_fonts("")
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    # "螺纹有效长度" 6 字、char_height=2.5，文字实际宽度（SHX 下）≈15；ODA 把 width 填成 14.93。
    msp.add_mtext("螺纹有效长度", dxfattribs={"width": 14.93, "char_height": 2.5})

    _remap_cjk_text_width_wrap(doc)

    mtext = list(msp)[0]
    assert r"\P" not in mtext.text, mtext.text


def test_remap_cjk_text_width_wrap_still_wraps_narrow_width() -> None:
    """框宽明显小于文字宽（真框宽约束，>1.3 倍）的 MTEXT 仍应折行。"""
    import ezdxf

    from cad2image.render import _configure_fonts, _remap_cjk_text_width_wrap

    _configure_fonts("")
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    # 文字压窄后宽约 23×2.5=57.5，框宽 15 明显放不下（比例约 3.8）→ 应折行。
    msp.add_mtext(
        "工步一：以B面为基准磨C面，C面磨削余量0.03以内",
        dxfattribs={"width": 15.0, "char_height": 2.5},
    )

    _remap_cjk_text_width_wrap(doc)

    mtext = list(msp)[0]
    assert r"\P" in mtext.text, mtext.text


def test_remap_cjk_text_width_wrap_skips_short_text_with_punct() -> None:
    """含全角标点的短文字不应被误折行（TrueType 标点比 SHX 宽导致的高估）。"""
    import ezdxf

    from cad2image.render import _configure_fonts, _remap_cjk_text_width_wrap

    _configure_fonts("")
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    # "全部：" 压窄后约 7.5，ODA 的 width=5.9（SHX 里冒号是窄的），比例 1.27 < 1.30 → 不折。
    msp.add_mtext("全部：", dxfattribs={"width": 5.9, "char_height": 2.5})

    _remap_cjk_text_width_wrap(doc)

    mtext = list(msp)[0]
    assert r"\P" not in mtext.text, mtext.text


def test_remap_leader_dimension_to_graphics_skips_incomplete() -> None:
    """构造不完整的 DIMENSION（缺 text_midpoint）不应崩溃，且保留原实体。"""
    import ezdxf

    from cad2image.render import _remap_leader_dimension_to_graphics

    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_linear_dim(base=(0, 0), p1=(0, 5), p2=(5, 5))
    dim = list(msp.query("DIMENSION"))[0]
    dim.dxf.dimtype = 163
    dim.dxf.discard("defpoint2")

    _remap_leader_dimension_to_graphics(doc)

    assert len(list(msp.query("DIMENSION"))) == 1


def test_remap_wide_polyline_to_graphics_expands_arrow() -> None:
    """带宽度的 LWPOLYLINE（PL 画的箭头）应展开成 LINE + SOLID。

    回归：ezdxf 1.1.3 的 TraceBuilder 对「宽度 0→W→0」的带宽度多段线（如坐标指引线
    箭头）生成的带状多边形退化成零宽、渲染不可见。这里手动按每段宽度展开：零宽段画
    LINE、有宽段画 SOLID。
    """
    import ezdxf

    from cad2image.render import _remap_wide_polyline_to_graphics

    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    pl = msp.add_lwpolyline([(0, 0), (100, 0), (125, 0)])
    pl.set_points(
        [(0, 0, 0.0, 0.0, 0.0), (100, 0, 10.0, 0.0, 0.0), (125, 0, 0.0, 0.0, 0.0)],
        format="xyseb",
    )

    _remap_wide_polyline_to_graphics(doc)

    assert "LWPOLYLINE" not in [e.dxftype() for e in msp]
    solids = list(msp.query("SOLID"))
    lines = list(msp.query("LINE"))
    assert len(solids) == 1  # 箭头（有宽段）
    assert len(lines) == 1  # 引线主体（零宽段）


def test_clamp_dpi_reduces_for_oversized_content() -> None:
    """超大内容（如十几米的嵌入图片）在默认 dpi 下应自动下调 dpi 防 OOM。

    回归：纯图片 CAD 里 OLE2FRAME 被放大到 16×10 米，dpi=100 光栅化需 10.7GB 内存、
    dpi=300 需 96GB，进程被 OOM killer 杀死报 ``BrokenProcessPool``。这里验证
    ``_clamp_dpi`` 按像素量上限下调 dpi。
    """
    from ezdxf.math import Vec2

    from cad2image.render import _clamp_dpi, _EmbeddedImage

    # 16328.6 × 10598 mm 的内容，dpi=100 下约 2682M 像素（远超 100M 上限）。
    image = _EmbeddedImage(
        image_bytes=b"",
        corner_min=Vec2(0, 0),
        corner_max=Vec2(16328.6, 10598.0),
    )
    clamped = _clamp_dpi(100, None, [image], RenderOptions())
    assert clamped < 100
    # 降幅后像素量应落在上限附近（约 100M），不会留几十 GB 的量。
    mm_per_inch = 25.4
    pixels = (16328.6 * clamped / mm_per_inch) * (10598.0 * clamped / mm_per_inch)
    assert pixels <= 100_000_000 * 1.01


def test_clamp_dpi_keeps_normal_content_unchanged() -> None:
    """普通尺寸内容（像素量在上限内）应保持 dpi 不变。"""
    from ezdxf.math import Vec2

    from cad2image.render import _clamp_dpi, _EmbeddedImage

    # 634 × 642 mm，dpi=300 下约 57M 像素，在 100M 上限内。
    image = _EmbeddedImage(image_bytes=b"", corner_min=Vec2(0, 0), corner_max=Vec2(634, 642))
    assert _clamp_dpi(300, None, [image], RenderOptions()) == 300


def test_clamp_dpi_uses_explicit_page_size() -> None:
    """显式 width_mm/height_mm 应优先于内容包围盒参与像素估算。"""
    from cad2image.render import _clamp_dpi

    # 显式 1000×1000 mm，dpi=300 下约 139M 像素，应略降。
    options = RenderOptions(width_mm=1000.0, height_mm=1000.0)
    clamped = _clamp_dpi(300, None, [], options)
    assert clamped < 300
    mm_per_inch = 25.4
    pixels = (1000.0 * clamped / mm_per_inch) * (1000.0 * clamped / mm_per_inch)
    assert pixels <= 100_000_000 * 1.01


def test_resolve_dpi_uses_resolution_for_small_content() -> None:
    """--resolution 目标长边像素应按内容长边换算 dpi，图小自动提 dpi 保证清晰度。

    回归：固定 --dpi 下小图（如数车2 的 195×74mm）像素很少、模糊；--resolution 应
    按内容长边换算 dpi，使长边达到目标像素。
    """
    from ezdxf.math import BoundingBox2d

    from cad2image.render import _resolve_dpi

    bbox = BoundingBox2d([(0.0, 0.0), (195.16, 74.29)])
    dpi = _resolve_dpi(RenderOptions(dpi=100, resolution=2048), bbox, [])
    assert dpi > 100  # 小图应自动提 dpi
    # 长边像素应约等于目标分辨率（取整误差内）。
    assert dpi * 195.16 / 25.4 == pytest.approx(2048, rel=0.01)


def test_resolve_dpi_falls_back_to_dpi_when_no_resolution() -> None:
    """未指定 --resolution 时退回固定 --dpi。"""
    from ezdxf.math import BoundingBox2d

    from cad2image.render import _resolve_dpi

    bbox = BoundingBox2d([(0.0, 0.0), (195.16, 74.29)])
    assert _resolve_dpi(RenderOptions(dpi=100), bbox, []) == 100


def test_resolve_dpi_clamps_oversized_resolution() -> None:
    """--resolution 换算出的 dpi 若导致超大页面，仍受像素上限钳制防 OOM。"""
    from ezdxf.math import BoundingBox2d

    from cad2image.render import _resolve_dpi

    # 16m 长的超大内容，--resolution 4k 换算的 dpi 很小（~6），不会超上限。
    bbox = BoundingBox2d([(0.0, 0.0), (16328.6, 10598.0)])
    dpi = _resolve_dpi(RenderOptions(dpi=300, resolution=4096), bbox, [])
    assert dpi <= 300
