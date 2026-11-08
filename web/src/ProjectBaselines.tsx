import { useState } from "react";
import { blob, cmd } from "./api";
import { ErrorLine, Tag, useAction, usePoll, useQuery } from "./ui";

type Reference = {code:string;episodeId:string;segment:string;versionId:string;sha256:string;matchesCurrent:boolean};
type Block = { references:Reference[]; key: string; label: string; text: string; version?: string; description?: string };
type Revision = { id: string; revision: number; key: string; label: string; before: string; after: string };
type Copy = {episodeId: string; code: string; sha256: string; effectiveSha256: string; path: string; state: string};
type Source = {
  kind: string; path?: string; stages: string[]; sha256?: string; blocks: Block[]; error?: string;
  revision: number; copies: Copy[]; history: Revision[];
};
type Segment = {segment: string; reason: string; hasPrompts: boolean};
type Preview = {before: string; after: string; previewHash: string; segments?: Segment[];
  episodes?: {episodeId: string; code: string; segments: Segment[]}[]};
const labels: Record<string, string> = {
  characterStandard: "角色外观与同框", soundStandard: "声音设定", promptStandard: "视频提示词",
  script: "剧本", plan: "制作方案", prompts: "分段提示词",
};

function scope(block:Block) {
  if (block.key === "blocks.grid_reference") return "九宫格分段";
  if (block.key === "blocks.cats_silent") return "猫角色出镜";
  if (block.key.startsWith("roles.")) return block.label+" 出镜";
  if (block.key.startsWith("pair_rules.")) return block.label;
  return block.key==="sound"?"声音设计与对照审查":"分段提示词";
}

function Segments({rows}:{rows:Segment[]}) {
  return rows.length ? <ul>{rows.map(s=><li key={s.segment}>{s.segment} · {s.reason} · {s.hasPrompts?"需重装配并复审":"起草时使用"}</li>)}</ul>
    : <p className="small muted">当前采用稿与制作方案中没有定位到适用段。</p>;
}

function Diff({before,after}:{before:string;after:string}) {
  return <div className="baseline-diff">{[["修订前",before],["修订后",after]].map(([label,text])=>
    <div key={label}><b>{label}</b><p>{text}</p></div>)}</div>;
}

function Editor({pid,source,block,done,cancel}:{pid:string;source:Source;block:Block;done:()=>void;cancel:()=>void}) {
  const draftKey=["baseline-draft",pid,source.kind,block.key,source.revision,source.sha256].join(":");
  const [text,setText]=useState(()=>sessionStorage.getItem(draftKey) ?? block.text);
  const [preview,setPreview]=useState<Preview|null>(null);
  const [ai,setAI]=useState(false);
  const [goal,setGoal]=useState("");
  const [provider,setProvider]=useState("");
  const [turn,setTurn]=useState<{threadId:string;turnId:string}|null>(null);
  const providers=useQuery<{providers:string[]}>(ai?"free_chat_options":null,{project_id:pid});
  const chat=useQuery<{turns:{id:string;reply?:string;status:string;reason?:string}[]}>(turn?"free_chat":null,{project_id:pid,thread_id:turn?.threadId});
  const reply=chat.data?.turns.find(t=>t.id===turn?.turnId);
  usePoll(!!turn && !chat.error && (!reply || ["queued","running"].includes(reply.status)),chat.reload,750);
  const action=useAction();
  const args={project_id:pid,kind:source.kind,block_key:block.key,text,expected_sha:source.sha256,expected_revision:source.revision};
  const executor=provider || providers.data?.providers[0] || "";
  function change(value:string) {sessionStorage.setItem(draftKey,value);setText(value);setPreview(null);}
  return <section className="card cyan baseline-edit"><div className={ai?"baseline-compose with-assistant":"baseline-compose"}><div><h3>修订{block.label}</h3>
    <textarea aria-label="修订正文" rows={6} value={text} onChange={e=>change(e.target.value)}/>
    <div className="row"><button className="btn" onClick={()=>setAI(!ai)}>让 AI 起草修订</button>
      <button className="btn" disabled={action.busy || text===block.text || !text.trim()} onClick={()=>action.run(async()=>setPreview(await cmd("revise_baseline",args)))}>比较修订</button>
      <button className="btn" onClick={cancel}>关闭编辑</button></div></div>
    {ai && <aside><h3>导演助手</h3><label>起草要求<input value={goal} onChange={e=>setGoal(e.target.value)}/></label>
      <select aria-label="起草执行器" value={executor} onChange={e=>setProvider(e.target.value)}>{providers.data?.providers.map(p=><option key={p}>{p}</option>)}</select>
      <button className="btn" disabled={action.busy || !goal.trim() || !executor || (!!turn && !reply) || ["queued","running"].includes(reply?.status || "")}
        onClick={()=>action.run(async()=>setTurn(await cmd("send_free_chat",{project_id:pid,provider:executor,
          text:"只返回可替换此固定块的完整正文。导演的修订要求为\n"+goal,baseline:{kind:source.kind,key:block.key,revision:source.revision,sha256:source.sha256}})))}>发送原文与要求</button>
      {turn && <p><a href={`#/p/${pid}/free/${turn.threadId}`}>查看起草记录</a> {reply?.reason || ((!reply || ["queued","running"].includes(reply.status))?"等待回复":"")}</p>}
      {reply?.reply && <div><p style={{whiteSpace:"pre-wrap"}}>{reply.reply}</p><button className="btn" onClick={()=>change(reply.reply!)}>带入修订正文</button></div>}
      {(providers.error || chat.error) && <p role="alert">{providers.error || chat.error}</p>}
    </aside>}</div>
    {preview && <section><Diff {...preview}/><h4>在制集引用</h4>
      {preview.episodes?.length ? preview.episodes.map(e=><div key={e.episodeId}><b>{e.code}</b><Segments rows={e.segments}/></div>) : <p>暂无需要跟进的在制集。</p>}
      <button className="btn primary" disabled={action.busy} onClick={()=>action.run(async()=>{await cmd("revise_baseline",{...args,preview_hash:preview.previewHash});sessionStorage.removeItem(draftKey);done();})}>采用为项目修订 R{source.revision+1}</button>
    </section>}
    <ErrorLine error={action.error}/>
  </section>;
}

function Follow({pid,revision,copies,reload}:{pid:string;revision:Revision;copies:Copy[];reload:()=>void}) {
  const [eid,setEid]=useState("");
  const [preview,setPreview]=useState<Preview|null>(null);
  const [notice,setNotice]=useState("");
  const act=useAction();
  const args={project_id:pid,episode_id:eid,revision_id:revision.id};
  const active=copies.filter(c=>c.state==="in_production");
  return active.length ? <div><select aria-label="跟进修订的在制集" value={eid} onChange={e=>{setEid(e.target.value);setPreview(null);setNotice("");}}>
    <option value="">选择在制集</option>{active.map(c=><option key={c.episodeId} value={c.episodeId}>{c.code}</option>)}</select>
    <button className="btn" disabled={!eid || act.busy} onClick={()=>act.run(async()=>{setNotice("");setPreview(await cmd("follow_baseline",args));})}>核对本集影响</button>
    {preview && <><Diff {...preview}/><Segments rows={preview.segments || []}/><button className="btn primary" disabled={act.busy}
      onClick={()=>act.run(async()=>{await cmd("follow_baseline",{...args,preview_hash:preview.previewHash});setPreview(null);setNotice("本集后续任务已选用此固定块");reload();})}>本集采用此固定块</button></>}
    {notice && <p role="status">{notice}</p>}<ErrorLine error={act.error}/></div> : null;
}

function SourceCard({pid,source,reload}:{pid:string;source:Source;reload:()=>void}) {
  const [editing,setEditing]=useState("");
  return <article className="card line baseline-source">
    <div className="spread"><h2>{labels[source.kind]}</h2><div className="row">{source.stages.map(stage=><Tag key={stage}>{labels[stage]}</Tag>)}</div></div>
    <p className="small muted">{source.path} · {source.revision?"项目修订 R"+source.revision:"项目原件"}</p>
    {source.error && <p role="alert">{source.error}</p>}
    {source.blocks.length>0 && <div className="baseline-row baseline-head"><span>固定块</span><span>版本</span><span>适用范围</span><span>采用稿逐字引用</span></div>}
    {source.blocks.map(block=><details key={block.key}><summary><span className="baseline-row"><b>{block.label}</b>
      <span>{block.version || (source.revision?"R"+source.revision:"原件")}</span><span>{scope(block)}</span>
      <span>{block.references.length?block.references.length+" 段":"暂无"}</span></span></summary>
      <p style={{whiteSpace:"pre-wrap"}}>{block.text}</p>
      {block.references.length>0 && <div className="row"><span>已采用 TXT 逐字引用</span>{block.references.map(r=><a
        href={`#/e/${r.episodeId}/prompts?v=${r.versionId}&segment=${encodeURIComponent(r.segment)}`} key={r.episodeId+r.segment}>{r.code} · {r.segment}{r.matchesCurrent?"":" · 旧版原文"}</a>)}</div>}
      {block.description && <details><summary>完整外观说明</summary><p>{block.description}</p></details>}
      <div className="spread"><small className="muted">{block.key}</small><button className="btn" onClick={()=>setEditing(block.key)}>修订{block.label}</button></div>
      {editing===block.key && <Editor key={block.key+source.sha256} {...{pid,source,block}} cancel={()=>setEditing("")} done={()=>{setEditing("");reload();}}/>}
    </details>)}
    {source.history.map((r,i,list)=><details key={r.id}><summary>R{r.revision} · {r.label}修订记录</summary><Diff {...r}/>
      {!list.slice(0,i).some(n=>n.key===r.key) && <Follow pid={pid} copies={source.copies} revision={r} reload={reload}/>}</details>)}
    <details><summary>来源指纹与开工副本（{source.copies.length} 集）</summary>
      {source.sha256 && <p className="small">项目现行 SHA-256 · {source.sha256}</p>}
      {source.copies.map(copy=><p className="small" key={copy.episodeId}>{copy.code} · {copy.path}<br/>
        <a href={blob(copy.sha256)} target="_blank" rel="noreferrer">开工原件 · {copy.sha256}</a>
        {copy.effectiveSha256!==copy.sha256 && <><br/><a href={blob(copy.effectiveSha256)} target="_blank" rel="noreferrer">本集选用 · {copy.effectiveSha256}</a></>}</p>)}
    </details>
  </article>;
}

export function ProjectBaselines({pid}:{pid:string}) {
  const sources=useQuery<Source[]>("project_baselines",{project_id:pid});
  return <section className="project-baselines"><h1>制作基准</h1>
    {sources.error && <p role="alert">{sources.error}</p>}
    {!sources.data && !sources.error && <p>正在读取项目基准…</p>}
    {sources.data?.length===0 && <p className="empty">本项目尚未配置制作基准。</p>}
    {sources.data?.map(source=><SourceCard key={pid+source.kind} pid={pid} source={source} reload={sources.reload}/>)}
  </section>;
}
