const assert=require("node:assert/strict");
const {chromium}=require("playwright");
(async()=>{
  const base="http://127.0.0.1:"+process.env.ADW_TEST_PORT;
  const browser=await chromium.launch({executablePath:process.env.CHROME_PATH});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    const q=async(name,args={})=>(await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}})).json();
    assert.match((await q("storage_status")).dataDir,/\/adw-workflows-[^/]+\/data$/);
    const [project]=await q("list_projects");
    const eid=project.recent[0].id;
    await page.goto(base+"/#/e/"+eid+"/story");
    const button=name=>page.getByRole("button",{name,exact:true});
    await button("创作对话").click();
    const composer=page.locator(".composer:visible textarea");
    await composer.fill("结尾还能怎么处理？");
    await button("发送消息").click();
    await page.getByText("仅讨论，稿件未改动",{exact:true}).last().waitFor();
    const box=page.locator(".discussion-convert").last();
    await box.locator("summary").click();
    await box.getByLabel("落实到新稿的修改指令").fill("保留值班员登记的结尾。");
    await box.getByRole("button",{name:"按以上讨论生成新稿"}).click();
    await page.getByText("已生成 R02，待采用",{exact:false}).last().waitFor();
    assert((await q("director_decisions",{episode_id:eid})).some(d=>d.text==="保留值班员登记的结尾。"));
    for (const width of [1440,680]) {
      await page.setViewportSize({width,height:960});
      const picker=page.getByLabel("预览成果物");
      await picker.selectOption({index:1});
      const preview=page.getByRole("complementary",{name:"成果预览"});
      await preview.waitFor();
      assert((await preview.innerText()).length>50);
      await composer.fill("预览时继续写 "+width);
      await button("关闭预览").click();
      assert.equal(await composer.inputValue(),"预览时继续写 "+width);
    }
    console.log("PASS 阶段讨论、转稿、决定登记、双宽度成果预览与连续输入");
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
