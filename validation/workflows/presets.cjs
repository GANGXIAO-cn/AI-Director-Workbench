// Isolated local project and simulated executors. Exercise the same kickoff dialog as production.
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const {chromium} = require("playwright");
const base="http://127.0.0.1:"+process.env.ADW_TEST_PORT;
const out=path.resolve(__dirname,"../../修复验证_20261108");

async function main(){
  const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:900}});
    const errors=[];page.on("pageerror",e=>errors.push(e.message));
    const get=async(name,args={})=>{
      const res=await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}});
      assert.equal(res.status(),200);return res.json();
    };
    assert.match((await get("storage_status")).dataDir,/adw-workflows-/);
    const project=(await get("list_projects")).find(p=>p.name==="夜班柴犬");
    const pid=project.id, square="方形对白短片", ratio="1:1";
    const rows=()=>get("project_presets",{project_id:pid});
    const before=await rows();
    const btn=name=>page.getByRole("button",{name,exact:true});
    await page.goto(base+"/#/p/"+pid);
    await btn("＋ 新增一集").click();
    await page.locator(".modal input").fill("J30");
    await btn("新增并去开工").click();
    await page.getByPlaceholder("一句话想法").fill("两位值班员认错了伞");
    await btn("管理预设").click();
    await btn("复制为新预设").click();
    await page.getByLabel("新预设名称").fill(square);
    await page.getByLabel("画幅",{exact:true}).selectOption(ratio);
    await page.getByLabel("镜头节奏",{exact:true}).fill("角色认错物件时停一拍");
    await page.getByLabel("H3 分辨率",{exact:true}).selectOption("768p");
    await page.getByLabel("H3 候选数",{exact:true}).selectOption("2");
    await btn("保存新预设").click();
    await page.getByRole("status").filter({hasText:square}).waitFor();
    await btn("返回开工设置").click();
    assert.equal(await page.getByPlaceholder("一句话想法").inputValue(),"两位值班员认错了伞");
    await page.getByLabel("本集使用的规格预设").selectOption(square);
    await page.getByLabel("本集画幅覆盖").selectOption("4:3");
    await btn("开工，进入故事稿").click();
    await page.waitForURL(/\/e\/.+\/story/);
    const eid=new URL(page.url()).hash.split("/")[2];
    const kicked=await get("episode_view",{episode_id:eid});
    assert.equal(kicked.kickoff.spec.aspectRatio,"4:3");
    assert.equal(kicked.kickoff.spec.h3Resolution,"768p");
    assert.equal(kicked.kickoff.spec.h3Candidates,2);
    await page.goto(base+"/#/p/"+pid+"/preset");
    await page.getByLabel("查看项目预设").selectOption(square);
    await btn("改这个预设").click();
    await page.getByLabel("画幅",{exact:true}).selectOption("16:9");
    await page.getByText("J30 · 4:3 · "+square+" 第 1 版",{exact:true}).click();
    await page.getByText("· 本集覆盖",{exact:true}).waitFor();
    const widths=[];
    for(const width of [1440,1280,1024]){
      await page.setViewportSize({width,height:900});
      const dims=await page.locator(".project-presets").evaluate(el=>({width:el.clientWidth,scroll:el.scrollWidth}));
      assert.ok(dims.scroll<=dims.width+1,JSON.stringify(dims));
      await page.screenshot({path:path.join(out,"54_J30_规格预设_"+width+".png")});widths.push({width,...dims,viewport:width});
    }
    await btn("保存修改").click();
    await page.getByRole("status").filter({hasText:"第 2 版"}).waitFor();
    const after=await rows();
    assert.deepEqual(after.presets.find(p=>p.name==="default"),before.presets.find(p=>p.name==="default"));
    assert.equal(after.presets.find(p=>p.name===square).values.aspectRatio,"16:9");
    for(const item of before.episodes) assert.deepEqual(after.episodes.find(e=>e.id===item.id),item);
    const episode=after.episodes.find(e=>e.id===eid);
    assert.equal(episode.base.aspectRatio,ratio);assert.equal(episode.effective.aspectRatio,"4:3");
    assert.equal(episode.rev,1);
    const row=page.locator(".project-presets details").filter({has:page.getByText("J30 · 4:3 · "+square+" 第 1 版",{exact:true})});
    if (await row.getAttribute("open")===null) await row.locator("summary").click();
    await row.getByRole("button",{name:"调整本集规格"}).click();
    await row.getByLabel("本集新值").selectOption("21:9");
    await row.getByRole("button",{name:"预览本集变更"}).click();
    await row.getByText(/H3 打包画幅/).waitFor();
    assert.equal((await rows()).episodes.find(e=>e.id===eid).effective.aspectRatio,"4:3");
    await row.getByRole("button",{name:"确认本集变更"}).click();
    await page.getByText("J30 · 21:9 · "+square+" 第 1 版",{exact:true}).waitFor();
    const changed=(await rows()).episodes.find(e=>e.id===eid);
    assert.equal(changed.base.aspectRatio,"1:1");assert.equal(changed.effective.aspectRatio,"21:9");
    assert.deepEqual(errors,[]);
    await fs.writeFile(path.join(out,"J30_命名预设开工与影响核对.json"),JSON.stringify({project:pid,default:after.presets[0],named:after.presets.find(s=>s.name===square),
      newEpisode:episode,afterEpisodeOverride:changed,existingEpisodes:before.episodes.map(e=>e.code),widths},null,2)+"\n");
    const pairs=widths.map(w=>`<h2>${w.viewport} 像素</h2><div><figure><img src="../docs/figma/J30.png"><figcaption>J30 设计稿</figcaption></figure><figure><img src="54_J30_规格预设_${w.viewport}.png"><figcaption>本机工作台 · ${w.viewport} 像素</figcaption></figure></div>`).join("");
    await fs.writeFile(path.join(out,"J30_规格预设画板逐宽度对照.html"),'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>J30 规格预设对照</title><style>body{font:16px/1.6 system-ui;margin:2em}div{display:grid;grid-template-columns:1fr 1fr;gap:1em}figure{margin:0}img{width:100%}@media(max-width:720px){div{grid-template-columns:1fr}}</style><h1>规格预设与本集覆盖</h1>'+pairs+'</html>');
    console.log("PASS J30 shared preset copy, versioned kickoff, scoped override, persisted defaults, three widths");
  }finally{await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
