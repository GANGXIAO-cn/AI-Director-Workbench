/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useRef, useState } from "react";
import { blob, cmd, q } from "./api";
import { ErrorLine, Tag, useAction } from "./ui";

/** J15 本集素材 · 生成画布: inputs → generate node → candidates → downstream use.
 *  Layout/wiring is saved per episode; every generation still goes through the paid-batch confirmation. */
type Node = { id: string; type: "text" | "reference" | "lock" | "generate" | "usage"; x: number; y: number; title: string; data: any };
type Edge = { from: string; to: string; kind: "input" | "ref" | "pending" };
const W: Record<string, number> = { text: 200, reference: 190, lock: 190, generate: 250, usage: 210 };
const TYPE_LABEL: Record<string, string> = { text: "▤ 文本", reference: "▣ 参考图", lock: "▣ 锁定图", generate: "✦ 生成", usage: "✦ 下游用途" };
const PRESETS: [string, string, string][] = [["身份图", "generate", "正面全身，中性站姿，无剧情动作，纯色浅底；识别点必须清楚；不裁切。"],
  ["多角度", "generate", "同一角色正面、侧面、背面三视图，比例一致，纯色浅底。"],
  ["状态派生", "generate", "以锁定图为准，只改本段需要的状态（光线、湿乱、持物），空间关系不变。"],
  ["局部重画", "edit", "只改指定格，其余格保持不变。"]];

export default function Canvas({ ctx, providers, openBatch }: { ctx: any; providers: any; openBatch: (b: any) => void }) {
  const [doc, setDoc] = useState<{ nodes: Node[]; edges: Edge[] }>({ nodes: [], edges: [] });
  const [rev, setRev] = useState(0);
  const [sel, setSel] = useState<string | null>(null);
  const [cand, setCand] = useState<string | null>(null);
  const [link, setLink] = useState({ to: "", kind: "input" });
  const [compare, setCompare] = useState(false);
  const act = useAction();
  const drag = useRef<{ id: string; dx: number; dy: number } | null>(null);
  const load = async () => { const v = await q("canvas_view", { episode_id: ctx.eid }); setDoc(v.doc); setRev(v.revision); };
  useEffect(() => { load(); }, [ctx.eid]);
  const save = (next: typeof doc) => act.run(async () => {
    setDoc(next);
    const r = await cmd("save_canvas", { episode_id: ctx.eid, doc: next, expected_revision: rev });
    setRev(r.revision);
  });
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
  const onMove = (e: React.MouseEvent) => { const d = drag.current; if (!d) return; patch(d.id, { x: e.nativeEvent.offsetX - d.dx, y: e.nativeEvent.offsetY - d.dy }, false); };
  const onUp = () => { if (drag.current) { drag.current = null; save(doc); } };
  const generate = (n: Node) => act.run(async () => {
    const ins = inputsOf(n).filter((x) => x.e.kind !== "pending").map((x) => x.n.data.assetVersionId).filter(Boolean);
    const textCtx = inputsOf(n).filter((x) => x.n.type === "text").map((x) => `${x.n.title}：${x.n.data.body}`).join("\n");
    const prompt = [n.data.prompt, textCtx].filter(Boolean).join("\n");
    let r;
    if (n.data.operation === "edit") {
      const base = assetOf(cand ?? ins[0]);
      if (!base?.grid) throw new Error("局部重画需要先选中一张九宫格候选或接入九宫格");
      r = await cmd("redraw_cells", { episode_id: ctx.eid, base_version_id: base.id, cells: n.data.cells ?? [5], reference_version_ids: ins.filter((x: string) => x !== base.id), prompt, provider: n.data.provider, size: n.data.size, budget_cents: Math.round((n.data.budget ?? 2) * 100) });
    } else {
      r = await cmd("create_batch", { episode_id: ctx.eid, provider: n.data.provider, operation: "generate", size: n.data.size, budget_cents: Math.round((n.data.budget ?? 2) * 100),
        items: Array.from({ length: n.data.count }, (_, i) => ({ prompt, inputs: ins, segments: n.data.segments ?? [], target: { name: `${n.title} ${String.fromCharCode(65 + i)}`, category: "state" } })) });
    }
    patch(n.id, { data: { batchId: r.batchId } });
    openBatch(await q("batch_view", { batch_id: r.batchId }));   // 生成前进入批次确认
    ctx.reload();
  });
  const candidates = (n: Node) => (batchOf(n)?.requests ?? []).filter((r: any) => r.asset_version_id).map((r: any) => assetOf(r.asset_version_id)).filter(Boolean);
  const center = (n: Node, side: "in" | "out") => ({ x: n.x + (side === "out" ? W[n.type] : 0), y: n.y + 40 });
  return (
    <div style={{ position: "relative" }}>
      <div className="row" style={{ marginBottom: 8 }}>
        {(["text", "reference", "lock", "generate", "usage"] as const).map((t) => <button key={t} className="btn ghost" onClick={() => add(t)}>＋ {TYPE_LABEL[t]}</button>)}
        <span className="muted small">节点 {doc.nodes.length} · 拖动节点标题移动；选中节点出现工具条</span>
      </div>
      <div style={{ position: "relative", height: 760, overflow: "auto", background: "#0f1013", borderRadius: 12, border: "1px solid var(--line)" }}
        onMouseMove={onMove} onMouseUp={onUp} onClick={(e) => { if (e.target === e.currentTarget) setSel(null); }}>
        <div className="card" style={{ position: "absolute", left: 12, top: 12, width: 230, zIndex: 3, padding: 10 }}>
          <b className="small">本集生成记录</b>
          {ctx.batches.slice(-4).map((b: any) => <div key={b.id} className="spread tiny" style={{ marginTop: 4 }}><span>{b.id.slice(0, 6)} {b.spec.items[0]?.target?.name ?? b.operation} · {b.count} 张</span>
            <Tag tone={b.state === "completed" ? "green" : b.state === "pending_reconcile" ? "amber" : "blue"}>{b.state === "completed" ? "已完成" : b.state === "pending_reconcile" ? "待核对" : b.state === "awaiting_authorization" ? "待确认" : b.state}</Tag></div>)}
          <div className="muted tiny" style={{ marginTop: 4 }}>每批都记下实际输入、服务、张数和回执；结果不明标「待核对」。</div>
        </div>
        <svg style={{ position: "absolute", inset: 0, width: 2400, height: 1400, pointerEvents: "none" }}>
          {doc.edges.map((e, i) => { const a = doc.nodes.find((n) => n.id === e.from), b = doc.nodes.find((n) => n.id === e.to); if (!a || !b) return null;
            const p = center(a, "out"), q2 = center(b, "in");
            return <path key={i} d={`M${p.x},${p.y} C${p.x + 80},${p.y} ${q2.x - 80},${q2.y} ${q2.x},${q2.y}`} fill="none"
              stroke={e.kind === "input" ? "#22d3ee" : e.kind === "pending" ? "#f5b13d" : "#6b7280"} strokeWidth={1.6} strokeDasharray={e.kind === "pending" ? "6 5" : undefined} />; })}
        </svg>
        {doc.nodes.map((n) => {
          const b = n.type === "generate" ? batchOf(n) : null;
          const a = assetOf(n.data.assetVersionId);
          return (
            <div key={n.id} className={`card ${sel === n.id ? "sel" : ""}`} data-node={n.id} style={{ position: "absolute", left: n.x, top: n.y, width: W[n.type], padding: 10, zIndex: 2, border: sel === n.id ? "1.5px solid var(--white)" : "1px solid var(--line)" }}
              onClick={(e) => { e.stopPropagation(); setSel(n.id); }}>
              <div className="spread tiny" style={{ cursor: "move" }} onMouseDown={(e) => { drag.current = { id: n.id, dx: e.nativeEvent.offsetX, dy: e.nativeEvent.offsetY }; }}>
                <span className="muted">{TYPE_LABEL[n.type]}</span>
                {b && <Tag tone={b.state === "completed" ? "green" : b.state === "awaiting_authorization" ? "amber" : "blue"}>{b.state === "completed" ? "待评估" : b.state === "awaiting_authorization" ? "待确认" : b.state}</Tag>}
                {(n.type === "lock" || n.type === "reference") && a && <Tag tone={a.marks.includes("adopted") ? "green" : ""}>{a.marks.includes("adopted") ? "已采用" : `v${a.version_no}`}</Tag>}
              </div>
              <input value={n.title} onChange={(e) => patch(n.id, { title: e.target.value }, false)} onBlur={() => save(doc)} style={{ width: "100%", fontWeight: 700, background: "transparent", border: 0, padding: "4px 0" }} />
              {n.type === "text" && <textarea value={n.data.body} onChange={(e) => patch(n.id, { data: { body: e.target.value } }, false)} onBlur={() => save(doc)} style={{ minHeight: 90, fontSize: 12 }} />}
              {(n.type === "reference" || n.type === "lock") && <>
                {a ? <img src={blob(a.sha256)} style={{ width: "100%", borderRadius: 6 }} /> : <div className="muted tiny">选择素材</div>}
                <select value={n.data.assetVersionId ?? ""} onChange={(e) => patch(n.id, { data: { assetVersionId: e.target.value } })} style={{ width: "100%", marginTop: 4, fontSize: 11 }}>
                  <option value="">选择本集素材…</option>{ctx.assets.filter((x: any) => x.kind === "image").map((x: any) => <option key={x.id} value={x.id}>{x.name} v{x.version_no}</option>)}</select>
                <div className="muted tiny" style={{ marginTop: 4 }}>{n.type === "reference" ? "只作画风与同框比例参考" : "空间与身份以此为准"}</div></>}
              {n.type === "generate" && <>
                <div className="row" style={{ flexWrap: "nowrap", gap: 4 }}>{candidates(n).length ? candidates(n).map((c: any, i: number) => (
                  <div key={c.id} style={{ flex: 1, cursor: "pointer", outline: cand === c.id ? "2px solid #22d3ee" : "none", borderRadius: 6 }} onClick={(e) => { e.stopPropagation(); setSel(n.id); setCand(c.id); }}>
                    <img src={blob(c.sha256)} style={{ width: "100%", borderRadius: 6, filter: compare && i > 0 ? "grayscale(1)" : undefined }} />
                    <div className="tiny">{String.fromCharCode(65 + i)}{c.marks.includes("required") ? " · 必用" : c.marks.includes("backup") ? " · 补选" : ""}</div></div>))
                  : <div className="muted tiny">还没有候选。连接输入后在下方写要求并生成。</div>}</div>
                <div className="tiny" style={{ color: "var(--cyan)", marginTop: 4 }}>输入：{inputsOf(n).map((x) => x.n.title).join("、") || "无"}</div></>}
              {n.type === "usage" && <UsageNode n={n} doc={doc} assetOf={assetOf} ctx={ctx} />}
            </div>);
        })}
        {node && <div className="row" style={{ position: "absolute", left: node.x, top: Math.max(0, node.y - 44), zIndex: 4, background: "#1c1d21", border: "1px solid var(--line)", borderRadius: 10, padding: 4 }}>
          {node.type === "generate" && PRESETS.map(([l, op, text]) => <button key={l} className="btn ghost" onClick={() => patch(node.id, { data: { operation: op, prompt: text } })}>{l}</button>)}
          {node.type === "generate" && <button className="btn ghost" onClick={() => setCompare(!compare)}>看图评估</button>}
          {node.type === "generate" && <button className="btn ghost" disabled={!cand} onClick={() => act.run(async () => { await cmd("submit_reuse", { version_id: cand, usage: node.title }); ctx.reload(); })}>登记到复用库</button>}
          {node.type === "generate" && cand && <button className="btn ghost" onClick={() => act.run(async () => { await cmd("mark_image", { episode_id: ctx.eid, version_id: cand, mark: "required" }); ctx.reload(); })}>标必用</button>}
          <select value={link.to} onChange={(e) => setLink({ ...link, to: e.target.value })} style={{ fontSize: 12 }}><option value="">连线到…</option>
            {doc.nodes.filter((x) => x.id !== node.id).map((x) => <option key={x.id} value={x.id}>{x.title}</option>)}</select>
          <select value={link.kind} onChange={(e) => setLink({ ...link, kind: e.target.value })} style={{ fontSize: 12 }}><option value="input">实际输入</option><option value="ref">只作参考</option><option value="pending">待采用后接入</option></select>
          <button className="btn" disabled={!link.to} onClick={() => { save({ ...doc, edges: [...doc.edges, { from: node.id, to: link.to, kind: link.kind as Edge["kind"] }] }); setLink({ to: "", kind: "input" }); }}>连线</button>
          <button className="btn danger" onClick={() => { save({ nodes: doc.nodes.filter((x) => x.id !== node.id), edges: doc.edges.filter((e) => e.from !== node.id && e.to !== node.id) }); setSel(null); }}>删除</button>
        </div>}
        <div className="row tiny" style={{ position: "absolute", left: 12, bottom: 12, zIndex: 3, background: "#16171a", padding: "6px 10px", borderRadius: 8 }}>
          <span style={{ color: "#22d3ee" }}>— 实际输入（写入本集清单）</span><span style={{ color: "#f5b13d" }}>- - 待采用后才能接入</span><span className="muted">灰线：只作参考</span></div>
      </div>
      {node?.type === "generate" && (
        <div className="card" style={{ position: "sticky", bottom: 10, marginTop: 10, border: "1px solid var(--line)" }}>
          <div className="row tiny">{inputsOf(node).map((x) => <span key={x.n.id} className="tag">{x.n.title}{x.e.kind === "ref" ? " · 只作画风" : ""}</span>)}</div>
          <textarea placeholder="对这个生成节点写要求，例：正面全身，中性站姿…" value={node.data.prompt} onChange={(e) => patch(node.id, { data: { prompt: e.target.value } }, false)} onBlur={() => save(doc)} style={{ marginTop: 6 }} />
          <div className="row small">
            <select value={node.data.provider} onChange={(e) => patch(node.id, { data: { provider: e.target.value, size: providers[e.target.value]?.capabilities.sizes[0] } })}>{Object.keys(providers).map((p) => <option key={p}>{p}</option>)}</select>
            <select value={node.data.count} onChange={(e) => patch(node.id, { data: { count: +e.target.value } })}>{[1, 2, 3, 4].map((c) => <option key={c} value={c}>{c} 张</option>)}</select>
            <select value={node.data.size} onChange={(e) => patch(node.id, { data: { size: e.target.value } })}>{(providers[node.data.provider]?.capabilities.sizes ?? []).map((s: string) => <option key={s}>{s}</option>)}</select>
            <span className="muted">{node.data.operation === "edit" ? "局部重画（选中九宫格候选）" : "生成"} · 预算 ¥</span>
            <input style={{ width: 60 }} value={node.data.budget ?? 2} onChange={(e) => patch(node.id, { data: { budget: +e.target.value } }, false)} />
            <span className="muted tiny" style={{ marginLeft: "auto" }}>生成前进入批次确认，不会直接扣费</span>
            <button className="btn primary" disabled={act.busy} onClick={() => generate(node)}>{node.data.operation === "edit" ? "局部重画 1 张" : `生成 ${node.data.count} 张`} ›</button>
          </div>
        </div>)}
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
