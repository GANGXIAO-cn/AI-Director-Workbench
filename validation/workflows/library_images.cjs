const assert=require("node:assert/strict");
const {chromium}=require("playwright");

async function main(){
  const base="http://127.0.0.1:"+process.env.ADW_TEST_PORT;
  const browser=await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try {
    const page=await browser.newPage();page.setDefaultTimeout(10000);
    const errors=[];page.on("pageerror",e=>errors.push(e.message));
    const get=async(name,args={})=>{
      const r=await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}});
      assert.equal(r.status(),200,await r.text());return r.json();
    };
    assert.match((await get("storage_status")).dataDir,/\/adw-workflows-[^/]+\/data$/);
    const [project]=await get("list_projects"), pid=project.id, eid=project.recent[0].id;
    const library=()=>get("library",{project_id:pid});
    const old=await library();
    const img=async row=>(await page.request.get(base+"/api/blob/"+row.sha256)).body();
    const url=base+"/#/p/"+pid+"/library";
    const button=name=>page.getByRole("button",{name,exact:true});
    await page.goto(url);
    await button("添加图片").click();
    async function upload(row,revision){
      const modal=page.locator(".modal:visible");
      await modal.getByLabel("图片文件").setInputFiles({name:"落日.png",mimeType:"image/png",buffer:await img(row)});
      if(revision===1) await modal.getByLabel("图片分类").selectOption("prop");
      await modal.getByLabel("用途").fill("夕阳参考 "+revision);
      await button("保存到资产库").click();
      await page.getByText("夕阳参考 "+revision,{exact:false}).first().waitFor();
    }
    await upload(old[0],1);
    await button("添加新版本").click();await upload(old[1],2);
    await page.getByLabel("按资产分类筛选").selectOption("prop");
    assert.equal(await page.getByRole("checkbox").count(),2);
    const items=await library();
    const [v1,v2]=items.filter(x=>x.name==="落日.png");
    assert.equal(v1.version_no,1);assert.equal(v2.version_no,2);
    assert.notEqual(v1.sha256,v2.sha256);
    await page.getByLabel("附上 落日.png v2").check();
    await page.getByLabel("按资产分类筛选").selectOption("");
    for(const r of old) await page.getByLabel("附上 "+r.name+" v1").check();
    assert.ok(await page.getByLabel("附上 落日.png v1").isDisabled());
    await page.getByRole("link",{name:"带 6 张图去对话"}).click();
    await page.getByText(/本次附图 · 落日.png v2/).waitFor();
    assert.equal((await get("free_chat",{project_id:pid})).threads.length,0);
    await page.getByLabel("对话内容").fill("请对照这六张图的色温。");await button("发送").click();
    await page.getByText("codex · 已回复",{exact:true}).waitFor();
    const thread_id=new URL(page.url()).hash.split("/").at(-1);
    const chat=(await get("free_chat",{project_id:pid,thread_id})).turns[0];
    assert.deepEqual(chat.sources.filter(s=>s.role==="image").map(s=>s.sha256),[v2,...old].map(v=>v.sha256));
    await page.goto(url);
    await page.getByRole("img",{name:"落日.png v1",exact:true}).click();
    await button("限条件").click();await page.getByLabel("限制条件").fill("仅作色温参考");await button("保存限制条件").click();
    await page.getByLabel("资产目标集").selectOption(eid);
    await button("复制到目标集并改图").click();
    await page.getByAltText("本批底图").waitFor();
    await page.getByLabel("绘图要求").fill("保留色温，改为夜站。");
    await button("生成摘要，去确认").click();
    const modal=page.locator(".modal:visible");
    await modal.getByText("资产库底图",{exact:true}).waitFor();
    assert.match(await modal.innerText(),/落日.png v1 .*仅作色温参考/);
    const [batch]=await get("episode_batches",{episode_id:eid});
    assert.equal(batch.state,"awaiting_authorization");
    assert.equal(batch.spec.librarySources[v1.id].versionId,v1.asset_version_id);
    assert.equal(batch.inputGroups[0][0].sha256,v1.sha256);
    assert.ok(batch.spec.items[0].prompt.includes("底图使用条件：仅作色温参考"));
    await button("确认授权 1 张").click();
    await button("发送已授权的 1 张").click();
    await modal.getByText("完成",{exact:true}).first().waitFor();
    const done=(await get("batch_view",{batch_id:batch.id})).requests[0];
    const candidates=await get("episode_assets",{episode_id:eid});
    const result=candidates.find(v=>v.id===done.asset_version_id);
    const copy=candidates.find(v=>v.id===result.source_version_id);
    assert.equal(copy.source_version_id,v1.asset_version_id);assert.equal(result.asset_id,copy.asset_id);
    assert.equal((await library()).filter(v=>v.name==="落日.png").length,2);
    await modal.getByRole("button",{name:"关闭",exact:true}).click();
    await page.goto(base+"/#/e/"+eid+"/assets?tab=canvas");
    await page.locator('[data-node="'+result.id+'"]').click();
    await button("登记到复用库").click();
    await button("已登记到复用库").waitFor();
    assert.ok((await library()).some(v=>v.asset_version_id===result.id && v.sha256===result.sha256));
    assert.deepEqual(errors,[]);
    console.log("PASS 资产上传与新版本、分类、六图对话、限定底图的实际输入和模拟改图回执");
  } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
