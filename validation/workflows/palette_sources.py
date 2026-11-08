"""Compare every card's display payload to the mounted archive, and open a real card from a directory."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"server"))
from workbench.resources import pack_files
from workbench.palette_inputs import snapshot_inputs
from workbench.service import Workbench


def verify():
    temp=Path(tempfile.mkdtemp(prefix="adw-palette-sources-"))
    wb=Workbench(temp/"data",{})
    bundle=wb._resource_path("palette")
    first=wb.browse_palettes()
    listing=[c for page in range(1,(first["total"]+23)//24+1) for c in wb.browse_palettes(page=page)["rows"]]
    assert len(listing)==len({c["id"] for c in listing})==first["total"]==200
    records=[]
    with pack_files(bundle) as (entries,read):
        for card in listing:
            detail=wb.palette_detail(card["id"])
            base=detail["root"]+"/"
            colors=json.loads(read(base+"assets/color_palettes/palette.json"))["palette"]
            assert card["palette"]==colors and len(colors["primary"])==6 and len(colors["emotional"])==4
            source_paths=[f for f in ("assets/references/sources.md","assets/references/reference_sources.md",
                                      "assets/film_stills/_film_stills_source.md") if base+f in entries]
            assert source_paths and detail["sources"] and all(read(base+f).decode("utf-8-sig") in detail["sources"] for f in source_paths)
            assert len(detail["prompts"])==4 and detail["stills"] and detail["cover"]
            for name,digest in detail["fileHashes"].items():
                assert sha256(read(base+name)).hexdigest()==digest
            for name,body in detail["prompts"].items():
                assert body==read(base+name).decode("utf-8-sig")
            assert all(base+name in entries for name in detail["stills"]+[detail["cover"]])
            inputs=snapshot_inputs(detail)
            source_body=read(base+"色卡主文档.md").decode("utf-8-sig").splitlines()
            light_at=source_body.index("### 光影特征")
            light_lines=[]
            for line in source_body[light_at+1:]:
                if line.startswith(("#","---")):
                    break
                if line.strip():
                    light_lines.append(line)
            assert light_lines and all(line in inputs["visual"] for line in light_lines)
            assert all(color["hex"] in inputs["visual"] for color in [*colors["primary"],*colors["emotional"]])
            excerpts={}
            for kind, filename, title in (("image","conversational_prompts.md","通用描述段"),("video","video_prompts.md","MiniMax H3")):
                src=inputs[kind+"Source"]
                assert src["path"]=="prompts/"+filename and title in src["heading"]
                lines=read(base+src["path"]).decode("utf-8-sig").splitlines(keepends=True)
                excerpt="".join(lines[src["firstLine"]-1:src["lastLine"]]).rstrip("\r\n")
                assert inputs[kind]==excerpt and excerpt.strip() and "Seedance" not in excerpt
                assert ("光线处理" if kind=="image" else "核心创意") in excerpt
                excerpts[kind]=dict(src,sha256=sha256(excerpt.encode()).hexdigest())
            records.append(dict(id=card["id"],director=card["name"],category=card["category"],sources=source_paths,
                                lightingLines=len(light_lines),stageExcerpts=excerpts,stillFiles=len(detail["stills"]),sourceHashes=detail["fileHashes"]))
            if card["id"]=="001":
                original=detail
                sample=temp/"directory"
                for name in entries:
                    if name.startswith(base):
                        target=sample/name
                        target.parent.mkdir(parents=True,exist_ok=True)
                        target.write_bytes(read(name))
    wb.mount_resource("palette",str(sample))
    actual=wb.palette_detail("001")
    for key in ("palette","body","stills","sources","fileHashes","prompts","cover","collectionGuide"):
        assert actual[key]==original[key],key
    assert wb.palette_image("001",original["cover"])==(sample/original["root"]/original["cover"]).read_bytes()
    return dict(bundle=bundle.name,cards=len(records),categories=len(first["categories"]),primaryColorsPerCard=6,
                emotionalColorsPerCard=4,promptTypesPerCard=4,stills=sum(r["stillFiles"] for r in records),
                sourceFileCounts=dict(Counter(f for r in records for f in r["sources"])),
                directoryComparison="001",records=records)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("output",type=Path)
    args=parser.parse_args()
    report=verify()
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print({k:v for k,v in report.items() if k!="records"})
