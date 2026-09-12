#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OFD 转 PDF 转换器
=================

OFD（Open Fixed-layout Document，GB/T 33190-2016）是国家电子发票、电子证照
常用的版式文档格式，本质是一个 ZIP 包，内含 XML 描述的矢量页面内容。

本工具将 OFD 解析后用 PyMuPDF 重绘为 PDF：
  - 页面：OFD 单位为毫米，PDF 单位为点（1 mm = 2.83465 pt）
  - 文本：TextObject 定位 + TextCode 的 X/Y/DeltaX 逐字符控制字距
  - 矢量：PathObject 的 AbbreviatedData（M/L/C/B 路径命令）
  - 图片：MultiMedia 资源，按 Boundary/CTM 变换放置
  - 模板页：Document.xml 的 TemplatePage（ZOrder=Background/Foreground）
  - 注释/电子签章：外观数据未随文件嵌入（需验签环境）时无法还原，跳过并提示

用法：
  python ofd2pdf.py input.ofd [output.pdf]
  python ofd2pdf.py a.ofd b.ofd c.ofd          # 批量，输出到各文件同目录
  python ofd2pdf.py *.ofd -o 输出目录/          # 批量到指定目录
"""

import argparse
import io
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit("缺少依赖 PyMuPDF，请先执行: pip install pymupdf")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

OFD_NS = "http://www.ofdspec.org/2016"
MM2PT = 72.0 / 25.4  # OFD 坐标单位是毫米，PDF 是点

# ---------------------------------------------------------------------------
# 字体策略（重要：数字/字母重叠问题的根因与修复）
#
# OFD 的 TextCode 用 DeltaX 逐字符定位，数电发票中数字槽位固定为 0.5em。
# 若用 Helvetica/Droid 等数字宽度 0.55em 的字体，逐字累积即产生重叠。
# 中易宋体（simsun.ttc）的 ASCII 字符恰为 0.5em 等宽，与 DeltaX 完全吻合，
# 因此所有文本统一用宋体渲染，并嵌入子集（未嵌入时 Adobe 会本地替换导致错位）。
# ---------------------------------------------------------------------------


def find_cjk_font():
    """探测可用的中文等宽友好字体文件，返回 (别名, 文件路径或 None)

    依次扫描多个常见系统字体目录（Windows / macOS / Linux），
    优先选择 ASCII 字符为 0.5em 等宽的宋体类字体。
    """
    candidates = [
        # Windows
        ("SimSun", "C:/Windows/Fonts/simsun.ttc"),
        ("SimSun", "C:/Windows/Fonts/simsun.ttc.ttf"),
        ("MSYaHei", "C:/Windows/Fonts/msyh.ttc"),
        ("SimHei", "C:/Windows/Fonts/simhei.ttf"),
        # macOS
        ("SimSun", "/System/Library/Fonts/Supplemental/Songti.ttc"),
        ("MSYaHei", "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        ("SimHei", "/System/Library/Fonts/STHeiti Light.ttc"),
        # Linux（常见发行版的宋体/黑体包）
        ("SimSun", "/usr/share/fonts/truetype/arphic/uming.ttc"),
        ("SimHei", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        ("MSYaHei", "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
        ("SimHei", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ]
    for alias, path in candidates:
        if Path(path).exists():
            return alias, path
    return None, None


FONT_ALIAS, FONT_FILE = find_cjk_font()
# 找不到本机字体文件时退回 PyMuPDF 内置 CJK 字体（不嵌入，宽度略有偏差）
FONT_FALLBACK = "china-s"
if FONT_FILE is None:
    # 明确提示退化影响，而非静默产生可能重叠的输出
    print("警告: 未找到本机中文字体文件，将使用内置 CJK 字体渲染文本。"
          "数字宽度可能与 OFD 版式有偏差，请在 Adobe 中检查是否重叠。",
          file=sys.stderr)

# 路径命令：每个命令后跟的数值个数（B 与 C 均为三次贝塞尔）
PATH_CMD_ARITY = {"M": 2, "L": 2, "C": 6, "B": 6, "S": 4}


def qn(tag):
    """带命名空间的标签名"""
    return f"{{{OFD_NS}}}{tag}"


def parse_floats(s, count=None):
    """解析 '1.5 2 3' / 逗号分隔的数值串"""
    vals = [float(x) for x in re.split(r"[\s,]+", s.strip()) if x]
    if count is not None and len(vals) < count:
        raise ValueError(f"数值个数不足: {s!r}")
    return vals


def parse_color(value_str, alpha_str=None):
    """OFD 颜色 'R G B'（0-255）+ Alpha（0-255，默认不透明）-> (r,g,b) 0-1 浮点或 None"""
    if not value_str:
        return None
    r, g, b = parse_floats(value_str, 3)[:3]
    alpha = 255.0
    if alpha_str is not None:
        try:
            alpha = float(alpha_str)
        except ValueError:
            pass
    if alpha <= 0:
        return None  # 全透明按未设置处理
    return (r / 255.0, g / 255.0, b / 255.0)


def local(elem):
    """去掉命名空间的标签名"""
    return elem.tag.rsplit("}", 1)[-1]


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

class Style:
    """绘制参数（对应 OFD DrawParam），逐层合并"""
    __slots__ = ("fill", "stroke", "line_width")

    def __init__(self):
        self.fill: tuple = (0.0, 0.0, 0.0)    # 默认填充黑色
        self.stroke: tuple = (0.0, 0.0, 0.0)  # 默认描边黑色
        self.line_width: float = 0.35         # 默认线宽 0.35mm

    def copy(self):
        s = Style()
        s.fill, s.stroke, s.line_width = self.fill, self.stroke, self.line_width
        return s


class OFDDoc:
    """解析后的 OFD 文档"""

    def __init__(self):
        self.pages = []        # [(width_mm, height_mm, draws)]
        self.doc_info = {}     # DocInfo 元数据
        self.skipped = []      # 无法还原而跳过的内容说明


# ---------------------------------------------------------------------------
# 解析器
# ---------------------------------------------------------------------------

class OFDParser:
    def __init__(self, path, verbose=False):
        self.verbose = verbose
        self.zf = zipfile.ZipFile(path)
        # 兼容 zip 内文件名被 URL 编码的情况
        self.names = {}
        for info in self.zf.infolist():
            self.names[info.filename] = info.filename
            self.names[unquote(info.filename)] = info.filename

        self.draw_params = {}   # DrawParam ID -> Style
        self.fonts = {}         # Font ID -> 字体声明名
        self.medias = {}        # MultiMedia ID -> (zip 内路径, 格式)
        self.templates = {}     # TemplatePage ID -> (BaseLoc, ZOrder)
        self.doc = OFDDoc()

    def log(self, msg):
        if self.verbose:
            print(f"  [ofd] {msg}")

    # ---- zip 读取与路径解析 ----

    def read(self, loc, base=""):
        """按 OFD 规则解析文件路径：绝对路径以 / 开头，相对路径基于 base 目录"""
        if not loc:
            return None
        loc = unquote(loc.strip())
        if loc.startswith("/"):
            full = loc.lstrip("/")
        else:
            base = base.strip("/")
            full = f"{base}/{loc}" if base else loc
        full = full.replace("\\", "/")
        real = self.names.get(full)
        if real is None:
            self.log(f"警告: 包内文件不存在 {full}")
            return None
        return self.zf.read(real)

    @staticmethod
    def dirname(loc):
        return loc.rsplit("/", 1)[0] if "/" in loc else ""

    @staticmethod
    def join(base, rel):
        if rel.startswith("/"):
            return rel.lstrip("/")
        base = base.strip("/")
        return f"{base}/{rel}" if base else rel

    @staticmethod
    def parse_xml(data):
        """容错解析 XML：优先按声明编码，失败则退回 GBK/UTF-8"""
        if data is None:
            return None
        try:
            return ET.fromstring(data)
        except ET.ParseError:
            for enc in ("gbk", "utf-8", "gb18030"):
                try:
                    return ET.fromstring(data.decode(enc).encode("utf-8"))
                except (UnicodeDecodeError, ET.ParseError):
                    continue
        return None

    # ---- 主流程 ----

    def parse(self):
        root = self.parse_xml(self.read("OFD.xml"))
        if root is None:
            raise ValueError("无法解析 OFD.xml，文件可能不是有效的 OFD 包")
        doc_root_loc = None
        for body in root.iter(qn("DocBody")):
            info = body.find(qn("DocInfo"))
            if info is not None:
                for child in info:
                    self.doc.doc_info[local(child)] = (child.text or "").strip()
            loc = body.find(qn("DocRoot"))
            if loc is not None:
                doc_root_loc = (loc.text or "").strip()
        if not doc_root_loc:
            raise ValueError("OFD.xml 中未找到 DocRoot")

        self.parse_document(doc_root_loc)
        return self.doc

    def parse_document(self, doc_loc):
        base = self.dirname(doc_loc)
        root = self.parse_xml(self.read(doc_loc))
        if root is None:
            raise ValueError(f"无法解析 {doc_loc}")

        common = root.find(qn("CommonData"))
        default_box = None
        if common is not None:
            area = common.find(qn("PageArea"))
            if area is not None:
                pb = area.find(qn("PhysicalBox"))
                if pb is not None and pb.text:
                    default_box = parse_floats(pb.text, 4)

            # 资源文件
            for tag, key in (("PublicRes", "public"), ("DocumentRes", "document")):
                for res in common.findall(qn(tag)):
                    res_loc = self.join(base, (res.text or "").strip())
                    self.parse_resource(res_loc, base)

            # 模板页
            for tpl in common.findall(qn("TemplatePage")):
                tid = tpl.get("ID")
                self.templates[tid] = (
                    self.join(base, tpl.get("BaseLoc", "")),
                    tpl.get("ZOrder", "Background"),
                )

        # 逐页解析
        pages = root.find(qn("Pages"))
        if pages is None:
            return
        for page in pages.findall(qn("Page")):
            page_loc = self.join(base, page.get("BaseLoc", ""))
            self.parse_page(page_loc, page.get("ID"), default_box)

    def parse_resource(self, res_loc, doc_base):
        root = self.parse_xml(self.read(res_loc))
        if root is None:
            return
        # 资源内 MediaFile 的 BaseLoc 相对于资源文件所在目录
        base_loc = root.get("BaseLoc") or ""
        res_base = self.join(doc_base, base_loc or self.dirname(res_loc))
        if base_loc and not base_loc.startswith("/"):
            res_base = self.join(self.dirname(res_loc), base_loc)

        # DrawParam：支持 Relative 指向另一个 DrawParam 继承
        for dp in root.iter(qn("DrawParam")):
            style = Style()
            rel = dp.get("Relative")
            if rel and rel in self.draw_params:
                parent = self.draw_params[rel]
                style.fill, style.stroke, style.line_width = (
                    parent.fill, parent.stroke, parent.line_width)
            if dp.get("LineWidth"):
                try:
                    style.line_width = float(dp.get("LineWidth"))
                except ValueError:
                    pass
            for child in dp:
                if local(child) == "FillColor":
                    c = parse_color(child.get("Value"), child.get("Alpha"))
                    if c:
                        style.fill = c
                elif local(child) == "StrokeColor":
                    c = parse_color(child.get("Value"), child.get("Alpha"))
                    if c:
                        style.stroke = c
            self.draw_params[dp.get("ID")] = style

        for f in root.iter(qn("Font")):
            self.fonts[f.get("ID")] = f.get("FontName", "")

        for mm in root.iter(qn("MultiMedia")):
            mf = mm.find(qn("MediaFile"))
            if mf is not None and mf.text:
                file_loc = self.join(res_base, mf.text.strip())
                self.medias[mm.get("ID")] = (file_loc, mm.get("Format", ""))

    # ---- 页面解析：生成绘制指令序列 ----

    def parse_page(self, page_loc, page_id, default_box):
        root = self.parse_xml(self.read(page_loc))
        if root is None:
            self.log(f"警告: 无法解析页面 {page_loc}")
            return
        base = self.dirname(page_loc)

        w, h = None, None
        area = root.find(qn("Area"))
        if area is not None:
            pb = area.find(qn("PhysicalBox"))
            if pb is not None and pb.text:
                box = parse_floats(pb.text, 4)
                w, h = box[2], box[3]
        if w is None and default_box:
            w, h = default_box[2], default_box[3]
        if w is None:
            w, h = 210.0, 297.0  # 兜底 A4

        draws = []

        # 页面引用的模板（Background 先画，Foreground 后画）
        page_base = self.dirname(page_loc)
        for tpl in root.findall(qn("Template")):
            tid = tpl.get("TemplateID")
            if tid in self.templates:
                tpl_loc, _ = self.templates[tid]
                self.collect_content(tpl_loc, self.dirname(tpl_loc), draws,
                                     tag=f"tpl:{tid}")
            else:
                self.log(f"警告: 页面引用了未知模板 {tid}")

        # 页面自身内容
        self.collect_content(page_loc, page_base, draws, tag="page")

        self.doc.pages.append((w, h, draws))

    def collect_content(self, loc, base, draws, tag=""):
        """解析 Page/Tpl Content.xml，把 Layer 中的对象压入 draws"""
        root = self.parse_xml(self.read(loc))
        if root is None:
            return
        content = root.find(qn("Content"))
        if content is None:
            return
        for layer in content.iter(qn("Layer")):
            layer_style = self.style_from_ref(layer.get("DrawParam"))
            for obj in layer:
                name = local(obj)
                if name == "TextObject":
                    self.parse_text(obj, layer_style, draws)
                elif name == "ImageObject":
                    self.parse_image(obj, layer_style, draws)
                elif name == "PathObject":
                    self.parse_path(obj, layer_style, draws)
                # CompositeObject / 其他类型暂不支持
                # else:
                #     self.log(f"跳过对象类型 {name}")

    # ---- 样式合并：对象级 > Layer DrawParam > 默认 ----

    def style_from_ref(self, ref_id):
        style = Style()
        if ref_id and ref_id in self.draw_params:
            p = self.draw_params[ref_id]
            style.fill, style.stroke, style.line_width = (
                p.fill, p.stroke, p.line_width)
        return style

    def obj_style(self, obj, layer_style):
        """合并对象自身的 DrawParam 引用与内联颜色/线宽"""
        style = layer_style.copy()
        ref = obj.get("DrawParam")
        if ref and ref in self.draw_params:
            p = self.draw_params[ref]
            style.fill, style.stroke, style.line_width = (
                p.fill, p.stroke, p.line_width)
        for child in obj:
            name = local(child)
            if name == "FillColor":
                c = parse_color(child.get("Value"), child.get("Alpha"))
                if c:
                    style.fill = c
            elif name == "StrokeColor":
                c = parse_color(child.get("Value"), child.get("Alpha"))
                if c:
                    style.stroke = c
            elif name == "LineWidth":
                try:
                    style.line_width = float(child.text or "")
                except ValueError:
                    pass
        return style

    # ---- 各对象类型 ----

    def parse_text(self, obj, layer_style, draws):
        boundary = obj.get("Boundary")
        if not boundary:
            return
        bx, by, bw, bh = parse_floats(boundary, 4)
        style = self.obj_style(obj, layer_style)

        size_mm = 3.0
        if obj.get("Size"):
            try:
                size_mm = float(obj.get("Size"))
            except ValueError:
                pass
        hscale = 1.0
        if obj.get("HScale"):
            try:
                hscale = float(obj.get("HScale"))
            except ValueError:
                pass

        # 统一使用中易宋体（数字为 0.5em 等宽，与 DeltaX 槽位吻合）
        font_name = FONT_ALIAS if FONT_FILE else FONT_FALLBACK

        # 收集 TextCode 段
        segments = []  # (text, x_mm, y_mm, [delta_x...])
        cur_x, cur_y = 0.0, 0.0
        first = True
        for tc in obj.iter(qn("TextCode")):
            text = tc.text or ""
            if not text:
                # TextCode 可能包着子节点，取全部文本
                text = "".join(tc.itertext())
            try:
                x = float(tc.get("X", "0"))
                y = float(tc.get("Y", "0"))
            except ValueError:
                x, y = 0.0, 0.0
            try:
                deltas = parse_floats(tc.get("DeltaX", "")) if tc.get("DeltaX") else []
            except ValueError:
                deltas = []
            if first:
                cur_x, cur_y = x, y
                first = False
            else:
                # 后续 TextCode 相对前一段结束点偏移
                cur_x, cur_y = cur_x + x, cur_y + y
            segments.append((text, cur_x, cur_y, deltas))
            # 段末位置 = 起点 + 各字符步进
            adv = sum(deltas[:len(text)]) * hscale if deltas else 0.0
            cur_x += adv

        if segments:
            draws.append(("text", {
                "boundary": (bx, by, bw, bh),
                "size_mm": size_mm,
                "font": font_name,
                "color": style.fill,
                "hscale": hscale,
                "segments": segments,
            }))

    def parse_image(self, obj, layer_style, draws):
        boundary = obj.get("Boundary")
        rid = obj.get("ResourceID")
        if not boundary or not rid:
            return
        media = self.medias.get(rid)
        if media is None:
            self.log(f"警告: 图片资源 {rid} 未在资源文件中声明")
            return
        data = self.read(media[0])
        if data is None:
            return
        bx, by, bw, bh = parse_floats(boundary, 4)

        # CTM 矩阵（a b c d e f），作用于图像单位方格
        ctm = None
        if obj.get("CTM"):
            try:
                a, b, c, d, e, f = parse_floats(obj.get("CTM"), 6)
                ctm = (a, b, c, d, e, f)
            except ValueError:
                ctm = None

        draws.append(("image", {
            "boundary": (bx, by, bw, bh),
            "data": data,
            "ctm": ctm,
        }))

    def parse_path(self, obj, layer_style, draws):
        boundary = obj.get("Boundary")
        if not boundary:
            return
        bx, by, bw, bh = parse_floats(boundary, 4)
        style = self.obj_style(obj, layer_style)

        abbr = obj.find(qn("AbbreviatedData"))
        if abbr is None or not (abbr.text or "").strip():
            return
        tokens = re.findall(r"[A-Za-z]+|[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?",
                            abbr.text)
        # 解析为子路径：[[ (cmd, pts...), ... ]]
        subpaths = []   # 每条: [点列表(局部mm)], 操作序列
        ops = []        # (cmd, 数值列表)
        for tok in tokens:
            if re.match(r"^[A-Za-z]+$", tok):
                cmd = tok.upper()
                if cmd in PATH_CMD_ARITY:
                    ops.append((cmd, []))
            elif ops:
                ops[-1][1].append(float(tok))

        stroke = obj.get("Stroke", "true") != "false"
        fill = obj.get("Fill", "false") == "true"
        # 有颜色填充声明而无 Fill 属性的老文件兜底
        if not fill and not stroke:
            stroke = True

        draws.append(("path", {
            "boundary": (bx, by, bw, bh),
            "ops": ops,
            "stroke": stroke,
            "fill": fill,
            "color": style.stroke,
            "fill_color": style.fill,
            "width_mm": style.line_width,
        }))


# ---------------------------------------------------------------------------
# PDF 渲染
# ---------------------------------------------------------------------------

def render(doc: OFDDoc, out_path: Path):
    pdf = fitz.open()
    for w_mm, h_mm, draws in doc.pages:
        page = pdf.new_page(width=w_mm * MM2PT, height=h_mm * MM2PT)
        # 每页按颜色缓存一个 TextWriter：所有文本最后统一写入，
        # 使页面只产生少量字体对象（而非每个文本对象一个）
        tw_cache = {}  # color -> [TextWriter, font_obj, used]

        def get_tw(color):
            entry = tw_cache.get(color)
            if entry is None:
                fobj = (fitz.Font(fontname=FONT_ALIAS, fontfile=FONT_FILE)
                        if FONT_FILE else fitz.Font(FONT_FALLBACK))
                entry = [fitz.TextWriter(page.rect), fobj, False]
                tw_cache[color] = entry
            return entry

        for kind, kw in draws:
            if kind == "path":
                draw_path(page, kw)
            elif kind == "image":
                draw_image(page, kw)
            elif kind == "text":
                draw_text(page, kw, get_tw)

        # 统一写出各颜色文本层
        for color, (tw, _fobj, used) in tw_cache.items():
            if used:
                tw.write_text(page, color=color)

    # 写入元数据
    meta = {"producer": "ofd2pdf (PyMuPDF)", "creator": "ofd2pdf"}
    info = doc.doc_info
    if info.get("DocID"):
        meta["title"] = info["DocID"]
    if info.get("Author"):
        meta["author"] = info["Author"]
    if info.get("CreationDate"):
        meta["creationDate"] = info["CreationDate"]
    pdf.set_metadata(meta)

    # 裁剪字体子集：只保留实际用到的字形，避免整字体嵌入导致文件膨胀
    try:
        pdf.subset_fonts(verbose=False)
    except Exception as e:
        print(f"  [ofd] 警告: 字体子集化失败 {e}", file=sys.stderr)

    pdf.save(str(out_path), garbage=3, deflate=True)
    pdf.close()


def _pt(v_mm):
    return v_mm * MM2PT


def draw_path(page, kw):
    bx, by, _, _ = kw["boundary"]
    shape = page.new_shape()
    # 当前子路径点（页面坐标，pt）
    cur = []
    has_curve = False
    subpaths = []

    def flush():
        nonlocal cur, has_curve
        if cur:
            subpaths.append((cur, has_curve))
            cur, has_curve = [], False

    x = y = 0.0
    start = (0.0, 0.0)
    for cmd, nums in kw["ops"]:
        if cmd == "M" and len(nums) >= 2:
            flush()
            x, y = nums[0], nums[1]
            start = (x, y)
            cur.append((_pt(bx + x), _pt(by + y)))
        elif cmd == "L" and len(nums) >= 2:
            x, y = nums[0], nums[1]
            if not cur:  # 无 M 的裸 L，视作 M
                cur.append((_pt(bx), _pt(by)))
            cur.append((_pt(bx + x), _pt(by + y)))
        elif cmd in ("C", "B") and len(nums) >= 6:
            x1, y1, x2, y2, x3, y3 = nums[:6]
            if not cur:
                cur.append((_pt(bx), _pt(by)))
            # PyMuPDF: draw_bezier(p1, p2, p3, p4) 以当前点为起点
            # 这里用低层方式记录曲线段：借助 shape.draw_bezier 逐段绘制
            p0 = cur[-1]
            shape.draw_bezier(
                fitz.Point(p0),
                fitz.Point(_pt(bx + x1), _pt(by + y1)),
                fitz.Point(_pt(bx + x2), _pt(by + y2)),
                fitz.Point(_pt(bx + x3), _pt(by + y3)),
            )
            cur.append((_pt(bx + x3), _pt(by + y3)))
            has_curve = True
            x, y = x3, y3

    if not subpaths and not cur:
        return
    flush()

    # 纯直线子路径用 polyline 一次绘制；曲线段已直接写入 shape
    for pts, has_c in subpaths:
        if not has_c:
            shape.draw_polyline([fitz.Point(px, py) for px, py in pts])

    if kw["fill"] and kw["stroke"]:
        shape.finish(color=kw["color"], fill=kw["fill_color"],
                     width=_pt(kw["width_mm"]), closePath=True)
    elif kw["fill"]:
        shape.finish(fill=kw["fill_color"], closePath=True)
    else:
        shape.finish(color=kw["color"], width=_pt(kw["width_mm"]),
                     closePath=False)
    shape.commit()


def draw_image(page, kw):
    bx, by, bw, bh = kw["boundary"]
    rect = fitz.Rect(_pt(bx), _pt(by), _pt(bx + bw), _pt(by + bh))
    data = kw["data"]
    ctm = kw["ctm"]
    # CTM（a b c d e f）作用于图像单位方格；样例中均为纯缩放
    # （b=c=e=f=0），Boundary 已给出最终位置，直接插入。
    # 其余情况（旋转/斜切）用 PIL AFFINE 预变换兜底。
    if ctm and not (ctm[1] == 0 and ctm[2] == 0 and
                    ctm[4] == 0 and ctm[5] == 0):
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(data)).convert("RGBA")
            a, b, c, d, _e, _f = ctm
            w_px, h_px = im.size
            # 逆变换映射：输出像素 -> 源像素
            im = im.transform(
                (max(1, int(round(w_px * abs(a)))), max(1, int(round(h_px * abs(d))))),
                Image.Transform.AFFINE,
                (1.0 / a, -b / (a * d), 0.0, 0.0, 1.0 / d, 0.0),
                resample=Image.Resampling.BICUBIC,
            )
            buf = io.BytesIO()
            im.save(buf, "PNG")
            data = buf.getvalue()
        except Exception as e:
            print(f"  [ofd] 警告: 图片 CTM 变换失败 {e}", file=sys.stderr)
    try:
        page.insert_image(rect, stream=data, keep_proportion=False)
    except Exception:
        # 某些冷门编码格式兜底：经 PIL 转 PNG 再插入
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(data)).convert("RGB")
            buf = io.BytesIO()
            im.save(buf, "PNG")
            page.insert_image(rect, stream=buf.getvalue(),
                              keep_proportion=False)
        except Exception as e:
            print(f"  [ofd] 警告: 图片插入失败 {e}", file=sys.stderr)


def draw_text(page, kw, get_tw):
    bx, by, bw, bh = kw["boundary"]
    size = _pt(kw["size_mm"])
    hscale = kw["hscale"]

    # 从页面级缓存取 TextWriter 与字体对象（见 render 中的说明）
    tw, fobj, _ = get_tw(kw["color"])

    for text, tx, ty, deltas in kw["segments"]:
        x = _pt(bx + tx)
        y = _pt(by + ty)
        for i, ch in enumerate(text):
            if ch.strip():
                if fobj.has_glyph(ord(ch)):
                    tw.append(fitz.Point(x, y), ch, font=fobj, fontsize=size)
                    get_tw(kw["color"])[2] = True
                else:
                    # 字体缺字兜底：用内置 CJK 字体逐字写入
                    try:
                        page.insert_text(fitz.Point(x, y), ch, fontsize=size,
                                         fontname=FONT_FALLBACK,
                                         color=kw["color"])
                    except Exception:
                        pass
            # 无 DeltaX 时按当前字体实际宽度步进，避免用估算值造成累积偏差
            if i < len(deltas):
                x += _pt(deltas[i]) * hscale
            else:
                x += fobj.text_length(ch, size)


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

def convert_one(src: Path, dst: Path, verbose=False):
    print(f"转换: {src.name} -> {dst.name}")
    parser = OFDParser(str(src), verbose=verbose)
    doc = parser.parse()
    n_text = sum(1 for _, _, ds in doc.pages for k, _ in ds if k == "text")
    n_img = sum(1 for _, _, ds in doc.pages for k, _ in ds if k == "image")
    n_path = sum(1 for _, _, ds in doc.pages for k, _ in ds if k == "path")
    render(doc, dst)
    print(f"  完成: {len(doc.pages)} 页, 文本 {n_text}, 图片 {n_img}, "
          f"路径 {n_path}")
    return dst


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="将 OFD 版式文件（电子发票等）转换为 PDF",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("用法：")[-1])
    ap.add_argument("inputs", nargs="+", help="OFD 文件（支持多个/通配符）")
    ap.add_argument("-o", "--output",
                    help="输出 PDF 路径或目录（多输入时必须是目录）")
    ap.add_argument("-v", "--verbose", action="store_true", help="输出详细日志")
    args = ap.parse_args(argv)

    srcs = []
    for pat in args.inputs:
        p = Path(pat)
        if p.is_dir():
            srcs.extend(sorted(p.glob("*.ofd")))
        else:
            srcs.append(p)

    if not srcs:
        ap.error("未找到输入文件")

    out = Path(args.output) if args.output else None
    if len(srcs) == 1:
        dst = out if (out and out.suffix.lower() == ".pdf") else \
            Path(srcs[0]).with_suffix(".pdf")
        dst.parent.mkdir(parents=True, exist_ok=True)
        convert_one(Path(srcs[0]), dst, args.verbose)
    else:
        outdir = out if out else Path(".")
        if out and out.suffix.lower() == ".pdf":
            ap.error("多输入时 -o 必须是目录")
        outdir.mkdir(parents=True, exist_ok=True)
        ok = 0
        for s in srcs:
            try:
                convert_one(s, outdir / (s.stem + ".pdf"), args.verbose)
                ok += 1
            except Exception as e:
                print(f"  失败: {s.name}: {e}", file=sys.stderr)
        print(f"共 {len(srcs)} 个，成功 {ok} 个")

    return 0


if __name__ == "__main__":
    sys.exit(main())
