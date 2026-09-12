# OFDtoPDF - OFD 转 PDF 转换器

将 OFD 版式文件（GB/T 33190-2016，国家电子发票/电子证照常用格式）转换为 PDF。

## 特性

- **纯 Python 实现**，仅依赖 PyMuPDF（Pillow 可选兜底）
- 保真还原：文本（含逐字符字距 DeltaX）、矢量路径（表格线）、图片、模板页
- 生成的 PDF **文本可搜索、可复制**
- 支持批量转换、指定输出目录
- 自动写入文档元数据（DocID、Author、CreationDate）

## 安装

```bash
pip install pymupdf pillow
```

## 用法

```bash
# 单文件转换（输出到同目录同名 .pdf）
python ofd2pdf.py input.ofd

# 指定输出路径
python ofd2pdf.py input.ofd output.pdf

# 批量转换
python ofd2pdf.py a.ofd b.ofd c.ofd

# 批量转换到指定目录
python ofd2pdf.py *.ofd -o 输出目录/

# 详细日志
python ofd2pdf.py input.ofd -v
```

## 示例验证

样例文件 `26958479111070344004-4794290931816.ofd`（数电航空客票行程单，230×133.5mm 版式）转换结果：

- 1 页，67 个文本对象、3 张图片、25 条矢量路径全部还原
- 中文标题/字段/表格线/深航 Logo/监制章/二维码均正确渲染
- PDF 文本可搜索（约 491 字符可提取），文件约 112 KB

## 已知限制

| 项目 | 说明 |
|------|------|
| 电子签章 | OFD 签章外观数据未随文件嵌入（需验签环境才能渲染）时跳过，`-v` 可见提示 |
| CompositeObject | 组合对象暂不支持（样例文件未涉及） |
| 附件 Attachments | XML 附件不带入 PDF |

## 数字重叠问题（已修复）

早期版本在 Adobe 中查看时数字/字母出现重叠，根因有两个：

1. **字体未嵌入**：只引用字体名（`ext='n/a'`），Adobe 本地替换字体后宽度失控
2. **宽度不匹配**：OFD 的 DeltaX 给每个数字留 0.5em 槽位，但 Helvetica/Droid
   的数字宽 0.55em，逐字累积产生重叠

修复方式（[ofd2pdf.py](ofd2pdf.py)）：

- 统一使用中易宋体 SimSun（其 ASCII 字符恰为 **0.5em 等宽**，与 DeltaX 完全吻合）
- 通过 `TextWriter` 写入并调用 `subset_fonts()` **嵌入字体子集**
- 无 DeltaX 的字符按当前字体实际宽度步进（`Font.text_length`），而非估算值

## 实现说明

OFD 本质是 ZIP 包，内部结构：

```
OFD.xml                     # 文档入口（DocRoot、DocInfo）
Doc_0/Document.xml          # 页面列表、模板、资源声明
Doc_0/PublicRes.xml         # 公共资源（字体、DrawParam）
Doc_0/DocumentRes.xml       # 文档资源（图片 MultiMedia）
Doc_0/Pages/Page_N/Content.xml  # 页面内容（文本/路径/图片对象）
Doc_0/Tpls/Tpl_N/Content.xml    # 模板页（背景表格线等）
```

坐标单位为毫米（1 mm = 72/25.4 pt），按 ZOrder（模板 Background → 页面内容）
顺序重绘到 PyMuPDF 页面。文本按 TextCode 的 X/Y/DeltaX 逐字符定位，保证与
原版式对齐；路径解析 AbbreviatedData 的 M/L/C/B 命令。
