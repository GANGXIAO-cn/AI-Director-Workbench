import type { ReactNode } from "react";
import { PROVIDER_LABEL } from "./ui";

type Props = {provider?:string; model?:string; requestedModel?:string; effort?:string; children:ReactNode};

export function ModelReply({provider,model,requestedModel,effort,children}:Props) {
  const family = provider && (PROVIDER_LABEL[provider] ?? provider);
  const modelLabel = model || (requestedModel ? `请求：${requestedModel}（服务商未回报）` : "");
  const title = [family,modelLabel,effort].filter(Boolean).join(" · ") || "助手";
  const version = model?.match(/\d+(?:\.\d+)?/)?.[0];
  const mark = version || (model || family || "AI").slice(0,2).toUpperCase();
  return <div className="model-reply">
    <span className="model-portrait" data-provider={provider} title={title} aria-hidden="true">{mark}</span>
    <div className="model-reply-content"><div className="model-byline">{title}</div>{children}</div>
  </div>;
}
