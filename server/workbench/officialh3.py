"""Translate only H3 media references at the package boundary."""
import re

FORMAT = "h3_official"
KINDS = {"Picture": "图片", "Video": "视频", "Audio": "音频"}
MEDIA = re.compile(r"<(Picture|Video|Audio) (\d+)>")
LABEL = re.compile(r"(图片|视频|音频)(\d+)")


# H3 preserves original spoken lines and visible lettering in quotes.
QUOTED_LITERAL = re.compile(r""""[^"\n]*"|“[^”\n]*”|‘[^’\n]*’|「[^」\n]*」|『[^』\n]*』|(?<!\w)'[^'\n]+'(?!\w)""")
HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def has_unquoted_chinese(text):
    return bool(HAN.search(QUOTED_LITERAL.sub("", text)))


def package_text(text):
    return MEDIA.sub(lambda match: KINDS[match[1]] + match[2], text)


def native_label(label):
    match = LABEL.fullmatch(label)
    if match:
        kind = next(kind for kind, prefix in KINDS.items() if prefix == match[1])
        return f"<{kind} {match[2]}>"
    return label


def set_audio(text, change, silent):
    if silent:
        text = re.sub(r"(?ms)^overall_soundscape:[^\n]*\n?.*?(?=^non_diegetic_music:)", "overall_soundscape:\nN/A\n\n", text)
    if "bgm" in change or silent:
        music = (change.get("music_description") or "").strip() if change.get("bgm") else "N/A"
        if not music:
            raise ValueError("请填写本段配乐描述")
        text = re.sub(r"(?ms)^non_diegetic_music:.*\Z", lambda _: "non_diegetic_music:\n" + music, text)
    return text
