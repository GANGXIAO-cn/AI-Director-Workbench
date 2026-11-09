const assert=require("node:assert/strict");
const {chromium}=require("playwright");
async function main(){
  const base="http://127.0.0.1:"+process.env.ADW_TEST_PORT;
  const browser=await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try {
    const context=await browser.newContext({permissions:["clipboard-read","clipboard-write"]});
    const page=await context.newPage();
    const get=async(name,args={})=>{
      const r=await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}});
      assert.equal(r.status(),200);return r.json();
    };
    assert.match((await get("storage_status")).dataDir,/adw-workflows-/);
    const [[project],data]=await Promise.all([get("list_projects"),get("activity_log")]);
    const image=data.items.filter(r=>r.label==="图像生成");
    assert.deepEqual(new Set(image.map(r=>r.status)),new Set(["已完成","失败","发送结果待核对"]));
    const unknown=image.find(r=>r.needsReconcile);
    const batch=await get("batch_view",{batch_id:unknown.batch});
    assert.match(batch.requests.find(r=>r.state==="unknown").error,/FAKE_LOG_TOKEN/);
    assert.doesNotMatch(JSON.stringify(data),/FAKE_LOG_TOKEN|PRIVATE_LOG_NAME|Authorization|Bearer/);
    assert.ok(data.items.some(r=>r.label==="故事起草"));assert.ok(data.items.some(r=>r.label==="自由对话"&&r.time));
    await page.goto(base+"/");
    const button=name=>page.getByRole("button",{name,exact:true});
    await button("执行日志").click();
    const panel=page.locator(".modal:visible");
    await panel.getByText("通道额度不足",{exact:true}).waitFor();
    await panel.getByText("等待通道回执超时",{exact:true}).waitFor();
    const warning=panel.getByText(/重发可能重复扣费/);
    assert.equal(await warning.count(),1);
    await button("复制本页脱敏日志").click();
    await panel.getByText("已复制",{exact:true}).waitFor();
    const clipboard=await page.evaluate(()=>navigator.clipboard.readText());
    assert.ok(clipboard.includes(unknown.id) && clipboard.includes("重发可能重复扣费") && clipboard.includes("原始诊断已隐藏"));
    assert.doesNotMatch(clipboard,/FAKE_LOG_TOKEN|PRIVATE_LOG_NAME|Authorization|Bearer/);
    await panel.getByLabel("日志项目").selectOption(project.id);
    await panel.locator("article").filter({hasText:"发送结果待核对"}).getByRole("link",{name:"查看批次回执"}).click();
    await page.waitForURL(u=>u.hash.includes("batch="+unknown.batch));
    await page.locator(".modal:visible").getByText("待核对",{exact:true}).first().waitFor();
    console.log("PASS 首页日志、文本与图像状态、未知回执扣费提示、剪贴板脱敏和原批次跳转");
  } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
