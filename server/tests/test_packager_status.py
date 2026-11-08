from hashlib import sha256
from pathlib import Path

from helpers import Env
from workbench.delivery import DEFAULT_PACKAGER


def test_packager_probe_reads_without_running_and_reports_invalid_source(tmp_path, monkeypatch):
    wb = Env(tmp_path).wb
    file = tmp_path / "packager.py"
    monkeypatch.setenv("ADW_PACKAGER_PATH", str(file))
    assert wb.packager_status()["state"] == "unavailable"
    file.write_text("raise RuntimeError('must run only on export')\ndef load_project(path): pass\n")
    result = wb.packager_status()
    assert result["state"] == "unavailable" and "package_contents" in result["reason"]
    with file.open("a") as stream:
        stream.write("def package_contents(*args): pass\ndef write_zip(*args): pass\n")
    assert wb.packager_status()["state"] == "ready"
    broken = tmp_path / "invalid.py"
    broken.write_text("if )")
    wb.put_setting("packager_path", str(broken))
    assert wb.packager_status()["state"] == "unavailable"
    assert wb.packager_status()["path"] == str(broken)


def test_successful_local_zip_attests_only_the_loaded_tool_bytes(tmp_path):
    import pytest
    if not DEFAULT_PACKAGER.is_file():
        pytest.skip("本机缺少独立 H3 打包器")
    env = Env(tmp_path)
    wb = env.wb
    file = tmp_path / "packager.py"
    raw = DEFAULT_PACKAGER.read_bytes()
    file.write_bytes(raw)
    wb.put_setting("packager_path", str(file))
    ep = env.full_episode(segments={"G01":[8000]})
    assert wb.packager_status()["lastPackage"] is None
    package = wb.package(ep["delivery"]["deliveryId"], ["G01"], "proof")
    status = wb.packager_status()
    assert status["sha256"] == sha256(raw).hexdigest()
    assert status["lastPackage"]["id"] == package["packageId"]
    assert status["lastPackage"]["sha256"] == sha256(Path(package["path"]).read_bytes()).hexdigest()
    file.write_bytes(raw+b"\n# local revision\n")
    assert wb.packager_status()["lastPackage"] is None
    assert wb.package(ep["delivery"]["deliveryId"], ["G01"], "proof") == package
    assert wb.packager_status()["lastPackage"] is None
