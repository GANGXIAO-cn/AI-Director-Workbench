// The loopback server uses an isolated, locally generated test delivery.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {chromium} = require("playwright");
const base = "http://127.0.0.1:"+process.env.ADW_TEST_PORT;
const out = path.resolve(__dirname,"../../修复验证_20261108");

async function main() {
  const browser = await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH});
  try {
    const ctx = await browser.newContext({viewport:{width:1280,height:880},permissions:["clipboard-read","clipboard-write"]});
    const page = await ctx.newPage();
    const errors = [];
    page.on("pageerror", e => errors.push(e.message));
    const query = async (key,args={}) => {
      const r=await page.request.get(base+"/api/q/"+key,{params:{args:JSON.stringify(args)}});
      assert.ok(r.ok());return r.json();
    };
    assert.match((await query("storage_status")).dataDir,/adw-workflows-/);
    const project=(await query("list_projects"))[0].id;
    const episode=(await query("project_overview",{project_id:project})).episodes.find(e=>e.deliveries.some(d=>d.scope==="full"));
    const delivery=episode.deliveries.find(d=>d.scope==="full").id;
    const url=base+"/api/delivery-file/"+delivery+"/";
    const manifest=await (await page.request.get(url+"episode.json")).json();
    await page.goto(url+"00_图文对照.html");
    await page.locator("main h1").waitFor();
    const buttons=page.locator("section[data-segment] button");
    assert.equal(await buttons.count(),manifest.segments.length);
    const first=page.locator("section[data-segment]").first();
    const segment=manifest.segments[0];
    const txt=await (await page.request.get(url+segment.prompt)).text();
    assert.equal(await first.locator("pre").textContent(),txt);
    assert.ok(txt.startsWith("\n"));
    console.log("验证图片解码",manifest.segments.length,"段");
    const media=await page.locator("ol.cards img").evaluateAll(async imgs=>{
      for (const img of imgs) img.loading="eager";
      await Promise.race([Promise.all(imgs.map(img=>img.decode())),
        new Promise((_,reject)=>setTimeout(()=>reject(new Error("图片解码超时")),10000))]);
      return imgs.map(img=>[img.naturalWidth,img.naturalHeight]);
    });
    assert.ok(media.length && media.every(([w,h])=>w>0&&h>0));
    const last=manifest.segments.at(-1).id;
    await page.locator("[data-segment-link]").last().click();
    assert.equal(decodeURIComponent(new URL(page.url()).hash),"#segment-"+last);
    await page.locator("[data-image-selection-link]").click();
    assert.equal(manifest.segments.length,2);
    await page.getByRole("link",{name:"本集角色设定",exact:true}).click();
    const baseline = page.locator("#episode-cast li").filter({has: page.getByRole("heading",{name:"阿福",exact:true})});
    assert.ok((await baseline.textContent()).includes("已登记出镜段 G01"));
    await baseline.locator("img").evaluate(img=>img.decode());
    assert.ok(await baseline.locator("img").evaluate(img=>img.naturalWidth>0));
    await page.getByRole("link",{name:"分段角色绑定"}).click();
    assert.equal(await page.locator("#episode-bindings li").count(),2);
    await page.locator("[data-image-selection-link]").click();
    const unused=manifest.workbench.page.images.find(i=>!i.segments.length);
    const pick=page.locator('[data-pick="'+unused.id+'"]');
    await pick.check();await page.locator("#selection-note").fill("用于结尾");
    const message=await page.locator("#selection-message").inputValue();
    assert.ok(message.includes(await pick.getAttribute("data-pick")) && message.includes("用于结尾"));
    console.log("验证补选消息复制");
    await page.locator("#copy-selection").click();
    const readClipboard=()=>page.evaluate(()=>Promise.race([navigator.clipboard.readText(),
      new Promise((_,reject)=>setTimeout(()=>reject(new Error("读取隔离浏览器剪贴板超时")),5000))]));
    assert.equal(await readClipboard(),message);
    const pub=page.locator("[data-publish-pack] pre").first();
    const pubLabel=await pub.getAttribute("data-copy-label");
    await page.getByRole("button",{name:pubLabel,exact:true}).click();
    assert.equal(await readClipboard(),await pub.textContent());
    await buttons.first().click();
    assert.equal(await readClipboard(),txt);
    // Reject the preferred API, exercise the real browser fallback, then deny both.
    await page.evaluate(()=>{
      Object.defineProperty(navigator.clipboard,"writeText",{configurable:true,value:async()=>{throw new Error("denied");}});
      document.querySelector("section [role=status]").textContent="";
    });
    await buttons.first().click();
    await page.waitForFunction(()=>document.querySelector("section [role=status]").textContent==="已复制");
    assert.equal(await readClipboard(),txt);
    await page.evaluate(()=>{document.execCommand=()=>false;});
    await buttons.first().click();
    await first.getByText("复制未成功，请选中下方内容手动复制").waitFor();
    await page.locator("#copy-selection").click();
    assert.equal(await page.locator("#selection-message").evaluate(e=>e.value.slice(e.selectionStart,e.selectionEnd)),message);
    assert.equal(await first.locator("pre").textContent(),txt);
    await page.reload();
    await page.setViewportSize({width:390,height:844});
    await page.locator("[data-segment-link]").first().click();
    const size=await page.evaluate(()=>({viewport:innerWidth,actual:document.documentElement.scrollWidth}));
    assert.ok(size.actual<=size.viewport,JSON.stringify(size));
    await page.screenshot({path:path.join(out,"07_交付页移动端分段与复制.png")});
    assert.deepEqual(errors,[]);
    const result={segments:manifest.segments.length,decodedImages:media.length,publishFields:await page.locator("[data-publish-field]").count(),
      firstSegmentMatchesTXT:true,segmentAnchor:true,clipboard:"实际读取并逐字比对",fallback:"execCommand 成功与双通道失败均验证",
      gallerySelection:true,viewport:size,runtimeErrors:errors};
    fs.writeFileSync(path.join(out,"图文交付页浏览器验收.json"),JSON.stringify(result,null,2)+"\n");
    console.log("PASS",result);
  } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
