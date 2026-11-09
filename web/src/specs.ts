export const specLabels:Record<string,string> = {assetRoot:"项目素材目录",h3Resolution:"H3 分辨率",h3Candidates:"H3 候选数",h3InstanceType:"H3 实例档位",filmFormat:"作品形态",shootingFormat:"拍摄格式",shotRhythm:"镜头节奏",postText:"画面文字",promptLanguage:"提示词语言",aspectRatio:"画幅",segmentMaxSeconds:"每段最长秒数",targetDuration:"全片时长",packaging:"默认交付范围",sound:"声音要求",style:"画面风格"};
export function frameOrientation(value: string) {
  const match = /^(\d+):(\d+)$/.exec(value);
  if (!match) return "画幅待确认";
  const width = Number(match[1]), height = Number(match[2]);
  if (!width || !height) return "画幅待确认";
  if (width === height) return "方形画幅";
  return width < height ? "竖屏短片" : "横屏短片";
}
export const specChoices:Record<string,Record<string,string>> = {
  h3Resolution:{"":"随打包器",default:"通道默认","480p":"480p","768p":"768p","1080p":"1080p"},
  h3Candidates:{"":"随打包器","1":"1 个","2":"2 个","4":"4 个"},
  h3InstanceType:{"":"随打包器",default:"默认",plus:"plus",ultra:"ultra"},
  aspectRatio:Object.fromEntries(["1:1","2:3","3:2","3:4","4:3","9:16","16:9","21:9"].map(s=>[s,s])),
  targetDuration:{story_driven:"由故事决定",over_60:"严格超过 60 秒"},packaging:{full:"整集交付",trial:"限定段落"}
};
