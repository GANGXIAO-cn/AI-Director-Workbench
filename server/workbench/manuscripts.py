"""Extract local manuscript text; preserve source order and report unreadable material."""
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zlib import error as ZlibError
from zipfile import BadZipFile, ZipFile

import pymupdf

WORD_NAMESPACES = {
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "http://purl.oclc.org/ooxml/wordprocessingml/main",
}


def word_text(data):
    with ZipFile(BytesIO(data)) as archive:
        info = archive.getinfo("word/document.xml")
        if info.file_size > 32 * 1024 ** 2:
            raise ValueError("DOCX 正文超过 32 MiB")
        root = ElementTree.fromstring(archive.read(info))
    namespace = root.tag.rpartition("}")[0].lstrip("{")
    if namespace not in WORD_NAMESPACES:
        raise ValueError("无法识别 Word 正文结构")
    prefix = "{" + namespace + "}"
    body = root.find(prefix + "body")
    if body is None:
        raise ValueError("DOCX 缺少正文")
    fragments = []

    def read(node):
        if node.tag in (prefix + "del", prefix + "moveFrom"):
            return
        if node.tag == prefix + "t":
            fragments.append(node.text or "")
        elif node.tag == prefix + "tab":
            fragments.append("\t")
        elif node.tag in (prefix + "br", prefix + "cr"):
            fragments.append("\n")
        else:
            for child in node:
                read(child)
            if node.tag == prefix + "p":
                fragments.append("\n")

    read(body)
    return "".join(fragments).strip()


def pdf_text(data):
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        if not doc.is_pdf or doc.needs_pass:
            raise ValueError("PDF 无法读取或需要密码")
        pages = []
        image_pages = []
        for page in doc:
            text = page.get_text("text", sort=True).strip()
            if not text and page.get_image_info():
                image_pages.append(str(page.number + 1))
            pages.append(text)
    text = "\n\n".join(pages).strip()
    warning = None
    if image_pages:
        warning = "第 " + "、".join(image_pages) + " 页只有图像，未提取文字"
        if not text:
            raise ValueError(warning + "，请先做文字识别再导入")
    return text, warning


def extract_manuscript(name, data):
    suffix = Path(name).suffix.lower()
    method = {".docx": "Word 正文段落与表格", ".pdf": "PDF 文本层"}.get(suffix, "UTF-8 原文")
    diagnostic = None
    try:
        if suffix == ".docx":
            text = word_text(data)
        elif suffix == ".pdf":
            text, diagnostic = pdf_text(data)
        else:
            text = data.decode("utf-8-sig").strip()
            if "\x00" in text:
                raise ValueError("UTF-8 正文含有空字节")
        if not text:
            raise ValueError("文件为空或没有可提取的正文")
        return "parsed", text, diagnostic, method
    except UnicodeDecodeError as exc:
        diagnostic = f"不是 UTF-8 文本：{exc.reason}"
    except (BadZipFile, KeyError, ElementTree.ParseError, NotImplementedError, ZlibError, RuntimeError, ValueError, OSError) as exc:
        diagnostic = f"{method}读取失败：{exc}"
    return "failed", None, diagnostic, method
