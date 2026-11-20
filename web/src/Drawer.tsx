import { createPortal } from "react-dom";
import { useEffect, useRef, type ReactNode } from "react";

type DialogProps = {label:string;close:()=>void;children:ReactNode};

export function Drawer(props:DialogProps) {
  return <WorkbenchDialog {...props} placement="side"/>;
}

export function WorkbenchDialog({label, close, children, placement}:{placement:"side"|"center"} & DialogProps) {
  const ref=useRef<HTMLDialogElement>(null);
  useEffect(()=>{
    const dialog=ref.current!;
    const opener=document.activeElement;
    dialog.showModal();
    return ()=>{
      dialog.close();
      if(opener instanceof HTMLElement && opener.isConnected)opener.focus({preventScroll:true});
    };
  },[]);
  return createPortal(<dialog ref={ref} className={placement==="center"?"media-dialog":"input-drawer"} aria-label={label} onCancel={close}
    onClick={e=>{
      if(e.target!==e.currentTarget)return;
      const bounds=e.currentTarget.getBoundingClientRect();
      if(e.clientX<bounds.left || e.clientX>bounds.right || e.clientY<bounds.top || e.clientY>bounds.bottom)close();
    }}>
    <header className="spread"><h3>{label}</h3><button className="btn" onClick={close}>关闭</button></header>
    {children}
  </dialog>,document.body);
}
