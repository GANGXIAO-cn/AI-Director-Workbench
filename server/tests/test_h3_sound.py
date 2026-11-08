import pytest

from workbench import readable
from workbench.archivechecks import archived_sound


def sample(strategy):
    headings = readable.GRID if strategy == "grid" else readable.FLEX
    parts = dict.fromkeys(headings, "沿用开场。")
    motion, sound = ("镜头描述：", "声音与音乐：") if strategy == "grid" else ("【镜头与动作】", "【声音与文字】")
    parts[motion] = "00:00.000–00:08.000\n起镜，雨滴打在玻璃上。"
    parts[sound] = "overall_soundscape: 雨声与脚步\n售票员的台词\nnon_diegetic_music: N/A"
    return dict(id="01", reference_strategy=strategy, prompt_format="readable_v3", prompt="segment.txt"), readable.render(parts)


@pytest.mark.parametrize("strategy", ["grid", "flex"])
def test_editor_changes_only_music_and_handles_legacy_newlines(strategy):
    seg, original = sample(strategy)
    text = original.replace("\n", "\r\n")
    after = readable.edit_sound_fields(text, strategy, "弦乐")
    assert after == text.replace("non_diegetic_music: N/A", "non_diegetic_music: 弦乐")
    readable.validate(dict(seg, txt=after, sound={"bgm": True}), strategy, 8000)
    assert readable.edit_sound_fields(after, strategy, "N/A") == text
    old = text.replace("overall_soundscape: 雨声与脚步", "overall_soundscape:\r\nN/A")
    with pytest.raises(ValueError, match="full_silence"):
        readable.validate(dict(seg, txt=old), strategy, 8000)
    lifted = readable.edit_sound_fields(old, strategy, "N/A")
    assert "overall_soundscape" not in lifted and "\r\nN/A" not in lifted
    silent = readable.edit_sound_fields(text, strategy, "N/A", "画面保留票价牌。")
    assert "售票员的台词" not in silent and "雨声与脚步" not in silent and "画面保留票价牌。" in silent
    readable.validate(dict(seg, txt=silent, sound={"silent": True}), strategy, 8000)
    with pytest.raises(ValueError, match="发声台词"):
        readable.validate(dict(seg, txt=silent.replace("起镜，", "起镜，<d>Hello!</d>"), sound={"silent": True}), strategy, 8000)


@pytest.mark.parametrize("strategy", ["grid", "flex"])
def test_sound_diagnoses_match_in_authoring_and_frozen_receipt(strategy):
    seg, text = sample(strategy)
    marker = "non_diegetic_music: N/A"
    class Files:
        def read(self, path):
            return modified.encode()
    for modified, phrase in [
        (text.replace(marker, ""), "漏写"),
        (text.replace(marker, marker+"\n"+marker), "重复"),
        (text.replace(marker, "non_diegetic_music:\nN/A"), "同行"),
        (text.replace(marker, "non_diegetic_music: 弦乐"), "要求"),
        (text.replace(marker, marker+"\noverall_soundscape:\nN/A"), "full_silence"),
        (text.replace(marker, "").replace("起镜，", marker+"\n起镜，"), "错栏"),
    ]:
        with pytest.raises(ValueError, match=phrase):
            readable.check_h3_sound(seg, modified)
        result = archived_sound(Files(), {"segments":[seg], "h3_rules":"v1"})
        assert result["state"] == "failed" and phrase in result["details"]["segments"][0]["message"]
    modified = text
    result = archived_sound(Files(), {"segments":[seg]})
    assert result["state"] == "failed" and result["details"]["segments"][0]["state"] == "passed"


def test_export_rechecks_seg_text_against_generation_settings(tmp_path):
    from workbench.delivery import verify_sound_fields

    seg, text = sample("grid")
    (tmp_path / seg["prompt"]).write_text(text)
    data = {"segments":[seg], "h3_rules":"v1"}
    files = {seg["prompt"]:"listed"}
    verify_sound_fields(tmp_path, data, files)
    seg.update(native_bgm="generate", generation_settings={"audio":False})
    with pytest.raises(ValueError, match="填写具体配乐描述"):
        verify_sound_fields(tmp_path, data, files)
    (tmp_path / seg["prompt"]).write_text(readable.edit_sound_fields(text, "grid", "弦乐"))
    with pytest.raises(ValueError, match="打开生成声音"):
        verify_sound_fields(tmp_path, data, files)
    seg.update(native_bgm="none", full_silence=True, generation_settings={"audio":True})
    (tmp_path / seg["prompt"]).write_text(readable.edit_sound_fields(text, "grid", "N/A", ""))
    with pytest.raises(ValueError, match="全段静音"):
        verify_sound_fields(tmp_path, data, files)
    seg["generation_settings"]["audio"] = False
    verify_sound_fields(tmp_path, data, files)
