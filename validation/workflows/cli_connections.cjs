const assert = require("node:assert/strict");
const path = require("node:path");
const {chromium} = require("playwright");

async function main() {
  const base = "http://127.0.0.1:"+process.env.ADW_TEST_PORT;
  const browser = await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:960}});
    page.setDefaultTimeout(8500);
    const q = async (name,args={})=>{
      const r=await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}});
      assert.ok(r.ok(),await r.text());return r.json();
    };
    const data=(await q("storage_status")).dataDir;
    assert.match(data,/adw-workflows-[^/]+\/data$/);
    const [project]=await q("list_projects");
    const eid=project.recent[0].id;
    await page.goto(base+"/#/sys/settings");
    const menu=page.getByRole("navigation",{name:"设置分类"});
    const nav=page.getByRole("navigation",{name:"主导航"});
    await menu.getByRole("link",{name:"色卡资料位置"}).click();
    await page.getByLabel("资料路径").waitFor();
    assert.equal(await nav.getByRole("link",{name:"设置",exact:true}).getAttribute("aria-current"),"page");
    await page.reload();
    await page.getByLabel("资料路径").waitFor();
    await menu.getByRole("link",{name:"执行器连接与型号"}).click();
    const settings=page.locator(".grid3 .card");
    const providers=[
      ["Codex","codex","codex","studio-model","high","ChatGPT · 已登录"],
      ["Claude Code","claude","claude","sonnet","medium","studio@example.invalid · 已登录"],
      ["Antigravity","antigravity","agy","gemini-local-test",null,"登录状态待确认"]
    ];
    for(const [label,provider,bin,model,effort] of providers) {
      const card=settings.filter({has:page.getByText(label,{exact:true})});
      await card.getByLabel("可执行文件路径").fill(path.join(path.dirname(data),"alternate",bin));
      await card.getByRole("button",{name:"保存路径"}).click();
      await card.getByText(/local-fixture-2/).waitFor();
      await card.getByRole("button",{name:"刷新本机型号"}).click();
      await card.getByRole("status").waitFor();
      await card.getByRole("button",{name:"验证连接"}).click();
      await card.getByText(provider==="antigravity"?/Google CLI 已返回可用型号/:/本机登录已验证/).waitFor();
      await card.getByLabel("默认型号").selectOption(model);
      if(effort) await card.getByLabel("默认强度").selectOption(effort);
      await card.getByLabel("连接代理").selectOption("off");
      await card.getByRole("button",{name:"保存连接设置"}).click();
      await page.waitForFunction(async p=>{
        const r=await fetch("/api/q/model_catalog");
        return (await r.json()).text[p].connection.revision===1;
      },provider);
    }
    await page.reload();
    for(const [label,,bin,model,effort] of providers) {
      const card=settings.filter({has:page.getByText(label,{exact:true})});
      assert.equal(await card.getByLabel("可执行文件路径").inputValue(),path.join(path.dirname(data),"alternate",bin));
      assert.equal(await card.getByLabel("默认型号").inputValue(),model);
      assert.equal(await card.getByLabel("连接代理").inputValue(),"off");
      if(effort) assert.equal(await card.getByLabel("默认强度").inputValue(),effort);
    }
    await page.goto(base+"/#/e/"+eid+"/story");
    for(const [,provider,,model,effort,identity] of providers) {
      await page.getByLabel("创作执行器").selectOption(provider);
      await page.getByLabel("当前账号连接").filter({hasText:identity}).waitFor();
      assert.equal(await page.getByLabel("创作型号").inputValue(),model);
      if(effort) assert.equal(await page.getByLabel("推理强度").inputValue(),effort);
      await page.locator(".composer textarea").fill("检查本集开场 "+provider);
      const result=page.waitForResponse(r=>r.request().method()==="POST"&&/\/(create_task|post_message)$/.test(r.url()));
      await page.locator(".composer").getByRole("button",{name:"↑"}).click();
      const response=await result;
      assert.ok(response.ok(),await response.text());
      const {runId}=await response.json();
      const manifest=(await q("run_manifest",{run_id:runId})).manifest;
      assert.deepEqual([manifest.provider,manifest.requestedModel,manifest.requestedEffort,manifest.executorConnection.proxy],[provider,model,effort,"off"]);
    }
    console.log("PASS 三个 CLI 的路径、只读检测、原生型号刷新、默认设置、助手账号状态及请求参数");
  } finally {await browser.close();}
}
main().catch(err=>{console.error(err);process.exitCode=1;});
