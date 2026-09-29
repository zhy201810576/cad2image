"""DXF → PNG/SVG 渲染核心。

基于 ``ezdxf.addons.drawing``，PNG 走 PyMuPDF 后端（圆弧渲染为真圆弧，无 GDI 毛须），
SVG 走 ezdxf SVG 后端。渲染流程：

    readfile → 选布局 → 确定页面尺寸 → Configuration → Frontend.draw_layout → 输出
"""

from __future__ import annotations

import math
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import ezdxf
import fitz  # type: ignore[import-untyped]
from ezdxf import bbox
from ezdxf.addons.drawing import Frontend, RenderContext, pymupdf
from ezdxf.addons.drawing import layout as layout_module
from ezdxf.addons.drawing.backend import BackendProperties, NumpyPoints2d
from ezdxf.addons.drawing.svg import SVGBackend
from ezdxf.addons.drawing.unified_text_renderer import UnifiedTextRenderer
from ezdxf.entities import Dimension, DXFGraphic, MultiLeader
from ezdxf.enums import TextEntityAlignment
from ezdxf.fonts import fonts as ezdxf_fonts
from ezdxf.layouts import BlockLayout, Layout
from ezdxf.math import Vec2, Vec3

from cad2image.config import RenderOptions, build_drawing_configuration

_SUPPORTED_IMAGE_FORMATS = {".png"}

# 匹配 XML 声明中的 encoding 属性，用于归一化为 utf-8。
_XML_ENCODING_RE = re.compile(r"(encoding=)['\"][^'\"]*['\"]")

# 匹配 ODA 输出的多字节转义 \M+nXXXX（GBK hex）。ODA File Converter 在 Linux 上处理
# CAD2000 老版本 DWG 时，中文会以 \M+5XXXX 形式输出 GBK 字节（5 为字节数前缀，
# XXXX 是 GBK 双字节的十六进制）；ezdxf 不认识 M 转义，会原样渲染成乱码。
_M_PLUS_ESCAPE_RE = re.compile(r"\\[Mm]\+([0-9A-Fa-f]{4,5})")

# 需要解码多字节转义的文字实体类型。注意：DIMENSION 不在此列——它的渲染文本不在
# text 覆盖（组码 1）里，而在关联的匿名几何块中，需由 _remap_dimension_geometry_texts
# 单独处理（见下）。
_TEXT_ENTITIES = {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}

# 匹配 MTEXT 内联字体覆盖 \fXXX; / \FXXX;（XXX 为字体名，含可选的 |flags）。
# 部分图纸用内联 \fNSimSun 覆盖样式字体，而 ezdxf 对内联字体的解析与样式字体不同
# ——内联字体名无法命中扫描目录、回退到默认字体，导致西文/直径符号等方框。移除内联
# 覆盖后文本统一回落到已 remap 到内置字体的样式字体，渲染正确。
_INLINE_FONT_RE = re.compile(r"\\[fF][^;]*;")

# 内置字体缺少字形的符号 → 视觉等价的有字形符号。思源宋体（SourceHanSerifSC）缺少
# 直径符号 U+2300（渲染成 .notdef 方框），用有字形的 U+00D8 替代，保证直径符号可见。
_GLYPH_FALLBACKS = {chr(0x2300): chr(0x00D8)}

# AutoCAD 控制码 %%c/%%d/%%p 及字面百分号 %%%。ezdxf 不解析这些控制码，会原样渲染成
# "%c" 之类；这里展开为内置字体实际包含的字形（%%c → Ø 而非 U+2300，因宋体缺后者）。
_AUTOCAD_CONTROL_RE = re.compile(r"%%[cCdDpP%]")
_AUTOCAD_CONTROL_MAP = {"c": chr(0x00D8), "d": chr(0x00B0), "p": chr(0x00B1)}

# 形位公差（TOLERANCE / AcDbFcf）内容里的 GDT 符号编码。AutoCAD 用 ``{\Fgdt;X}`` 内联
# 切换到 gdt 符号字体、用单个字母 X 表示一个形位公差符号。映射依据 ObjectARX 文档
# AcDbFcf::setText 的符号表（小写字母 → 形位公差符号、材料条件修饰符等）。
# 直径符号 n 用 U+00D8 而非 U+2300，因为内置思源宋体缺 U+2300 字形（见 _GLYPH_FALLBACKS）。
_GDT_SYMBOLS = {
    "j": chr(0x2316),  # 位置度 ⌖
    "r": chr(0x25CE),  # 同轴度/同心度 ◎
    "i": chr(0x232F),  # 对称度 ⌯
    "f": chr(0x2225),  # 平行度 ∥
    "b": chr(0x22A5),  # 垂直度 ⊥
    "a": chr(0x2220),  # 倾斜度 ∠
    "g": chr(0x232D),  # 圆柱度 ⌭
    "c": chr(0x25B1),  # 平面度 ▱
    "e": chr(0x25CB),  # 圆度 ○
    "u": chr(0x2500),  # 直线度 ─
    "d": chr(0x2313),  # 面轮廓度 ⌓
    "k": chr(0x2312),  # 线轮廓度 ⌒
    "h": chr(0x2197),  # 圆跳动 ↗
    "t": chr(0x2330),  # 全跳动 ⌰
    "n": chr(0x00D8),  # 直径 Ø
    "m": chr(0x24C2),  # 最大实体 Ⓜ
    "l": chr(0x24C1),  # 最小实体 Ⓛ
    "s": chr(0x24C8),  # 独立原则 Ⓢ
    "p": chr(0x24C5),  # 投影公差带 Ⓟ
}
# 匹配 TOLERANCE 内容里的 ``\Fgdt;X`` 符号（不区分大小写）。
_GDT_FONT_RE = re.compile(r"\\[Ff]gdt;([a-zA-Z])")


class _SafeRenderBackend(pymupdf.PyMuPdfRenderBackend):
    """修复 PyMuPDF 1.24.11 的 Vec2 bug，并按页面较小边自动调整相对线宽。

    上游 ``Shape.draw_polyline`` 会把原始 ``Vec2`` 直接传给 ``updateRect``，
    而 ``pymupdf.Rect(Vec2, Vec2)`` 无法解析 ``Vec2``（报 ``float(None)``），
    导致含 SOLID 箭头（尺寸/引线标注）的图纸渲染崩溃。这里在绘制填充多边形前
    将顶点转换为普通 tuple 以规避该问题。

    相对线宽基准改用页面较小边（ezdxf 默认用较大边），避免极扁/极长图纸
    （如 2000x190mm）的线宽被放大得过粗而粘连；且不用 ``int()`` 取整，
    避免小图线宽被截断到 0。
    """

    def __init__(self, page: layout_module.Page, settings: layout_module.Settings) -> None:
        super().__init__(page, settings)
        smaller_pt = min(page.width_in_mm, page.height_in_mm) * pymupdf.MM_TO_POINTS
        self.max_stroke_width = max(self.abs_min_stroke_width, smaller_pt * settings.max_stroke_width)
        self.min_stroke_width = max(self.abs_min_stroke_width, self.max_stroke_width * settings.min_stroke_width)

    def draw_filled_polygon(self, points: NumpyPoints2d, properties: BackendProperties) -> None:
        vertices: list[Vec2] = points.vertices()
        if len(vertices) < 3:
            return
        shape = self.new_shape()  # type: ignore[no-untyped-call]
        shape.draw_polyline([(float(v.x), float(v.y)) for v in vertices])
        self.finish_filling(shape, properties)
        shape.commit()


class _SafePyMuPdfBackend(pymupdf.PyMuPdfBackend):
    """返回使用 :class:`_SafeRenderBackend` 的 PyMuPDF 后端。"""

    @staticmethod
    def make_backend(page: layout_module.Page, settings: layout_module.Settings) -> _SafeRenderBackend:
        return _SafeRenderBackend(page, settings)


# 线程安全：``_defer_content_wrap`` 会临时替换进程级全局的 ``fitz.Page.wrap_contents``。
# 用引用计数 + 锁让并发渲染共享同一补丁：首个进入者捕获原实现并打补丁，最后一个退出者
# 恢复原实现；中间并发进入者只递增计数，互不干扰，使同进程多线程并发渲染成为可能。
_wrap_contents_lock = threading.RLock()
_wrap_contents_refcount = 0
_wrap_contents_original: object | None = None


def _no_op_wrap_contents(*_args: object, **_kwargs: object) -> None:
    """``wrap_contents`` 的 no-op 替代实现（仅光栅化期间启用）。"""
    return None


@contextmanager
def _defer_content_wrap() -> Iterator[None]:
    """渲染光栅化期间禁用 PyMuPDF 的内容流平衡扫描。

    ``Shape.commit()`` 每次都会调用 ``Page.wrap_contents()``，后者通过
    ``pdf_count_q_balance`` 扫描整条已累积的内容流来计数 ``q/Q`` 操作符，N 次
    commit 累积成 O(N²)（实测在中等图纸上是 ``get_pixmap_bytes`` 的最大热点）。
    ezdxf 后端生成的填充/描边内容只用 ``w/J/j/gs/S/f`` 等操作符、不含 ``q/Q``，
    本身已平衡，因此该扫描是纯开销——逐字节比对下输出完全一致，却可提速约 2.5 倍，
    且实体越多收益越大。

    线程安全：引用计数 + 锁共享补丁（见模块级 ``_wrap_contents_*``），首个进入者
    打补丁、最后一个退出者恢复，同进程内多个线程可并发进入而不互相踩踏。
    """
    global _wrap_contents_refcount, _wrap_contents_original
    with _wrap_contents_lock:
        if _wrap_contents_refcount == 0:
            _wrap_contents_original = fitz.Page.wrap_contents
            fitz.Page.wrap_contents = _no_op_wrap_contents
        _wrap_contents_refcount += 1
    try:
        yield
    finally:
        with _wrap_contents_lock:
            _wrap_contents_refcount -= 1
            if _wrap_contents_refcount == 0:
                fitz.Page.wrap_contents = _wrap_contents_original
                _wrap_contents_original = None


def render_dxf(dxf_path: str | Path, output_path: str | Path, options: RenderOptions) -> Path:
    """把 DXF 渲染为 PNG 或 SVG，根据输出文件扩展名自动选择后端。

    Args:
        dxf_path: 源 DXF 文件路径。
        output_path: 输出文件路径（``.png`` 或 ``.svg``）。
        options: 渲染参数。

    Returns:
        输出文件路径。

    Raises:
        FileNotFoundError: 当源 DXF 不存在时。
        ValueError: 当输出扩展名不受支持，或渲染参数非法时。
        RuntimeError: 当 DXF 解析或渲染失败时。
    """
    target = Path(output_path)
    suffix = target.suffix.lower()
    if suffix == ".png":
        return render_to_png(dxf_path, target, options)
    if suffix == ".svg":
        return render_to_svg(dxf_path, target, options)
    raise ValueError(f"不支持的输出格式 '{suffix}'，仅支持 .png / .svg")


def render_to_png(dxf_path: str | Path, output_path: str | Path, options: RenderOptions) -> Path:
    """把 DXF 渲染为 PNG（PyMuPDF 后端）。

    Args:
        dxf_path: 源 DXF 文件路径。
        output_path: 输出 PNG 文件路径。
        options: 渲染参数。

    Returns:
        输出 PNG 文件路径。
    """
    target = Path(output_path)
    # 必须先扫描字体：bbox 测量文本尺寸时会加载字体，若字体未就绪会回退到内置 arial
    # 并被全局字体管理器缓存，导致后续（含中文字体）全部失效。
    _configure_fonts(options.font_dir)
    doc = _read_document(dxf_path)
    _decode_multibyte_text(doc)
    _expand_autocad_control_codes(doc)
    _remap_missing_glyphs(doc)
    _remap_text_style_fonts(doc)
    _remap_cjk_text_styles(doc)
    _remap_mtext_inline_fonts(doc)
    _remap_dimension_geometry_texts(doc)
    _remap_dimension_properties(doc)
    _clear_mleader_proxy_graphics(doc)
    _remap_mleader_properties(doc)
    _remap_mleader_text(doc)
    _remap_tolerance_to_graphics(doc)
    dxf_layout = _select_layout(doc, options.layout_name)
    page = _determine_page(dxf_layout, options)
    drawing_config = build_drawing_configuration(options)

    context = RenderContext(doc, ctb=_validate_ctb(options.ctb))
    backend = _SafePyMuPdfBackend()
    Frontend(context, backend, config=drawing_config).draw_layout(dxf_layout, finalize=True)
    settings = _build_render_settings(options)
    with _defer_content_wrap():
        image_bytes = backend.get_pixmap_bytes(page, fmt="png", dpi=options.dpi, settings=settings)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(image_bytes)
    return target


def render_to_svg(dxf_path: str | Path, output_path: str | Path, options: RenderOptions) -> Path:
    """把 DXF 渲染为 SVG（ezdxf SVG 后端）。

    Args:
        dxf_path: 源 DXF 文件路径。
        output_path: 输出 SVG 文件路径。
        options: 渲染参数。

    Returns:
        输出 SVG 文件路径。
    """
    target = Path(output_path)
    # 必须先扫描字体：bbox 测量文本尺寸时会加载字体，若字体未就绪会回退到内置 arial
    # 并被全局字体管理器缓存，导致后续（含中文字体）全部失效。
    _configure_fonts(options.font_dir)
    doc = _read_document(dxf_path)
    _decode_multibyte_text(doc)
    _expand_autocad_control_codes(doc)
    _remap_missing_glyphs(doc)
    _remap_text_style_fonts(doc)
    _remap_cjk_text_styles(doc)
    _remap_mtext_inline_fonts(doc)
    _remap_dimension_geometry_texts(doc)
    _remap_dimension_properties(doc)
    _clear_mleader_proxy_graphics(doc)
    _remap_mleader_properties(doc)
    _remap_mleader_text(doc)
    _remap_tolerance_to_graphics(doc)
    dxf_layout = _select_layout(doc, options.layout_name)
    page = _determine_page(dxf_layout, options)
    drawing_config = build_drawing_configuration(options)

    context = RenderContext(doc, ctb=_validate_ctb(options.ctb))
    backend = SVGBackend()
    Frontend(context, backend, config=drawing_config).draw_layout(dxf_layout, finalize=True)
    settings = _build_render_settings(options)
    svg_string = _normalize_svg_encoding(backend.get_string(page, settings=settings))

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(svg_string, encoding="utf-8")
    return target


def _read_document(dxf_path: str | Path) -> ezdxf.document.Drawing:
    """读取 DXF 文档，缺文件或内容非法时抛出带上下文的异常。"""
    source = Path(dxf_path)
    if not source.is_file():
        raise FileNotFoundError(f"源 DXF 文件不存在：{source}")
    try:
        return ezdxf.readfile(str(source))
    except (ezdxf.DXFError, OSError) as exc:
        raise RuntimeError(f"无法解析 DXF 文件 {source}：{exc}") from exc


# 线程安全：ezdxf 字体管理器是进程级单例，``scan_folder`` 会重建其索引，并发扫描
# 不安全。用锁 + 已扫描目录集合，使每个目录只扫描一次（幂等且线程安全），避免并发
# 渲染时多线程同时 scan_folder 互相踩踏。字体目录在进程生命周期内通常不变，一次即可。
_font_scan_lock = threading.Lock()
_scanned_font_dirs: set[Path] = set()


def _configure_fonts(font_dir: str) -> None:
    """把附加字体目录中的 SHX/TTF 字体扫描进 ezdxf 全局字体管理器。

    总是先扫描项目自带的 ``fonts/`` 目录（含从 TTC 提取出的中文字体），再扫描
    用户通过 ``--font-dir`` 指定的附加目录。字体管理器是进程级单例，扫描后该进程内
    后续所有渲染都能解析到这些字体。

    线程安全：扫描加锁且每个目录只扫一次（见模块级 ``_font_scan_lock`` /
    ``_scanned_font_dirs``），并发渲染安全；附加目录不存在时仍每次校验并抛错。

    Args:
        font_dir: 附加字体目录路径，空字符串表示仅扫描项目自带 ``fonts/``。

    Raises:
        FileNotFoundError: 当附加字体目录不存在时。
    """
    manager = ezdxf_fonts.font_manager
    bundled = _bundled_font_dir()
    dirs: list[Path] = []
    if bundled.is_dir():
        dirs.append(bundled)
    if font_dir:
        path = Path(font_dir)
        if not path.is_dir():
            raise FileNotFoundError(f"字体目录不存在：{path}")
        dirs.append(path.resolve())
    with _font_scan_lock:
        for directory in dirs:
            if directory not in _scanned_font_dirs:
                manager.scan_folder(directory)
                _scanned_font_dirs.add(directory)


def _bundled_font_dir() -> Path:
    """返回随包内置的 ``fonts/`` 目录（含中文字体）。

    字体作为包数据随 wheel 一起分发，目录相对包目录定位（``__file__`` 的父目录），
    因此在源码、editable 安装与正式安装三种布局下都能命中同一份字体。

    目录不存在时返回的路径仅用于 ``is_dir`` 判断，调用方据此决定是否扫描。
    """
    return Path(__file__).resolve().parent / "fonts"


# 专有字体 → 随包内置的开源字体（SIL OFL 1.1）。ezdxf 按文件名查找字体，
# 因此只需把文字样式的 font 名重写为内置字体的文件名即可命中。
_OPEN_CJK_FONT = "SourceHanSerifSC-Regular.ttf"  # 中文（含拉丁字符，TrueType 轮廓）
_OPEN_MONO_FONT = "NotoSansMono-Regular.ttf"  # ASCII 等宽，近似 CAD 单线字体

# 西文单线字体名（Autodesk 标准 SHX，近似 CAD 单线字体），规范化（小写、去 .shx/.ttf
# 后缀）后比较。命中 → 内置等宽字体；其余字体名（含所有中文字体名、中文 bigfont SHX、
# 未知名字）一律映射到内置中文字体。
#
# 这里刻意**不再维护"中文字体名白名单"**：用户会用到什么中文字体名（SimHei / FangSong /
# KaiTi / 宋体 / 仿宋 / 微软雅黑…）无法穷举，且不少是纯英文（SimHei、FangSong_GB2312
# 等）、名字里不含汉字、也不以 .shx 结尾，旧的白名单 + 汉字启发式会漏判 → 误入等宽
# 字体（无中文字形）→ 中文方框。改为"默认落思源宋体"：思源宋体含 CJK 与拉丁字形，
# 无论哪种字体名落到它都不会方框，代价仅是西文字体观感统一（单线变衬线），可接受。
_LATIN_MONO_FONT_STEMS = {
    "romans", "romanc", "romand", "romant",
    "txt", "monotxt",
    "simplex", "complex",
    "isocp", "isocp2", "isocp3", "isocpeur",
    "italic", "italicc", "italict",
    "gothic", "gothice", "gothicg", "gothici",
    "gdt",
    "arial",  # Arial（含 Arial.shx）：西文无衬线，等宽观感更接近 CAD 单线
}


def _normalize_font_stem(font_name: str) -> str:
    """规范化字体名：小写并去掉 .shx/.ttf 扩展名，得到用于白名单比较的 stem。"""
    lower = font_name.lower()
    for suffix in (".shx", ".ttf"):
        if lower.endswith(suffix):
            return lower[: -len(suffix)]
    return lower


def _map_font_name(font_name: str) -> str:
    """把字体名确定性映射到随包内置的两个开源字体之一。

    西文单线字体（Autodesk SHX）→ 内置等宽字体；其余（含所有中文字体名、中文
    bigfont SHX、空字体名、未知字体名）→ 内置思源宋体。思源宋体含 CJK 与拉丁字形，
    作为默认兜底，保证无论图纸引用哪种字体名，中文都不会渲染成方框。

    Args:
        font_name: 文字样式原始 font 名（如 ``"NSimSun.ttf"``、``"romans.shx"``）。

    Returns:
        内置开源字体文件名（``SourceHanSerifSC-Regular.ttf`` 或
        ``NotoSansMono-Regular.ttf``）。
    """
    name = font_name.strip()
    if not name:
        # 部分 ODA 版本转出的文字样式 font 为空。此时无从判断中西文，而内置中文
        # 字体同时含 CJK 与拉丁字形，作为兜底最安全（否则 ezdxf 回退到无中文字形
        # 的 arial，中文变方框）。
        return _OPEN_CJK_FONT
    if _normalize_font_stem(name) in _LATIN_MONO_FONT_STEMS:
        return _OPEN_MONO_FONT
    return _OPEN_CJK_FONT


def _remap_text_style_fonts(doc: ezdxf.document.Drawing) -> None:
    """把文档中引用专有字体的文字样式重写为随包内置的开源字体。

    在创建渲染上下文之前调用，确保引用 SimSun / romans / txt 等专有字体的图纸能
    命中开源替代字体，而不是回退到无中文字形的系统默认字体（中文变方框）。
    """
    for style in doc.styles:
        mapped = _map_font_name(style.dxf.font)
        if mapped != style.dxf.font:
            style.dxf.font = mapped


def _ensure_cjk_style(doc: ezdxf.document.Drawing) -> str:
    """返回一个指向内置中文字体的样式名，不存在则创建。

    用于把含中文但样式字体无法渲染中文的文字实体（如空样式名、或引用 ``arial.ttf``
    等纯西文字体）重指到中文字体。优先复用已映射到内置中文字体的既有样式，避免每张
    图都新建样式。
    """
    for style in doc.styles:
        if style.dxf.get("font", "") == _OPEN_CJK_FONT:
            return str(style.dxf.name)
    name = "_cad2image_cjk"
    doc.styles.add(name, font=_OPEN_CJK_FONT)
    return name


def _contains_cjk(text: str) -> bool:
    """判断字符串是否含 CJK 汉字（用于识别需要中文字形渲染的文字）。"""
    return any("一" <= ch <= "鿿" for ch in text)


def _remap_cjk_text_styles(doc: ezdxf.document.Drawing) -> None:
    """把含中文但样式字体非中文字体的文字实体，重指到内置中文字体样式。

    ODA 转出的部分文字实体样式名为空（尤其 DIMENSION 几何块内的 MTEXT），ezdxf 渲染
    时把空样式回退到 ``Standard``（ODA 常写为 ``arial.ttf``，无中文字形）→ 中文方框。
    只重写样式表无效——这些实体的样式名本身是空的、根本没指到中文字体样式。这里按
    文字内容判中文，把这类实体统一指到 :func:`_ensure_cjk_style` 返回的样式。
    """
    cjk_style_name = _ensure_cjk_style(doc)
    for entity in _iter_text_entities(doc):
        raw = entity.dxf.get("text", "")
        if not _contains_cjk(raw):
            continue
        style_name = entity.dxf.get("style", "") or "Standard"
        style = doc.styles.get(style_name)
        font = style.dxf.get("font", "") if style is not None else ""
        if font == _OPEN_CJK_FONT:
            continue  # 已经是中文字体
        entity.dxf.style = cjk_style_name


def _iter_text_entities(doc: ezdxf.document.Drawing) -> Iterator[DXFGraphic]:
    """遍历文档内所有文字实体，覆盖模型/图纸空间与块定义（含匿名块）。

    ezdxf 渲染 INSERT 引用的块时会递归渲染块定义内的文字实体，而这些实体只存在于
    ``doc.blocks``（不在 ``doc.layouts``）。若只遍历 ``doc.layouts`` 会漏掉块内文字
    （标题栏、图框、工艺单等里的中文），导致内联字体 / 控制码 / 多字节转义未被处理，
    中文或符号变方框。
    """
    for block in doc.blocks:
        for entity in block:
            if entity.dxftype() in _TEXT_ENTITIES:
                yield entity


def _remap_mtext_inline_fonts(doc: ezdxf.document.Drawing) -> None:
    """移除 MTEXT 内联字体覆盖（``\\fXXX;`` / ``\\FXXX;``），回落到样式字体。

    ezdxf 对 MTEXT 内联字体的解析与样式字体不同：内联字体名（如 ``NSimSun`` 或
    改写后的 TTF 文件名）无法命中扫描目录、回退到默认字体，导致其中的西文/直径
    符号等变方框。移除内联覆盖后，文本统一使用样式字体（已在
    :func:`_remap_text_style_fonts` 中映射到内置字体），渲染正确。
    """
    for entity in _iter_text_entities(doc):
        if entity.dxftype() != "MTEXT":
            continue
        raw = entity.dxf.get("text", "")
        if not raw or "\\f" not in raw.lower():
            continue
        new = _INLINE_FONT_RE.sub("", raw)
        if new != raw:
            entity.dxf.text = new


def _remap_dimension_geometry_texts(doc: ezdxf.document.Drawing) -> None:
    """重映射 DIMENSION 几何块内的文字实体。

    ezdxf 渲染 DIMENSION 时读的是关联匿名几何块里的 MTEXT/TEXT（由 ODA 转换时写死，
    含 ``%%c`` 控制码与 ``\\f`` 内联字体），**而不是** ``dxf.text``（组码 1 的覆盖），
    因此只改 ``dxf.text`` 不生效——直径尺寸标注的 ``%%c`` 不展开 → 方框。

    这里直接对几何块内的文字实体应用与普通文字相同的重映射链（解码 \\M+、展开
    %%c/%%d/%%p、替换缺字形、剥离内联字体），保证直径/度/正负符号正确渲染。
    """
    for layout in doc.layouts:
        for entity in layout:
            if not isinstance(entity, Dimension):
                continue
            block = entity.get_geometry_block()
            if block is None:
                continue
            for block_entity in block:
                if block_entity.dxftype() not in _TEXT_ENTITIES:
                    continue
                raw = block_entity.dxf.get("text", "")
                if not raw:
                    continue
                new = _decode_multibyte_escapes(raw)
                new = _expand_autocad_control_codes_in_text(new)
                for src, dst in _GLYPH_FALLBACKS.items():
                    if src in new:
                        new = new.replace(src, dst)
                new = _INLINE_FONT_RE.sub("", new)
                if new != raw:
                    block_entity.dxf.text = new


def _remap_dimension_properties(doc: ezdxf.document.Drawing) -> None:
    """把 DIMENSION 几何块子实体的 ByBlock 颜色/线宽改成标注实体自身的值。

    ODA 转出的 DIMENSION 几何块内子实体（尺寸线、箭头、文字）颜色与线宽为 ByBlock（0 /
    -2），语义是"继承标注实体"。但 ezdxf 渲染 DIMENSION 时把几何块子实体当**虚拟实体**
    处理（没有块引用上下文），ByBlock 会被解析成布局默认值（颜色 ACI 7 白/黑、线宽
    0.25mm），而不是标注实体自己的 / 图层的值——导致 CTB"使用对象颜色"对标注不生效、
    线宽不认图层设置。

    这里把几何块子实体的 ByBlock 颜色/线宽改为标注实体自身的值（ByLayer 或显式），
    使解析回到正确链（ByLayer → 图层色/图层线宽 / 显式值）。
    """
    for layout in doc.layouts:
        for entity in layout:
            if not isinstance(entity, Dimension):
                continue
            block = entity.get_geometry_block()
            if block is None:
                continue
            dim_color = entity.dxf.color  # 默认 BYLAYER(256)，或显式 ACI，或 BYBLOCK(0)
            dim_lineweight = entity.dxf.lineweight  # 默认 BYLAYER(-1)，或显式线宽
            for block_entity in block:
                if block_entity.dxf.get("color", None) == ezdxf.const.BYBLOCK:
                    block_entity.dxf.color = dim_color
                if block_entity.dxf.get("lineweight", None) == ezdxf.const.LINEWEIGHT_BYBLOCK:
                    block_entity.dxf.lineweight = dim_lineweight


def _clear_mleader_proxy_graphics(doc: ezdxf.document.Drawing) -> None:
    """清除 MULTILEADER 实体的代理图形（proxy graphic），强制走原生渲染。

    ODA File Converter 转出的 MULTILEADER 代理图形把字体硬编码为 Windows 字体名
    （如 ``simsun.ttc``，一个 TTC 集合）。ezdxf 渲染 MULTILEADER 时，其
    ``__virtual_entities__`` 无论全局代理图形策略如何，都会**优先**用代理图形
    （``virtual_entities(proxy_graphic=True)``），而代理图形里解析出的 TEXT 样式名是
    ``simsun.ttc``——该 .ttc 不被字体管理器索引、无法命中 → 回退默认字体 → 中文变方框。

    清除代理图形后，``virtual_entities`` 回落到原生 RenderEngine，文本改用真实的
    文字样式（已在 :func:`_remap_text_style_fonts` 中映射到内置中文字体），中文正常。
    """
    for layout in doc.layouts:
        for entity in layout:
            if entity.dxftype() == "MULTILEADER":
                entity.proxy_graphic = None
    for block in doc.blocks:
        for entity in block:
            if entity.dxftype() == "MULTILEADER":
                entity.proxy_graphic = None


def _remap_mleader_properties(doc: ezdxf.document.Drawing) -> None:
    """把 MULTILEADER 引线/箭头/块内容的 ByBlock 颜色/线宽改回引线实体自身值。

    ODA 转出的 MULTILEADER 的 CONTEXT_DATA 里，引线（leader line）与箭头块的颜色为
    ByBlock(0)，引线的线宽（``leader_lineweight``）也是 ByBlock(-2)。ezdxf 渲染时这些
    虚拟实体被解析成布局默认值（颜色 ACI 7、线宽 0.25mm），而非引线实体的颜色 / 图层
    颜色 / 图层线宽 → 引线颜色与线宽都不随图层 / CTB 变化。这里把 ByBlock 改成引线实体
    的 ``dxf.color`` 与 ``dxf.lineweight``（默认 ByLayer）。
    """
    for layout in doc.layouts:
        for entity in layout:
            if not isinstance(entity, MultiLeader):
                continue
            target_color = ezdxf.colors.encode_raw_color(entity.dxf.color)  # 默认 BYLAYER(256)
            for leader in entity.context.leaders:
                for line in leader.lines:
                    if line.color == ezdxf.colors.BY_BLOCK_RAW_VALUE:
                        line.color = target_color
            block = entity.context.block
            if block is not None and block.color == ezdxf.colors.BY_BLOCK_RAW_VALUE:
                block.color = target_color
            if entity.dxf.get("leader_lineweight", None) == ezdxf.const.LINEWEIGHT_BYBLOCK:
                entity.dxf.leader_lineweight = entity.dxf.lineweight  # 默认 BYLAYER(-1)


def _iter_mleaders(doc: ezdxf.document.Drawing) -> Iterator[MultiLeader]:
    """遍历文档内所有 MULTILEADER 实体，覆盖模型/图纸空间与块定义。

    与 :func:`_iter_text_entities` 同理：MULTILEADER 也可能存在于块定义（如标题栏、
    图框），只遍历 ``doc.layouts`` 会漏掉块内引线文字。
    """
    for block in doc.blocks:
        for entity in block:
            if isinstance(entity, MultiLeader):
                yield entity


def _remap_mleader_text(doc: ezdxf.document.Drawing) -> None:
    """重映射 MULTILEADER 内容 MTEXT 的字体/控制码，与普通文字走同一重映射链。

    ezdxf 渲染 MULTILEADER 时，其内容来自 ``context.mtext.default_content``（组码 304）
    并经由 ``make_mtext`` 转成普通 MTEXT 交给文字渲染器。ODA 转出的引线内容常带内联字体
    （``\\fFangSong`` / ``\\fISOCPEUR``）与 AutoCAD 控制码（``%%c`` 直径等），而现有的
    文字级重映射函数（``_iter_text_entities``）只处理 TEXT/MTEXT/ATTRIB/ATTDEF，**不覆盖
    引线内容**——导致引线文字里的中文（内联 FangSong 命中失败 → 回退默认字体）变方框、
    直径符号 ``%%c`` 不展开（原样渲染成 "%c"）。

    这里把引线内容也解码 \\M+、展开 %%c/%%d/%%p、替换缺字形、剥离内联字体；并对含中文
    但样式非中文字体的引线内容，把其样式句柄（``style_handle``）重指到内置中文字体样式，
    与 :func:`_remap_cjk_text_styles` 对普通文字的处理对齐。
    """
    cjk_style = doc.styles.get(_ensure_cjk_style(doc))
    cjk_handle = cjk_style.dxf.handle if cjk_style is not None else "0"
    for entity in _iter_mleaders(doc):
        mtext_data = entity.context.mtext
        if mtext_data is None:
            continue
        raw = mtext_data.default_content
        if not raw:
            continue
        new = _decode_multibyte_escapes(raw)
        new = _expand_autocad_control_codes_in_text(new)
        for src, dst in _GLYPH_FALLBACKS.items():
            if src in new:
                new = new.replace(src, dst)
        new = _INLINE_FONT_RE.sub("", new)
        if new != raw:
            mtext_data.default_content = new
        if _contains_cjk(new):
            style = doc.entitydb.get(mtext_data.style_handle)
            font = style.dxf.get("font", "") if style is not None else ""
            if font != _OPEN_CJK_FONT and cjk_handle:
                mtext_data.style_handle = cjk_handle


def _convert_tolerance_cell(raw: str) -> str:
    """把 TOLERANCE 单个单元格的原始文本转换为可渲染文本。

    展开 GDT 符号（``\\Fgdt;X`` → Unicode 形位公差符号）、剥离内联字体与其它 MTEXT
    格式码、去掉包裹的花括号，得到纯文本内容。
    """
    text = _GDT_FONT_RE.sub(lambda m: _GDT_SYMBOLS.get(m.group(1).lower(), m.group(0)), raw)
    text = _INLINE_FONT_RE.sub("", text)
    text = re.sub(r"\\[A-Za-z][^;]*;", "", text)
    return text.replace("{", "").replace("}", "")


def _parse_tolerance_content(content: str) -> list[str]:
    """把 TOLERANCE 内容按 ``%%v`` 分隔成单元格文本，丢弃空单元格。

    AutoCAD 的 TOLERANCE 编辑对话框会在内容里追加多余的 ``%%v``（仅用于对话框回填，
    不参与实际渲染，见 ObjectARX AcDbFcf::setText 说明），这里直接丢弃空单元格，得到
    实际的形位公差框格序列（首格符号、次格公差值、后续各格基准字母）。
    """
    return [_convert_tolerance_cell(cell) for cell in content.split("%%v") if cell.strip()]


def _remap_tolerance_to_graphics(doc: ezdxf.document.Drawing) -> None:
    """把 TOLERANCE（形位公差）实体转换为 ezdxf 可渲染的图形。

    ezdxf 1.1.3 的 drawing 前端**不支持 TOLERANCE**：它既不在派发表里，也不实现
    ``__virtual_entities__`` 协议，最终走 ``skip_entity`` 被整体丢弃 → 图纸上的同心度/
    形位公差框（含基准框）全部缺失。

    这里把每个 TOLERANCE 解析成框格序列（符号/公差值/基准），量取各格文字宽度后画成
    矩形框 + 分隔竖线 + 居中文字，放进一个临时块，再用带旋转角的 INSERT 替换原实体。
    这样既支持水平也支持垂直（``x_axis_vector`` 决定方向）的形位公差框，且 PNG/SVG
    后端通用。
    """
    tolerances: list[tuple[BlockLayout, DXFGraphic]] = []
    for block in doc.blocks:
        for entity in block:
            if entity.dxftype() == "TOLERANCE":
                tolerances.append((block, entity))

    if not tolerances:
        return

    # 文字宽度用内置中文字体（含拉丁与 GDT 符号字形）量取，保证框格宽度与渲染一致。
    font_face = ezdxf_fonts.font_manager.get_font_face(_OPEN_CJK_FONT)
    renderer = UnifiedTextRenderer()

    for block, entity in tolerances:
        cells = _parse_tolerance_content(entity.dxf.get("content", ""))
        if not cells:
            block.delete_entity(entity)
            continue
        text_height = _tolerance_text_height(doc, entity)
        gap = text_height * 0.5  # 每格左右留白
        box_height = text_height * 2.0  # 形位公差框高约为字高 2 倍

        cell_widths: list[float] = []
        for cell in cells:
            width = 0.0 if not cell.strip() else renderer.get_text_line_width(cell, font_face, text_height)
            cell_widths.append(width + gap * 2.0)

        block_name = _build_tolerance_block(doc, entity, cells, cell_widths, box_height, text_height, gap)
        direction = entity.dxf.get("x_axis_vector", (1.0, 0.0, 0.0))
        angle = math.degrees(math.atan2(direction[1], direction[0]))
        insert = Vec3(entity.dxf.insert)
        attribs: dict[str, object] = {
            "rotation": angle,
            "layer": entity.dxf.layer,
        }
        block.add_blockref(block_name, insert, dxfattribs=attribs)
        block.delete_entity(entity)


def _tolerance_text_height(doc: ezdxf.document.Drawing, entity: DXFGraphic) -> float:
    """读取 TOLERANCE 所用标注样式的文字高度（DIMTXT），默认 2.5。"""
    dimstyle_name = entity.dxf.get("dimstyle", "")
    dimstyle = doc.dimstyles.get(dimstyle_name) if dimstyle_name else None
    if dimstyle is not None:
        value = dimstyle.dxf.get("dimtxt", 2.5)
        if value and value > 0:
            return float(value)
    return 2.5


def _build_tolerance_block(
    doc: ezdxf.document.Drawing,
    entity: DXFGraphic,
    cells: list[str],
    cell_widths: list[float],
    box_height: float,
    text_height: float,
    gap: float,
) -> str:
    """构建形位公差框块，返回块名。块内局部坐标：框左下角在 (0,0)，沿 +X 排布。

    块内实体：
        - 矩形框四条边（LINE）
        - 单元格之间的分隔竖线（LINE）
        - 每格居中文字（TEXT，使用内置中文字体样式）
    最终由调用方以 ``x_axis_vector`` 的角度旋转 INSERT，实现水平/垂直框。
    """
    name = f"_cad2image_tol_{entity.dxf.handle}"
    if name in doc.blocks:
        return name
    block = doc.blocks.new(name)
    cjk_style = _ensure_cjk_style(doc)
    total_width = sum(cell_widths)

    # 框线：下、上、左、右
    block.add_line((0, 0), (total_width, 0))
    block.add_line((0, box_height), (total_width, box_height))
    block.add_line((0, 0), (0, box_height))
    block.add_line((total_width, 0), (total_width, box_height))

    # 分隔竖线
    x = 0.0
    for i, width in enumerate(cell_widths):
        x += width
        if i < len(cell_widths) - 1:
            block.add_line((x, 0), (x, box_height))

    # 每格居中文字
    x = 0.0
    for cell, width in zip(cells, cell_widths):
        text = block.add_text(cell, dxfattribs={"style": cjk_style, "height": text_height})
        text.set_placement((x + width / 2.0, box_height / 2.0), align=TextEntityAlignment.MIDDLE_CENTER)
        x += width

    return name


def _decode_multibyte_escapes(text: str) -> str:
    """把 ODA 的多字节转义 ``\\M+nXXXX`` 解码为 Unicode 汉字。

    ODA File Converter 在 Linux 上处理 CAD2000 老版本 DWG 时，中文文本会以
    ``\\M+5XXXX`` 形式输出 GBK 字节（``5`` 为字节数前缀，``XXXX`` 是 GBK 双字节
    的十六进制）。ezdxf 的 MTEXT 解析器不识别 ``M`` 转义，会原样渲染成乱码。
    这里在渲染前把转义解码为真正的汉字。

    Args:
        text: 含 ``\\M+XXXX`` 转义的原始文本。

    Returns:
        解码后的文本；无法解码的转义原样保留。
    """

    def replace(match: re.Match[str]) -> str:
        hexstr = match.group(1)
        if len(hexstr) == 5 and hexstr[0] in "0123456789":
            hexstr = hexstr[1:]  # 去掉 ODA 的字节数前缀
        try:
            return bytes.fromhex(hexstr).decode("gbk")
        except (ValueError, UnicodeDecodeError):
            return match.group(0)

    return _M_PLUS_ESCAPE_RE.sub(replace, text)


def _decode_multibyte_text(doc: ezdxf.document.Drawing) -> None:
    """解码文档内所有文字实体的多字节转义（见 :func:`_decode_multibyte_escapes`）。"""
    for entity in _iter_text_entities(doc):
        raw = entity.dxf.get("text", "")
        if not raw:
            continue
        decoded = _decode_multibyte_escapes(raw)
        if decoded != raw:
            entity.dxf.text = decoded


def _remap_missing_glyphs(doc: ezdxf.document.Drawing) -> None:
    """把内置字体缺少字形的符号替换为视觉等价的有字形符号。

    思源宋体缺少直径符号 U+2300（渲染成 .notdef 方框），用有字形的 U+00D8 替代，
    保证直径符号可见。映射表见模块级 ``_GLYPH_FALLBACKS``。
    """
    for entity in _iter_text_entities(doc):
        raw = entity.dxf.get("text", "")
        if not raw:
            continue
        new = raw
        for src, dst in _GLYPH_FALLBACKS.items():
            if src in new:
                new = new.replace(src, dst)
        if new != raw:
            entity.dxf.text = new


def _expand_autocad_control_codes_in_text(text: str) -> str:
    """展开单段文本里的 AutoCAD 控制码 ``%%c`` / ``%%d`` / ``%%p`` / ``%%%``。

    ``%%c`` → Ø（U+00D8）、``%%d`` → °（U+00B0）、``%%p`` → ±（U+00B1）、``%%%`` → ``%``。
    ezdxf 不解析这些控制码、会原样渲染成 "%c" 之类，故渲染前统一展开为内置字体实际
    包含的字形（宋体缺 U+2300，用有字形的 U+00D8）。
    """

    def repl(match: re.Match[str]) -> str:
        code = match.group(0)[2:]
        if code == "%":
            return "%"
        return _AUTOCAD_CONTROL_MAP[code.lower()]

    return _AUTOCAD_CONTROL_RE.sub(repl, text)


def _expand_autocad_control_codes(doc: ezdxf.document.Drawing) -> None:
    """展开文档内所有文字实体的 AutoCAD 控制码（见 :func:`_expand_autocad_control_codes_in_text`）。"""
    for entity in _iter_text_entities(doc):
        raw = entity.dxf.get("text", "")
        if not raw or "%%" not in raw:
            continue
        new = _expand_autocad_control_codes_in_text(raw)
        if new != raw:
            entity.dxf.text = new


def _validate_ctb(ctb: str) -> str:
    """校验 CTB 打印样式表路径，缺文件时 fail-fast。"""
    if not ctb:
        return ""
    path = Path(ctb)
    if not path.is_file():
        raise FileNotFoundError(f"CTB 打印样式表不存在：{path}")
    return str(path)


def _build_render_settings(options: RenderOptions) -> layout_module.Settings:
    """构建传给 ``get_pixmap_bytes``/``get_string`` 的布局设置。

    相对线宽策略的 ``[min_stroke_width, max_stroke_width]`` 区间在此可配，
    用于控制线宽随页面缩放的粗细范围。
    """
    return layout_module.Settings(
        max_stroke_width=options.relative_max_stroke_width,
        min_stroke_width=options.relative_min_stroke_width,
    )


def _normalize_svg_encoding(svg_string: str) -> str:
    """把 SVG XML 声明中的编码归一化为 utf-8。

    ezdxf 在中文 Windows 上可能产出 ``encoding='cp936'`` 的声明，与后续以
    utf-8 写盘不一致，这里统一改写为 utf-8。
    """
    return _XML_ENCODING_RE.sub(r"\1'utf-8'", svg_string, count=1)


def _select_layout(doc: ezdxf.document.Drawing, layout_name: str | None) -> Layout:
    """选择要渲染的布局：``None`` 表示模型空间，否则按名称查找。"""
    if layout_name is None:
        return doc.modelspace()
    try:
        return doc.layout(layout_name)
    except KeyError as exc:
        available = [layout.name for layout in doc.layouts]
        raise ValueError(f"布局 '{layout_name}' 不存在，可用布局：{available}") from exc


def _determine_page(dxf_layout: Layout, options: RenderOptions) -> layout_module.Page:
    """确定渲染页面尺寸。

    优先级：显式 ``width_mm/height_mm`` → 图纸空间页面设置 → 内容包围盒自适应。
    """
    margins = layout_module.Margins.all(0)
    if options.width_mm is not None and options.height_mm is not None:
        if options.width_mm <= 0 or options.height_mm <= 0:
            raise ValueError(f"页面宽高必须为正数，得到 {options.width_mm} x {options.height_mm}")
        return layout_module.Page(options.width_mm, options.height_mm, layout_module.Units.mm, margins=margins)
    if options.width_mm is not None or options.height_mm is not None:
        raise ValueError("页面宽高（--width / --height）必须同时指定")

    page_from_layout = _page_from_paperspace(dxf_layout)
    if page_from_layout is not None and not options.fit_to_extents:
        return page_from_layout

    return _page_from_extents(dxf_layout, options.margin)


def _page_from_paperspace(dxf_layout: Layout) -> layout_module.Page | None:
    """若为图纸空间且定义了页面尺寸，返回对应 Page，否则返回 ``None``。"""
    if dxf_layout.is_modelspace:
        return None
    dxf_layout_obj = dxf_layout.dxf_layout  # 取底层 DXFLayout
    width = float(dxf_layout_obj.dxf.get("paper_width", 0.0) or 0.0)
    height = float(dxf_layout_obj.dxf.get("paper_height", 0.0) or 0.0)
    if width <= 0 or height <= 0:
        return None
    # ezdxf 1.1.x 的 stub 将参数声明为 Layout，运行时实际接受 DXFLayout。
    return layout_module.Page.from_dxf_layout(dxf_layout_obj)  # type: ignore[arg-type]


def _page_from_extents(dxf_layout: Layout, margin: float) -> layout_module.Page:
    """按布局内容包围盒确定页面，四周留百分比余量。

    ``margin`` 为内容较小边长的百分比（0–100）。页面尺寸置 0，交由 ``get_pixmap_bytes``
    按实际渲染内容（player 的 bbox）自动推导，余量通过 ``Margins`` 表达——这样四周余量
    均匀，且不受 ``bbox.extents`` 与真实渲染内容之间的偏差影响（``bbox.extents`` 会把
    某些实体（如文字）估算得过宽，导致显式页面宽度失真、上下贴边）。
    """
    extents = bbox.extents(dxf_layout, fast=True)
    if not extents.has_data:
        raise RuntimeError("布局内容为空或包围盒无效，无法确定渲染范围")
    width = float(extents.size.x)
    height = float(extents.size.y)
    if width <= 0 or height <= 0:
        raise RuntimeError("布局内容为空或包围盒无效，无法确定渲染范围")
    margin_size = min(width, height) * margin / 100.0
    return layout_module.Page(
        0.0, 0.0, layout_module.Units.mm, margins=layout_module.Margins.all(margin_size)
    )
