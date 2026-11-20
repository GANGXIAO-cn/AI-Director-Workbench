"""Run a Workbench segment against an operator-owned online H3 workflow."""
from collections import Counter
import json
import re

from .officialh3 import MEDIA, package_text

RATIOS = dict(
    zip(
        ("1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9"),
        (
            "Square",
            "Portrait Photo",
            "Photo",
            "Portrait Standard",
            "Standard",
            "Portrait Widescreen",
            "Widescreen",
            "Ultrawide",
        ),
        strict=True,
    )
)
SLOTS = {
    "image": ("ref_images.ref_image_", "LoadImage", "image"),
    "video": ("ref_videos.ref_video_", "VHS_LoadVideo", "video"),
    "audio": ("ref_audios.ref_audio_", "LoadAudio", "audio"),
}


def prompt_for_segment(text, refs, kinds, official):
    """Compile the same ordered labels as the packager for either prompt format."""
    counts = Counter()
    labels = {}
    for ref in refs:
        kind = kinds[ref["assetId"]]
        counts[kind] += 1
        media = {"image": "Picture", "video": "Video", "audio": "Audio"}[kind]
        labels[ref["label"]] = f"<{media} {counts[kind]}>"
    if official:
        for tag in MEDIA.finditer(text):
            if tag.group() not in labels.values():
                raise ValueError(f"提示词引用了未提交的媒体 {tag.group()}")
        text = package_text(text)
    return re.sub(
        r"(?:图片|视频|音频)\d+|@(?:Image|Video|Audio)\d+",
        lambda match: labels[match.group()],
        text,
    )


def overrides(graph, segment, assets, files, seed, *, probe_empty=False):
    """Override values on the online graph. Null optional ports are a trial until verified."""
    nodes = []

    def put(node, expected, field, value):
        item = graph.get(node, {})
        if item.get("class_type") != expected or field not in item.get("inputs", {}):
            raise ValueError(f"在线工作流与设置不符：{node}.{field}")
        nodes.append(dict(nodeId=node, fieldName=field, fieldValue=value))

    params = segment["params"]
    put("263", "Text", "text", segment["prompt"])
    ratio = params["ratio"]
    put("252", "ResolutionSelector", "aspect_ratio", f"{ratio} ({RATIOS[ratio]})")
    put("259", "PrimitiveFloat", "value", params["duration"])
    put("256", "RandomNoise", "noise_seed", seed)
    h3 = graph["265"]
    if h3["class_type"] != "MiniMaxH3ReferenceToVideo":
        raise ValueError("在线工作流缺少 H3 全能参考节点")
    used = set()
    counts = Counter()
    for ref in segment["refs"]:
        aid = ref["assetId"]
        kind = assets[aid]["kind"]
        prefix, cls, field = SLOTS[kind]
        port = prefix + str(counts[kind])
        counts[kind] += 1
        link = h3["inputs"].get(port)
        if not isinstance(link, list) or len(link) != 2:
            raise ValueError(f"在线工作流没有接入 {port}")
        put(str(link[0]), cls, field, files[aid])
        used.add(port)
    for port in h3["inputs"]:
        if (
            port.startswith(
                ("ref_images.", "ref_videos.", "ref_audios.", "ref_video_audios.")
            )
            and port not in used
        ):
            if not probe_empty:
                raise ValueError(f"当前在线图有额外的参考输入 {port}，请选择匹配的在线工作流")
            put("265", h3["class_type"], port, None)
    output = graph["214"]
    if (
        output["class_type"] != "VHS_VideoCombine"
        or bool(output["inputs"].get("audio")) != params["audio"]
    ):
        raise ValueError("在线工作流输出音轨与分段要求不同")
    return nodes


class RunningHub:
    def __init__(self, client, site, key):
        if (
            site not in ("https://www.runninghub.cn", "https://www.runninghub.ai")
            or not key
        ):
            raise ValueError("缺少有效 RunningHub 站点或凭证")
        self.client, self.site, self.key = client, site, key

    def post(self, route, **payload):
        res = self.client.post(
            self.site + route,
            headers={"Authorization": "Bearer " + self.key},
            **payload,
        )
        res.raise_for_status()
        return res.json()

    def graph(self, workflow):
        data = self.post(
            "/api/openapi/getJsonApiFormat",
            json=dict(apiKey=self.key, workflowId=workflow),
        )
        return json.loads(data["data"]["prompt"])

    def upload(self, name, raw):
        result = self.post(
            "/openapi/v2/media/upload/binary", files={"file": (name, raw)}
        )
        data = result.get("data") or {}
        path = data.get("fileName") or data.get("filename")
        if (
            str(result.get("code")) not in ("0", "200")
            or not isinstance(path, str)
            or not path
            or path == "None"
            or "://" in path
        ):
            raise ValueError(f"上传未获得内部文件名，返回码 {result.get('code')}")
        return path

    def submit(self, workflow, nodes, instance):
        return self.post(
            "/task/openapi/create",
            json=dict(
                apiKey=self.key,
                workflowId=workflow,
                nodeInfoList=nodes,
                instanceType=instance,
                addMetadata=False,
            ),
        )

    def query(self, task):
        return self.post(
            "/task/openapi/outputs", json=dict(apiKey=self.key, taskId=task)
        )
