#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将《OFD转PDF工具使用说明.txt》排版为精美的 PDF。

设计要点：
- 使用系统自带中易宋体/黑体（注册 TTF 后 reportlab 可自动换行、嵌入子集）
- 标题色块 + 章节标题分隔线 + 列表圆点 + 命令行代码框 + 示例表格
- A4 纵向，页眉页脚（文档名 + 页码）
"""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether,
                                ListFlowable, ListItem, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)

# ---------------------------------------------------------------------------
# 字体注册（Windows 自带中易宋体/黑体）
# ---------------------------------------------------------------------------

pdfmetrics.registerFont(TTFont("SimSun", r"C:\Windows\Fonts\simsun.ttc"))
pdfmetrics.registerFont(TTFont("SimHei", r"C:\Windows\Fonts\simhei.ttf"))
pdfmetrics.registerFontFamily("SimSun", normal="SimSun", bold="SimHei",
                              italic="SimSun", boldItalic="SimHei")

# ---------------------------------------------------------------------------
# 配色与样式
# ---------------------------------------------------------------------------

C_PRIMARY = colors.HexColor("#1a5276")   # 主色：深蓝
C_ACCENT = colors.HexColor("#e67e22")    # 强调：橙
C_TEXT = colors.HexColor("#2c3e50")      # 正文
C_LIGHT = colors.HexColor("#eaf2f8")     # 浅蓝底
C_CODE_BG = colors.HexColor("#2d3436")   # 代码框底
C_CODE_FG = colors.HexColor("#dfe6e9")   # 代码框字
C_BORDER = colors.HexColor("#aab7c4")    # 分隔线

st_title = ParagraphStyle("title", fontName="SimHei", fontSize=22,
                          leading=30, textColor=colors.white,
                          alignment=1)
st_subtitle = ParagraphStyle("subtitle", fontName="SimSun", fontSize=11,
                             leading=16, textColor=colors.HexColor("#d6eaf8"),
                             alignment=1)
st_h1 = ParagraphStyle("h1", fontName="SimHei", fontSize=14, leading=20,
                       textColor=C_PRIMARY, spaceBefore=14, spaceAfter=6)
st_body = ParagraphStyle("body", fontName="SimSun", fontSize=10.5,
                         leading=17, textColor=C_TEXT,
                         spaceBefore=3, spaceAfter=3, firstLineIndent=21)
st_body_ni = ParagraphStyle("body_ni", parent=st_body, firstLineIndent=0)
st_bullet = ParagraphStyle("bullet", fontName="SimSun", fontSize=10.5,
                           leading=16, textColor=C_TEXT,
                           spaceBefore=1, spaceAfter=1)
st_code = ParagraphStyle("code", fontName="SimSun", fontSize=9.5,
                         leading=15, textColor=C_CODE_FG)
st_note = ParagraphStyle("note", fontName="SimSun", fontSize=9.5,
                         leading=15, textColor=colors.HexColor("#7d6608"))
st_cell = ParagraphStyle("cell", fontName="SimSun", fontSize=10,
                         leading=14, textColor=C_TEXT)
st_cell_head = ParagraphStyle("cell_head", fontName="SimHei", fontSize=10,
                              leading=14, textColor=colors.white)


def banner(title, subtitle):
    """文档顶部大标题横幅"""
    inner = Table(
        [[Paragraph(title, st_title)], [Paragraph(subtitle, st_subtitle)]],
        colWidths=[168 * mm],
    )
    inner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C_PRIMARY),
        ("TOPPADDING", (0, 0), (-1, 0), 10 * mm),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 10 * mm),
        ("TOPPADDING", (0, 1), (-1, 1), 2 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 2 * mm),
        ("LEFTPADDING", (0, 0), (-1, -1), 8 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8 * mm),
    ]))
    outer = Table([[inner]], colWidths=[172 * mm])
    outer.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C_PRIMARY),
        ("ROUNDEDCORNERS", [8, 8, 8, 8]),
        ("LEFTPADDING", (0, 0), (-1, -1), 2 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5 * mm),
    ]))
    return outer


def h1(text):
    """章节标题：左侧色条 + 文字 + 底部分隔线"""
    t = Table([[Paragraph(text, st_h1)]], colWidths=[172 * mm])
    t.setStyle(TableStyle([
        ("LINEBEFORE", (0, 0), (0, -1), 3, C_ACCENT),
        ("LINEBELOW", (0, 0), (-1, -1), 0.7, C_BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def code_block(lines):
    """深色命令行风格代码框"""
    rows = [[Paragraph(ln.replace(" ", "&nbsp;"), st_code)] for ln in lines]
    t = Table(rows, colWidths=[164 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C_CODE_BG),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, 0), 7),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 7),
        ("TOPPADDING", (0, 1), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -2), 1),
    ]))
    return t


def tip_box(text):
    """提示框：浅蓝底 + 左侧橙色竖条"""
    t = Table([[Paragraph(f"<b>提示</b>&nbsp;&nbsp;{text}", st_note)]],
              colWidths=[164 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C_LIGHT),
        ("LINEBEFORE", (0, 0), (0, -1), 3, C_ACCENT),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def bullets(items):
    return ListFlowable(
        [ListItem(Paragraph(it, st_bullet), leftIndent=18) for it in items],
        bulletType="bullet", start="●", bulletFontSize=6,
        bulletOffsetY=-2, leftIndent=18,
    )


def cmd_table(header, rows):
    """示例命令表格"""
    data = [[Paragraph(h, st_cell_head) for h in header]]
    data += [[Paragraph(c, st_cell) for c in r] for r in rows]
    widths = [58 * mm, 114 * mm]
    t = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), C_PRIMARY),
        ("GRID", (0, 0), (-1, -1), 0.5, C_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f8fafc")))
    t.setStyle(TableStyle(style))
    return t


# ---------------------------------------------------------------------------
# 页面模板：页眉页脚
# ---------------------------------------------------------------------------

DOC_NAME = "OFD 转 PDF 工具（免安装版）使用说明"


def on_page(canvas, doc):
    canvas.saveState()
    w, h = A4
    # 页眉
    canvas.setFont("SimSun", 8)
    canvas.setFillColor(colors.HexColor("#85929e"))
    canvas.drawString(20 * mm, h - 12 * mm, DOC_NAME)
    canvas.setStrokeColor(C_BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(20 * mm, h - 14 * mm, w - 20 * mm, h - 14 * mm)
    # 页脚：页码
    canvas.setFont("SimSun", 8.5)
    canvas.drawCentredString(w / 2, 12 * mm, f"— {doc.page} —")
    canvas.restoreState()


# ---------------------------------------------------------------------------
# 文档内容
# ---------------------------------------------------------------------------

story = []
story.append(banner("OFD 转 PDF 工具",
                    "免安装版 · 使用说明  |  无需 Python，拷贝即用"))
story.append(Spacer(1, 6 * mm))

# 一、这是什么
story.append(h1("一、这是什么"))
story.append(Paragraph(
    "<b>ofd2pdf.exe</b> 是一个将 OFD 格式文件转换为 PDF 格式的工具。"
    "OFD 是国家电子发票、电子证照常用的版式文件格式（如增值税电子发票、"
    "航空客票行程单等）。很多场景需要把 OFD 转成更通用的 PDF 来打印、"
    "存档或发送。", st_body))
story.append(Spacer(1, 2 * mm))
story.append(Paragraph("本程序是<b>【免安装版】</b>：", st_body_ni))
story.append(bullets([
    "不需要安装 Python",
    "不需要安装任何其他软件或依赖",
    "把 ofd2pdf.exe 拷贝到任意 Windows 电脑上即可直接使用",
]))
story.append(Spacer(1, 4 * mm))

# 二、系统要求
story.append(h1("二、系统要求"))
story.append(bullets([
    "Windows 7 及以上版本（64 位）",
    "无其他要求",
]))
story.append(Spacer(1, 2 * mm))
story.append(tip_box(
    "程序会自动使用系统自带的中文字体（宋体），转换出的 PDF 字体已嵌入文件，"
    "在任何电脑上打开、打印效果一致，文字可搜索、可复制。"))
story.append(Spacer(1, 4 * mm))

# 三、使用方法
story.append(h1("三、使用方法"))

story.append(Paragraph("◆ 方法一：拖放（最简单，推荐）", st_body_ni))
story.append(Spacer(1, 1 * mm))
story.append(bullets([
    "用鼠标选中一个或多个 .ofd 文件",
    "按住左键，把它们拖到 ofd2pdf.exe 的图标上，松开",
    "会弹出一个黑色命令行窗口，显示转换进度",
    "转换完成后，PDF 文件生成在<b>OFD 文件所在的同一个文件夹</b>里，"
    "文件名与原文件相同，只是后缀变成 .pdf",
]))
story.append(Spacer(1, 1.5 * mm))
story.append(code_block([
    "例如：",
    "  D:\\发票\\26958479.ofd   →   D:\\发票\\26958479.pdf",
]))
story.append(Spacer(1, 4 * mm))

story.append(Paragraph("◆ 方法二：命令行（适合批量处理）", st_body_ni))
story.append(Spacer(1, 1 * mm))
story.append(bullets([
    "按 Win + R 键，输入 cmd，回车，打开命令行窗口",
    "输入命令，格式：<b>ofd2pdf.exe 的完整路径&nbsp;&nbsp;OFD 文件的路径</b>",
]))
story.append(Spacer(1, 2 * mm))
story.append(cmd_table(
    ["用途", "命令示例"],
    [
        ["转换单个文件（PDF 生成在 OFD 同目录）",
         "ofd2pdf.exe D:\\发票\\26958479.ofd"],
        ["转换并指定 PDF 保存位置和名称",
         "ofd2pdf.exe D:\\发票\\26958479.ofd -o D:\\PDF\\发票.pdf"],
        ["批量转换（PDF 都生成在 D:\\PDF 文件夹里）",
         "ofd2pdf.exe D:\\发票\\*.ofd -o D:\\PDF"],
        ["批量转换（PDF 生成在各自 OFD 文件同目录）",
         "ofd2pdf.exe 文件1.ofd 文件2.ofd 文件3.ofd"],
    ]))
story.append(Spacer(1, 3 * mm))
story.append(Paragraph("可选参数：", st_body_ni))
story.append(cmd_table(
    ["参数", "说明"],
    [
        ["-o", "指定输出的 PDF 路径或文件夹"],
        ["-v", "显示详细转换日志（排查问题时使用）"],
    ]))
story.append(Spacer(1, 4 * mm))

# 四、转换结果说明
story.append(h1("四、转换结果说明"))
story.append(Paragraph("转换完成后，窗口会显示类似信息：", st_body_ni))
story.append(Spacer(1, 1.5 * mm))
story.append(code_block([
    "转换: xxx.ofd -> xxx.pdf",
    "  完成: 1 页, 文本 67, 图片 3, 路径 25",
]))
story.append(Spacer(1, 2 * mm))
story.append(cmd_table(
    ["项", "含义"],
    [
        ["页数", "PDF 的页数"],
        ["文本", "还原的文字内容数量"],
        ["图片", "还原的图片数量（发票监制章、二维码、Logo 等）"],
        ["路径", "还原的表格线、边框等矢量图形数量"],
    ]))
story.append(Spacer(1, 2 * mm))
story.append(Paragraph(
    "如果批量转换，最后会显示“共 N 个，成功 M 个”。个别文件转换失败不影响"
    "其他文件，失败原因会显示在窗口中。", st_body))
story.append(Spacer(1, 4 * mm))

# 五、常见问题
story.append(h1("五、常见问题"))

faqs = [
    ("双击 exe 后窗口一闪就没了？",
     "本程序需要指定 OFD 文件才能工作，直接双击只会打印帮助信息然后退出。"
     "请用“方法一”拖放，或“方法二”命令行。"),
    ("杀毒软件提示 ofd2pdf.exe 有风险？",
     "这是误报。程序由 PyInstaller 打包（自解压运行时），部分杀毒软件会对其"
     "告警。可将本程序添加到杀毒软件的信任列表 / 白名单中。"),
    ("转换出来的 PDF 中数字或字母重叠？",
     "本版本已修复该问题（字体以 0.5em 等宽嵌入，与 OFD 版式完全对齐）。"
     "如仍遇到，请用 -v 参数重新转换并把日志反馈给开发者。"),
    ("电子签章（红色印章）没有转换出来？",
     "OFD 的电子签章外观数据未随文件嵌入时（需验签环境才能显示），程序会"
     "自动跳过该签章，不影响其他内容。这是文件本身的特性，非转换错误。"),
    ("转换后到哪里找 PDF 文件？",
     "默认在 OFD 文件所在的同一个文件夹里，文件名相同、后缀为 .pdf。"
     "若使用了 -o 参数，则在指定位置。"),
]
for q, a in faqs:
    qt = Table([[Paragraph(f"<b>问：{q}</b>", st_body_ni)]], colWidths=[164 * mm])
    qt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C_LIGHT),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    at = Table([[Paragraph(f"答：{a}", st_body_ni)]], colWidths=[164 * mm])
    at.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#d5dbdb")),
    ]))
    story.append(KeepTogether([qt, at, Spacer(1, 2 * mm)]))

# 六、退出码
story.append(h1("六、退出码说明（供脚本调用参考）"))
story.append(cmd_table(
    ["退出码", "含义"],
    [
        ["0", "全部转换成功"],
        ["非 0", "存在转换失败或参数错误，详情见窗口输出"],
    ]))

# ---------------------------------------------------------------------------
# 构建
# ---------------------------------------------------------------------------

out_path = Path(r"E:\Program\OFDtoPDF\dist\OFD转PDF工具使用说明.pdf")
doc = BaseDocTemplate(
    str(out_path), pagesize=A4,
    leftMargin=20 * mm, rightMargin=20 * mm,
    topMargin=20 * mm, bottomMargin=20 * mm,
    title=DOC_NAME, author="ofd2pdf",
)
frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
doc.addPageTemplates([PageTemplate(id="page", frames=[frame], onPage=on_page)])
doc.build(story)
print("已生成:", out_path)
