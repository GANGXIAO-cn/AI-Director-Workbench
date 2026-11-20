# 工作台精剪包

生产入口读取本集已采用的分段原片、各段审片剪点、制作交接和已返回的配乐。工作台在精剪过程目录内部合并时间坐标，随后调用原技能体检、分析节拍、按剪辑表出片和实测质检。PUGGY ROOM 使用自己的品牌片尾。正文中的“粗剪稿”也可以指独立接收的一条剪辑素材。工作台直接从已采用的分段进入精剪。

## 随包文件

- 原技能的 SKILL.md、README.md、交接参考、接收模板、11 个制作脚本，另有可运行的分集剪辑表示例。
- 品牌片尾工程包含全部图层、商品图、参考设计、字体、两种画幅的音频及三个商品的六个成品。三个补充字体分别用于中文报告、监控画面和可编辑字幕。
- CapCut 9.x 的明文结构样板含视频、音频、文字轨和素材登记结构。导出时将所用媒体和字幕字体带入草稿。安装到草稿库沿用原技能的显式安装入口。
- 原目录的两份历史备份按原名存档。日常生产使用顶层 finecut 下的脚本。
- runtime/darwin-arm64 含 FFmpeg、ffprobe、许可证和 Python 3.11 对应的离线 NumPy、Pillow 安装包。离线轮子的共同目标是 macOS 14 及以上的 Apple Silicon。FFmpeg 来自 imageio-ffmpeg 的 macOS ARM 静态构建，ffprobe 来自 @ffprobe-installer/darwin-arm64。其他平台需提供适用的 FFmpeg、ffprobe、Python、NumPy、Pillow。
- ZIP 使用标准 Unix 执行权限。请用系统归档工具解压，保留目录结构与媒体工具的执行位。

## 独立运行

需要可用的 Python 3.11（含 pip）。工作台及独立使用者均可从任何位置运行包内 launch.py。它优先使用随包媒体工具，缺少 Python 依赖时在上述平台从离线轮子安装到用户缓存。可用 ADW_FINECUT_CACHE 指定缓存目录。

```sh
python3 "/path/to/milo-leo-finecut/launch.py" finecut/analyze.py "/path/to/source.mp4" "/path/to/work/体检"
python3 "/path/to/milo-leo-finecut/launch.py" finecut/beats.py "/path/to/bgm.wav" "/path/to/work/bgm_beats.json"
python3 "/path/to/milo-leo-finecut/launch.py" finecut/qa.py "/path/to/work"
python3 "/path/to/milo-leo-finecut/launch.py" finecut/capcut_export.py build "/path/to/work" "本集精剪"
```

本包独立生成草稿。需要安装时在目标电脑关闭 CapCut，再运行同一脚本的 install 子命令。ADW_CAPCUT_ROOT 可指定已有草稿库。工作台的模型登录由目标设备提供。
