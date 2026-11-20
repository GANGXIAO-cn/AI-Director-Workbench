export type Rework = {
  number:number;
  change:string;
  review?:{reason?:string;usableStart?:number;usableEnd?:number;observations?:string[]};
};

function Note({note}:{note:Rework}){
  const r=note.review;
  return <>
    {r?.reason && <p><strong>返工原因</strong>　{r.reason}</p>}
    {r?.usableStart!=null && r.usableEnd!=null && <p>{r.usableEnd>r.usableStart
      ? `原片可用 ${r.usableStart}–${r.usableEnd} 秒` : "本轮没有可用区间"}</p>}
    <p><strong>本段局部审片修改意见</strong>　{note.change}</p>
    {!!r?.observations?.length && <details><summary>逐时段审片记录</summary>
      {r.observations.map((line,i)=><p key={i}>{line}</p>)}</details>}
  </>;
}

export function ReworkNotes({notes,segment}:{notes:Rework[];segment:string}){
  const latest=notes.at(-1);
  if(!latest)return null;
  return <section className="rework-notes" aria-label={segment+" 返工记录"}>
    <b>{segment} · 第 {latest.number} 次返工</b>
    <Note note={latest}/>
    {notes.slice(0,-1).reverse().map(note=><details key={note.number}>
      <summary>第 {note.number} 次返工</summary><Note note={note}/></details>)}
  </section>;
}
