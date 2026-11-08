const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const {chromium} = require("playwright");

async function main() {
  const base = "http://127.0.0.1:" + process.env.ADW_TEST_PORT;
  const dir = path.join(__dirname, "../../修复验证_20261108");
  let mode = "normal";
  const calls = [];
  const fixture = http.createServer((req, res) => {
    calls.push({method:req.method, path:req.url, credentialMatched:req.headers.authorization === "Bearer local-fixture-token"});
    res.setHeader("content-type", "application/json");
    if (mode === "redirect") {res.writeHead(302, {Location:"/forbidden"}); res.end(); return;}
    if (mode === "fail") {res.writeHead(429); res.end("local-fixture-token"); return;}
    const data = req.url.endsWith("after=m-01") ? {data:[{id:"m-02"}]} : {data:[{id:"m-01"}], has_more:true, last_id:"m-01"};
    res.end(JSON.stringify(data));
  });
  await new Promise(resolve => fixture.listen(0,"127.0.0.1",resolve));
  const browser = await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1400,height:950}});
    const errors = [];
    page.on("pageerror", e => errors.push(e.message));
    await page.goto(base + "/#/sys/settings");
    await page.getByRole("link",{name:"我的模型服务"}).click();
    const button = name => page.getByRole("button",{name,exact:true});
    const read = async () => (await (await page.request.get(base+"/api/q/api_providers")).json())[0];
    await page.getByLabel("CLI 限流后").selectOption("same_cli");
    await page.waitForFunction(async ()=>(await (await fetch("/api/q/model_catalog")).json()).quotaRetryPolicy==="same_cli");
    await button("添加服务商").click();
    await page.getByLabel("服务商名称").fill("本机目录验收");
    await page.getByLabel("接口基址").fill("http://127.0.0.1:" + fixture.address().port + "/v1");
    await button("保存服务商").click();
    await page.getByText("密钥未配置",{exact:true}).waitFor();
    await button("配置与模型").click();
    await page.getByLabel("当前服务进程密钥").fill("local-fixture-token");
    await button("应用密钥").click();
    await page.getByText("密钥已配置",{exact:true}).waitFor();
    await button("测试连接 · 不生成").click();
    await page.getByText(/模型目录可达/).waitFor();
    assert.deepEqual((await read()).models, []);
    await button("拉取全部模型").click();
    const first = page.getByRole("row").filter({hasText:"m-01"});
    await first.getByLabel("图片").check();
    await page.getByText(/图片 1 · 识图 0/).waitFor();
    await first.getByLabel("识图").check();
    await page.getByText(/图片 1 · 识图 1/).waitFor();
    await page.reload();
    await button("配置与模型").click();
    assert.ok(await first.getByLabel("识图").isChecked());
    assert.equal(await page.getByLabel("CLI 限流后").inputValue(),"same_cli");
    await page.getByLabel("筛选分类").selectOption("unknown");
    assert.equal(await first.count(),0);
    await page.getByRole("row").filter({hasText:"m-02"}).waitFor();
    await page.getByLabel("筛选分类").selectOption("all");
    await page.screenshot({path:path.join(dir, "21_独立模型服务商与目录.png")});
    mode = "fail";
    await button("拉取全部模型").click();
    await page.getByText(/模型目录返回 HTTP 429/).waitFor();
    mode = "redirect";
    await button("测试连接 · 不生成").click();
    await page.getByText(/模型目录返回 HTTP 302/).waitFor();
    const saved = await read();
    assert.equal(saved.models.length,2);
    assert.deepEqual(saved.models[0].categories, ["image","vision"]);
    assert.equal(JSON.stringify(saved).includes("local-fixture-token"),false);
    assert.deepEqual(errors,[]);
    assert.deepEqual(calls.map(c=>c.path),["/v1/models","/v1/models","/v1/models?after=m-01","/v1/models","/v1/models"]);
    assert.ok(calls.every(c=>c.method==="GET" && c.credentialMatched));
    fs.writeFileSync(path.join(dir,"模型服务商目录验收.json"),JSON.stringify({calls,models:saved.models,lastDiagnostic:saved.diagnostic,errors},null,2)+"\n");
    console.log("Provider browser workflow passed");
  } finally {await browser.close(); fixture.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
