const assert=require("node:assert/strict");
const {chromium}=require("playwright");

async function main(){
  const base="http://127.0.0.1:"+process.env.ADW_TEST_PORT;
  const browser=await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try {
    const page=await browser.newPage();
    const query=async(name,args={})=>{
      const r=await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}});
      assert.ok(r.ok(),await r.text());return r.json();
    };
    assert.match((await query("storage_status")).dataDir,/\/adw-workflows-[^/]+\/data$/);
    const {token}=await (await page.request.get(base+"/api/session")).json();
    const command=async(name,data)=>{
      const r=await page.request.post(base+"/api/cmd/"+name,{headers:{"x-adw-token":token},data});
      assert.ok(r.ok(),await r.text());return r.json();
    };
    const [project]=await query("list_projects"),pid=project.id,eid=project.recent[0].id;
    async function change(key,value){return command("episode_spec_change",{episode_id:eid,key,value,confirm:true});}
    await change("segmentMaxSeconds","7");await change("targetDuration","over_60");
    await page.goto(base+"/#/e/"+eid+"/plan");
    await page.getByText("G01 共 8 秒，超过本集 7 秒上限",{exact:true}).waitFor();
    await page.getByText(/本集目标严格超过 60 秒，当前设计 8 秒/).waitFor();
    await page.getByRole("button",{name:"生成段",exact:true}).click();
    const strategy=page.locator(".center tr").filter({hasText:"G01"}).getByRole("combobox");
    assert.equal(await strategy.inputValue(),"flex");
    assert.deepEqual(await strategy.locator("option").allTextContents(),["自由参考","九宫格"]);
    const issues=async()=>(await query("delivery_problems",{episode_id:eid}))[0].map(item=>item.problem);
    assert.ok((await issues()).some(s=>s.includes("7 秒上限")));
    assert.ok((await issues()).some(s=>s.includes("严格超过 60 秒")));
    await change("segmentMaxSeconds","8");await change("targetDuration","story_driven");
    await page.reload();await page.getByRole("heading",{name:"制作方案",exact:true}).waitFor();
    assert.equal(await page.getByText(/超过本集 .* 秒上限|本集目标严格超过/).count(),0);
    assert.ok(!(await issues()).some(s=>s.includes("秒上限") || s.includes("严格超过")));
    const [asset]=await query("episode_assets",{episode_id:eid});
    const reuse=await command("submit_reuse",{version_id:asset.id,usage:"夜班身份核对"});
    await command("review_library",{entry_id:reuse.entryId,status:"limited",limits:"只作侧面身份参考"});
    await page.goto(base+"/#/p/"+pid+"/library");
    const filter=page.getByLabel("资产审核状态");
    await filter.selectOption({label:"限条件"});
    await page.getByText("夜班身份核对",{exact:false}).first().waitFor();
    assert.equal(await filter.inputValue(),"limited");
    await filter.selectOption({label:"停用"});await page.getByText("库中还没有条目。").waitFor();
    console.log("PASS 本集时长上下限在镜头表与交付核验一致；生成策略和资产状态按中文操作并保留原值");
  } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
