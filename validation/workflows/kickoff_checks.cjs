const assert=require("node:assert/strict");
const {chromium}=require("playwright");

async function main() {
  const base="http://127.0.0.1:"+process.env.ADW_TEST_PORT;
  const browser=await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try {
    const page=await browser.newPage({viewport:{width:800,height:620}});
    const [project]=await (await page.request.get(base+"/api/q/list_projects")).json();
    assert.equal(project.name,"默片开工核对");
    let runs=0;
    page.on("request",r=>{if(r.url().endsWith("/cmd/kickoff_check_run")) runs++;});
    const button=name=>page.getByRole("button",{name,exact:true});
    const start=button("开工，进入故事稿");
    const idea=page.getByPlaceholder("一句话想法");
    const sample="让主角说出最后一句台词。";
    await page.goto(base+"/#/p/"+project.id);
    await button("＋ 新增一集").click();
    await button("新增并去开工").click();
    await idea.fill(sample);
    await page.getByText("全片无对白",{exact:true}).first().waitFor();
    await page.getByText("查看本集要求原文",{exact:true}).click();
    const panel = page.getByRole("region",{name:"开工设定核对"});
    assert.ok((await panel.innerText()).includes(sample));
    await page.getByText("手动标记冲突",{exact:true}).click();
    await panel.getByLabel("选择冲突的必守设定").selectOption("dialogue");
    await panel.getByPlaceholder("粘贴与设定冲突的本集原句").fill(sample);
    await button("确认两句冲突").click();
    await button("更正为可同时满足").click();
    assert.equal(runs,0);
    const checkedIdea="清晨。"+sample;
    await idea.fill(checkedIdea);
    await page.getByRole("button",{name:/用当前 CLI 检查冲突/}).click();
    await page.getByText("发现冲突",{exact:true}).waitFor();
    assert.ok(await start.isDisabled());
    const modal=page.locator(".modal").last();
    assert.ok(await modal.evaluate(el=>el.scrollWidth<=el.clientWidth+1));
    await start.scrollIntoViewIfNeeded();
    assert.ok(await start.evaluate(el=>{
      const r=el.getBoundingClientRect();
      return r.left>=0 && r.right<=innerWidth && r.top>=0 && r.bottom<=innerHeight;
    }));
    await idea.fill("主角在纸上写下台词。");
    await page.getByRole("button",{name:/用当前 CLI 检查冲突/}).waitFor();
    assert.ok(await start.isEnabled());
    await idea.fill(checkedIdea);
    await page.getByText("发现冲突",{exact:true}).waitFor();
    assert.ok(await start.isDisabled());
    assert.equal(runs,1);
    await idea.fill("主角在纸上写下台词。");
    await page.getByRole("button",{name:/用当前 CLI 检查冲突/}).waitFor();
    await start.click();
    await page.waitForURL(/#\/e\/[^/]+\/story$/);
    assert.equal(runs,1);
    console.log("PASS 本地开工、按需 CLI 查冲突、修改后重开、旧材料复用结果");
  } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
