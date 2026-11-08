"""Check genuine source baseline assets and seven isolated mutations without changing them."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from historical_baseline import ROOT, SKILL, PIN

sys.path.insert(0, str(ROOT/"server"))
from workbench.errors import Rejected
from workbench.roleassets import check_assets


def compare():
    prod = json.loads((ROOT/"configs/projects/puggy.json").read_text())["production"]
    source = Path(prod["assetRoot"])
    cfg = prod["roleAssets"]
    cli = SKILL/"scripts/check_assets.py"
    assert subprocess.check_output(["git","-C",SKILL,"show",PIN+":scripts/check_assets.py"]) == cli.read_bytes()
    root = Path(tempfile.mkdtemp(prefix="adw-role-parity-"))
    def run(name, path):
        oracle = subprocess.run([sys.executable,"-B",cli,"--root",path],capture_output=True,text=True,timeout=30)
        result = dict(sourceOK=not oracle.returncode,sourceIssue=oracle.stderr.strip().splitlines()[-1] if oracle.stderr.strip() else None)
        try:
            result["native"] = check_assets(path,cfg)
            result["workbenchOK"] = True
        except Rejected as exc:
            result.update(workbenchOK=False,workbenchIssue=str(exc))
        return name, result

    evidence = dict([run("current",source)])
    assert evidence["current"]["sourceOK"] and evidence["current"]["workbenchOK"]
    faults = [("missing_series","缺少系列设定","缺少系列设定"),("wrong_roles","有效角色清单","固定角色清单"),
              ("missing_voice","缺少固定声音","缺少固定声音"),("missing_image","资产缺失","角色原图缺失"),
              ("escaped_path","越出资产根目录","超出项目目录"),("wrong_hash","资产指纹不匹配","指纹不匹配"),
              ("missing_version","缺少版本","缺少版本")]
    for name,expected_source,expected_native in faults:
        copy = root/name
        copy.mkdir()
        manifest = json.loads((source/cfg["manifest"]).read_bytes())
        for r in manifest["assets"]:
            path = copy/r["path"]
            path.parent.mkdir(exist_ok=True)
            if name != "missing_image" or r["id"] != "milo":
                path.write_bytes((source/r["path"]).read_bytes())
        for key,skip in [("series","missing_series"),("voice","missing_voice")]:
            if name != skip:
                (copy/cfg[key]).write_bytes((source/cfg[key]).read_bytes())
        role = manifest["assets"][0]
        if name == "wrong_roles": role["id"] = "different"
        elif name == "wrong_hash": role["sha256"] = "0"*64
        elif name == "missing_version": role.pop("version",None)
        elif name == "escaped_path": role["path"] = "../standard.png"
        (copy/cfg["manifest"]).write_text(json.dumps(manifest))
        _, result = run(name,copy)
        assert not result["sourceOK"] and not result["workbenchOK"]
        assert expected_source in result["sourceIssue"] and expected_native in result["workbenchIssue"]
        evidence[name] = result
    return dict(sourceCommit=PIN, cases=evidence)


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("output",type=Path)
    args=parser.parse_args()
    proof=compare()
    args.output.write_text(json.dumps(proof,ensure_ascii=False,indent=2)+"\n")
    print(len(proof["cases"]), "matched")
