"""Reviewed production passages for short-film v5. Line ranges refer to the pinned video skill."""
from hashlib import sha256
from pathlib import Path

VIDEO = "milo-leo-short-video"
SHARED = [
    (VIDEO,"scene-assets","1-38","分镜、分段与空间锁定","plan space"),
    (VIDEO,"scene-assets","40,44-60","资产需求与实际输入映射","plan assets"),
    (VIDEO,"scene-assets","62-70","阶段完成条件","plan delivery"),
    (VIDEO,"scene-assets","72-78","关键结构与策略切换","plan space assets"),
    (VIDEO,"scene-assets","80-88","联合表的主要表演与观看窗口","plan"),
    (VIDEO,"camera-rhythm","1-20,36-68,72-90","设备视点、观看窗口、声音与剪点","plan"),
    (VIDEO,"camera-rhythm","92-96","原速回放与短接触返修","plan delivery"),
    (VIDEO,"camera-rhythm","98-106","摄影与动作中的表情","plan"),
    (VIDEO,"voice-dialogue","1,5,7","声线身份与当次表演","script prompts"),
    (VIDEO,"voice-dialogue","11-15,19-23,25-31","对白时窗与动作接续","script prompts"),
    (VIDEO,"voice-dialogue","33-45","乐队演奏与声音完整性","script prompts"),
    (VIDEO,"prompt-format","1-3,18,22-35","独立提示词的十类组成","prompts comparison"),
    (VIDEO,"prompt-format","37-45,49","提取本段的空间与因果","prompts"),
    (VIDEO,"prompt-format","51,55-57","现行制作基准与角色短块","prompts comparison"),
    (VIDEO,"prompt-format","59-108,112-120,124-134","参考、起幅、平铺时间段与声音","prompts"),
    (VIDEO,"prompt-format","137-141","通读与画布实际绑定","prompts comparison"),
]
PROJECT = [
    (VIDEO,"scene-assets","42","系列镜头中的商品来源","plan assets"),
    (VIDEO,"camera-rhythm","70","系列平行跟拍与反应切点","plan"),
    (VIDEO,"voice-dialogue","3,7,9,17,21,45","系列声音基准与台词示例","prompts script"),
    (VIDEO,"prompt-format","20,31,53-54,77,122","系列提示词基准与声音约束","prompts comparison"),
    (VIDEO,"prompt-format","47,110","系列推车开场与拉竿表情示例","prompts"),
]
EDITS = {
    "设备视点、观看窗口、声音与剪点":{"紧凑、清楚、有动作重音的人物喜剧":"符合本项目类型、清楚且有动作重音的叙事"},
    "摄影与动作中的表情":{"两猫":"同场角色","不持续龇牙、不用说话口型":"嘴部表现须与角色及本段对白设定相符"},
    "声线身份与当次表演":{
        "人类对白用简短自然英语、通用美式口音。":"对白使用项目指定的语言和口音。",
        "女主人基准以声音档案为准，例如年轻成年女性中音区、清晰略带轻微沙哑、口语化短句与明确句尾。":"具体角色声线以项目声音档案为准。"},
    "对白时窗与动作接续":{
        "日常对白从档案145–165词/分钟考虑":"日常对白从项目声音档案的语速范围考虑",
        "MILO、LEO 不编对白号":"无发声的角色不编对白号","[English]":"[Language]",
        "猫无对白":"角色无对白"},
    "乐队演奏与声音完整性":{
        "猫仍无对白/唱词，演奏与情绪不变成拟人配音。":"按本段设定处理对白、唱词和演奏。",
        "猫的无对白/唱词/拟人配音/内心独白从视频提示词标准.json完整引用cats_silent；不要将“猫无对白”误写成没有自然呼吸和动作声。":"从项目的视频提示词标准完整引用适用的声音约束；无对白的角色仍可有自然呼吸和动作声。"},
    "独立提示词的十类组成":{
        "写实画面风格":"项目规定的画面风格",
        "猫的无对白/唱词/拟人配音/内心独白；人类实际对白的英语原文、美式口音":"项目规定的角色发声边界；实际对白的项目语言原文、口音"},
    "提取本段的空间与因果":{"写 MILO、LEO、LUCI（女主人）等已确认名字":"写项目已确认的角色名字","让猫与":"让角色与"},
    "现行制作基准与角色短块":{"从config.json指定的现行制作基准":"从项目指定的现行制作基准"},
    "参考、起幅、平铺时间段与声音":{"有猫时完整cats_silent":"按本段角色完整引用适用的声音约束","写英语/固定声线":"写项目指定语言/固定声线","两猫反应":"同场角色反应"},
    "H3 原文查阅表":{"../../h3-prompt-writing/references/":"../guides/"},
}

# Full files are preserved separately for consultation. Only the applicable language
# and reference conventions accompany prompt writing and the second review pass.
GUIDES = [
    ("base-en.txt","2cfebc096a6e08370f288d468d90b60f7f9bcb938f94bf090816e910e48e75fc",
     "H3 关键帧、发声与配乐原文",[(50,70),(125,142),(152,154),(160,162)]),
    ("ref-en.txt","1e574f356716ad55612247ffb7bbccbcdb484ad96599d63c7dca1af186b1fab7",
     "H3 参考图、音轨与逐镜使用原文",[(60,74),(108,119),(244,256),(258,278)]),
]


def add_guides(files,stages,expected):
    for name,digest,title,ranges in GUIDES:
        source = Path(__file__).with_name("guides")/name
        raw = source.read_bytes()
        if sha256(raw).hexdigest() != digest:
            raise ValueError(f"H3 指南与冻结原件指纹不符：{name}")
        lines = raw.decode().splitlines()
        selected = "\n\n".join("\n".join(lines[start-1:end]) for start,end in ranges)+"\n"
        origin = dict(path=f"h3-prompt-writing/references/{name}",sha256=digest)
        for key,path,data,ref,uses in [
            (name,"guides/"+name,raw,origin,["h3_guides"]),
            (title,"sections/"+title+".md",selected.encode(),dict(origin,lines=",".join(f"{a}-{b}" for a,b in ranges)),["prompts","comparison"]),
        ]:
            files[key] = dict(path=path,sha256=sha256(data).hexdigest(),source=ref)
            expected[path] = data
            for stage in uses:
                stages.setdefault(stage,[]).append(key)
