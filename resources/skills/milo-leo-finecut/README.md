# MILO/LEO 共用精剪工具

每天粗剪 → 精剪都用这一套。机制都在 `finecut/` 里，每期只写一个 `episode.py`，里面是剪辑表和配乐编排。

**Python**：使用工作台当前解释器，部署见 [PORTABLE.md](PORTABLE.md)。

## 流程
```
P=python3; T=<本技能目录>/finecut; E=<episode>/过程稿/精剪
$P $T/analyze.py 粗剪稿/MMDD/xxx.mp4 $E/体检
# 实际核对草稿与当前导出对应后才可使用：
# $P $T/analyze.py <粗剪.mp4> $E/体检 --draft <草稿目录> --draft-verified --draft-verification-note "实际核对依据"
$P $T/beats.py 粗剪稿/MMDD/BGM.MP3 $E/bgm_beats.json
#   看 体检/体检报告.md、shots_*.jpg，写 $E/episode.py（参考 1018 那一期）
$P $E/episode.py                  # 默认渲染一版成片 + timeline.json
$P $T/qa.py $E                    # 质检报告.md、qa_timeline.png、qa_cuts.jpg
$P $T/capcut_export.py build $E "<草稿名>"     # 生成 CapCut 草稿并自检
$P $T/capcut_export.py install $E "<草稿名>"   # 先在 CapCut 里 Cmd+Q 退出
```

## 各模块
|文件|做什么|
|---|---|
|`assemble.py`|分段粗剪拼接（2026-11-01 起取代各期手写 concat.sh）：按序号拼成一条粗剪，竖素材铺满 1080×1920、横素材铺满 1920×1080，转 30fps、每段截整帧；同序号多版本不自行挑，用 `--files` 写明。段落版本/指纹/起止写 `segments.json`|
|`analyze.py`|粗剪体检，输出：镜头切点（默认只按视频检测；人工核对关联后的CapCut主轨边界才用于辅助）、≤5 帧短镜头、单帧闪、黑帧、糊帧、疑似爆音（孤立削波突发声）、疑似重复/拖沓段（同一动作隔几秒重演，用 1017 那段已知拖沓校准过）、跨镜头相似对、每镜头联系表|
|`beats.py`|BGM 速度、拍点、小节头、逐小节能量。拍点相位用高分辨率起音峰精修到约 3ms|
|`render.py`|`Timeline`（剪辑表用粗剪时间，自动算成片时间）、`snap_cuts`（剪点吸附 ±3 帧内的拍点，不越过镜头边界）、效果（fx：`speed` 变速/速度坡、`zoom`+`zoom0/zt/ease` 推镜/瞬推/停住、`shake` 冲击震屏、`cctv` 叠字，参考 2026-10-20-pillow-fort-war）、`audio_fixes` 按时间段修画内声、分轨混音（画内声/BGM/音效，BGM 侧链让位）、先限峰再两遍响度标准化|
|品牌动效字幕|**仅在用户要求字幕时加，默认不加**（2026-10-28 用户定）：`brand_captions.py` 的 `burn_brand_captions`。样式取自 Figma 品牌片尾：Bricolage 大字、奶油字墨描边硬投影，`*关键词*` 上黄胶囊，逐词回弹弹出，`fx` 可设 shake/slam。CAPTIONS 每条写 `text`（纯文本，进 SRT/草稿）+ `markup`（带星号）。CapCut 草稿只带静态样式（参考 2026-10-28-goat-parade）|
|小票卡字幕|用户要求字幕“和以前形式不同”时用（2026-10-31 首用）：`ticket_captions.py` 的 `burn_ticket_captions`，接口同品牌动效字幕，另可写 `kicker`（卡片角上的黄标签）、`x`、`angle`。奶油圆角卡+墨描边硬投影、墨字左对齐 92px，`*关键词*` 荧光笔划出，整卡倾角回弹入场，fx 为 stamp/shake（参考 2026-10-31-giant-pizza）|
|品牌片尾|每期默认都加（2026-10-28 起）。16:9 长片由 `fit_outro` 换成 10 秒 YouTube 结束画面（2026-11-01，Figma 300:587，`make_sting.py --landscape`）：标题 Another little MILO?、两个视频位、MILO 头像订阅圈、商品卡、MILO 探头，开头 1 秒入场后静止 9 秒供 YouTube 元素覆盖。`mux`/`save_timeline` 默认用 `outro_clip()`：取 `品牌片尾/out/` 里的 2 秒片尾，商品位按 ISO 周在 tote/mug/tee 之间轮换。`outro=None` 不加片尾，`outro=outro_clip("mug")` 指定商品，两处要一致。片尾接在成片末尾，`timeline.json` 写 `outro` 后 `capcut_export.py` 在视频轨末尾加一段。片尾不进 Timeline、不参与响度标准化（提示音约 -18 LUFS）。片尾源在 Figma 网站文件 283:586，改了设计就重新导出 `src/ref_final_frame.png` 再跑 `make_sting.py`|
|`qa.py`|成片质检：逐段画面和粗剪比对、复查成片切点和闪帧、实测声音事件和音乐断点的帧偏差、剪点离拍点的帧数、响度、时间线图（波形+剪点+拍点+事件）、剪点前后帧图|
|`capcut_export.py`|把时间线做成 CapCut 草稿：以随包的 CapCut 9.x 明文结构模板克隆，媒体打包进草稿的 Resources，登记前备份 root_meta_info.json，不覆盖同名草稿。2026-10-18 在 CapCut 9.5.0 实测能打开|

## 画幅（2026-11-01 起支持 16:9）
成片画幅跟随粗剪：`render_video` 开头按粗剪宽高设 `common.FRAME`，推镜/震屏、两种字幕、片尾、联系表和 CapCut 画布都读它；`timeline.json` 写 `frame`。竖屏一切照旧。横屏时品牌字幕字号 96px（竖屏 128）、折行宽 1500，小票字幕字号不变、折行宽 1300；字幕 `y` 按 1080 高写。首个横屏期：2026-11-01 大镖客2。

## 约定
- 剪辑表永远用**粗剪时间**写，注释写清删掉的段落和原因。
- 事件（撞击、音乐断点、音乐重进）写进 `EVENTS`，让 qa 实测对齐。我没法试听，这是唯一的客观核对。
- 接收与外部调整见[流程](references/intake-handoff.md)；制作意图交接不要求与实际粗剪相同。新体检登记源版本，同名/同时长的草稿仅为候选，`--draft`本身不启用其边界。渲染/质检核对已有粗剪指纹，版本变化必须重核定位与EDL。
- 默认从粗剪mp4取素材，用户提供原片时先核查实际可访问性及工具能力；已混入BGM的单轨视频不能凭再导出变成真正无BGM版。
- 这台机器的 ffmpeg 在 `-frames:v` 截帧时配 `apad` 会卡死（0% CPU 不退出），变速段补音频用“同文件第二路输入多读 0.2s + atrim”，别用 apad；也别用输出 `-t`，会截掉慢放段末帧。
