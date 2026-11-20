import { useState } from "react";
import { useDirectorCommand } from "./DirectorFiles";
import { ErrorLine, useAction, useQuery } from "./ui";

export const FIRST_DRAFT: Record<string,{label:string;goal?:string}> = {
  world: {label:"根据已采用稿起草世界规则"},
  treatment: {label:"根据已采用稿起草分场"},
  script: {label:"按采用大纲起草完整剧本"},
  plan: {label:"按剧本设计镜头与生成段",goal:"按已采用的剧本和素材编排镜头、表演与生成段，明确每个镜头的动作、时间窗口和前后接点。"},
  prompts: {label:"逐段起草本集 H3 提示词",goal:"按已采用的分镜顺序，一次起草一段 H3 提示词，每段完成后保存，再继续下一段。沿用所给逐段输入编号、固定块与时间窗口，写清起幅、表演、机位、反应、交接和声音。"},
  stress: {label:"根据已采用剧本生成审查报告"},
  asset_design: {label:"根据已采用稿起草素材设计", goal:"结合已采用剧本、角色、世界规则和适用的审查报告，按场次整理所需角色、场景和道具。注明可复用素材、状态变化、待准备项及出图提示词，形成素材设计稿。"},
  publish: {label:"根据已采用稿起草发布文案", goal:"按已采用的制作内容和项目发布设置起草发布文案，填写项目要求的标题、正文、标签及适用字段。"},
};

export function FirstDraft({stage,view,art,reload}:{stage:string;view:any;art:any;reload:()=>void | Promise<void>}) {
  const act=useAction();
  const goals=useQuery<Record<string,string>>("standard_goals");
  const submit=useDirectorCommand();
  const [includeImages,setIncludeImages]=useState(true);
  const config=stage==="script" && view.kickoff?.sourceIntent==="locked_screenplay"
    ? {label:"按定稿整理05剧本",goal:"按导入定稿整理完整剧本，保留原情节与准确对白。"}
    : FIRST_DRAFT[stage];
  if (!config || view.skippedStages[stage] || art?.versions.length) return null;
  // Publishing can be entered from Delivery before its artifact is opened.
  if (!art && !(stage==="publish" && view.artifacts.some((a:any)=>a.stage==="prompts" && a.adopted_version_id))) return null;
  const goal=config.goal ?? goals.data?.[stage];
  const pending=art?.tasks.some((t:any)=>t.state==="clarifying" || t.runs.some((r:any)=>["queued","running","waiting_quota"].includes(r.status)));
  if (pending) return null;
  return <section className="mcard first-draft" data-chat-kind="action">
    <b>{stage==="asset_design"?"列出本集需要的素材清单":"标准起草提示词"}</b>
    {stage==="asset_design" && <p>这一步只写文字，不出图，不消耗出图额度。</p>}
    <p>{goal}</p>
    {stage==="prompts" && <label><input type="checkbox" checked={includeImages} onChange={e=>setIncludeImages(e.target.checked)}/>同时发送本集已绑定的参考原图</label>}
    <button className="btn primary" disabled={!goal || act.busy || art?.editState==="protected"} onClick={()=>act.run(async()=>{
      await submit("create_task",{episode_id:view.episode.id,stage,goal,workspace_revision:art?.revision,...(stage==="prompts"?{prompt_images:includeImages}:{})},[]);
      await reload();
    })}>{config.label}</button>
    <ErrorLine error={act.error}/>
  </section>;
}
