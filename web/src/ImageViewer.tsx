import { useEffect, useState } from "react";

/**
 * 实测 126：全站图片在本页浮层中查看，不再弹出新页面。
 * 拦截所有指向 /api/blob/ 的链接点击；能按图片显示的在浮层打开，不是图片（加载失败）时才退回新窗口。
 */
export function ImageViewer() {
  const [src, setSrc] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [actual, setActual] = useState(false);
  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const link = (event.target as HTMLElement | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!link) return;
      const href = link.getAttribute("href") || "";
      if (!href.startsWith("/api/blob/")) return;
      event.preventDefault();
      const img = link.querySelector("img");
      setLabel(img?.getAttribute("alt") || link.getAttribute("aria-label") || link.textContent?.trim() || "图片");
      setActual(false);
      setSrc(href);
    };
    document.addEventListener("click", onClick, true);
    return () => document.removeEventListener("click", onClick, true);
  }, []);
  useEffect(() => {
    if (!src) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setSrc(null); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [src]);
  if (!src) return null;
  return <div className="image-viewer" role="dialog" aria-modal="true" aria-label={"查看图片 · " + label} onClick={() => setSrc(null)}>
    <div className="image-viewer-bar" onClick={e => e.stopPropagation()}>
      <span className="image-viewer-title">{label}</span>
      <button className="btn ghost" onClick={() => setActual(!actual)}>{actual ? "适应窗口" : "原始尺寸"}</button>
      <button className="btn" onClick={() => setSrc(null)} aria-label="关闭图片">关闭</button>
    </div>
    <div className={"image-viewer-stage" + (actual ? " actual" : "")} onClick={e => e.stopPropagation()}>
      <img src={src} alt={label} onClick={() => setActual(!actual)}
        onError={() => { window.open(src, "_blank", "noopener"); setSrc(null); }}/>
    </div>
  </div>;
}
