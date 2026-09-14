# 开发大纲：ODA File Converter + ezdxf 替换 Acme CAD Converter

> 目标：用「ODA File Converter（同内核、免费、仍维护）+ ezdxf（纯 Python）」重建
> DWG/DXF → SVG/PNG 管线，消除 Acme 的"残余杂线"bug，并作为 Python CLI 嵌入现有系统。

---

## 一、背景与目标

**问题根因（已定位）**：Acme CAD Converter 底层是 ODA Teigha 内核。DWG 被解析成"边矢量模型"后，
分别喂给三套后端——SVG/PDF（真圆弧，干净）与 GDI 图元栅格化（曲线被离散成折线段，出毛须）。
"快速模式"（`CQuickGraphy`）用更粗的离散所以没毛须，但损失圆度。故 bug 在 GDI 栅格化路径，
矢量输出是干净的。

**目标**：
1. 输出**无杂线**的高清 PNG 与 SVG；
2. 忠实复现原 CLI 的渲染参数（页面/DPI/线宽/打印样式/布局/字体）；
3. 可作为 Python CLI 直接嵌入现有系统。

**非目标**：像素级 100% 一致；3D 消隐（若图纸为 2D 工程图则不需要）。

---

## 二、架构（数据流）

```
DWG ──(ODA File Converter, DWG→DXF)──▶ DXF ──(ezdxf.addons.drawing)──▶ PNG / SVG
                                       ▲                                ▲
                                    稳定、可读文本                  PyMuPDF 后端（无 GDI bug）
```

- ODA File Converter 只负责"啃"DWG 二进制 → 标准 ASCII DXF；
- 其余全部在 Python 内完成（读取、渲染、样式、批处理）。

---

## 三、技术选型

| 组件 | 选择 | 说明 |
|---|---|---|
| DWG→DXF | ODA File Converter（免费 CLI） | 与 Acme 同内核（Teigha/Drawings SDK），结果可靠 |
| DXF 读取 | ezdxf（纯 Python） | 成熟、活跃维护 |
| 栅格化 | `ezdxf.addons.drawing` + `PyMuPdfBackend` | 圆弧走真圆弧，无 GDI 毛须 |
| SVG 输出 | ezdxf SVG 后端 / ODA FC 直接 SVG（备选，需实测） | 对比后定 |
| CLI | typer（子命令+类型校验）或 argparse | 兼容 Acme 参数语义 |
| 测试 | pytest | happy path + 错误路径 + 回归对照 |

---

## 四、项目结构（src 布局）

```
cad2image/
├── pyproject.toml
├── README.md
├── src/cad2image/
│   ├── __init__.py          # __all__ 声明公开接口
│   ├── cli.py               # CLI 入口（兼容 Acme 参数）
│   ├── dwg2dxf.py           # ODA File Converter 封装
│   ├── render.py            # ezdxf → PNG/SVG 核心
│   ├── config.py            # 参数 → ezdxf Configuration 映射
│   ├── plotstyle.py         # CTB/打印样式加载
│   └── batch.py             # 批量 + 报告
└── tests/
    ├── conftest.py
    ├── test_dwg2dxf.py
    ├── test_render.py
    └── test_cli.py
```

---

## 五、分阶段实施

### Phase 0 — 环境与工具验证

- [ ] 安装 ODA File Converter（https://www.opendesign.com/guestfiles/oda_file_converter）
- [ ] 确认 CLI：
  ```
  ODAFileConverter.exe <输入目录> <输出目录> <输入版本> <输出版本> <递归> <审计>
  # 例：DWG→DXF
  ODAFileConverter.exe "C:\in" "C:\out" ACAD2018 DXF 0 1
  ```
- [ ] 实测：对 `CAD Test/轴套.dwg` 跑一遍，确认 DXF 能被 ezdxf 读取
- [ ] 实测 ODA FC 是否支持直接 SVG 输出（23.x+），决定是否可简化为"ODA 直接出 SVG"
- [ ] 安装 Python 依赖：`pip install ezdxf pymupdf typer pytest`（matplotlib/numpy/Pillow 已装）

**验证标准**：一个 DWG 走通 `DWG→DXF→ezdxf→PNG`，圆弧平滑、无毛须。

---

### Phase 1 — 最小可行管线（happy path）

- [ ] `dwg2dxf.py`：封装 ODA FC。注意 ODA FC 是**目录级**转换，需处理
      "单文件 → 临时目录 → 收集输出 → 清理" 的编排（用 `tempfile` + `with` 管理资源）
- [ ] `render.py`：
  ```python
  import ezdxf
  from ezdxf.addons.drawing import Frontend, RenderContext, pymupdf, layout, config

  doc = ezdxf.readfile("out.dxf")
  ctx = RenderContext(doc)
  backend = pymupdf.PyMuPdfBackend()
  cfg = config.Configuration(background_policy=config.BackgroundPolicy.WHITE)
  frontend = Frontend(ctx, backend, config=cfg)
  frontend.draw_layout(doc.modelspace())
  page = layout.Page(w_mm, h_mm, layout.Units.mm, margins=layout.Margins.all(0))
  png = backend.get_pixmap_bytes(page, fmt="png", dpi=1200)
  ```
- [ ] `cli.py` 最小版：`python -m cad2image "轴套.dwg" -o 轴套.png --dpi 1200`

**验证标准**：`轴套.dwg`、`眼镜框粗实线.dwg` 输出 PNG 与 Acme 视觉一致，**圆弧处无杂线**。

---

### Phase 2 — 渲染保真度对齐

将 Acme CLI 参数逐一映射到 ezdxf `config.Configuration`：

| Acme 参数 | ezdxf 映射 |
|---|---|
| `/res N` | `get_pixmap_bytes(dpi=N)` |
| `/w /h`（mm） | `layout.Page(w, h, layout.Units.mm)` |
| `/e` `/ad`（缩放扩展） | Page 自动 fit / bbox 缩放 |
| `/b` 背景色 | `BackgroundPolicy` + background color |
| `/lw 0/1/2` | `lineweight_policy` + `lineweight_scaling` |
| `/p 1/2/3`（1bit/灰度/256色） | `color_policy`（MONOCHROME/GRAYSCALE/truecolor）；1bit 用阈值后处理 |
| `/pw myset` | CTB 加载（见 plotstyle.py，**需实测 ezdxf CTB API**） |
| `/a 0/-1/-2`（布局选择） | `frontend.draw_layout(具体 layout)` |
| SHX 字体 | ezdxf 内置 SHX 引擎 + `fonts/*.shx`（**中文大字体 hzdx.shx 是重点风险**） |

- [ ] 实现 `config.py`（参数映射）、`plotstyle.py`（CTB/打印样式）

**验证标准**：与 Acme 现有产物（`轴套.png`、`眼镜框粗实线.png`）逐项对照线宽/颜色/背景。

---

### Phase 3 — 批量 + CLI 兼容层

- [ ] `batch.py`：多文件、递归、转换报告（对齐 Acme `/l` 报告：0成功/1内存不足/2绘图失败/3打开失败/4无效文件）
- [ ] `cli.py` 完整参数对齐 + 退出码/错误输出规范化
- [ ] 批处理**部分失败不中断整批**（返回 successes/failures 结果对象）

**验证标准**：`CAD Test/` 下全部 DWG 批量跑通。

---

### Phase 4 — 回归验证 + 交付

- [ ] **关键验收**：拿到会出杂线的坏图纸 → 确认新管线输出**无杂线**
- [ ] 与 Acme 输出 side-by-side 回归（轴套/眼镜框/三角板/支撑架）
- [ ] README：安装、用法、参数映射表、已知限制
- [ ] 质量门禁：`ruff check .` + mypy strict + pytest 全绿

---

## 六、关键风险与决策点

| 风险 | 影响 | 应对 |
|---|---|---|
| ODA FC 直接出 SVG 质量不稳 | 路线 B 简化失败 | 固定走 DWG→DXF→ezdxf |
| SHX 中文大字体（hzdx.shx）渲染 | 中文标注丢失/乱码 | 优先测中文图纸；必要时 TTF 字体替换（ezdxf 支持） |
| 3D 消隐 `/hide` | 3D 图纸消隐缺失 | 先确认图纸是否纯 2D（工程图通常 2D） |
| `.cps` 自定义笔宽集无法直接读 | `/pw myset` 复现 | 反解 `myset.cps` 语义 → 映射为 ezdxf 线宽规则 |
| Xref 外部引用 | 转换缺内容 | 转换前确保 xref 同目录（与 Acme 行为一致） |

---

## 七、验收清单（最终）

- [ ] 坏图纸 → PNG **无残余杂线**
- [ ] 圆弧平滑（无多边形折痕）
- [ ] 线宽/颜色/背景/布局/字体与 Acme 视觉一致
- [ ] Windows 无头环境可运行
- [ ] ruff + mypy + pytest 通过
