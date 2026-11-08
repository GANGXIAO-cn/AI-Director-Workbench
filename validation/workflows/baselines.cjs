const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs/promises");
const path = require("node:path");
const {chromium} = require("playwright");

const base="http://127.0.0.1:"+process.env.ADW_TEST_PORT;
const out=path.resolve(__dirname,"../../修复验证_20261108");
const digest=bytes=>crypto.createHash("sha256").update(bytes).digest("hex");
async function main(){
  const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:920}});
    const errors=[]; page.on("pageerror",e=>errors.push(e.message));
    const get=async(name,args={})=>{
      const r=await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}});
      assert.equal(r.status(),200);return r.json();
    };
    assert.match((await get("storage_status")).dataDir,/adw-workflows-/);
    const pid=(await get("list_projects")).find(p=>p.name==="制作基准核对").id;
    const docs=()=>get("project_baselines",{project_id:pid});
    const before=(await docs()).find(d=>d.kind==="promptStandard");
    const old=before.blocks.find(b=>b.key==="blocks.grid_reference");
    assert.equal(old.references.length,1); assert.equal(old.references[0].segment,"G02");
    await page.goto(base+"/#/p/"+pid+"/baseline");
    const btn=name=>page.getByRole("button",{name,exact:true});
    const source=page.locator("article").filter({has:page.getByRole("heading",{name:"视频提示词",exact:true})});
    // Scope by the summary to keep nested disclosure controls independent.
    await source.getByRole("heading",{name:"视频提示词",exact:true}).waitFor();
    for(const width of [1440,1280,1024]){
      await page.setViewportSize({width,height:920});
      await page.locator(".project-baselines h1").scrollIntoViewIfNeeded();
      await page.screenshot({path:path.join(out,"55_J33_固定块清单_"+width+".png")});
    }
    const summary=source.locator("summary").filter({hasText:"九宫格参考"});
    await summary.first().click();
    await source.getByRole("link",{name:"J33 · G02",exact:true}).click();
    await page.waitForURL(/segment=G02/);
    await page.waitForFunction(phrase=>[...document.querySelectorAll("textarea")].some(t=>t.value.includes(phrase)),old.text);
    await page.goto(base+"/#/p/"+pid+"/baseline");
    if(!await btn("修订九宫格参考").isVisible()) await summary.first().click();
    await btn("修订九宫格参考").click();
    await btn("让 AI 起草修订").click();
    await page.getByLabel("起草要求").fill("强调核对动作方向");
    await page.getByLabel("起草执行器").selectOption("codex");
    await btn("发送原文与要求").click();
    await btn("带入修订正文").click();
    const revised=await page.getByLabel("修订正文").inputValue();
    assert.match(revised,/核对九宫格各格/);
    const thread=(await page.getByRole("link",{name:"查看起草记录"}).getAttribute("href")).split("/").at(-1);
    const transcript=await get("free_chat",{project_id:pid,thread_id:thread});
    const input=transcript.turns[0].sources.find(s=>s.role==="baseline");
    assert.equal(input.sha256,digest(old.text)); assert.ok(input.name.includes(before.sha256));
    await btn("比较修订").click();
    await btn("采用为项目修订 R1").waitFor();
    assert.equal((await docs()).find(d=>d.kind===before.kind).sha256,before.sha256);
    const editPanel=source.locator("section.card");
    assert.match(await editPanel.innerText(),/G02 · 提示词逐字引用/);
    assert.doesNotMatch(await editPanel.innerText(),/G01/);
    const widths=[];
    for(const width of [1440,1280,1024]){
      await page.setViewportSize({width,height:920});
      await editPanel.scrollIntoViewIfNeeded();
      const size=await page.locator(".project-baselines").evaluate(el=>({client:el.clientWidth,scroll:el.scrollWidth}));
      assert.ok(size.scroll<=size.client+1);
      const viewport=await page.evaluate(()=>({width:window.innerWidth,body:document.documentElement.scrollWidth}));
      assert.ok(viewport.body<=viewport.width+1,JSON.stringify(viewport));
      await page.screenshot({path:path.join(out,"55_J33_基准修订_"+width+".png")});
      widths.push({width,...size});
    }
    await btn("采用为项目修订 R1").click();
    await source.locator("summary").filter({hasText:"R1 · 九宫格参考修订记录"}).click();
    const copy=before.copies[0]; await page.getByLabel("跟进修订的在制集").selectOption(copy.episodeId);
    await btn("核对本集影响").click();await btn("本集采用此固定块").click();
    await page.getByRole("status").filter({hasText:"本集后续任务已选用此固定块"}).waitFor();
    const after=(await docs()).find(d=>d.kind===before.kind);
    assert.notEqual(before.sha256,after.sha256);assert.equal(after.copies[0].sha256,before.sha256);
    assert.equal(after.copies[0].effectiveSha256,after.sha256);
    await source.locator("summary").filter({hasText:"来源指纹与开工副本"}).click();
    for(const [label,sha] of [["开工原件",before.sha256],["本集选用",after.sha256]]){
      const href=await source.getByRole("link",{name:new RegExp(label)}).getAttribute("href");
      const res=await page.request.get(base+href);assert.equal(res.status(),200);assert.equal(digest(await res.body()),sha);
    }
    assert.deepEqual(errors,[]);
    await fs.writeFile(path.join(out,"J33_固定块修订与在制集采用.json"),JSON.stringify({widths,before:before.sha256,after:after.sha256,episode:after.copies[0],source:input,reference:old.references,revision:after.history[0],executor:"本机模拟"},null,2)+"\n");
    const sections=widths.map(w=>`<h2>${w.width} 像素</h2><div><figure><img src="../docs/figma/J33.png"><figcaption>J33 设计稿</figcaption></figure><figure><img src="55_J33_固定块清单_${w.width}.png"><figcaption>本机工作台 · 固定块清单</figcaption></figure></div><details open><summary>修订对照与导演助手</summary><img src="55_J33_基准修订_${w.width}.png"></details>`).join("");
    await fs.writeFile(path.join(out,"J33_制作基准画板逐宽度对照.html"),'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>制作基准 · J33</title><style>body{font:16px/1.6 system-ui;margin:2em}div{display:grid;grid-template-columns:1fr 1fr;gap:1em}figure{margin:0}img{width:100%}@media(max-width:720px){div{grid-template-columns:1fr}}</style><h1>制作基准 · 修订与引用</h1>'+sections+'</html>');
    console.log("PASS J33 source, reference segment, simulated AI draft, review, scoped adoption and old bytes");
  }finally{await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
