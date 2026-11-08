import { createContext, useContext, useEffect, useLayoutEffect, useState, type ReactNode } from "react";

const sizes = [12, 13, 15, 17] as const;
type Preferences = { size: number; focusChat: boolean };
const defaults: Preferences = { size: 13, focusChat: false };
const storageKey = "director.display.v1";

function read(): Preferences {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || "null");
    return { size: sizes.includes(saved?.size) ? saved.size : defaults.size, focusChat: saved?.focusChat === true };
  } catch { return defaults; }
}

const Display = createContext({ ...defaults, setSize: (_size:number) => {}, setFocusChat: (_enabled:boolean) => {} });
export const useDisplay = () => useContext(Display);

export function DisplayProvider({children}:{children:ReactNode}) {
  const [prefs, setPrefs] = useState(read);
  function update(changes: Partial<Preferences>) {
    const next = {...prefs, ...changes};
    try { localStorage.setItem(storageKey, JSON.stringify(next)); } catch { /* still apply in this window */ }
    setPrefs(next);
  }
  useEffect(() => {
    const sync = (event:StorageEvent) => { if (event.key === storageKey || event.key === null) setPrefs(read()); };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, []);
  useLayoutEffect(() => { document.documentElement.style.fontSize = prefs.size+"px"; }, [prefs.size]);
  return <Display.Provider value={{...prefs, setFocusChat: focusChat => update({focusChat}),
    setSize: size => { if (sizes.some(s=>s===size)) update({size}); }}}>{children}</Display.Provider>;
}

export function DisplaySettings() {
  const {size, focusChat, setSize, setFocusChat} = useDisplay();
  const index = sizes.findIndex(value=>value===size);
  return <section className="display-settings"><h1>界面与快捷键</h1>
    <h2>界面</h2>
    <div className="setting-line"><label id="font-label">界面字号</label><div className="row" role="group" aria-labelledby="font-label">
      <button className="btn" aria-label="缩小字号" disabled={index===0} onClick={()=>setSize(sizes[index-1])}>A−</button>
      <output aria-live="polite">{size} px</output>
      <button className="btn" aria-label="放大字号" disabled={index===sizes.length-1} onClick={()=>setSize(sizes[index+1])}>A+</button>
      <button className="btn ghost" disabled={size===defaults.size} onClick={()=>setSize(defaults.size)}>恢复默认字号</button>
    </div></div>
    <label className="setting-line"><span>专注对话</span><input type="checkbox" checked={focusChat} onChange={e=>setFocusChat(e.target.checked)}/></label>
    <p className="muted">打开单集时展开导演助手。可在助手顶部返回成果文档。</p>
    <h2>快捷键</h2><dl className="shortcuts">
      <dt>发送导演助手消息</dt><dd><kbd>⌘</kbd> / <kbd>Ctrl</kbd> + <kbd>Enter</kbd></dd>
      <dt>退出专注对话</dt><dd><kbd>Esc</kbd></dd>
      <dt>调整分栏宽度</dt><dd>聚焦分隔线后按 <kbd>←</kbd> 或 <kbd>→</kbd></dd>
      <dt>恢复分栏宽度</dt><dd>聚焦分隔线后按 <kbd>Home</kbd>，或双击分隔线</dd>
    </dl></section>;
}
