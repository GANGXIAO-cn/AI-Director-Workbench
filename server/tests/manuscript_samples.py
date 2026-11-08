"""Small local source documents used by import and browser checks."""
from io import BytesIO
from zipfile import ZipFile

import pymupdf
from PIL import Image


def word_sample():
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="doc" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr("word/document.xml", '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
        <w:p><w:r><w:t>站台</w:t><w:tab/><w:t>夜</w:t><w:br/><w:t>雨停了。</w:t></w:r></w:p>
        <w:tbl><w:tr><w:tc><w:p><w:r><w:t>镜头一</w:t></w:r></w:p></w:tc><w:tc><w:p><w:del><w:r><w:delText>旧台词</w:delText></w:r></w:del><w:r><w:t>他收起伞。</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
        </w:body></w:document>''')
    return output.getvalue()


def pdf_sample(pages, locked=False):
    with pymupdf.open() as doc:
        for text in pages:
            page = doc.new_page()
            if text is None:
                image = BytesIO()
                Image.new("RGB", (12, 12), "blue").save(image, "PNG")
                page.insert_image(page.rect, stream=image.getvalue())
            else:
                page.insert_text((45, 55), text)
        if locked:
            return doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="reader")
        return doc.tobytes()
