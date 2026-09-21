"""Regression tests for embedded cell images used by WPS/newer Excel."""

import io
import zipfile
import unittest

from openpyxl import Workbook

import mail_blaster_service as mb


_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _cell_image_xlsx() -> bytes:
    """Build a tiny XLSX whose image is stored as a DISPIMG cell image."""
    wb = Workbook()
    ws = wb.active
    ws.append(["图片", "name", "收件邮箱"])
    ws.append(['=_xlfn.DISPIMG("ID_TEST",1)', "Acme", "a@example.com"])
    raw = io.BytesIO()
    wb.save(raw)

    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw.getvalue())) as source, zipfile.ZipFile(out, "w") as target:
        for entry in source.infolist():
            target.writestr(entry, source.read(entry.filename))
        target.writestr("xl/media/image1.png", _PNG)
        target.writestr(
            "xl/cellimages.xml",
            """<cellImages xmlns=\"http://schemas.microsoft.com/office/spreadsheetml/2017/cellimage\">
              <cellImage><xdr:pic xmlns:xdr=\"http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing\">
                <xdr:nvPicPr><xdr:cNvPr id=\"1\" name=\"ID_TEST\"/></xdr:nvPicPr>
                <xdr:blipFill><a:blip xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\"
                  r:embed=\"rId1\" xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\"/></xdr:blipFill>
              </xdr:pic></cellImage>
            </cellImages>""",
        )
        target.writestr(
            "xl/_rels/cellimages.xml.rels",
            """<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">
              <Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/image\"
                Target=\"media/image1.png\"/>
            </Relationships>""",
        )
    return out.getvalue()


class CellImageParsingTest(unittest.TestCase):
    def test_dispimg_cell_image_is_attached_to_its_row(self):
        parsed = mb.parse_material_xlsx(_cell_image_xlsx())
        self.assertEqual(len(parsed["rows"]), 1)
        self.assertEqual(parsed["notices"], [])
        self.assertEqual(parsed["errors"], [])
        self.assertEqual(parsed["rows"][0]["image_bytes"], _PNG)


if __name__ == "__main__":
    unittest.main()
