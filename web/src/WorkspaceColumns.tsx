import { useRef } from "react";

const limits = [[160,200,320], [300,380,560]] as const;
export type Widths = [number,number];

export function savedWidths(): Widths {
  let saved;
  try { saved = JSON.parse(localStorage.getItem("ws-cols") || "null"); } catch { /* use defaults */ }
  const width = (side: 0|1) => {
    const [min, fallback, max] = limits[side];
    const value = saved?.[side];
    return Number.isFinite(value) ? Math.min(max,Math.max(min,value)) : fallback;
  };
  return [width(0),width(1)];
}

export function ColumnDivider({side, width, resize}: {side: 0|1; width: number; resize: (width:number)=>void}) {
  const start = useRef<{x:number; width:number} | null>(null);
  const [min, fallback, max] = limits[side];
  const direction = side === 0 ? 1 : -1;
  const label = side === 0 ? "调整目录宽度" : "调整助手宽度";
  const apply = (value: number) => resize(Math.max(min,Math.min(max,value)));
  return <div role="separator" aria-orientation="vertical" aria-label={label} tabIndex={0}
    aria-valuemin={min} aria-valuemax={max} aria-valuenow={width} title={label+"，双击恢复默认宽度"}
    className="colgrip" style={side === 0 ? {left:width+8} : {right:width}}
    onDoubleClick={()=>apply(fallback)}
    onKeyDown={e=>{
      if (e.key==="Home") {e.preventDefault(); apply(fallback);}
      if (e.key==="ArrowLeft" || e.key==="ArrowRight") {
        e.preventDefault(); apply(width+direction*(e.key==="ArrowLeft"?-10:10));
      }
    }}
    onPointerDown={e=>{
      if(e.button!==0 || !e.isPrimary) return;
      start.current={x:e.clientX,width}; e.currentTarget.setPointerCapture(e.pointerId);
    }}
    onPointerMove={e=>{if(start.current && e.currentTarget.hasPointerCapture(e.pointerId))
      apply(start.current.width+(e.clientX-start.current.x)*direction);}}
    onLostPointerCapture={()=>{start.current=null;}} />;
}
