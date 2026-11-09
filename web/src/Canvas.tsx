import { Type, Image, LockKeyhole, WandSparkles, Link2 } from "lucide-react";
import { budgetCents } from "./budget";
import { RequiredImage } from "./RequiredImage";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useRef, useState } from "react";
import { blob, cmd, q } from "./api";
import { ErrorLine, Modal, Tag, useAction } from "./ui";

/** J15 本集素材 · 生成画布: inputs → generate node → candidates → downstream use.
 *  Layout/wiring is saved per episode; every generation still goes through the paid-batch confirmation. */
type Node = { id: string; type: "text" | "reference" | "lock" | "generate" | "usage"; x: number; y: number; title: string; data: any };
type Edge = { from: string; to: string; kind: "input" | "ref" | "pending" };
const W: Record<string, number> = { text: 200, reference: 190, lock: 190, generate: 250, usage: 210 };
const NODE_TYPES = {
  text: {label:"文本", icon:Type}, reference: {label:"参考图", icon:Image},
  lock: {label:"锁定图", icon:LockKeyhole}, generate: {label:"生成", icon:WandSparkles}, usage: {label:"下游用途", icon:Link2}
};
function NodeLabel({type}:{type:Node["type"]}) {
  const {label, icon:Icon} = NODE_TYPES[type];
  return <><Icon size={14} aria-hidden="true"/> {label}</>;
}
const PRESETS: [string, string, string][] = [["身份图", "generate", "正面全身，中性站姿，无剧情动作，纯色浅底；识别点必须清楚；不裁切。"],
  ["2×2 身份卡","generate","同一角色四格身份对照：正面脸、侧面脸、正面全身、背面全身。四格保持同一角色、服装、配色和身份状态。"],
  ["多角度", "generate", "同一角色正面、侧面、背面三视图，比例一致，纯色浅底。"],
  ["状态派生", "generate", "以锁定图为准，只改本段需要的状态（光线、湿乱、持物），空间关系不变。"],
  ["局部重画", "edit", "只改指定格，其余格保持不变。"]];

export default function Canvas({ ctx, providers, openBatch }: { ctx: any; providers: any; openBatch: (b: any) => void }) {
  const [doc, setDoc] = useState<{ nodes: Node[]; edges: Edge[] }>({ nodes: [], edges: [] });
  const [loaded,setLoaded] = useState(false);
  const revision = useRef(0);
  const lastSaved = useRef(doc);
  const undo = useRef<typeof doc[]>([]);
  const [zoom,setZoom] = useState(1);
  const [cut,setCut] = useState(false);
  const board = useRef<HTMLDivElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const pan = useRef<{x:number;y:number;left:number;top:number}|null>(null);
  const saves = useRef(Promise.resolve());
  const [saveError, setSaveError] = useState<string | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [required,setRequired] = useState<any>(null);
  const [cand, setCand] = useState<string | null>(null);
  const [registered,setRegistered]=useState("");
  const [link, setLink] = useState({ to: "", kind: "input" });
  const [removeNode,setRemoveNode] = useState<Node|null>(null);
  const [compare, setCompare] = useState(false);
  const act = useAction();
  const drag = useRef<{ id: string; clientX: number; clientY: number; x: number; y: number } | null>(null);
  const load = async () => { const v = await q("canvas_view", { episode_id: ctx.eid }); setDoc(v.doc); lastSaved.current=v.doc; undo.current=[]; revision.current = v.revision; setLoaded(true); setSaveError(null); };
  useEffect(() => { load().catch(err=>setSaveError(err.message)); }, [ctx.eid]);
  const save = (next: typeof doc, record=true) => {
    if (record && JSON.stringify(next)!==JSON.stringify(lastSaved.current)) {
      undo.current=[...undo.current.slice(-39),lastSaved.current];
    }
    lastSaved.current=next;
    setDoc(next);
    saves.current = saves.current.then(async () => {
      const r = await cmd("save_canvas", { episode_id: ctx.eid, doc: next, expected_revision: revision.current });
      revision.current = r.revision;
      setSaveError(null);
    }).catch((err) => setSaveError(err.message));
  };
  const node = doc.nodes.find((n) => n.id === sel);
  const batchOf = (n: Node) => ctx.batches.find((b: any) => b.id === n.data.batchId);
  const assetOf = (id?: string) => ctx.assets.find((a: any) => a.id === id);
  const inputsOf = (n: Node) => doc.edges.filter((e) => e.to === n.id).map((e) => ({ e, n: doc.nodes.find((x) => x.id === e.from)! })).filter((x) => x.n);
  const add = (type: Node["type"]) => {
    const id = `${type}-${Date.now().toString(36)}`;
    const n: Node = { id, type, x: 270 + (doc.nodes.length % 4) * 290, y: 60 + Math.floor(doc.nodes.length / 4) * 300, title: { text: "角色档案", reference: "参考图", lock: "锁定图", generate: "新的生成", usage: "下游用途" }[type],
      data: type === "generate" ? { prompt: "", operation: "generate", provider: Object.keys(providers)[0] ?? "", count: 2, size: providers[Object.keys(providers)[0]]?.capabilities.sizes[0] ?? "" } : type === "text" ? { body: "体型：\n不可变识别点：\n服装：" } : {} };
    save({ ...doc, nodes: [...doc.nodes, n] }); setSel(id);
  };
  const patch = (id: string, p: any, persist = true) => {
    const next = { ...doc, nodes: doc.nodes.map((n) => n.id === id ? { ...n, ...p, data: { ...n.data, ...(p.data ?? {}) } } : n) };
    if (persist) save(next); else setDoc(next);
  };
  const onMove = (e: React.PointerEvent) => { const d = drag.current; if (!d) return; patch(d.id, { x: Math.max(0, d.x + (e.clientX - d.clientX)/zoom), y: Math.max(0, d.y + (e.clientY - d.clientY)/zoom) }, false); };
  const onUp = () => { if (drag.current) { drag.current = null; save(doc); } };
  const generate = (n: Node) => act.run(async () => {
    const ins = inputsOf(n).filter((x) => x.e.kind !== "pending").map((x) => x.n.data.assetVersionId).filter(Boolean);
    const textCtx = inputsOf(n).filter((x) => x.n.type === "text" && x.e.kind !== "pending").map((x) => `${x.n.title}：${x.n.data.body}`).join("\n");
    const inputNotes = inputsOf(n).filter(({ e, n: src }) => e.kind !== "pending" && src.data.assetVersionId)
      .map(({ e, n: src }, i) => `图${i + 1} · ${src.title}（${e.kind === "ref" ? "仅作风格参考" : "实际输入"}）`).join("\n");
    const prompt = [n.data.prompt, textCtx, n.data.operation === "edit" ? "" : inputNotes].filter(Boolean).join("\n");
    let r;
    if (n.data.operation === "edit") {
      const base = assetOf(cand ?? ins[0]);
      if (!base?.grid) throw new Error("局部重画需要先选中一张九宫格候选或接入九宫格");
      r = await cmd("redraw_cells", { episode_id: ctx.eid, base_version_id: base.id, segments:n.data.segments ?? [], cells: n.data.cells ?? [5], reference_version_ids: ins.filter((x: string) => x !== base.id), prompt, provider: n.data.provider, size: n.data.size, quality: n.data.quality ?? "low", budget_cents: budgetCents(n.data.budget ?? 2) });
    } else {
      r = await cmd("create_batch", { episode_id: ctx.eid, provider: n.data.provider, operation: "generate", size: n.data.size, quality: n.data.quality ?? "low", budget_cents: budgetCents(n.data.budget ?? 2),
        items: Array.from({ length: n.data.count }, (_, i) => ({ prompt, inputs: ins, segments: n.data.segments ?? [], target: { name: `${n.title} ${String.fromCharCode(65 + i)}`, category: "state" } })) });
    }
    patch(n.id, { data: { batchId: r.batchId } });
    openBatch(await q("batch_view", { batch_id: r.batchId }));   // 生成前进入批次确认
    ctx.reload();
  });
  const candidates = (n: Node) => (batchOf(n)?.requests ?? []).filter((r: any) => r.asset_version_id).map((r: any) => assetOf(r.asset_version_id)).filter(Boolean);
  const selectedPicture=node?.type==="generate" ? candidates(node).find((a:any)=>a.id===cand)
    : node && (node.type==="reference" || node.type==="lock") ? assetOf(node.data.assetVersionId) : null;
  const center = (n: Node, side: "in" | "out") => ({ x: n.x + (side === "out" ? W[n.type] : 0), y: n.y + 40 });
  const wire = (from:string,to:string,kind:Edge["kind"]="input") => {
    if (!from || from===to || !doc.nodes.some(n=>n.id===from) || !doc.nodes.some(n=>n.id===to)) return;
    save({...doc,edges:[...doc.edges.filter(e=>e.from!==from||e.to!==to),{from,to,kind}]});
  };
  const arrange = () => {
    const counts:Record<number,number>={};
    const nodes=doc.nodes.map(n=>{
      const col={text:0,lock:1,reference:2,generate:3,usage:4}[n.type];
      const index=counts[col] ?? 0; counts[col]=index+1;
      return {...n,x:270+col*330,y:70+index*320};
    });
    save({...doc,nodes});
  };
  function locate(n?:Node) {
    if(!n) return;
    setSel(n.id);
    viewport.current?.scrollTo({left:Math.max(0,n.x*zoom-80),top:Math.max(0,n.y*zoom-70)});
  }
  const width=Math.max(1300,...doc.nodes.map(n=>n.x+W[n.type]+120));
  const height=Math.max(740,...doc.nodes.map(n=>n.y+400));
  if (!loaded) return saveError ? <div className="card"><ErrorLine error={{message:saveError}}/>
    <button className="btn" onClick={()=>load().catch(err=>setSaveError(err.message))}>重新读取画布</button></div> : <p>正在读取画布…</p>;
  return (
    <div ref={board} className="canvas-board">
      <div className="row canvas-tools">
        {(["text", "reference", "lock", "generate", "usage"] as const).map((t) => <button key={t} className="btn ghost" onClick={() => add(t)}>＋ <NodeLabel type={t}/></button>)}
        <button className="btn" onClick={arrange}>整理布局</button><button className="btn" disabled={!undo.current.length} onClick={()=>{const prev=undo.current.pop(); if(prev) save(prev,false);}}>撤销</button>
        <button className={`btn ${cut?"primary":""}`} aria-pressed={cut} onClick={()=>setCut(!cut)}>剪断连线</button>
        <label>缩放 <select value={zoom} onChange={e=>setZoom(Number(e.target.value))}>{[0.5,0.75,1,1.25,1.5].map(z=><option key={z} value={z}>{z*100}%</option>)}</select></label>
        <select aria-label="定位节点" value={sel ?? ""} onChange={e=>locate(doc.nodes.find(n=>n.id===e.target.value))}>
          <option value="">定位节点</option>{doc.nodes.map(n=><option key={n.id} value={n.id}>{n.title}</option>)}</select>
        <select aria-label="跳到画布分区" value="" onChange={e=>locate(doc.nodes.find(n=>n.type===e.target.value))}>
          <option value="">跳到分区</option>{(Object.keys(NODE_TYPES) as Node["type"][]).map(type=>
            <option key={type} value={type} disabled={!doc.nodes.some(n=>n.type===type)}>{NODE_TYPES[type].label}区</option>)}
        </select>
        <button className="btn" onClick={()=>act.run(async()=>{if(document.fullscreenElement) await document.exitFullscreen();else await board.current?.requestFullscreen();})}>全屏</button>
        <a className="btn" href={location.href} target="_blank" rel="noreferrer">独立窗口</a>
      </div>
      {!doc.nodes.length && <div className="card first-use">
        <h2>这张画布还没有节点</h2><p>先写角色或画面要求，再把文本连到生成节点。</p>
        <button className="btn primary" onClick={()=>add("text")}>添加第一个文本节点</button>
      </div>}
      <div ref={viewport} className="canvas-viewport"
        onPointerDown={e=>{if((e.target as HTMLElement).closest(".card,button,input,select,textarea,path"))return;
          const v=e.currentTarget;pan.current={x:e.clientX,y:e.clientY,left:v.scrollLeft,top:v.scrollTop};v.setPointerCapture(e.pointerId);}}
        onPointerMove={e=>{if(pan.current){const p=pan.current;e.currentTarget.scrollTo(p.left+p.x-e.clientX,p.top+p.y-e.clientY);}else onMove(e);}}
        onPointerUp={()=>{pan.current=null;onUp();}} onPointerCancel={()=>{pan.current=null;onUp();}}>
        <div style={{position:"relative",width,height,zoom}} onClick={e=>{if(e.target===e.currentTarget)setSel(null);}}>
        <div className="card canvas-batch-card">
          <b className="small">本集生成记录</b>
          {ctx.batches.slice(-4).map((b: any) => <div key={b.id} className="spread tiny" style={{ marginTop: 4 }}><span>{b.id.slice(0, 6)} {b.spec.items[0]?.target?.name ?? b.operation} · {b.count} 张</span>
            <Tag tone={b.state === "completed" ? "green" : b.state === "pending_reconcile" ? "amber" : "blue"}>{b.state === "completed" ? "已完成" : b.state === "pending_reconcile" ? "待核对" : b.state === "awaiting_authorization" ? "待确认" : b.state}</Tag></div>)}
          <div className="muted tiny" style={{ marginTop: 4 }}>每批都记下实际输入、服务、张数和回执；结果不明标「待核对」。</div>
        </div>
        <svg style={{ position: "absolute", inset: 0, width, height, pointerEvents: "none" }}>
          {doc.edges.map((e, i) => { const a = doc.nodes.find((n) => n.id === e.from), b = doc.nodes.find((n) => n.id === e.to); if (!a || !b) return null;
            const p = center(a, "out"), q2 = center(b, "in");
            return <path key={i} style={{pointerEvents:cut?"stroke":"none",cursor:cut?"crosshair":undefined}} onClick={()=>{if(cut)save({...doc,edges:doc.edges.filter((_,j)=>j!==i)});}} d={`M${p.x},${p.y} C${p.x + 80},${p.y} ${q2.x - 80},${q2.y} ${q2.x},${q2.y}`} fill="none"
              stroke={e.kind === "input" ? "#22d3ee" : e.kind === "pending" ? "#f5b13d" : "#6b7280"} strokeWidth={cut?8:2} strokeDasharray={e.kind === "pending" ? "6 5" : undefined} />; })}
        </svg>
        {doc.nodes.map((n) => {
          const b = n.type === "generate" ? batchOf(n) : null;
          const a = assetOf(n.data.assetVersionId);
          return (
            <div key={n.id} className={`card canvas-node ${sel === n.id ? "sel" : "line"}`} data-node={n.id} style={{ left: n.x, top: n.y, width: W[n.type] }}
              onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();wire(e.dataTransfer.getData("application/x-adw-node"),n.id);}}
              onClick={(e) => { e.stopPropagation(); setSel(n.id); }}>
              <button className="canvas-port" aria-label={n.title+" 输出连线"} draggable onDragStart={e=>e.dataTransfer.setData("application/x-adw-node",n.id)} title="拖至目标节点连接">●</button>
              <div className="spread tiny" style={{ cursor: "move" }} onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); drag.current = { id: n.id, clientX: e.clientX, clientY: e.clientY, x: n.x, y: n.y }; }}>
                <span className="muted"><NodeLabel type={n.type}/></span>
                {b && <Tag tone={b.state === "completed" ? "green" : b.state === "awaiting_authorization" ? "amber" : "blue"}>{b.state === "completed" ? "待评估" : b.state === "awaiting_authorization" ? "待确认" : b.state}</Tag>}
                {(n.type === "lock" || n.type === "reference") && a && <Tag tone={a.marks.includes("adopted") ? "green" : ""}>{a.marks.includes("adopted") ? "已采用" : `v${a.version_no}`}</Tag>}
              </div>
              <input value={n.title} onChange={(e) => patch(n.id, { title: e.target.value }, false)} onBlur={() => save(doc)} className="canvas-node-title" />
              {n.type === "text" && <textarea value={n.data.body} onChange={(e) => patch(n.id, { data: { body: e.target.value } }, false)} onBlur={() => save(doc)} className="small canvas-node-body" />}
              {(n.type === "reference" || n.type === "lock") && <>
                {a ? <img src={blob(a.sha256)} className="canvas-node-preview" /> : <div className="muted tiny">选择素材</div>}
                <select value={n.data.assetVersionId ?? ""} onChange={(e) => patch(n.id, { data: { assetVersionId: e.target.value } })} className="tiny canvas-node-select">
                  <option value="">选择本集素材…</option>{ctx.assets.filter((x: any) => x.kind === "image").map((x: any) => <option key={x.id} value={x.id}>{x.name} v{x.version_no}</option>)}</select>
                <div className="muted tiny" style={{ marginTop: 4 }}>{n.type === "reference" ? "只作画风与同框比例参考" : "空间与身份以此为准"}</div></>}
              {n.type === "generate" && <>
                <div className="row" style={{ flexWrap: "nowrap", gap: 4 }}>{candidates(n).length ? candidates(n).map((c: any, i: number) => (
                  <div key={c.id} style={{ flex: 1, cursor: "pointer", outline: cand === c.id ? "2px solid #22d3ee" : "none", borderRadius: 6 }} onClick={(e) => { e.stopPropagation(); setSel(n.id); setCand(c.id); }}>
                    <img src={blob(c.sha256)} className="canvas-node-preview" style={{filter: compare && i > 0 ? "grayscale(1)" : undefined}} />
                    <div className="tiny">{String.fromCharCode(65 + i)}{c.marks.includes("required") ? " · 必用" : c.marks.includes("backup") ? " · 补选" : ""}</div></div>))
                  : <div className="muted tiny">还没有候选。连接输入后在下方写要求并生成。</div>}</div>
                <div className="tiny" style={{ color: "var(--cyan)", marginTop: 4 }}>当前已连输入：{inputsOf(n).filter(x => x.e.kind !== "pending" && (x.n.type === "text" || x.n.data.assetVersionId)).map(x => x.n.title).join("、") || "无"}</div>
                {batchOf(n) && <button className="btn" onClick={e => { e.stopPropagation(); openBatch(batchOf(n)); }}>查看冻结批次</button>}</>}
              {n.type === "usage" && <UsageNode n={n} doc={doc} assetOf={assetOf} ctx={ctx} />}
            </div>);
        })}
        {node && <div className="row canvas-node-tools" style={{left: node.x, top: Math.max(0, node.y - 44)}}>
          {node.type === "generate" && PRESETS.map(([l, op, text]) => <button key={l} className="btn ghost" onClick={() => patch(node.id, { data: { operation: op, prompt: text } })}>{l}</button>)}
          {node.type === "generate" && <button className="btn ghost" onClick={() => setCompare(!compare)}>看图评估</button>}
          {["generate","reference","lock"].includes(node.type) && <button className="btn ghost" disabled={!selectedPicture || !!selectedPicture.grid || act.busy} onClick={()=>act.run(async()=>{
            await cmd("submit_reuse",{version_id:selectedPicture.id,usage:node.title});setRegistered(selectedPicture.id);
          })}>{selectedPicture && registered===selectedPicture.id ? "已登记到复用库" : "登记到复用库"}</button>}
          {node.type === "generate" && selectedPicture && <button className="btn ghost" onClick={() => setRequired(selectedPicture)}>必用要求</button>}
          <select value={link.to} onChange={(e) => setLink({ ...link, to: e.target.value })} style={{ fontSize: "0.92rem" }}><option value="">连线到…</option>
            {doc.nodes.filter((x) => x.id !== node.id).map((x) => <option key={x.id} value={x.id}>{x.title}</option>)}</select>
          <select value={link.kind} onChange={(e) => setLink({ ...link, kind: e.target.value })} style={{ fontSize: "0.92rem" }}><option value="input">实际输入</option><option value="ref">只作参考</option><option value="pending">待采用后接入</option></select>
          <button className="btn" disabled={!link.to} onClick={() => { wire(node.id,link.to,link.kind as Edge["kind"]); setLink({ to: "", kind: "input" }); }}>连线</button>
          <button className="btn danger" onClick={()=>setRemoveNode(node)}>删除</button>
        </div>}
        <div className="row tiny" style={{ position: "absolute", left: 12, bottom: 12, zIndex: 3, background: "#16171a", padding: "6px 10px", borderRadius: 8 }}>
          <span style={{ color: "#22d3ee" }}>— 实际输入（写入本集清单）</span><span style={{ color: "#f5b13d" }}>- - 待采用后才能接入</span><span className="muted">灰线：只作参考</span></div>
      </div>
      </div>
      {node?.type === "generate" && (
        <div className="card" style={{ position: "sticky", bottom: 10, marginTop: 10, border: "1px solid var(--line)" }}>
          <div className="row tiny">{inputsOf(node).map((x) => <button key={x.n.id} className="tag" title="在要求中引用已连接节点" onClick={()=>patch(node.id,{data:{prompt:(node.data.prompt ?? "")+" @"+x.n.title}})}>{x.n.title}{x.e.kind === "ref" ? " · 只作画风" : ""}</button>)}</div>
          <textarea placeholder="对这个生成节点写要求，例：正面全身，中性站姿…" value={node.data.prompt} onChange={(e) => patch(node.id, { data: { prompt: e.target.value } }, false)} onBlur={() => save(doc)} style={{ marginTop: 6 }} />
          <div className="row small">
            <select value={node.data.provider} onChange={(e) => patch(node.id, { data: { provider: e.target.value, size: providers[e.target.value]?.capabilities.sizes[0] } })}>{Object.keys(providers).map((p) => <option key={p}>{p}</option>)}</select>
            <select value={node.data.count} onChange={(e) => patch(node.id, { data: { count: +e.target.value } })}>{[1, 2, 3, 4].map((c) => <option key={c} value={c}>{c} 张</option>)}</select>
            <select value={node.data.size} onChange={(e) => patch(node.id, { data: { size: e.target.value } })}>{(providers[node.data.provider]?.capabilities.sizes ?? []).map((s: string) => <option key={s}>{s}</option>)}</select>
            <label>用于段 <input key={node.id+JSON.stringify(node.data.segments)} placeholder="G01、G02" style={{width:95}}
              defaultValue={(node.data.segments ?? []).join("、")} onBlur={e=>{
                const segments = Array.from(new Set(e.target.value.split(/[,，、\s]+/).filter(Boolean)));
                if (JSON.stringify(segments)!==JSON.stringify(node.data.segments ?? [])) patch(node.id,{data:{segments}});
              }}/></label>
            <select aria-label="图像质量" value={node.data.quality ?? "low"} onChange={(e) => patch(node.id, { data: { quality: e.target.value } })}>{(providers[node.data.provider]?.capabilities.quality ?? []).map((v: string) => <option key={v}>{v}</option>)}</select>
            <span className="muted">{node.data.operation === "edit" ? "局部重画（选中九宫格候选）" : "生成"} · 预算 {providers[node.data.provider]?.currency}</span>
            <input style={{ width: 60 }} value={node.data.budget ?? 2} onChange={(e) => patch(node.id, { data: { budget: e.target.value } }, false)} />
            <span className="muted tiny" style={{ marginLeft: "auto" }}>生成前进入批次确认，不会直接扣费</span>
            <button className="btn primary" disabled={act.busy} onClick={() => generate(node)}>{node.data.operation === "edit" ? "局部重画 1 张" : `生成 ${node.data.count} 张`} ›</button>
          </div>
        </div>)}
      {removeNode && <Modal onClose={()=>setRemoveNode(null)}><h2>移除节点“{removeNode.title}”及其连线？</h2>
        <button className="btn" autoFocus onClick={()=>setRemoveNode(null)}>取消</button>
        <button className="btn danger" onClick={()=>{
          const id=removeNode.id;
          save({...doc,nodes:doc.nodes.filter(n=>n.id!==id),edges:doc.edges.filter(e=>e.from!==id && e.to!==id)});
          setRemoveNode(null);setSel(null);
        }}>确认移除节点</button></Modal>}
      {required && <RequiredImage ctx={ctx} asset={required} close={()=>setRequired(null)}/>}
      {saveError && <div className="row"><ErrorLine error={{ message: saveError }} /><button className="btn" onClick={async () => { await saves.current; await load().catch(err=>setSaveError(err.message)); }}>重新载入已保存画布</button></div>}
      <ErrorLine error={act.error} />
    </div>
  );
}

function UsageNode({ n, doc, assetOf, ctx }: any) {
  const [refs, setRefs] = useState<any[]>([]);
  const src = doc.edges.find((e: any) => e.to === n.id);
  const from = doc.nodes.find((x: any) => x.id === src?.from);
  const vid = from?.type === "generate" ? ctx.batches.find((b: any) => b.id === from.data.batchId)?.requests.find((r: any) => r.asset_version_id)?.asset_version_id : from?.data.assetVersionId;
  useEffect(() => { if (vid) q("image_references", { version_id: vid }).then(setRefs).catch(() => setRefs([])); }, [vid]);
  const a = assetOf(vid);
  return <div className="small">
    <div className="muted tiny">输入：{from ? from.title : "未连线"}{a ? ` · ${a.name} v${a.version_no}` : ""}</div>
    <div style={{ color: "var(--cyan)" }} className="tiny">用于：{refs.length ? refs.map((r) => `${r.segment} ${r.label}`).join("、") : "尚未被提示词引用"}</div>
    {src?.kind === "pending" && <div className="tiny" style={{ color: "var(--amber)" }}>待采用后才能接入</div>}
  </div>;
}
