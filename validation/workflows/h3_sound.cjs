const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const {chromium} = require("playwright");

async function main() {
  const base = "http://127.0.0.1:" + process.env.ADW_TEST_PORT;
  const evidence = path.resolve(__dirname,"../../修复验证_20261108");
  const browser = await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:960}});
    const errors=[];
    page.on("pageerror",err=>errors.push(err.message));
    const query = async(name,args={})=>{
      const r = await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}});
      assert.equal(r.status(),200); return r.json();
    };
    assert.match((await query("storage_status")).dataDir,/adw-workflows-/);
    const [project] = await query("list_projects");
    const eid = project.recent[0].id;
    const artifact = async()=>(await query("episode_view",{episode_id:eid})).artifacts.find(a=>a.stage==="prompts");
    const initial=await artifact();
    const original=initial.adopted_version_id;
    await page.goto(base+"/#/e/"+eid+"/prompts?v="+original);
    const field=page.getByLabel("G01 提示词正文");
    const source=await field.inputValue();
    const marker="non_diegetic_music: N/A";
    const edited=source.replace(marker,"overall_soundscape: 屋檐雨声与脚步\n票价牌不变。\n"+marker);
    assert.notEqual(edited,source);
    await field.fill(edited);
    await page.getByPlaceholder("环境声（逗号分隔）").fill("脚步、雨声");
    await page.getByLabel("本段配乐描述").fill("低音弦乐渐入");
    async function click(name) {
      const pending=page.waitForResponse(r=>r.url().endsWith("/api/cmd/set_sound"));
      await page.getByRole("button",{name,exact:true}).click();
      const r=await pending; assert.equal(r.status(),200,await r.text());
      const result=await r.json();
      if (result.versionId) {
        const saved=(await artifact()).versions.find(v=>v.id===result.versionId).content.segments.G01.txt;
        await page.waitForFunction(({id,txt})=>location.hash.includes(id) &&
          document.querySelector('textarea[aria-label="G01 提示词正文"]')?.value===txt,{id:result.versionId,txt:saved});
      }
      return result;
    }
    const plus=await click("加背景音乐");
    assert.equal(await field.inputValue(),edited.replace(marker,"non_diegetic_music: 低音弦乐渐入"));
    const minus=await click("去掉背景音乐");
    assert.equal(await field.inputValue(),edited);
    assert.deepEqual(minus.sound.ambience,["脚步","雨声"]);
    const silencePreview=await click("全静音");
    assert.ok(silencePreview.needsConfirm);
    assert.equal((await artifact()).versions.length,initial.versions.length+2);
    await page.getByLabel("静音后保留的画面文字及固定说明").fill("票价牌不变。");
    await page.getByRole("button",{name:"仍然全静音"}).scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(evidence,"44_全静音前核对原文与保留说明.png")});
    const muted=await click("仍然全静音");
    const silent=await field.inputValue();
    assert.ok(silent.includes("overall_soundscape: N/A") && silent.includes("票价牌不变。") && !silent.includes("屋檐雨声"));
    const unmuted=await click("解除全静音");
    assert.ok(!(await field.inputValue()).includes("overall_soundscape: N/A"));
    const versions=(await artifact()).versions;
    assert.equal(versions.find(v=>v.id===original).content.segments.G01.txt,source);
    assert.equal(versions.find(v=>v.id===minus.versionId).content.segments.G01.txt,edited);
    await page.goto(base+"/#/e/"+eid+"/delivery");
    await page.getByRole("link",{name:"将这次交付打包给 H3"}).first().click();
    await page.getByLabel("出包声音",{exact:true}).selectOption("on");
    await page.getByRole("button",{name:"验包并导出 ZIP"}).click();
    await page.getByText(/打包器拒绝：G02：全段静音/).waitFor();
    await page.getByLabel("出包声音",{exact:true}).selectOption("");
    async function inspect(){
      const response=page.waitForResponse(r=>r.url().endsWith("/api/cmd/package_preview"));
      await page.getByRole("button",{name:"预览所选段"}).click();
      const r=await response;
      assert.equal(r.status(),200,await r.text());
      return r.json();
    }
    const region=page.getByRole("region",{name:"出包预览"});
    const only=page.getByRole("button",{name:"G03",exact:true});
    await only.click();
    const partial=await inspect();
    assert.deepEqual(partial.segments.map(s=>s.id),["G01","G02"]);
    await only.click();
    await region.waitFor({state:"hidden"});
    await page.getByLabel("出包候选数").selectOption("4");
    await page.getByLabel("参数应用范围").selectOption("G02");
    await page.getByLabel("出包候选数").selectOption("2");
    const preview=await inspect();
    await region.waitFor();
    assert.deepEqual(preview.segments.map(s=>s.id),["G01","G02","G03"]);
    assert.notEqual(preview.segments[1].refs[0].sourceId,preview.segments[1].refs[0].packedId);
    assert.equal(preview.segments[1].origins.resolution,"单段设置");
    assert.equal(preview.segments[2].origins.resolution,"打包器默认");
    assert.deepEqual(preview.segments.map(s=>[s.params.candidates,s.origins.candidates]),[[4,"本次覆盖"],[2,"本次单段覆盖"],[4,"本次覆盖"]]);
    const sourceFiles=[];
    for(const part of preview.segments){
      const panel=region.locator("details").filter({has:page.locator("summary").filter({hasText:part.id})});
      const links=panel.locator("a");
      assert.equal(await links.count(),part.refs.length);
      for(const [i,ref] of part.refs.entries()){
        assert.ok((await links.nth(i).innerText()).includes(ref.name));
        const response=await page.request.get(base+(await links.nth(i).getAttribute("href")));
        assert.equal(response.status(),200);
        const digest=require("node:crypto").createHash("sha256").update(await response.body()).digest("hex");
        assert.equal(digest,ref.sha256);
        sourceFiles.push({segment:part.id,label:ref.label,sha256:digest,sourceId:ref.sourceId,packedId:ref.packedId});
      }
    }
    await page.context().grantPermissions(["clipboard-read","clipboard-write"],{origin:base});
    await page.getByRole("button",{name:"复制图文页路径"}).click();
    const copiedPath=await page.evaluate(()=>navigator.clipboard.readText());
    assert.ok(copiedPath.endsWith("00_图文对照.html") && copiedPath.includes("adw-workflows-"));
    await page.getByRole("button",{name:"复制出包说明"}).click();
    await page.getByRole("button",{name:"已复制出包说明"}).waitFor();
    const copiedHandoff=await page.evaluate(()=>navigator.clipboard.readText());
    assert.ok(copiedHandoff.includes(copiedPath) && sourceFiles.every(r=>copiedHandoff.includes(r.sha256)));
    const viewports=[];
    for(const width of [1024,1280,1440]){
      await page.setViewportSize({width,height:900});
      await region.scrollIntoViewIfNeeded();
      const geometry=await region.evaluate(el=>({
        pageWidth:document.documentElement.scrollWidth,viewport:innerWidth,
        tableWidth:el.querySelector(".package-table").clientWidth,contentWidth:el.querySelector("table").scrollWidth
      }));
      assert.ok(geometry.pageWidth<=width+1,JSON.stringify(geometry));
      const shot="45_J12出包预览_"+width+".png";
      await page.screenshot({path:path.join(evidence,shot)});
      viewports.push({width,shot,...geometry});
    }
    const pending=page.waitForResponse(r=>r.url().endsWith("/api/cmd/package"));
    await page.getByRole("button",{name:"验包并导出 ZIP"}).click();
    const zip=await (await pending).json();
    await page.getByText("已导出",{exact:true}).waitFor();
    const {execFileSync}=require("node:child_process");
    assert.match(zip.path,/adw-workflows-/);
    const packed=JSON.parse(execFileSync("/usr/bin/unzip",["-p",zip.path,"project.json"],{encoding:"utf8"}));
    const params=Object.fromEntries(packed.segments.map(s=>[s.id,s.params]));
    assert.deepEqual(Object.values(params).map(s=>s.audio),[true,false,true]);
    assert.deepEqual([params.G02.resolution,params.G02.instanceType,params.G02.candidates,params.G02.seed],["768p","plus",2,19]);
    assert.ok(packed.segments[1].prompt.includes("overall_soundscape: N/A"));
    assert.ok(packed.segments[2].prompt.includes("non_diegetic_music: 节奏舒缓的弦乐"));
    assert.deepEqual(preview.segments.map(s=>s.params),packed.segments.map(s=>s.params));
    for(const [i,seg] of packed.segments.entries()){
      assert.deepEqual(seg.refs,preview.segments[i].refs.map(r=>({assetId:r.packedId,label:r.label})));
    }
    const studio=path.resolve(__dirname,"../../../自媒体 2/h3-canvas-studio");
    execFileSync(process.execPath,["--import",path.join(studio,"node_modules/tsx/dist/loader.mjs"),
      path.join(__dirname,"h3_import.mjs"),studio,zip.path,path.join(evidence,"新交付H3真实导入与引用绑定.json")]);
    const packageSha256=require("node:crypto").createHash("sha256").update(await fs.readFile(zip.path)).digest("hex");
    const token=(await (await page.request.get(base+"/api/session")).json()).token;
    const r=await page.request.post(base+"/api/cmd/create_project",{headers:{"x-adw-token":token},data:{config_id:"puggy",name:"历史分段声音核对"}});
    assert.equal(r.status(),200);
    const {projectId}=await r.json();
    const baseline=JSON.parse(await fs.readFile(path.join(evidence,"历史集H3声音声明逐段对照.json")));
    const historical=baseline.episodes.find(e=>e.episode==="2026-10-23-leaf-tornado");
    await page.goto(base+"/#/p/"+projectId+"/knowledge");
    await page.getByText(/核对历史集资料（/).click();
    await page.getByLabel("历史集",{exact:true}).selectOption(historical.episode);
    await page.getByRole("button",{name:"导入副本并核对"}).click();
    const summary=page.getByText("查看逐段声音声明",{exact:true});
    await summary.click();
    const list=summary.locator("..").locator("li");
    assert.equal(await list.count(),historical.details.segments.length);
    for (const [index,row] of historical.details.segments.entries()) {
      assert.equal(await list.nth(index).locator("code").innerText(),row.sha256);
      assert.ok((await list.nth(index).innerText()).includes(row.message??"通过"));
    }
    assert.deepEqual(errors,[]);
    await fs.writeFile(path.join(evidence,"H3声音开关与历史回执浏览器核对.json"),JSON.stringify({
      original,plus:plus.versionId,minus:minus.versionId,muted:muted.versionId,unmuted:unmuted.versionId,
      verifiedEdits:["配乐开关保留未保存正文和环境声","静音原文及保留说明","解除静音","旧稿字节"],
      exportedZip:{packageSha256,params,previewHash:preview.previewHash,sourceFiles,viewports,copiedPathAndHandoff:true}, historical:{episode:historical.episode,segments:await list.count(),state:historical.state},errors},null,2)+"\n");
    console.log("PASS H3 sound toggles, unsaved editor, confirmed notes, immutable old versions and historical TXT digests");
  } finally {await browser.close();}
}
main().catch(err=>{console.error(err);process.exitCode=1;});
