import {execFileSync} from "node:child_process";
import {fileURLToPath} from "node:url";
import {readFileSync} from "node:fs";

export function buildInfo() {
  const root = fileURLToPath(new URL("..",import.meta.url));
  const git = (...args)=>execFileSync("git",args,{cwd:root,encoding:"utf8",stdio:["ignore","pipe","ignore"]}).trim();
  let commit=null, modified=null;
  try {
    commit=git("rev-parse","--short=12","HEAD");
    modified=Boolean(git("status","--porcelain","--","web"));
  } catch { /* Source archives can be built without Git. */ }
  const {version}=JSON.parse(readFileSync(new URL("./package.json",import.meta.url),"utf8"));
  return {version,commit,modified,builtAt:new Date().toISOString()};
}
