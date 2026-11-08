const assert=require("node:assert/strict");
const path=require("node:path");
const fs=require("node:fs/promises");
const {createHash}=require("node:crypto");
const {chromium}=require("playwright");

async function main(){
 const base="http://127.0.0.1:"+process.env.ADW_TEST_PORT;
 const browser=await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:950}});
  const errors=[];page.on("pageerror",e=>errors.push(e.message));
  async function q(name,args={}){const r=await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}});assert.equal(r.status(),200);return r.json();}
  const token=(await (await page.request.get(base+"/api/session")).json()).token;
  async function cmd(name,data){const r=await page.request.post(base+"/api/cmd/"+name,{headers:{"x-adw-token":token},data});const j=await r.json();assert.equal(r.status(),200,JSON.stringify(j));return j;}
  const storage=await q("storage_status");assert.match(storage.dataDir,/adw-workflows-/);
  const project=(await q("list_projects")).find(p=>p.name==="场景对照验证");
  const eid=project.recent[0].id;
  const dir=path.resolve(__dirname,"../../修复验证_20261108");
  await page.goto(base+"/#/e/"+eid+"/space");
  await page.getByText("本集采用图与场景对照",{exact:true}).click();
  await page.getByRole("checkbox",{name:"场02"}).check();
  await page.getByLabel("场02 的锁定图").selectOption({index:1});
  await page.getByLabel(/蓝围裙.*的服装参考角色/).selectOption("afu");
  await page.getByRole("button",{name:"保存场景对照设置"}).click();
  await page.getByRole("button",{name:"查看场景对照图"}).click();
  const img=page.getByAltText("场02场景对照图");
  await img.waitFor();await img.evaluate(el=>el.decode());
  assert.deepEqual(await img.evaluate(el=>[el.naturalWidth,el.naturalHeight]),[1600,1440]);
  await page.getByRole("link",{name:"蓝围裙 v1",exact:true}).waitFor();
  async function inspectZoom(panel, slider, picture, expected){
    await picture.evaluate(el=>el.decode());
    await slider.focus();
    await slider.press("End");
    assert.equal(await slider.inputValue(),"400");
    const metrics=await picture.evaluate(el=>({image:el.clientWidth,viewport:el.parentElement.clientWidth,
      natural:[el.naturalWidth,el.naturalHeight]}));
    assert.ok(Math.abs(metrics.image-4*metrics.viewport)<4 && metrics.viewport>0);
    assert.equal(await panel.locator("output").innerText(),"400%");
    const url=await picture.getAttribute("src");
    const raw=url.startsWith("originals/") ? await fs.readFile(path.join(offline,url))
      : await (await page.request.get(new URL(url,base).href)).body();
    assert.equal(createHash("sha256").update(raw).digest("hex"),expected);
    return {...metrics,sha256:expected};
  }
  const costume=page.getByRole("link",{name:"蓝围裙 v1",exact:true});
  const hash=(await costume.getAttribute("href")).split("/").pop();
  const livePanel=costume.locator("..").locator("details");
  await livePanel.locator("summary").click();
  const liveZoom=await inspectZoom(livePanel,livePanel.getByRole("slider"),livePanel.getByRole("img"),hash);
  await livePanel.locator("summary").click();
  await img.scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(dir,"29_当前集场景与服装身份对照.png")});
  await page.getByText("导出离线对照页与原图",{exact:true}).click();
  const offline=path.join(path.dirname(storage.dataDir),"对照图副本");
  await page.getByLabel("新目录的绝对路径").fill(offline);
  await page.getByRole("button",{name:"导出场景对照页"}).click();
  await page.getByRole("status").filter({hasText:"index.html"}).waitFor();
  const off=await browser.newPage();await off.goto("file://"+encodeURI(path.join(offline,"index.html")));
  await off.locator("img").evaluate(el=>el.decode());
  assert.equal(await off.getByRole("link",{name:"蓝围裙 v1",exact:true}).count(),1);
  await off.getByRole("link",{name:"放大细节 · 蓝围裙 v1",exact:true}).click();
  const offlineZoom=await inspectZoom(off,off.getByRole("slider"),off.locator("#source"),hash);
  await off.getByRole("link",{name:"返回场景对照"}).click();
  await off.getByRole("heading",{name:"场景对照",exact:true}).waitFor();
  const {projectId:pid}=await cmd("create_project",{config_id:"puggy",name:"历史场景对照验证"});
  await page.goto(base+"/#/p/"+pid+"/knowledge");
  const hist=page.locator("details").filter({has:page.locator("summary").filter({hasText:/核对历史集资料/})}).first();
  await hist.locator("summary").first().click();
  await hist.locator("select").first().selectOption("2026-11-03-拍照");
  await hist.getByRole("button",{name:"导入副本并核对"}).click();
  await hist.getByText("查看各段九宫格尺寸",{exact:true}).click();
  const sizes=hist.locator("details").filter({has:page.locator("summary").filter({hasText:"查看各段九宫格尺寸"})}).last();
  const rows=await sizes.locator("li").allTextContents();
  assert.equal(rows.length,2);
  assert.ok(rows.every(row=>/段 0[12] · .+ · \d+ × \d+ 像素/.test(row)));
  await sizes.scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(dir,"31_拍照历史集画幅与九宫格原图尺寸.png")});
  await hist.getByRole("button",{name:"查看场景对照图"}).click();
  const real=hist.locator("img[alt$='场景对照图']");
  await real.waitFor();await real.evaluate(el=>el.decode());
  await real.scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(dir,"30_拍照历史集场景对照.png")});
  const receipt=await hist.getByLabel("核对版本").inputValue();
  const report=await cmd("historical_contact_sheet",{project_id:pid,receipt});
  const histItem=report.scenes[0].sources[report.scenes[0].pages[0].sourceIndexes[0]];
  const histSummary=hist.getByLabel("放大细节 · "+(histItem.reference_label||histItem.name||histItem.id),{exact:true}).first();
  await histSummary.click();
  const histDetail=histSummary.locator("..");
  const historicalZoom=await inspectZoom(histDetail,histDetail.getByRole("slider"),histDetail.getByRole("img"),histItem.sha256);
  await histDetail.scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(dir,"48_历史场景原图四倍查看.png")});
  const native=JSON.parse(await fs.readFile(path.join(offline,"index.json")));
  const headers={"x-adw-token":token};
  const list=await q("episode_assets",{episode_id:eid});
  const source=list.find(a=>a.name==="蓝围裙");
  const invalid=await page.request.post(base+"/api/upload/asset",{headers,multipart:{
    meta:JSON.stringify({projectId:pid,episodeId:eid,name:"错误项目"}),
    file:{name:"original.png",mimeType:"image/png",buffer:await (await page.request.get(base+"/api/blob/"+source.sha256)).body()}
  }});
  assert.equal(invalid.status(),422);
  assert.equal((await q("episode_assets",{episode_id:eid})).length,list.length);
  const {episodeId:otherEid}=await cmd("create_episode",{project_id:pid,display_code:"归属核对"});
  const adoptionStatuses=[];
  for(const confirm of [false,true]){
    const r=await page.request.post(base+"/api/cmd/adopt_image",{headers,data:{
      episode_id:otherEid,version_id:source.id,command_id:"invalid-"+confirm,confirm}});
    assert.equal(r.status(),422);
    adoptionStatuses.push(r.status());
  }
  await page.goto(base+"/#/e/"+eid+"/assets");
  await page.getByRole("button",{name:/^放大 .+ v1$/}).first().click();
  const modal=page.locator(".modal");
  const regular=await modal.getByRole("link",{name:"打开原文件"}).getAttribute("href");
  await inspectZoom(modal,modal.getByRole("slider"),modal.getByRole("img"),regular.split("/").pop());
  await modal.getByRole("button",{name:"关闭原图"}).click();
  const grids=report.scenes[0].sources.filter(s=>s.id.startsWith("grid"));
  assert.equal(grids.length,2);
  for(const g of grids){
    const uploaded=await page.request.post(base+"/api/upload/asset",{headers,multipart:{
      meta:JSON.stringify({projectId:project.id,episodeId:eid,name:"拍照 · "+g.id,category:"grid",grid:true}),
      file:{name:g.id+".png",mimeType:"image/png",buffer:await (await page.request.get(base+"/api/blob/"+g.sha256)).body()}
    }});
    assert.equal(uploaded.status(),200);
    assert.equal((await uploaded.json()).sha256,g.sha256);
  }
  await page.reload();
  const pictures=page.locator(".candidate-preview");
  await pictures.first().waitFor();
  await page.getByRole("button",{name:"灰度",exact:true}).click();
  assert.equal(await pictures.first().locator("img").evaluate(el=>getComputedStyle(el).filter),"grayscale(1)");
  await page.getByRole("button",{name:"手机尺寸",exact:true}).click();
  assert.equal(await pictures.first().locator("img").evaluate(el=>el.clientWidth+2*parseFloat(getComputedStyle(el).borderLeftWidth)),180);
  await page.getByRole("button",{name:"并排",exact:true}).click();
  assert.equal(await pictures.count(),2);
  const originalPair=await pictures.locator("img").evaluateAll(rows=>rows.map(el=>el.getAttribute("src")));
  await page.getByLabel("左侧比较图").selectOption(await page.getByLabel("右侧比较图").inputValue());
  assert.deepEqual(await pictures.locator("img").evaluateAll(rows=>rows.map(el=>el.getAttribute("src"))),[...originalPair].reverse());
  for(const pic of await pictures.locator("img").all())await pic.evaluate(el=>el.decode());
  const layouts=[];
  for(const width of [1440,1280,1024]){
    await page.setViewportSize({width,height:950});
    await page.reload();
    await page.getByRole("button",{name:"并排",exact:true}).click();
    await Promise.all((await pictures.locator("img").all()).map(p=>p.evaluate(el=>el.decode())));
    assert.equal(await page.locator(".project-tabs").count(),1);
    const layout=await page.locator(".asset-stage").evaluate(el=>{
      const btn=el.querySelector(".tabs .tab");
      const range=document.createRange();range.selectNodeContents(btn);
      const pics=[...el.querySelectorAll(".candidate-preview")].map(p=>p.getBoundingClientRect());
      return {overflow:el.scrollWidth-el.clientWidth,tabLines:range.getClientRects().length,
        pictureWidths:pics.map(p=>Math.round(p.width)),sideBySide:pics[1].left>=pics[0].right};
    });
    assert.ok(layout.overflow<=1 && layout.tabLines===1 && layout.sideBySide && Math.min(...layout.pictureWidths)>90,JSON.stringify(layout));
    const file="50_J10_素材与选图_"+width+".png";
    await page.screenshot({path:path.join(dir,file),animations:"disabled"});layouts.push({width,file,...layout});
  }
  await pictures.last().click();
  await modal.getByRole("heading",{name:/grid02/}).waitFor();
  assert.equal(await modal.getByRole("img",{name:/第 \d+ 格/}).count(),9);
  await inspectZoom(modal,modal.getByRole("slider"),modal.locator("img"),grids[1].sha256);
  await modal.getByRole("button",{name:"关闭原图"}).click();
  await fs.writeFile(path.join(dir,"J10_素材与选图画板逐宽度对照.html"),'<!doctype html><meta charset="utf-8"><title>J10 素材与选图</title><style>body{font:16px system-ui;margin:24px}section{display:grid;grid-template-columns:1fr 1fr;gap:12px}img{width:100%;height:auto}figure{margin:0}</style><h1>J10 素材与选图</h1>'+layouts.map(l=>'<h2>'+l.width+' 像素</h2><section><figure><figcaption>Figma 画板</figcaption><img src="../docs/figma/J10.png"></figure><figure><figcaption>工作台 · 历史原图在隔离项目内并排</figcaption><img src="'+l.file+'"></figure></section>').join(""));
  assert.deepEqual(errors,[]);
  await fs.writeFile(path.join(dir,"场景对照图浏览器验收.json"),JSON.stringify({native,historical:{folder:"2026-11-03-拍照",receipt,report},
    liveZoom,offlineZoom,historicalZoom,candidateComparison:{layouts,gridHashes:grids.map(g=>g.sha256),regularOriginal:regular,grid02Cells:9,
      grayscale:true,phoneOuterWidth:180,swappedBothColumns:true},
    ownership:{mismatchedUpload:invalid.status(),adoptionStatuses,
      candidateCountUnchanged:list.length},errors},null,2)+"\n");
  console.log("PASS native episode scene assignment, anchor, independent identity/outfit, portable report and historical scene images");
 } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
