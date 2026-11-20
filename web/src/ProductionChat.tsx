import {useEffect,useRef,useState} from "react";
import {EpisodeMessages,StageExecutor} from "./Assistant";
import {cmd} from "./api";
import {ChatMarkdown} from "./ChatMarkdown";
import {ModelReply} from "./ModelReply";
import {STAGES,PROVIDER_LABEL,ErrorLine, useAction, usePoll, useQuery} from "./ui";

import {ReworkNotes,type Rework} from "./ReworkNotes";
import {directorText} from "./topics";

type Turn = {id:string;text:string;reply:string;status:string;reason:string;provider:string;model?:string;effort?:string;editQueued?:boolean;replanned?:boolean;basis?:{deliveryId?:string}};
export function ProductionChat({project,episode,stage,delivery,view,reload}:{
  project:string;episode:string;stage:string;delivery:string|null;view:any;reload:()=>void;
}) {
  const models=useQuery("film_executors",{episode_id:episode,stage});
  const expert=models.data?.expert;
  const catalog=useQuery<any>("model_catalog");
  const [modelOpen,setModelOpen]=useState<"create"|"review"|"expert"|null>(null);
  const scope={project_id:project,episode_id:episode,stage};
  const chat=useQuery<{threads:{id:string}[];turns:Turn[]}>("free_chat",scope);
  const basis=useQuery<{
    交付版本:string|null;集号:string;标题:string;故事梗概:string;导演决定:{code:string;text:string}[];
    分段?:{段:string;进度:string;返工记录?:Rework[];承接?:{source:string;slot:number;seconds:number|null}[];审片?:{cutAdvice?:string}}[];
    精剪成片?:{state?:string};发布与配乐要求:{title?:string;suno_style?:string};配乐?:string;精剪已接收?:boolean;
    其他阶段讨论:{stage:string;text:string;reply:string}[];
  }>("film_chat_context",{episode_id:episode,stage,delivery_id:delivery});
  const context=basis.data;
  const adopted=context?.分段?.filter(s=>s.进度==="已采用")??[];
  usePoll(!!context?.分段?.some(s=>["待派发","排队中","审片中","生成中"].includes(s.进度)) || context?.配乐==="生成中",basis.reload,10000);
  const act=useAction();
  const [text,setText]=useState("");
  const stream=useRef<HTMLDivElement>(null);
  const following=useRef(true);
  const turns=chat.data?.turns??[];
  const last=turns.at(-1);
  useEffect(()=>{
    if(last && stream.current && following.current)stream.current.scrollTop=stream.current.scrollHeight;
  },[last?.id,last?.status]);
  const waiting=!!last && ["queued","running"].includes(last.status);
  usePoll(waiting,chat.reload);
  const ready=!!expert && !!chat.data && !act.busy && !waiting && !!text.trim();
  const submit=(message:string, intent?:"revise")=>act.run(async()=>{
    if(!expert || !chat.data || act.busy || waiting || !message.trim())return;
    following.current=true;
    await cmd("send_free_chat",{...scope,text:message,...(intent?{finecut_mode:intent}:{}),provider:expert.provider,previous:last?.id,thread_id:chat.data?.threads[0]?.id,delivery_id:delivery});
    if(message===text)setText("");await chat.reload();
  });
  const send=()=>submit(text);
  const name=stage==="finecut"?"精剪":"视频与配乐";
  return <aside className="assistant film-chat" aria-label={name+"导演助手"}>
    <div className="head">导演助手 · {name}</div>
    <div className="msgs" ref={stream} onScroll={e=>{const n=e.currentTarget;following.current=n.scrollHeight-n.clientHeight-n.scrollTop<60;}} role="log" aria-label="本阶段对话">
      {context && <section className="mcard" aria-label="本集制作上下文"><b>{context.集号} · 本集制作上下文</b>
        <p>{context.标题}</p>
        {context.分段 && <p>{context.分段.length} 段中已采用 {adopted.length} 段 · BGM {context.配乐 || "尚未排队"}</p>}
        {context.分段?.map(s=><ReworkNotes key={s.段} segment={s.段} notes={s.返工记录??[]}/>)}
        {adopted.flatMap(s=>(s.承接??[]).map(d=><p key={s.段+"-"+d.slot}>{s.段} 图 {d.slot} 承接 {d.source}{d.seconds!=null && ` 的 ${d.seconds} 秒原帧`}</p>))}
        {context.导演决定.map(d=><details key={d.code}><summary>导演决定 {d.code}</summary><p className="chat-prose">{directorText(d.text)}</p></details>)}
        {context.故事梗概 && <details><summary>采用的故事梗概</summary><p>{context.故事梗概}</p></details>}
        {context.发布与配乐要求.suno_style && <details><summary>本集配乐要求</summary><p>{context.发布与配乐要求.suno_style}</p></details>}
        {adopted.filter(s=>s.审片?.cutAdvice).map(s=><details key={s.段}><summary>{s.段} 的剪点建议</summary><p>{s.审片?.cutAdvice}</p></details>)}
      </section>}
      {basis.error && <p className="error">{basis.error}</p>}
      <EpisodeMessages view={view} stage={stage} reload={reload}/>
      {context?.其他阶段讨论.map((t,i)=><details key={i}><summary>{STAGES.find(([id])=>id===t.stage)?.[1]??t.stage} · {t.text.slice(0,50)}</summary><ChatMarkdown text={t.reply}/></details>)}
      {turns.map(t=><article key={t.id}>
      <div className="msg user">{t.text}</div>
      <ModelReply provider={t.provider} model={t.model} effort={t.effort}>
        {t.reply?<ChatMarkdown text={t.reply}/>:<p>{t.reason || (waiting?"正在回复…":t.status)}</p>}
      </ModelReply>
      {stage==="finecut" && t===last && t.status==="succeeded" && (!t.editQueued || (t.replanned && (view.productionStatus?.finecut || context?.精剪成片?.state==="修改受阻"))) && t.basis?.deliveryId===(delivery || context?.交付版本) &&
        <button className="btn" disabled={act.busy || waiting} onClick={()=>submit(t.text,"revise")}>{t.replanned?"重新按原反馈修改":"按这条反馈重剪"}</button>}
      </article>)}</div>
    <div className="composer"><textarea value={text} aria-label="给导演助手的消息" placeholder={stage==="finecut"?"提出剪辑修改，例如「第 5 段开头再剪掉半秒」":"询问分段承接、审片或配乐进度"}
      onChange={e=>setText(e.target.value)} onKeyDown={e=>{if((e.metaKey||e.ctrlKey)&&e.key==="Enter"&&!e.nativeEvent.isComposing){e.preventDefault();void send();}}}/>
      <div className="composer-tools">
        {models.data && (["create","review","expert"] as const).map(role=>{
          const slot=models.data[role];
          const label={create:"写稿",review:"独审",expert:"专家"}[role];
          const modelName=catalog.data?.text[slot.provider]?.models?.find((m:any)=>m.id===slot.model)?.label
            || slot.model || (PROVIDER_LABEL[slot.provider]??slot.provider)+" 默认";
          return <div key={role} className="composer-model"
            onBlur={e=>{if(e.relatedTarget && !e.currentTarget.contains(e.relatedTarget))setModelOpen(current=>current===role?null:current);}}
            onKeyDown={e=>{if(e.key==="Escape"){setModelOpen(null);e.currentTarget.querySelector("button")?.focus();}}}>
            <button className="model-trigger" aria-expanded={modelOpen===role} disabled={!catalog.data}
              onClick={()=>setModelOpen(modelOpen===role?null:role)} title={(role==="expert"?"本集":name)+" · "+label+" · "+modelName}>
              <small>{label}</small><span>{modelName}</span><i aria-hidden="true">⌄</i>
            </button>
            {modelOpen===role && catalog.data && <div className="executor-panel">
              <StageExecutor role={role} slot={slot} catalog={catalog.data} refreshCatalog={catalog.reload}
                busy={act.busy} onChange={value=>act.run(async()=>{
                  if(role==="expert")await cmd("save_episode_expert",{episode_id:episode,...value});
                  else await cmd("save_film_executor",{episode_id:episode,stage,role,...value});
                  await models.reload();
                  if(role==="expert")await reload();
                })}/>
            </div>}
          </div>;
        })}
        <button className="btn primary film-send" aria-label="发送消息" disabled={!ready} onClick={send}>↑</button>
      </div>
      {models.error && <p className="error">{models.error}</p>}
      {catalog.error && <p className="error">{catalog.error}</p>}
      {chat.error&&<p className="error">{chat.error}</p>}<ErrorLine error={act.error}/></div>
  </aside>;
}
