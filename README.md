# cad2image · 让 CAD 图纸渲染告别残余杂线

用「ODA File Converter + ezdxf」重建 DWG/DXF → PNG/SVG 渲染管线，替换 Acme CAD Converter。

消除 Acme 底层 GDI 栅格化路径产生的「残余杂线」问题——圆弧走真圆弧，无多边形折痕与毛须。

## 数据流

```
DWG ──(ODA File Converter, DWG→DXF)──▶ DXF ──(ezdxf.addons.drawing)──▶ PNG / SVG
                                       ▲                                ▲
                                    稳定、可读文本                  PyMuPDF 后端（无 GDI bug）
```

- ODA File Converter 负责解析 DWG 二进制 → 标准 ASCII DXF（与 Acme 同 Teigha 内核）。
- 其余读取、渲染、样式、批处理全部在 Python 内完成。

## 安装

要求 Python 3.8（ezdxf 1.1.x 为 3.8 可用的最新大版本，且是 PyMuPDF 渲染后端的引入版本）。

```bash
python -m pip install -e ".[dev]"
```

需要单独安装 ODA File Converter（免费，https://www.opendesign.com/guestfiles/oda_file_converter）。

ODA 可执行文件路径解析优先级：

1. 环境变量 `ODA_FILE_CONVERTER_PATH`
2. 默认路径 `D:\ODA\ODAFileConverter_title 21.5.0\ODAFileConverter.exe`

## 快速开始

```bash
# 单个 DWG → PNG
python -m cad2image 轴套.dwg -o 轴套.png --dpi 1200

# 单个 DWG → SVG
python -m cad2image 轴套.dwg -o 轴套.svg

# 直接渲染已转好的 DXF（跳过 ODA）
python -m cad2image 轴套.dxf -o 轴套.png

# 批量处理目录
python -m cad2image "CAD Test/" -o out/ --recursive
```

安装后也可用控制台命令 `cad2image`。

## CLI 参数

| 参数 | 说明 | 默认 |
|---|---|---|
| `--output, -o` | 输出文件/目录 | 与输入同目录 |
| `--dpi` | PNG 分辨率 | 300 |
| `--format, -f` | 输出格式 `png` / `svg` | png |
| `--background` | `default` / `white` / `black` / `off` | white |
| `--color` | `color` / `monochrome` / `grayscale` / `black` / `white` | color |
| `--lineweight` | `absolute` / `relative` | absolute |
| `--lineweight-scaling` | 线宽整体缩放系数（仅绝对线宽生效） | 1.0 |
| `--min-lineweight` | 最小打印线宽（mm） | 无 |
| `--relative-max-stroke-width` | 相对线宽：最粗线宽占页面较小边比例 | 0.001（0.1%） |
| `--relative-min-stroke-width` | 相对线宽：最细线宽占最粗线宽比例 | 0.05（5%） |
| `--ctb` | CTB 打印样式表路径 | 无 |
| `--font-dir` | 附加的 SHX/TTF 字体目录 | 无 |
| `--layout` | 布局名（缺省模型空间） | 模型空间 |
| `--width` / `--height` | 页面尺寸（mm） | 自适应 |
| `--fit` | 按内容包围盒自适应页面 | False |
| `--margin` | 内容自适应页面时的四周余量（%，相对内容较小边） | 3.0 |
| `--recursive` | 目录批量时递归子目录 | False |
| `--oda-path` | ODA 可执行文件路径 | 环境变量/默认路径 |

退出码：`0` 成功；`1` 转换或渲染失败（批量时存在任一失败即非零）。

## Python API 使用

除 CLI 外，也可作为 Python 库导入，提供三层能力：**渲染**（DXF→PNG/SVG）、**转换**（DWG→DXF）、**批处理**。

> 前置条件：仅 DWG→DXF 转换需要 ODA File Converter；纯 DXF 渲染不需要。

### 渲染 DXF → PNG/SVG

```python
from cad2image import RenderOptions, render_dxf

# 按输出扩展名自动选 PNG/SVG 后端
render_dxf("轴套.dxf", "轴套.png", RenderOptions(dpi=300))
render_dxf("轴套.dxf", "轴套.svg", RenderOptions())
```

### DWG → 图片（端到端）

```python
from cad2image import RenderOptions, process_dwg

out = process_dwg("轴套.dwg", "out/", RenderOptions(dpi=600))  # 返回 Path
```

### 分步转换（先转 DXF 再渲染，便于缓存）

```python
from cad2image import convert_dwg_to_dxf, render_to_svg

dxf = convert_dwg_to_dxf("轴套.dwg", "dxf_cache/")  # 返回 Path
render_to_svg(dxf, "轴套.svg", RenderOptions())
```

### 批量处理目录

```python
from cad2image import RenderOptions, process_directory

result = process_directory("CAD Test/", "out/", RenderOptions(),
                           output_format="png", recursive=True)
print(f"成功 {result.success_count}，失败 {result.failure_count}")
for f in result.failures:
    print(f"[{f.stage}] {f.source.name}: {f.error}")  # stage: convert / render
```

### CTB 打印样式

```python
from cad2image import RenderOptions, load_ctb, get_lineweight, render_dxf

styles = load_ctb("黑白线型.ctb")
lw = get_lineweight(styles, aci=1)  # 颜色索引 1 的线宽(mm)，未覆盖时为 None

render_dxf("图.dxf", "图.png", RenderOptions(ctb="黑白线型.ctb"))
```

### RenderOptions 常用参数

```python
RenderOptions(
    dpi=300,                          # PNG 分辨率
    background="white",               # default/white/black/off
    color_policy="color",             # color/monochrome/grayscale/black/white
    lineweight_policy="absolute",     # absolute/relative
    lineweight_scaling=1.0,           # 线宽整体缩放（仅绝对线宽）
    min_lineweight=None,              # 最小打印线宽(mm)
    relative_max_stroke_width=0.001,  # 相对线宽最粗比例
    relative_min_stroke_width=0.05,   # 相对线宽最细比例
    ctb="",                           # CTB 样式表路径
    font_dir="",                      # 附加字体目录（SHX/TTF）
    layout_name=None,                 # 布局名，None=模型空间
    width_mm=None, height_mm=None,    # 显式页面尺寸(mm)
    fit_to_extents=False,             # 按内容包围盒自适应
    margin=3.0,                       # 自适应时四周余量(%)
)
```

错误统一抛 `FileNotFoundError` / `ValueError` / `RuntimeError`，可放心捕获；中文与 ASCII 标注随包内置开源字体（见下文「字体与中文」）。

## Acme 参数映射

| Acme 参数 | 本工具映射 |
|---|---|
| `/res N` | `--dpi N` |
| `/w` `/h`（mm） | `--width` / `--height` |
| `/e` `/ad`（缩放扩展） | `--fit`（包围盒自适应） |
| `/b` 背景色 | `--background` |
| `/lw 0/1/2` | `--lineweight` + `--lineweight-scaling` |
| `/p 1/2/3`（1bit/灰度/256色） | `--color`（灰度暂以 monochrome 近似，待 Phase 2） |
| `/pw myset` | `plotstyle.load_ctb`（尚未实现，见已知限制） |
| `/a 0/-1/-2`（布局选择） | `--layout` |
| `/l` 报告 | 批处理汇总 + 退出码 |

## 项目结构

```
src/cad2image/
├── cli.py         # typer CLI 入口
├── dwg2dxf.py     # ODA File Converter 封装（目录级转换编排）
├── render.py      # ezdxf → PNG/SVG 渲染核心
├── config.py      # 参数 → ezdxf Configuration 映射
├── plotstyle.py   # CTB 打印样式（占位）
└── batch.py       # 批量 + 部分失败结果对象
```

## 测试

```bash
pytest                     # 单元测试（无外部依赖）
pytest -m integration      # 集成测试（需真实 DWG + ODA）
```

集成测试通过环境变量提供真实图纸：

```bash
CAD_TEST_DWG=E:/path/to/轴套.dwg pytest tests/test_integration.py -m integration
```

## 打包发布

```bash
# 构建 wheel（本机需加 --no-build-isolation 规避构建隔离环境联网拉 setuptools 的 SSL 劫持）
python -m pip wheel . --no-deps --no-build-isolation -w dist

# 构建 sdist（可选）
python -c "import setuptools.build_meta as b; b.build_sdist('dist')"

# 安装到干净环境并验证中文渲染
python -m pip install dist/cad2image-0.1.0-py3-none-any.whl
python -m cad2image 轴套.dwg -o 轴套.png
```

产物 `dist/cad2image-0.1.0-py3-none-any.whl` 自带开源字体（Noto Sans SC / Noto Sans Mono）与 `py.typed`，无 `--no-build-isolation` 需要时可用标准 `python -m build` 生成 wheel + sdist。

## 质量门禁

```bash
ruff check .
ruff format .
mypy src/
pytest
```

## 字体与中文

渲染时**默认自动扫描**随包内置的 `fonts/` 目录，也可通过 `--font-dir` 追加其它目录。内置字体均为**开源字体（SIL OFL 1.1）**，随仓库与 wheel 一起分发，中文与 ASCII 渲染开箱即用：

| 字体文件 | 用途 | 许可 |
|---|---|---|
| `NotoSansSC-Regular.otf` | 中文（含拉丁字符） | SIL OFL 1.1 |
| `NotoSansMono-Regular.ttf` | ASCII 等宽，近似 CAD 单线字体 | SIL OFL 1.1 |

> 完整许可与署名见 `src/cad2image/fonts/OFL.txt` 与 `NOTICE`。

CAD 图纸的文字样式常引用专有字体（微软 `SimSun`/`NSimSun`、Autodesk `romans.shx`/`txt.shx` 等）。渲染时会自动把这些字体名**重写为内置开源字体**：

- `SimSun` / `NSimSun` / `宋体` / `新宋体` / 中文 bigfont（`hztxt` / `gbcbig` 等）→ `NotoSansSC-Regular.otf`
- 其余 SHX 字形字体（`romans.shx` / `txt.shx` / `simplex` 等）→ `NotoSansMono-Regular.ttf`

> 替换会改变文字外观（SimSun → Noto Sans SC、SHX 单线体 → Noto Sans Mono 等宽），但保证纯开源、无专有字体再分发风险。若需严格保留原字体观感，可自行将对应字体放入 `fonts/` 或通过 `--font-dir` 指定。

**重要限制**：ezdxf 1.1.x 的 `ShapeFile` 解析器**不支持 bigfont 中文大字体**（`HZDX.SHX`/`gbcbig.shx` 等，源码中明确抛出 `UnsupportedShapeFile("BIGFONT shapes are not supported yet")`），因此本项目不内置 SHX 大字体；引用这类字体的图纸会被自动重写为 `NotoSansSC-Regular.otf`，中文可正常渲染。

## 已知限制

- **灰度输出**（Acme `/p 2`）：暂以 monochrome 近似，真正的 256 级灰度需像素级后处理。
- **SHX 中文 bigfont**：ezdxf 1.1.x 不支持，中文走 TTF 字体（见上）。
- **3D 消隐**（Acme `/hide`）：未实现，假设输入为 2D 工程图。
- **Xref 外部引用**：转换前需确保 xref 文件与主图同目录（与 Acme 行为一致）。
- **页面单位**：模型空间包围盒自适应时假定绘图单位为 mm（`$INSUNITS` 未做换算）。

## License

[MIT](./LICENSE)
