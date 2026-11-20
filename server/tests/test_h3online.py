from types import SimpleNamespace

import pytest

from workbench.h3online import RunningHub, overrides, prompt_for_segment


def test_submit_uses_online_workflow_not_local_graph():
    calls = []

    class Client:
        def post(self, url, **kwargs):
            calls.append((url, kwargs["json"]))
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"code": 0, "data": {"taskId": "123"}},
            )

    hub = RunningHub(Client(), "https://www.runninghub.ai", "sample")
    nodes = [dict(nodeId="263", fieldName="text", fieldValue="action")]
    assert hub.submit("456", nodes, "plus")["data"]["taskId"] == "123"
    _, body = calls[0]
    assert (
        body["workflowId"] == "456"
        and body["nodeInfoList"] == nodes
        and "workflow" not in body
    )


def test_unused_slots_do_not_inherit_workflow_defaults():
    def node(cls, **inputs):
        return dict(class_type=cls, inputs=inputs)

    graph = {
        "263": node("Text", text=""),
        "252": node("ResolutionSelector", aspect_ratio=""),
        "259": node("PrimitiveFloat", value=8),
        "256": node("RandomNoise", noise_seed=12),
        "265": node(
            "MiniMaxH3ReferenceToVideo",
            **{
                "ref_images.ref_image_0": ["51", 0],
                "ref_images.ref_image_1": ["49", 0],
                "ref_videos.ref_video_0": ["27", 0],
            }
        ),
        "51": node("LoadImage", image="old"),
        "214": node("VHS_VideoCombine", audio=["115", 0]),
    }
    seg = dict(
        params=dict(audio=True, ratio="9:16", duration=8),
        refs=[dict(assetId="x")],
        prompt="cat",
    )
    args = (graph, seg, {"x": {"kind": "image"}}, {"x": "api/cat.png"}, 13)
    with pytest.raises(ValueError, match="额外"):
        overrides(*args)
    nodes = overrides(*args, probe_empty=True)
    assert {x["fieldName"] for x in nodes if x["fieldValue"] is None} == {
        "ref_images.ref_image_1",
        "ref_videos.ref_video_0",
    }
    assert {"nodeId": "51", "fieldName": "image", "fieldValue": "api/cat.png"} in nodes


def test_prompt_labels_follow_per_type_upload_order():
    refs = [
        dict(assetId="a", label="图片1"),
        dict(assetId="b", label="音频1"),
        dict(assetId="c", label="图片2"),
    ]
    kinds = {"a": "image", "b": "audio", "c": "image"}
    assert prompt_for_segment("图片2和音频1", refs, kinds, False) == "<Picture 2>和<Audio 1>"
    assert prompt_for_segment("<Picture 2>", refs, kinds, True) == "<Picture 2>"
    with pytest.raises(ValueError, match="未提交"):
        prompt_for_segment("<Picture 3>", refs, kinds, True)
