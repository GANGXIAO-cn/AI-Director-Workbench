import { createPortal } from "react-dom";
import { useEffect, useRef, type ReactNode } from "react";

export function Drawer({label, close, children}:{label:string;close:()=>void;children:ReactNode}) {
  const ref=useRef<HTMLDialogElement>(null);
  useEffect(()=>{
    const dialog=ref.current!;
    dialog.showModal();
    return ()=>dialog.close();
  },[]);
  return createPortal(<dialog ref={ref} className="input-drawer" aria-label={label} onCancel={close}
    onClick={e=>{if(e.target===ref.current && e.clientX < ref.current.getBoundingClientRect().left) close();}}>
    <header className="spread"><h3>{label}</h3><button className="btn" onClick={close}>关闭</button></header>
    {children}
  </dialog>,document.body);
}
