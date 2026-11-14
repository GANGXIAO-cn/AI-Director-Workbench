import { ChatMarkdown } from "./ChatMarkdown";
import { ModelReply } from "./ModelReply";
import { useDirectorCommand } from "./DirectorFiles";
import { useState } from "react";
import { cmd } from "./api";
import { ErrorLine, Modal, PROVIDER_LABEL, STAGES, rno, useAction, usePoll, useQuery } from "./ui";

const previous: Record<string,string> = {script:"story",plan:"script",prompts:"plan",publish:"prompts"};
const stageName = (key:string)=>STAGES.find(([id])=>id===key)?.[1] ?? key;
type TrialTurn = {
  id:string; text:string; reply:string; reason:string; status:string; attempt:string;
  provider?:string; model?:string; effort?:string;
  basis:{label:string;stage?:string;number?:number};
};
type TrialArtifact = {stage:string; adopted_version_id:string|null; versions:{id:string;display_no:number}[]};

export function StageTrial({projectId,episodeId,stage,provider,artifacts,currentVersion,close,reload}: {
  projectId:string;episodeId:string;stage:string;provider:string;artifacts:TrialArtifact[];currentVersion:string|null;close:()=>void;reload:()=>void;
}) {
  const args = {project_id:projectId,episode_id:episodeId,stage};
  const chat = useQuery<{threads:{id:string}[];turns:TrialTurn[]}>("free_chat",args);
  const direct = artifacts.find(a=>a.stage===previous[stage]);
  const own = artifacts.find(a=>a.stage===stage);
  const focused = own?.versions.find(v=>v.id===currentVersion)?.id;
  const [source,setSource] = useState(focused ?? own?.adopted_version_id ?? direct?.adopted_version_id ?? "");
  const [message,setMessage] = useState("");
  const act = useAction();
  const submit = useDirectorCommand();
  const last = chat.data?.turns.at(-1);
  const running = !!last && ["running","queued"].includes(last.status);
  usePoll(running,chat.reload);
  const transcript = chat.data?.turns ?? [];
  const send = () => act.run(async()=>{
    await submit("send_free_chat",{...args,provider,text:message,thread_id:chat.data?.threads[0]?.id,
      previous:last?.id,source_version:source || null},[]);
    setMessage(""); await chat.reload();
  });
  return <Modal width={740} onClose={close}>
    <div className="spread"><h2>{stageName(stage)} · 讨论与试写</h2><button className="btn" onClick={close}>关闭</button></div>
    {transcript.map(t=><article className="free-turn trial-turn" key={t.id}>
      <div className="msg user">{t.text}</div>
      <ModelReply provider={t.provider} model={t.model} effort={t.effort}>
      <p className="small muted">依据 · {t.basis.stage && stageName(t.basis.stage)+" "+rno(t.basis.number!)+" · "}{t.basis.label}</p>
      {t.reply && <ChatMarkdown text={t.reply}/>}
      {t.status==="succeeded" && (!previous[stage] || direct?.adopted_version_id) &&
        <button className="btn" data-chat-kind="action" disabled={act.busy} onClick={()=>act.run(async()=>{
          await submit("create_task",{episode_id:episodeId,stage,goal:"参考导演选用的试写，结合当前正式依据另起草稿",trial_turn_id:t.id},[]);
          reload();close();
        })}>引用此回复起草阶段新稿</button>}
      {t.reason && <p data-chat-kind="action">{t.reason}</p>}
      {t.id===last?.id && (running ? <button className="btn" onClick={()=>act.run(async()=>{
        await cmd("stop_free_chat",{project_id:projectId,turn_id:t.id,expected_attempt:t.attempt});await chat.reload();
      })}>停止本次回复</button> : ["failed","waiting_quota","stopped"].includes(t.status) &&
        <button className="btn" disabled={act.busy} onClick={()=>act.run(async()=>{
          await cmd("retry_free_chat",{project_id:projectId,turn_id:t.id,expected_attempt:t.attempt});await chat.reload();
        })}>按原输入重试</button>)}
      </ModelReply>
    </article>)}
    <div className="col">
      <label>本次依据 <select value={source} onChange={e=>setSource(e.target.value)}>
        <option value="">本集开工资料与本条要求</option>
        {artifacts.flatMap(a=>a.versions.map(v=><option key={v.id} value={v.id}>{stageName(a.stage)} {rno(v.display_no)} · {a.adopted_version_id===v.id?"已采用":"未采用"}</option>))}
      </select></label>
      <label>讨论或试写要求<textarea value={message} onChange={e=>setMessage(e.target.value)}/></label>
      <button className="btn primary" disabled={!chat.data || !message.trim() || act.busy || running} onClick={send}>
        {running?"等待回复":"发送给 "+(PROVIDER_LABEL[provider] ?? provider)}</button>
    </div>
    {chat.error && <p className="error">{chat.error}</p>}<ErrorLine error={act.error}/>
  </Modal>;
}
