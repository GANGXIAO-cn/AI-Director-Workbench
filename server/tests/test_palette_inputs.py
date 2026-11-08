import json
from hashlib import sha256

import pytest
from helpers import Env
from workbench.errors import Conflict, Rejected
from workbench.palette_inputs import IMAGE, VIDEO, LEGACY_IMAGE


def test_directory_reselection_and_actual_stage_inputs(tmp_path):
    env = Env(tmp_path)
    wb = env.wb
    pid, eid = env.new_episode()
    env.adopt_story(eid)
    env.script(eid)
    env.save_adopt(eid, "plan", env.plan86({"G01":[8000]}))
    library = tmp_path/"mounted"
    root = library/"导演卡"
    palette = root/"assets/color_palettes/palette.json"
    palette.parent.mkdir(parents=True)
    palette.write_text(json.dumps(dict(director=dict(id="001",name_zh="测试导演",name_en="Director",category="测试"),
                                       palette=dict(primary=[dict(name_zh="夜蓝",hex="#102030",ratio=.4)],
                                                    emotional=[dict(hex="#BB2211",emotion="警觉")]))))
    body = root/"色卡主文档.md"
    body.write_text("## 核心风格一句话\n> 冷光构图\n## 色板\n### 光影特征\n- 旧光线\n---\n## 其他\n无关文字")
    (root/"prompts").mkdir()
    (root/IMAGE).write_text("## 一、通用描述段（整段复制）\n```text\n冷色窗光，空间留白。\n```\n无关示例")
    (root/VIDEO).write_bytes("## 三、Seedance 2.0\r\n无关说明\r\n## 四、MiniMax H3（海螺）\r\n```text\r\nH3 参考\r\n时间窗\r\n```\r\n## 五、Runway".encode())
    wb.mount_resource("palette",str(library))
    old = wb.save_palettes(pid,["001"], expected_hashes=[])[0]
    batch = wb.create_batch(eid,"fake-image","generate",
                            [dict(prompt="站台雨棚", inputs=[], target=dict(name="雨棚", category="scene"))], size="64x64",command_id="light-before")
    spec = wb.batch_view(batch["batchId"])["spec"]
    assert "旧光线" in spec["items"][0]["prompt"] and "冷色窗光，空间留白。" in spec["items"][0]["prompt"]
    assert "无关" not in spec["items"][0]["prompt"] and "videoSource" not in spec["palettes"][0]
    body.write_text(body.read_text().replace("旧光线","新光线"))
    renewed = wb.save_palettes(pid,["001"],expected_hashes=[old["sha256"]])[0]
    assert "新光线" in renewed["visual"] and "旧光线" not in renewed["visual"]
    assert "40%" in renewed["visual"] and "警觉 #BB2211" in renewed["visual"]
    assert renewed["sha256"]!=old["sha256"] and renewed["fileHashes"][body.name]==sha256(body.read_bytes()).hexdigest()
    with pytest.raises(Conflict):
        wb.save_palettes(pid,[],expected_hashes=[old["sha256"]])
    task = wb.create_task(eid,"prompts","按本集要求起草")
    manifest = wb.run_manifest(task["runId"])["manifest"]
    entry, = [i for i in manifest["inputs"] if i["role"]=="visual_reference"]
    actual = json.loads(wb.store.read_blob(entry["sha256"]))
    assert actual["video"]=="H3 参考\r\n时间窗" and actual["videoSource"]["firstLine"]==5
    assert "image" not in actual and "imageSource" not in actual and "新光线" in actual["visual"]
    assert "旧光线" in wb.batch_view(batch["batchId"])["spec"]["palettes"][0]["visual"]
    (root/VIDEO).write_text("## 三、Seedance 2.0\n无 H3 样本")
    with pytest.raises(Rejected,match="H3"):
        wb.save_palettes(pid,["001"])
    assert wb.project_palettes(pid)==[renewed]


def test_custom_image_before_and_after_format_update(tmp_path):
    env = Env(tmp_path)
    pid, _ = env.new_episode()
    wb = env.wb
    sample = "用户写的光线。\n"*230
    cid = wb.add_palette("手写色卡","倾斜构图",["#ABBCCD"],sample,"手写视频")["id"]
    fresh = wb.save_palettes(pid,[cid])[0]
    assert fresh["image"]==sample and fresh["imageSource"]["path"]==IMAGE
    root = wb.store.root/"director-palettes"/cid
    (root/IMAGE).rename(root/LEGACY_IMAGE)
    old = wb.save_palettes(pid,[cid])[0]
    assert old["image"]==sample and old["imageSource"]["path"]==LEGACY_IMAGE
    assert old["video"]=="手写视频"
