import { useState } from "react";
import { Modal, rno, useQuery } from "./ui";

type Version = {id:string; display_no:number; created_at:number};
const labels:Record<string,string> = {synopsis:"故事梗概",cast:"角色表",handoff:"交接说明",notes:"备注",spine:"故事脊",
  timeline:"故事时间线",props:"规则与道具",causes:"因果",adaptation:"改编依据",scenes:"分场",shots:"镜头",
  segments:"生成段",rhythmTable:"节奏表",title:"标题",body:"正文",tags:"话题标签",music:"音乐",cues:"音乐对位",
  extraFields:"项目附加字段",suno_title:"配乐标题",suno_style:"配乐风格",suno_exclude:"排除风格"};
export function VersionCompare({versions,current,adopted,close}:{
  versions:Version[];current:string;adopted:string|null;close:()=>void;
}) {
  const other = adopted && adopted!==current ? adopted : versions.filter(v=>v.id!==current).at(-1)!.id;
  const [left,setLeft] = useState(other);
  const [right,setRight] = useState(current);
  const diff = useQuery<{fields:Record<string,{op:string;text:string}[]>}>("compare",{a_id:left,b_id:right});
  const options=versions.map(v=><option value={v.id} key={v.id}>{rno(v.display_no)} · {new Date(v.created_at*1000).toLocaleString()}</option>);
  return <Modal width={820} onClose={close}><div className="spread"><h2>版本差异</h2><button className="btn" onClick={close}>关闭</button></div>
    <div className="row"><label>原版本 <select value={left} onChange={e=>setLeft(e.target.value)}>{options}</select></label>
      <label>比较版本 <select value={right} onChange={e=>setRight(e.target.value)}>{options}</select></label></div>
    {diff.error && <p className="error">{diff.error}</p>}
    {diff.data && (Object.keys(diff.data.fields).length ? Object.entries(diff.data.fields).map(([key,parts])=>
      <section key={key}><h3>{labels[key] ?? key}</h3><div className="field">{parts.map((p,i)=>p.op==="del" ?
        <del className="d" key={i}>{p.text}</del> : p.op==="add" ? <ins className="d" key={i}>{p.text}</ins> : p.text)}</div></section>)
      : <p>两版内容相同。</p>)}
  </Modal>;
}
