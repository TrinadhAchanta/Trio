"""Documents: Markdown parsing, conversion to DOCX/PDF/PPTX/XLSX, AI generation, editing, isolation."""

import io

from docx import Document
from openpyxl import load_workbook
from pptx import Presentation

from app.services.documents import parse, render

from .conftest import make_client, register

SAMPLE = """# Quarterly report

Revenue grew **12%** with *strong* demand for `trio-api`.

## Highlights

- New customers: 40
- Churn fell
  - nested detail

1. First
2. Second

| Region | Revenue | Formula |
|---|---|---|
| EU | 1200 | =1+1 |
| US | 3400.5 | n/a |

> A quoted line

```python
print("hi")
```

---

Unicode check: café, naïve, 東京, emoji ✓
"""


def test_parse_block_model():
    kinds = [b.kind for b in parse(SAMPLE)]
    assert kinds[:3] == ["heading", "para", "heading"]
    assert {"bullet", "ordered", "table", "quote", "code", "hr"} <= set(kinds)
    para = parse(SAMPLE)[1]
    assert any(r.bold and r.text == "12%" for r in para.runs) and any(r.code for r in para.runs)


def test_docx():
    data, media, name = render(SAMPLE, "docx")
    assert name == "Quarterly-report.docx" and media.endswith("document")
    d = Document(io.BytesIO(data))
    text = "\n".join(p.text for p in d.paragraphs)
    assert "Quarterly report" in text and "New customers: 40" in text and "東京" in text
    assert d.tables[0].cell(1, 0).text == "EU"


def test_pdf():
    data, media, _ = render(SAMPLE, "pdf")
    assert data.startswith(b"%PDF") and media == "application/pdf" and len(data) > 1500
    from pypdf import PdfReader
    text = PdfReader(io.BytesIO(data)).pages[0].extract_text()
    assert "Quarterly report" in text and "Revenue grew" in text


def test_pptx_one_slide_per_section():
    data, _, _ = render(SAMPLE, "pptx")
    prs = Presentation(io.BytesIO(data))
    titles = [s.shapes.title.text for s in prs.slides]
    assert titles == ["Quarterly report", "Highlights"]


def test_xlsx_tables_become_sheets_and_formulas_are_neutralized():
    data, _, _ = render(SAMPLE, "xlsx")
    ws = load_workbook(io.BytesIO(data))["Table 1"]
    assert [c.value for c in ws[1]] == ["Region", "Revenue", "Formula"]
    assert ws["B2"].value == 1200 and ws["B3"].value == 3400.5
    assert ws["C2"].value == "'=1+1" and ws["C2"].data_type != "f"


def test_export_endpoint(authed):
    r = authed.post("/api/documents/export", json={"markdown": SAMPLE, "format": "docx"})
    assert r.status_code == 200 and 'filename="Quarterly-report.docx"' in r.headers["content-disposition"]
    assert authed.post("/api/documents/export", json={"markdown": "x", "format": "exe"}).status_code == 422


def test_generate_edit_download_and_isolation(authed):
    r = authed.post("/api/documents/generate", json={"prompt": "Write a memo about the new office", "kind": "memo",
                                                     "model": "mock:mock-echo"})
    assert r.status_code == 201
    doc = r.json()
    assert doc["markdown"] and doc["model"] == "mock:mock-echo"
    r = authed.patch(f"/api/documents/{doc['id']}", json={"title": "Office memo", "markdown": "# Office memo\n\nWe move in May."})
    assert r.json()["title"] == "Office memo"
    for fmt in ("docx", "pdf", "pptx", "xlsx", "md"):
        r = authed.get(f"/api/documents/{doc['id']}/download?format={fmt}")
        assert r.status_code == 200 and f"Office-memo.{fmt}" in r.headers["content-disposition"]
    assert [d["id"] for d in authed.get("/api/documents").json()] == [doc["id"]]
    other = make_client()
    register(other, "olga@example.com")
    assert other.get(f"/api/documents/{doc['id']}").status_code == 404
    assert other.get(f"/api/documents/{doc['id']}/download").status_code == 404
    assert authed.delete(f"/api/documents/{doc['id']}").status_code == 204
