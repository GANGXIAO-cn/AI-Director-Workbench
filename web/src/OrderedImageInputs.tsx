import { blob } from "./api";

type FrozenInput = { versionId?: string | null; sha256?: string | null; name?: string; versionNo?: number };

export function OrderedImageInputs({ inputs }: { inputs?: FrozenInput[] | null }) {
  if (!inputs) return <p className="muted small">这份历史记录缺少输入清单</p>;
  if (!inputs.length) return <p className="muted small">本项未使用参考图</p>;
  return <ol aria-label="按序输入的原图" className="frozen-inputs">
    {inputs.map((entry, index) => <li key={index}>
      {entry.sha256 && <a href={blob(entry.sha256)} target="_blank" rel="noreferrer" aria-label={`打开第 ${index + 1} 张原图`}>
        <img src={blob(entry.sha256)} alt="" loading="lazy"/>
      </a>}
      <span>{entry.name || "参考原图"} {entry.versionNo != null && `v${entry.versionNo}`}</span>
      <details><summary>版本与指纹</summary><div>版本 {entry.versionId || "未记录"}</div><div>SHA-256 {entry.sha256 || "未记录"}</div></details>
    </li>)}
  </ol>;
}
