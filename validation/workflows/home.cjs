const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

async function main() {
  const base = "http://127.0.0.1:" + process.env.ADW_TEST_PORT;
  const out = path.join(__dirname, "../../修复验证_20261108");
  const browser = await chromium.launch({executablePath:process.env.CHROME_PATH, headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1024}});
    page.setDefaultTimeout(7000);
    const get = async name => (await page.request.get(base+"/api/q/"+name)).json();
    assert.match((await get("storage_status")).dataDir,/\/adw-workflows-[^/]+\/data$/);
    const [original] = await get("list_projects");
    const token = (await (await page.request.get(base+"/api/session")).json()).token;
    for (const name of ["第二个项目","第三个项目"]) {
      const response = await page.request.post(base+"/api/cmd/create_project",{headers:{"x-adw-token":token},data:{name,config_id:"_template"}});
      assert.equal(response.status(),200);
    }
    const list = await get("list_projects");
    await page.addInitScript(id => localStorage.setItem("lastProject",id),original.id);
    let releaseList, releaseConfigs;
    const listGate = new Promise(resolve => {releaseList=resolve;});
    const configGate = new Promise(resolve => {releaseConfigs=resolve;});
    let listRequests=0, configRequests=0, overviewRequests=0;
    const errors=[], writes=[];
    page.on("pageerror",err=>errors.push(err.message));
    page.on("request",req=>{
      if (req.url().includes("/api/q/project_overview?")) overviewRequests++;
      if (req.method()==="POST") writes.push(req.url());
    });
    const unavailable = route => route.fulfill({status:503,contentType:"application/json",body:JSON.stringify({error:"验收模拟暂时不可用"})});
    await page.route("**/api/q/list_projects?*",async route=>{
      if (++listRequests===1) { await listGate; await unavailable(route); } else await route.continue();
    });
    await page.route("**/api/q/available_configs?*",async route=>{
      if (++configRequests===1) { await configGate; await unavailable(route); } else await route.continue();
    });
    await page.goto(base+"/#/");
    const btn=name=>page.getByRole("button",{name,exact:true});
    const submit=page.getByRole("button",{name:/^(继续|一句话开拍)$/});
    const draft=page.locator(".bigbox textarea");
    const pop=page.locator(".bigbox .pop");
    await draft.fill("雨夜末班车到站，乘客在门口寻找失物。");
    await btn("选择项目").click();
    await pop.getByText("正在读取项目配置…").waitFor();
    await pop.getByText("正在读取项目列表…").waitFor();
    assert.ok(await submit.isDisabled());
    releaseList();releaseConfigs();
    await pop.getByRole("button",{name:"重试项目配置"}).waitFor();
    await pop.getByRole("button",{name:"重试项目列表"}).waitFor();
    assert.ok(await submit.isDisabled());
    await pop.getByRole("button",{name:"重试项目列表"}).click();
    await pop.getByRole("button",{name:original.name,exact:true}).waitFor();
    await pop.getByRole("button",{name:"重试项目配置"}).click();
    const configs=await get("available_configs");
    const selected=configs.find(c=>c.configId!=="_template");
    await pop.getByText("用模板新建项目",{exact:true}).click();
    await pop.getByRole("button",{name:"＋ 新项目（"+selected.name+" 配置）",exact:true}).click();
    await btn("新项目（"+selected.name+"）").click();
    await pop.getByRole("button",{name:original.name,exact:true}).click();
    assert.match(await draft.inputValue(),/末班车/);
    const recent=page.getByRole("region",{name:"最近剧集"});
    const episode=original.recent[0];
    const href="#/e/"+episode.id+"/"+(episode.state==="delivered"?"delivery":"story");
    await recent.locator('a[href="'+href+'"]').waitFor();
    const widths=[];
    for(const width of [1440,1280,1024,800]) {
      await page.setViewportSize({width,height:1024});
      const layout=await page.evaluate(()=>{
        const place=selector=>{
          const el=document.querySelector(selector), rect=el.getBoundingClientRect();
          return {left:Math.round(rect.left),width:Math.round(rect.width),right:Math.round(rect.right)};
        };
        return {scroll:document.documentElement.scrollWidth,composer:place(".bigbox"),submit:place('.bigbox button[data-home-submit]')};
      });
      assert.ok(layout.scroll<=width && layout.submit.right<=width && layout.composer.left>=0);
      if (width>=1024) await page.screenshot({path:path.join(out,"57_J01_创作首页_"+width+".png"),fullPage:true});
      widths.push({width,...layout});
    }
    assert.equal(listRequests,2); // initial failed request plus a single shared retry
    assert.equal(overviewRequests,2); // initial selected project plus explicit return from a new template
    assert.equal(configRequests,2);
    assert.deepEqual(writes,[]);
    const homeMetrics={listRequests,configRequests,overviewRequests,writes:[...writes]};
    const observedCount=await page.locator(".home .projcard").count();
    assert.equal(observedCount,list.filter(p=>p.recentVisible).length);
    await recent.locator('a[href="'+href+'"]').click();
    await page.waitForURL("**/"+href);
    await page.locator(".center").waitFor();
    assert.deepEqual(errors,[]);
    const pairs=widths.filter(w=>w.width>=1024).map(w=>'<h2>'+w.width+' 像素</h2><div><figure><img src="../docs/figma/J01.png"><figcaption>J01 设计稿</figcaption></figure><figure><img src="57_J01_创作首页_'+w.width+'.png"><figcaption>本地浏览器</figcaption></figure></div>').join("");
    await fs.writeFile(path.join(out,"J01_创作首页画板逐宽度对照.html"),'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>创作首页对照</title><style>body{font:16px/1.6 system-ui;margin:2em}div{display:grid;grid-template-columns:1fr 1fr;gap:1em}figure{margin:0}img{width:100%}@media(max-width:720px){div{grid-template-columns:1fr}}</style><h1>创作首页</h1><p>五种创作入口、项目选择、规格、快捷操作、最近项目与剧集均使用实际工作台数据。想法入口直接输入文字，导入入口选择稿件。按审查意见移除装饰卡和头像。项目封面取自本地样本，空项目使用默认底色。</p>'+pairs+'</html>');
    await fs.writeFile(path.join(out,"J01_项目与配置加载重试.json"),JSON.stringify({projects:list.length,cards:observedCount,homeMetrics,destination:href,viewports:widths,errors},null,2)+"\n");
    console.log("PASS home selection, delayed and failed requests, shared summary, recent episode and four viewports");
  } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
