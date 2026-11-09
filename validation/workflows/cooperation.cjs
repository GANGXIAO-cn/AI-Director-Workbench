const assert = require("node:assert/strict");
const {chromium} = require("playwright");
const base = "http://127.0.0.1:"+process.env.ADW_TEST_PORT;
async function main() {
  const browser = await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:960}});
    page.setDefaultTimeout(7000);
    const query = async(name,args={})=>{
      const res = await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}});
      assert.ok(res.ok(),await res.text());return res.json();
    };
    assert.match((await query("storage_status")).dataDir,/\/adw-workflows-[^/]+\/data$/);
    const errors=[];page.on("pageerror",err=>errors.push(err.message));
    const [project] = await query("list_projects");
    await page.goto(base);
    await page.locator(".bigbox textarea").fill("钟楼的最后一次报时");
    await page.getByRole("button",{name:"继续",exact:true}).click();
    const modal = page.locator(".modal:visible");
    await modal.getByLabel("合作档位").selectOption("beginner");
    await modal.getByRole("button",{name:"开工，进入故事稿"}).click();
    await page.waitForURL(/\/e\/.+\/story/);
    const eid = page.url().split("/").at(-2);
    const view = ()=>query("episode_view",{episode_id:eid});
    assert.equal((await view()).cooperationMode,"beginner");
    await page.getByLabel("合作档位").selectOption("expert");
    await page.waitForFunction(()=>document.querySelector(".catalog select")?.value==="expert");
    await page.reload();
    assert.equal(await page.getByLabel("合作档位").inputValue(),"expert");
    for (const stage of ["space","cast"]) {
      await page.goto(base+"/#/e/"+eid+"/"+stage);
      await page.getByRole("button",{name:"跳过这一步"}).click();
      const confirm=page.getByRole("button",{name:"确认跳过"});
      assert.ok(await confirm.isDisabled());
      await page.getByLabel("跳过原因").fill("本集沿用已定资料 "+stage);await confirm.click();
      await page.getByText("本步已跳过 · 本集沿用已定资料 "+stage).waitFor();
      await page.reload();
      await page.locator(".catalog .item.on").getByText("已跳过").waitFor();
      assert.equal((await view()).skippedStages[stage],"本集沿用已定资料 "+stage);
    }
    await page.getByRole("button",{name:"恢复这一步"}).click();
    await page.getByRole("button",{name:"跳过这一步"}).waitFor();
    assert.deepEqual(Object.keys((await view()).skippedStages),["space"]);
    await page.goto(base+"/#/e/"+eid+"/script");
    await page.getByRole("button",{name:"讨论与试写"}).click();
    await modal.getByLabel("讨论或试写要求").fill("先试写钟楼停摆的一场");
    await modal.getByRole("button",{name:/发送给/}).click();
    await modal.getByText(/已按本轮资料回应/).waitFor();
    assert.equal(await modal.getByRole("button",{name:/引用此回复/}).count(),0);
    await page.reload();await page.getByRole("button",{name:"讨论与试写"}).click();
    await modal.getByText(/已按本轮资料回应/).waitFor();
    assert.ok(!(await view()).artifacts.some(a=>a.stage==="script"));
    const chat=await query("free_chat",{project_id:project.id,episode_id:eid,stage:"script"});
    assert.equal(chat.turns[0].status,"succeeded");
    assert.deepEqual(errors,[]);
    console.log("PASS 开工选档与重开、两个可选阶段跳过及恢复、无正式上游的剧本试写及重读");
  } finally {await browser.close();}
}
main().catch(err=>{console.error(err);process.exitCode=1;});
