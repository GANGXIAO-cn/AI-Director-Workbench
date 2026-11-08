from pathlib import Path

import pytest

from helpers import Env
from manuscript_samples import pdf_sample, word_sample
from workbench.errors import Rejected


@pytest.mark.parametrize("name,content,expected,method,warning", [
    ("分镜.DOCX", word_sample(), "站台\t夜\n雨停了。\n镜头一\n他收起伞。", "Word 正文段落与表格", None),
    ("script.PDF", pdf_sample(["Platform", "Umbrella"]), "Platform\n\nUmbrella", "PDF 文本层", None),
    ("mixed.pdf", pdf_sample(["Platform", None]), "Platform", "PDF 文本层", "第 2 页"),
    ("locked.pdf", pdf_sample(["Platform"], locked=True), None, "PDF 文本层", "密码"),
    ("scanned.pdf", pdf_sample([None]), None, "PDF 文本层", "文字识别"),
    ("broken.docx", b"broken", None, "Word 正文段落与表格", "失败"),
    ("broken.pdf", b"%PDF-broken", None, "PDF 文本层", "失败"),
])
def test_manuscript_import(tmp_path, name, content, expected, method, warning):
    wb = Env(tmp_path).wb
    pid = wb.create_project("_template")["projectId"]
    result = wb.import_file(pid, name, content, "script_source", command_id="import-file")
    assert wb.import_file(pid, name, content, "script_source", command_id="import-file") == result
    record = wb.read_import(result["importId"])
    assert record["extractionMethod"] == method and record["intact"]
    assert (wb.store.root / Path(record["relpath"])).read_bytes() == content
    assert record["parsed_text"] == expected
    assert warning in record["diagnostic"] if warning else record["diagnostic"] is None
    args = dict(episode_id=None, project_id=pid, display_code="E01", source_type="import",
                source_import_id=result["importId"], executor_plan={"create":{"provider":"codex"},"review":{"provider":"claude"}})
    if expected:
        kickoff = wb.start_episode(**args)
        view = wb.episode_view(kickoff["episodeId"])
        assert view["sourceImport"]["parsed_text"] == expected
    else:
        assert result["parseStatus"] == "failed"
        with pytest.raises(Rejected):
            wb.start_episode(**args)
        assert wb.project_view(pid)["episodes"] == []
