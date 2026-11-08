const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs/promises");
const {chromium} = require("playwright");

async function main() {
 const base="http://127.0.0.1:"+process.env.ADW_TEST_PORT;
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH});
 try {
  const ctx=await browser.newContext({viewport:{width:1400,height:900}});
  const page=await ctx.newPage();
  const errors=[];ctx.on("page",p=>p.on("pageerror",e=>errors.push(e.message)));
  const req=async(url,key,args={})=>{
    const r=await page.request.get(url+"/api/q/"+key,{params:{args:JSON.stringify(args)}});
    assert.equal(r.status(),200);return r.json();
  };
  const root=(await req(base,"storage_status")).dataDir;
  assert.match(root,/adw-workflows-[^/]+\/data$/);
  const {id,name,recent,deliveries}= (await req(base,"list_projects"))[0];
  const original=await req(base,"episode_view",{episode_id:recent[0].id});
  const folder=path.join(path.dirname(root),"制作盘 项目一");
  await page.goto(base+"/#/projects");
  await page.getByRole("button",{name:"管理项目 "+name}).click();
  await page.getByRole("button",{name:"另存独立项目"}).click();
  await page.getByLabel("新项目文件夹绝对路径").fill(folder);
  await page.getByRole("button",{name:"另存项目",exact:true}).click();
  await page.getByRole("status").filter({hasText:folder}).waitFor();
  await page.getByRole("button",{name:"关闭",exact:true}).click();
  await page.getByText("打开项目文件夹",{exact:true}).click();
  async function enter(p){
   await page.getByLabel("项目文件夹绝对路径",{exact:true}).fill(p);
   await page.getByRole("button",{name:"打开目录",exact:true}).click();
   const link=page.getByRole("link",{name:"进入独立项目"});
   await link.waitFor();
   return Promise.all([ctx.waitForEvent("page"),link.click()]).then(([tab])=>tab);
  }
  const external=await enter(folder);
  await external.getByRole("navigation",{name:"已打开的项目"}).getByRole("link",{name,exact:true}).waitFor();
  await external.getByText("独立项目 · 保存到 "+folder,{exact:true}).waitFor();
  const url=new URL(external.url()).origin;
  assert.notEqual(url,base);
  const token=(await (await page.request.get(base+"/api/session")).json()).token;
  const open=(folder,expected)=>page.request.post(base+"/api/project-folder/open",{
    headers:{"x-adw-token":token},data:{folder,expected}
  });
  assert.equal(new URL((await (await open(folder)).json()).url).origin,url);
  const exportPage=await page.request.get(url+"/api/delivery-file/"+deliveries[0].id+"/00_图文对照.html");
  assert.match(exportPage.headers()["content-security-policy"],/script-src 'sha256-/);
  assert.equal((await req(url,"storage_status")).dataDir,folder);
  assert.equal((await req(url,"verify_delivery",{delivery_id:deliveries[0].id})).ok,true);
  assert.deepEqual(await req(url,"episode_view",{episode_id:recent[0].id}),original);
  await external.goto(url+"/#/projects");
  await external.getByRole("button",{name:"管理项目 "+name}).click();
  await external.getByRole("button",{name:"重命名"}).click();
  await external.getByLabel("项目名称",{exact:true}).fill("外盘修改后");
  await external.getByRole("button",{name:"保存名称"}).click();
  await external.getByRole("button",{name:"管理项目 外盘修改后"}).waitFor();
  assert.equal((await req(base,"list_projects"))[0].name,name);
  const lost=folder+" 临时离线";
  await fs.rename(folder,lost);
  await fs.mkdir(folder);
  const offline=await page.request.get(url+"/api/q/list_projects");assert.equal(offline.status(),503);
  assert.match((await offline.json()).error,/离线/);
  assert.deepEqual(await fs.readdir(folder),[]);
  await fs.rename(folder,folder+" 其他空目录");
  await fs.rename(lost,folder);
  assert.equal((await req(url,"list_projects"))[0].name,"外盘修改后");
  await external.close();
  await page.getByRole("button",{name:"关闭目录"}).click();
  await page.getByRole("link",{name:"进入独立项目"}).waitFor({state:"hidden"});
  const moved=folder+" 已移动";
  await fs.rename(folder,moved);
  await page.reload();
  await page.getByText("打开项目文件夹",{exact:true}).click();
  await page.getByText(/目录离线/).waitFor();
  await page.getByRole("button",{name:"重新定位"}).click();
  const denied=await open(moved,"other-project-fingerprint");assert.equal(denied.status(),422);
  const reopened=await enter(moved);
  await reopened.getByRole("navigation",{name:"已打开的项目"}).getByRole("link",{name:"外盘修改后",exact:true}).waitFor();
  const reopenedBase=new URL(reopened.url()).origin;
  assert.equal((await req(reopenedBase,"list_projects"))[0].id,id);
  assert.equal((await req(reopenedBase,"verify_delivery",{delivery_id:deliveries[0].id})).ok,true);
  const evidence=path.join(__dirname,"../../修复验证_20261108");
  await page.screenshot({path:path.join(evidence,"32_已移位的独立项目目录.png")});
  const manifest=JSON.parse(await fs.readFile(path.join(moved,"导演工作台项目.json")));
  assert.deepEqual(errors,[]);
  await fs.writeFile(path.join(evidence,"独立项目目录与移位重开.json"),JSON.stringify({project:id,episode:recent[0].id,
    delivery:deliveries[0].id,sourceName:name,reopenedName:"外盘修改后",manifest,sameEpisode:true,offlineStatus:offline.status(),replacementDirectoryUntouched:true,verifiedBeforeAndAfterMove:true,errors},null,2)+"\n");
  console.log("PASS export, open editable external project, close, move and reopen with intact delivery");
 } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
