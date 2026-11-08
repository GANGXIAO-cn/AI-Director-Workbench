// Run against the installed H3 importer in an isolated local store. No generation service is used.
import assert from "node:assert/strict";
import {createHash,randomUUID} from "node:crypto";
import {mkdtemp,readFile,writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import path from "node:path";
import {pathToFileURL} from "node:url";

const [studio,file,report,mode] = process.argv.slice(2);
const load = name=>import(pathToFileURL(path.resolve(studio,name)));
const dir = await mkdtemp(path.join(tmpdir(),"adw-h3-consumer-"));
process.chdir(dir);
process.env.RUNNINGHUB_REUSE_EXISTING_KEY="false";
const {readPackage,previewPackage,commitPackage} = await load("server/projectPackage.ts");
const {Store} = await load("server/store.ts");
const {orderedRefs,compilePrompt} = await load("shared/model.ts");
const digest=data=>createHash("sha256").update(data).digest("hex");
const bytes=await readFile(file);
if (mode==="legacy-invalid") {
  await assert.rejects(readPackage(bytes), error=>error.issues?.some(i=>i.path[0]==="assets" && i.path.at(-1)==="origin"));
  await writeFile(report,JSON.stringify({sha256:digest(bytes),rejectedByH3:"assets[*].origin"},null,2)+"\n");
} else {
  const {manifest,files}=await readPackage(bytes);
  const store=new Store(dir);
  try {
    const empty={id:randomUUID(),name:"工作台离线导入验收",nodes:[],edges:[],viewport:{x:0,y:0,zoom:1},updatedAt:""};
    store.saveProject(empty);
    const pre=await previewPackage(store,bytes);
    const request={token:pre.token,requestId:randomUUID(),projectId:empty.id,updatedAt:empty.updatedAt};
    const p=await commitPackage(store,request);
    const nodes=p.nodes.filter(n=>n.type==="generation");
    assert.equal(nodes.length,manifest.segments.length);
    assert.equal(p.edges.length,manifest.segments.reduce((n,s)=>n+s.refs.length,0));
    const assets=store.assets();
    const origins={};
    for(const asset of manifest.assets){
      const actual=assets.find(a=>a.packageSource.assetId===asset.id);
      assert.equal(actual.packageSource.origin,asset.origin);
      assert.deepEqual(await readFile(actual.path),files.get(asset.path));
      origins[asset.id]=asset.origin;
    }
    const segments=nodes.map((node,i)=>{
      const seg=manifest.segments[i], refs=orderedRefs(p,node.id,assets);
      assert.deepEqual(node.data.params,seg.params);
      assert.deepEqual(refs.map(r=>assets.find(a=>a.id===r.assetId).packageSource.assetId),seg.refs.map(r=>r.assetId));
      const compiled=compilePrompt(node.data.prompt,refs);
      const labels=new Map(seg.refs.map(r=>[r.label,r.assetId]));
      const compiledExpected=seg.prompt.trim().replace(/(?:图片|视频|音频)\d+|@(?:Image|Video|Audio)\d+/g,label=>{
        const ref=refs.find(r=>assets.find(a=>a.id===r.assetId).packageSource.assetId===labels.get(label));
        return "<"+({image:"Picture",video:"Video",audio:"Audio"})[ref.kind]+" "+ref.ordinal+">";
      });
      assert.equal(compiled,compiledExpected);
      return {id:seg.id,params:seg.params,referenceOrder:seg.refs,compiledPrompt:compiled};
    });
    assert.equal((await commitPackage(store,request)).nodes.length,p.nodes.length);
    const again=await previewPackage(store,bytes);
    assert.equal(again.reused,assets.length);
    assert.equal(store.versions().length,0);
    await writeFile(report,JSON.stringify({sha256:digest(bytes),scope:manifest.scope,segments,origins,
      assets:assets.length,edges:p.edges.length,reusedOnNextPreview:again.reused,
      sourceHashes:Object.fromEntries(await Promise.all(["shared/projectPackage.ts","server/projectPackage.ts","shared/model.ts"].map(async name=>[name,digest(await readFile(path.join(studio,name)))])))},null,2)+"\n");
  } finally {store.close();}
}
