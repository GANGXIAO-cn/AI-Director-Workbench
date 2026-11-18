import { useEffect, useState } from "react";
import { blob, cmd, q } from "./api";
import { ErrorLine, rno, useAction, useQuery } from "./ui";
import { RequiredImage } from "./RequiredImage";

type Need = {
  id:string; need_key:string; role_id?:string; kind:string; title:string; description:string; scene_ids:string[]; missing:string[];
  decision:string; decision_reason:string; image_prompt:string; source_prompt:string; prompt_changed:boolean;
  revision:number; adopted_version_id:string|null; candidates:string[]; generation_requested:boolean;
  request:{batchId:string;batchState?:string;state:string}|null;
};
const groups=["角色身份","场景锁定图","道具基准","状态变体"];
const category=(kind:string)=>({character:"identity",location:"scene"}[kind] || kind);
function group(r:Need) {
  return ({identity:0,scene:1,state:3,costume:3}[category(r.kind)] ?? 2);
}
function status(r:Need) {
  if(r.request?.batchState==="awaiting_authorization") return "待批准";
  if(r.request) return r.request.state==="unknown"?"待核对":"出图中";
  if(r.adopted_version_id) return r.decision==="reuse"?"已有图":"已完成";
  if(r.decision==="text_only") return "只用文字";
  if(r.decision==="pending") return "待决定";
  return r.generation_requested || !r.candidates.length?"待出图":"待选图";
}
function readChannel() {
  try {return JSON.parse(localStorage.getItem("asset-worksheet-channel") || "{}") || {};} catch {return {};}
}
function CandidateImage({asset}:{asset:any}) {
  return <a href={blob(asset.sha256)} target="_blank" rel="noreferrer"><img src={blob(asset.sha256)} alt={asset.name} loading="lazy"/></a>;
}

export function AssetWorksheet({ctx,space}:{ctx:any;space:React.ReactNode}) {
  const deps=ctx.batches.map((b:any)=>b.id+":"+b.state).join(",");
  const sheet=useQuery<{versionId:string|null;rows:Need[];complete:boolean}>("asset_worksheet",{episode_id:ctx.eid},[deps]);
  const clearance=useQuery<{blockers:{id:string;type:string;text:string}[]}>("stress_worklist",{episode_id:ctx.eid},[ctx.view]);
  const art=ctx.view.artifacts.find((a:any)=>a.stage==="asset_design");
  const list=art?.versions.find((v:any)=>v.id===art.adopted_version_id) ?? art?.versions.at(-1);
  const items=Array.isArray(list?.content.assets)?list.content.assets:[];
  const scriptArt=ctx.view.artifacts.find((a:any)=>a.stage==="script");
  const script=scriptArt?.versions.find((v:any)=>v.id===scriptArt.adopted_version_id);
  const scenes=script?.content.scenes || [];

  const library=useQuery<any[]>("library",{project_id:ctx.view.episode.project_id});
  const providers=useQuery<any>("image_providers");
  const [preferences,choose]=useState(readChannel);
  const [excluded,setExcluded]=useState<string[]>([]);
  const [productPicker,setProductPicker]=useState<string|null>(null);
  const catalog=useQuery<{products:{code:string;name:string}[]}>(productPicker ? "project_resources" : null,{project_id:ctx.view.episode.project_id,episode_id:ctx.eid});
  const [required,setRequired]=useState<any>(null);
  const supplement=useQuery<any[]>("production_supplements",{episode_id:ctx.eid});
  const act=useAction();
  const rows=sheet.data?.rows || [];
  const requestedGroup=new URLSearchParams(window.location.hash.split("?")[1] || "").get("group");
  useEffect(()=>{
    if(requestedGroup==="scene" && rows.length){
      document.getElementById("worksheet-scenes")?.scrollIntoView({block:"start"});
    }
  },[requestedGroup,rows.length]);
  // 实测 143：停下卡片“查看这一项”跳到对应条目并短暂高亮。
  const requestedNeed=new URLSearchParams(window.location.hash.split("?")[1] || "").get("need");
  useEffect(()=>{
    if(!requestedNeed || !rows.length)return;
    const card=document.querySelector<HTMLElement>(`[data-need="${CSS.escape(requestedNeed)}"]`);
    if(!card)return;
    card.scrollIntoView({block:"center"});card.classList.add("need-highlight");
    const t=setTimeout(()=>card.classList.remove("need-highlight"),2500);return()=>clearTimeout(t);
  },[requestedNeed,rows.length]);

  const waitingBatches=ctx.batches.filter((batch:any)=>
    ["awaiting_authorization","authorized"].includes(batch.state) &&
    rows.some(row=>row.request?.batchId===batch.id) &&
    batch.requests.every((request:any)=>request.state==="pending"));
  const waitingCount=waitingBatches.reduce((count:number,batch:any)=>count+batch.count,0);

  // 实测 139：出图通道与型号是本集设置，一改就保存，连续制作下一次出图起跟着用；尺寸只影响这里手动出图。
  const episodeChannel=useQuery<any>("episode_image_channel",{episode_id:ctx.eid});
  const channels=Object.keys(providers.data || {});
  const provider=channels.includes(episodeChannel.data?.provider)?episodeChannel.data.provider:channels[0];
  const caps=providers.data?.[provider]?.capabilities;
  const model=caps?.models?.includes(episodeChannel.data?.model)?episodeChannel.data.model:caps?.models?.[0];
  // 尺寸默认取最接近本集画幅的一项（与连续制作一致），不再默认列表第一项。
  const ratio=String(ctx.view.kickoff?.spec?.aspectRatio||"9:16").split(":").map(Number);
  const fit=(v:string)=>{const [w,h]=v.split("x").map(Number);return Math.abs(Math.log(w/h/(ratio[0]/ratio[1])))||0;};
  const nearest=[...(caps?.sizes||[])].sort((a:string,b:string)=>fit(a)-fit(b))[0];
  const size=caps?.sizes?.includes(preferences.size)?preferences.size:nearest;
  const quality=["auto","high"].find(v=>caps?.quality?.includes(v)) ?? caps?.quality?.[0];
  function changeChannel(values:any) {
    if(values.provider || "model" in values){
      const nextProvider=values.provider || provider;
      const nextModel=values.provider ? (providers.data?.[nextProvider]?.capabilities?.models?.[0] ?? null) : values.model;
      act.run(async()=>{await cmd("save_episode_image_channel",{episode_id:ctx.eid,provider:nextProvider,model:nextModel||null});await episodeChannel.reload();});
      if(values.provider)choose({...preferences,size:""});
      return;
    }
    const next={...preferences,size:values.size};choose(next);localStorage.setItem("asset-worksheet-channel",JSON.stringify(next));
  }
  const refresh=async()=>{await Promise.all([sheet.reload(),ctx.reload()]);};
  const link=(stage:string)=>`#/e/${ctx.eid}/${stage}`;
  const ready=rows.filter(r=>status(r)==="待出图" && !r.missing.length && !excluded.includes(r.id));
  const pending=rows.filter(r=>status(r)==="待决定");
  const picks=rows.filter(r=>status(r)==="待选图");
  const first=(row:Need)=>document.getElementById("need-"+row.id)?.scrollIntoView({block:"center"});
  const setNeed=(r:Need,action:string,extra:any={})=>cmd("set_asset_need",{episode_id:ctx.eid,need_key:r.need_key,revision:r.revision,action,...extra});
  const select=async(selections:Need[],bulk:boolean,vid?:string)=>{
    await cmd("adopt_asset_needs",{episode_id:ctx.eid,bulk,selections:Object.fromEntries(selections.map(r=>[r.need_key,{versionId:vid||r.candidates[0],revision:r.revision}]))});
    await refresh();
  };
  const count=(label:string)=>rows.filter(r=>status(r)===label).length;
  if(!sheet.data || !clearance.data)return <p>{sheet.error || clearance.error || "正在读取备图工作单…"}</p>;
  const blockers=clearance.data.blockers;
  const issueIds=blockers.filter(b=>b.type==="issue").map(b=>b.id);
  const stagesToCheck=[...new Set(blockers.filter(b=>b.type==="impact").map(b=>b.text.split("：")[0]))];
  const awaitingReview=!ctx.view.skippedStages.stress && !ctx.view.artifacts.some((a:any)=>a.stage==="stress" && a.adopted_version_id);
  const needsReview=blockers.length>0 || awaitingReview;
  const listHref=link("asset_design")+(list?"?v="+list.id:"");
  if(needsReview || !sheet.data.versionId) return <section className="asset-worksheet worksheet-entry">
    <h1>备图工作单</h1><p className="muted">本集已采用素材清单 {rno(list?.display_no)} · 对照剧本 {rno(script?.display_no)}</p>
    <section className="card worksheet-next">
      <h2>{needsReview?"先完成故事审查":list?"先采用素材清单":"先起草素材清单"}</h2>
      {needsReview ? <>{blockers.length ? <>{issueIds.length>0 && <p>待处理的审查问题：{issueIds.join("、")}</p>}
        {stagesToCheck.length>0 && <p>待核对的采用稿：{stagesToCheck.sort().join("、")}</p>}
        <details><summary>查看原因</summary><ul>{blockers.map(b=><li key={b.id}>{b.text}</li>)}</ul></details></> : <p>06 尚未采用审查报告，可在故事审查页完成审查或登记跳过原因。</p>}
        <a className="btn primary" href={link("stress")}>去 06 故事审查</a></> :
        <><p>{list?"核对下方条目后，在清单页采用这一版，即可逐项复用或出图。":"先在素材清单页起草并采用本集需要的角色、场景和道具。"}</p>
        <a className="btn primary" href={listHref}>{list?"查看并采用 "+rno(list.display_no):"去起草素材清单"}</a></>}
    </section>
    {list && <section className="card"><div className="spread"><h2>素材清单 {rno(list.display_no)}</h2><span>{art.adopted_version_id===list.id?"已采用":"待采用"} · {items.length} 项</span></div>
      <ul className="worksheet-list-preview">{items.map((item:any,i:number)=><li key={item.id||i}>{item.title || item.name || item.description || item.id}</li>)}</ul>
      {needsReview && <a href={listHref}>查看完整清单</a>}</section>}
  </section>;
  return <section className="asset-worksheet"><h1>备图工作单</h1><p className="muted">本集已采用素材清单 {rno(list?.display_no)} · 对照剧本 {rno(script?.display_no)}</p>
    {!rows.length ? <><p>本集的素材清单采用于工作单上线之前，重新采用一次清单即可生成工作单。</p>
      <button className="btn" disabled={act.busy} onClick={()=>act.run(async()=>{
        await cmd("initialize_asset_worksheet",{episode_id:ctx.eid,version_id:sheet.data!.versionId});await refresh();
      })}>用当前采用清单生成工作单</button></> : <>
    <p>共 {rows.length} 项 · 已有图 {count("已有图")} · 待决定 {pending.length} · 待出图 {count("待出图")} · 待选图 {picks.length} · 已完成 {rows.filter(r=>r.adopted_version_id||r.decision==="text_only").length}</p>
    <div className="row wrap worksheet-toolbar">
      {pending.length && !ready.length ? <button className="btn primary" onClick={()=>first(pending[0])}>逐项决定复用还是出图</button> :
       ready.length ? <button className="btn primary" disabled={act.busy||!provider||!size} onClick={()=>act.run(async()=>{
          const b=await cmd("prepare_asset_batch",{episode_id:ctx.eid,provider,model,size,quality,
            needs:Object.fromEntries(ready.map(r=>[r.need_key,r.revision]))});await sheet.reload();ctx.openBatch({id:b.batchId});
       })}>按清单出图（{ready.length} 张）</button> : picks.length ? <button className="btn primary" onClick={()=>first(picks[0])}>去选图</button> :
       sheet.data.complete ? <a className="btn primary" href={link("plan")}>进入 08 分镜</a> :
       rows.some(r=>r.request) ? <button className="btn primary" onClick={()=>ctx.openBatch({id:rows.find(r=>r.request)!.request!.batchId})}>查看进行中的批次</button> : <span>请勾选待出图项或先完成所列依赖。</span>}
      <label>出图通道 <select value={provider||""} onChange={e=>changeChannel({provider:e.target.value,model:"",size:""})}>{channels.map(p=><option key={p}>{p}</option>)}</select></label>
      {!!caps?.models?.length && <label>型号 <select value={model} onChange={e=>changeChannel({model:e.target.value})}>{caps.models.map((m:string)=><option key={m}>{m}</option>)}</select></label>}
      {!!caps && <label>尺寸 <select value={size} onChange={e=>changeChannel({size:e.target.value})}>{caps.sizes.map((s:string)=><option key={s}>{s}</option>)}</select></label>}
      <span className="muted small">通道和型号是本集设置，连续制作下一张图起也按它出；尺寸按画幅自动选</span>
    </div>
    {waitingBatches.length>0 && <section className="card worksheet-approval">
      <h2>待批准出图 · {waitingCount} 张</h2>
      <ul>{waitingBatches.map((batch:any)=><li key={batch.id}>
        {rows.filter(row=>row.request?.batchId===batch.id).map(row=>row.title).join("、")} · {batch.provider} · {batch.size}
        <button className="btn" onClick={()=>ctx.openBatch({id:batch.id})}>查看内容</button>
      </li>)}</ul>
      <button className="btn primary" disabled={act.busy} onClick={()=>act.run(async()=>{
        try {
          const details=await Promise.all(waitingBatches.map((batch:any)=>q<any>("batch_view",{batch_id:batch.id})));
          await cmd("authorize_batches",{episode_id:ctx.eid,batches:Object.fromEntries(details.map(batch=>[batch.id,batch.summary_hash]))});
          const errors:string[]=[];
          for(const batch of details){
            try {await cmd("execute_batch",{batch_id:batch.id});}
            catch(error){errors.push(error instanceof Error ? error.message : String(error));}
          }
          if(errors.length)throw new Error(errors.join("；"));
        } finally {await refresh();}
      })}>批量批准并生成 {waitingCount} 张</button>
    </section>}
    {groups.map((label,index)=>{
      const members=rows.filter(r=>group(r)===index);
      if(!members.length)return null;
      const undecided=members.filter(r=>status(r)==="待决定");
      const unpicked=members.filter(r=>status(r)==="待选图" && ctx.assets.some((asset:any)=>asset.id===r.candidates[0] && asset.category===category(r.kind)));
      return <section key={label} id={index===1?"worksheet-scenes":undefined}><div className="spread"><h2>{label}</h2><div className="row">
        {undecided.length>1 && <button className="btn" disabled={act.busy} onClick={()=>act.run(async()=>{
          try {for(const r of undecided) await setNeed(r,"generate");} finally {await refresh();}
        })}>本组待决定项都出新图</button>}
        {unpicked.length===1 && <button className="btn" onClick={()=>first(unpicked[0])}>查看待选图：{unpicked[0].title}</button>}
        {index!==0 && unpicked.length>1 && <button className="btn" title={unpicked.map(r=>r.title).join("、")} disabled={act.busy} onClick={()=>act.run(()=>select(unpicked,true))}>采用已出图的 {unpicked.length} 项（各第一张）</button>}
      </div></div>
      <div className="worksheet-grid">{members.map(r=><NeedRow key={r.id} r={r} ctx={ctx} scenes={scenes} library={library.data||[]} rows={rows}
        productPicker={productPicker===r.id} toggleProducts={()=>setProductPicker(productPicker===r.id?null:r.id)}
        products={catalog.data?.products} productError={catalog.error}
        useProduct={async(code:string)=>{await setNeed(r,"reuse",{product_code:code});setProductPicker(null);await refresh();}}
        checked={!excluded.includes(r.id)} toggle={()=>setExcluded(excluded.includes(r.id)?excluded.filter(id=>id!==r.id):[...excluded,r.id])}
        change={async(action:string,extra:any)=>{
          await setNeed(r,action,extra);
          try {
            if(action==="generate"){
              const latest=await q<{rows:Need[]}>("asset_worksheet",{episode_id:ctx.eid});
              const updated=latest.rows.find(item=>item.need_key===r.need_key)!;
              if(updated.missing.length){
                throw new Error("已加入待出图，请先完成依赖图："+updated.missing.map(key=>latest.rows.find(item=>item.need_key===key)?.title||key).join("、"));
              }
              const batch=await cmd("prepare_asset_batch",{
                episode_id:ctx.eid,provider,model,size,quality:caps?.quality?.[0],
                needs:{[updated.need_key]:updated.revision}
              });
              ctx.openBatch({id:batch.batchId});
            }
          } finally {await refresh();}
        }}
        adopt={(vid:string)=>select([r],false,vid)} required={setRequired}/>)}</div></section>;
    })}
    <details><summary>按场次登记空间锁定</summary>{space}</details>
    <details><summary>有 {supplement.data?.filter(r=>r.state==="proposed").length||0} 条制作补充待确认</summary>
      <a href={link("space")}>查看制作补充</a></details>
    </>}
    <ErrorLine error={act.error}/>{(providers.error||library.error) && <p className="error">{providers.error||library.error}</p>}
    {required && <RequiredImage ctx={ctx} asset={required} close={()=>setRequired(null)}/>}
  </section>;
}
function NeedRow({r,ctx,scenes,library,rows,checked,toggle,change,adopt,required,productPicker,toggleProducts,products,productError,useProduct}:any) {
  const [prompt,setPrompt]=useState(r.image_prompt);
  useEffect(()=>setPrompt(r.image_prompt),[r.image_prompt]);
  const [reason,setReason]=useState(r.decision_reason || "");
  const [changing,setChanging]=useState(false);
  const act=useAction();
  const state=status(r);
  const asset=(id:string)=>ctx.assets.find((a:any)=>a.id===id);
  const picture=asset(r.adopted_version_id || (r.generation_requested ? null : r.candidates[0]));
  const text=(r.title+" "+r.description).toLowerCase();
  function relevance(e:any) {
    const fields=(e.name+" "+e.usage).toLowerCase();
    let score=text.includes(e.name.toLowerCase())?20:0;
    const terms=new Set<string>(text.match(/[a-z0-9]+|[\u3400-\u9fff]+/g)||[]);
    for(const term of terms) {
      if(fields.includes(term)) score+=4;
      else if(term.length>=2) for(let i=1;i<term.length;i++) if(fields.includes(term.slice(i-1,i+1)))score++;
    }
    return score;
  }
  const candidates=library.filter((e:any)=>["approved","limited"].includes(e.status) && (e.category===category(r.kind) && (category(r.kind)!=="identity" || !!r.role_id && e.roles?.includes(r.role_id))))
    .sort((a:any,b:any)=>relevance(b)-relevance(a));
  const edit=(action:string,extra:any={})=>act.run(()=>change(action,extra));
  return <article className="card worksheet-row" id={"need-"+r.id} data-need={r.need_key}>
    <div className="worksheet-head">{picture ? <CandidateImage asset={picture}/> : <span className="worksheet-placeholder">暂无图</span>}
      <div className="worksheet-description"><h3>{r.title}</h3><small>{groups[group(r)]} · {r.scene_ids.join("、")||"全剧"}</small><p>{r.description}</p></div><b className="worksheet-status">{state}</b></div>
    <details className="worksheet-scene-evidence"><summary>查看本集使用场次（{r.scene_ids.length} 场）</summary>
      {!r.scene_ids.length && <p>清单未标明使用场次，需要核对本集用途。</p>}
      {r.scene_ids.map((id:string)=>{
        const scene=scenes.find((s:any)=>s.id===id);
        return <div key={id}><h4>{scene?.heading || id}</h4><p>{scene?.action || "当前采用剧本中未找到此场次，需要核对清单。"}</p></div>;
      })}
    </details>
    <details><summary>出图提示词</summary><textarea aria-label={r.title+"的提示词"} value={prompt} onChange={e=>setPrompt(e.target.value)}/>
      <button className="btn" disabled={act.busy||!!r.request||prompt===r.image_prompt} onClick={()=>edit("edit_prompt",{prompt})}>保存提示词</button></details>
    {!!r.prompt_changed && <div>清单提示词已更新<p>{r.source_prompt}</p><button className="btn" onClick={()=>edit("use_source")}>使用清单提示词</button> <button className="btn" onClick={()=>edit("keep_prompt")}>保留手改提示词</button></div>}
    {r.request ? <button className="btn" onClick={()=>ctx.openBatch({id:r.request.batchId})}>查看批次</button> : <>
      {["prop","product"].includes(r.kind) && <div>
        <button className="btn" aria-expanded={productPicker} onClick={toggleProducts}>从商品库选主图</button>
        {productPicker && <div className="worksheet-product-picker">
          {productError ? <p className="error">{productError}</p> : !products ? <p>正在读取商品库…</p> :
            !products.length ? <p>当前项目没有商品主图。</p> : products.map((p:{code:string;name:string})=>
              <button className="btn" key={p.code} disabled={act.busy} aria-label={`采用 ${p.code} ${p.name} 主图`}
                onClick={()=>act.run(()=>useProduct(p.code))}>
                <img loading="lazy" alt="" src={`/api/project-product/${ctx.view.episode.project_id}/${p.code}?episode_id=${ctx.eid}`}/>
                <span>{p.code} {p.name}</span>
              </button>)}
        </div>}
      </div>}
      {state==="待出图" && <label><input type="checkbox" checked={checked} disabled={!!r.missing.length} onChange={toggle}/>加入本次出图</label>}
      {!!r.missing.length && <p>先完成：{r.missing.map((key:string)=>rows.find((n:Need)=>n.need_key===key)?.title||key).join("、")}</p>}
      {r.decision==="text_only" && <p>{r.decision_reason}</p>}
      {<button className="btn primary" disabled={act.busy||!prompt.trim()} onClick={()=>edit("generate",{prompt})}>{r.adopted_version_id || r.candidates.length ? "重新生图" : "出新图"}</button>}
      {state!=="待决定" && <button className="btn" onClick={()=>setChanging(!changing)}>{changing?"收起选择":r.adopted_version_id?"换一张或重出":"更改备图决定"}</button>}
      {(state==="待决定" || changing) && <div><details><summary>查看可选复用图（{candidates.length} 张）</summary>{!candidates.length && <p>复用库里没有匹配的图</p>}
        {candidates.map((e:any)=><div className="card" key={e.id}><CandidateImage asset={e}/><p>{e.name} · {e.usage} · {e.limits||"无登记限制"} · {e.sourceEpisodeCode||"项目素材"}</p>
          <button className="btn" disabled={act.busy} onClick={()=>edit("reuse",{entry_id:e.id,reason})}>用这张</button>
          <button className="btn" disabled={act.busy} onClick={()=>edit("generate",{entry_id:e.id,reason})}>以它为底改图</button></div>)}
        </details><label>决定理由 <input placeholder="可不填" value={reason} onChange={e=>setReason(e.target.value)}/></label>
        <button className="btn" disabled={act.busy} onClick={()=>edit("generate",{reason})}>出新图</button>
        <button className="btn" disabled={act.busy} onClick={()=>edit("text_only",{reason})}>只用文字描述</button></div>}
      {!!r.candidates.length && (!r.adopted_version_id || changing) && <div className="worksheet-choices">{r.candidates.map((vid:string)=>{
        const img=asset(vid);return img && img.category===category(r.kind) && <div key={vid}><CandidateImage asset={img}/><p>{img.name} v{img.version_no}</p>
          <button className="btn" disabled={act.busy||vid===r.adopted_version_id} onClick={()=>act.run(()=>adopt(vid))}>采用</button>
          {category(r.kind)!=="identity" && <button className="btn" onClick={()=>required(img)}>标必用</button>}
          <details><summary>带意见重出或带图修改</summary><textarea aria-label="修订后的出图提示词" value={prompt} onChange={e=>setPrompt(e.target.value)}/>
            <button className="btn" disabled={act.busy||!prompt.trim()} onClick={()=>edit("generate",{prompt})}>按意见重出</button>
            <button className="btn" disabled={act.busy||!prompt.trim()} onClick={()=>edit("generate",{version_id:vid,prompt})}>带图修改</button></details>
        </div>;})}</div>}
    </>}<ErrorLine error={act.error}/>
  </article>;
}
