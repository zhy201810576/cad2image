"""cad2image — DWG/DXF → PNG/SVG 渲染管线。

用 ODA File Converter（DWG→DXF）+ ezdxf（DXF→PNG/SVG）重建 Acme CAD Converter
的渲染能力，消除 GDI 栅格化路径产生的「残余杂线」问题。

公开接口：
    - convert_dwg_to_dxf / convert_directory：DWG → DXF（ODA File Converter 封装）
    - render_dxf / render_to_png / render_to_svg：DXF → PNG/SVG（ezdxf 渲染核心）
    - process_dwg / process_directory：单文件 / 目录批处理
    - RenderOptions：渲染参数
"""

from cad2image.batch import BatchResult, process_directory, process_dwg
from cad2image.config import RenderOptions, get_oda_converter_path
from cad2image.dwg2dxf import convert_directory, convert_dwg_to_dxf
from cad2image.plotstyle import get_lineweight, load_ctb
from cad2image.render import render_dxf, render_to_png, render_to_svg

__all__ = [
    "BatchResult",
    "RenderOptions",
    "convert_directory",
    "convert_dwg_to_dxf",
    "get_lineweight",
    "get_oda_converter_path",
    "load_ctb",
    "process_directory",
    "process_dwg",
    "render_dxf",
    "render_to_png",
    "render_to_svg",
]

__version__ = "0.1.5"
