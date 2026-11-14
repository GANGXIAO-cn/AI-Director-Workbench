import Markdown, {defaultUrlTransform} from "react-markdown";

function localReference(url:string) {
  return /^(?:file:|\/(?!\/))/i.test(url);
}

export function ChatMarkdown({text}:{text:string}) {
  return <div className="chat-prose chat-markdown"><Markdown
    urlTransform={url=>localReference(url) ? "" : defaultUrlTransform(url)}
    components={{
      a:({href,children})=>{
        if (!href) {
          if (typeof children==="string" && localReference(children)) {
            const name=children.split("/").filter(Boolean).at(-1);
            return <span>{name || "本地资料"}</span>;
          }
          return <span>{children}</span>;
        }
        return <a href={href} target="_blank" rel="noreferrer">{children}</a>;
      },
      img:({alt})=><span>{alt || "图片"}</span>,
    }}
  >{text}</Markdown></div>;
}
