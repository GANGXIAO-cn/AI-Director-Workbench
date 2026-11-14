const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1500,height:960}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(10000);
  const origin='http://127.0.0.1:'+process.env.ADW_TEST_PORT;
  await page.goto(origin);
  const idea='送错蛋糕的学徒追上末班渡轮，必须在离岸前找到寿星。';
  // 实测 114：首页唯一按钮“一句话开拍”按运行模式执行；本脚本验证手工路径。
  const create=async text=>{await page.goto(origin+'/#/');await page.getByRole('button',{name:/^规格预设 ·/}).click();await page.getByLabel('运行模式').selectOption('manual');await page.getByRole('button',{name:'完成',exact:true}).click();
    await page.locator('.bigbox textarea').fill(text);await page.getByRole('button',{name:'一句话开拍',exact:true}).click();await page.waitForURL(/#\/e\/.+\/story$/);return page.url().split('/').at(-2);};
  const eid=await create(idea);
  const ep=async()=> (await page.request.get(origin+'/api/q/episode_view',{params:{args:JSON.stringify({episode_id:eid})}})).json();
  const chat=page.locator('.assistant:visible');
  await chat.locator(".msg.user").filter({hasText:idea}).waitFor();
  await chat.getByText('已生成 R01，待采用',{exact:true}).waitFor();
  await chat.getByText('更多工具',{exact:true}).click();await chat.getByText('本集开工资料',{exact:true}).waitFor();
  await chat.getByRole('button',{name:'收起',exact:true}).click();
  await chat.locator('textarea').fill('现在是什么状态，总结一下。');await chat.getByRole('button',{name:'发送消息'}).click();
  await chat.getByRole('paragraph').filter({hasText:/^本次只讨论当前稿件。$/}).waitFor();
  assert.equal((await ep()).artifacts.find(x=>x.stage==='story').versions.length,1);
  await chat.getByRole('button',{name:'补充独审'}).click();
  await chat.getByRole('button',{name:'采用 R01',exact:true}).click();
  await page.locator('.story-result:visible').waitFor();
  await create('随机选');await chat.getByRole('button',{name:'想一个新故事',exact:true}).click();
  await chat.getByText('已生成 R01，待采用',{exact:true}).waitFor();
  await create('学徒临开船才说出真相【测试：退回】');
  await chat.getByRole('button',{name:'补充独审'}).click();
  const issue=chat.locator('.chat-issue').first();
  await issue.getByRole('button',{name:'按本版放行'}).click();
  await issue.getByText(/已放行（仅本版）/).waitFor();
  await chat.getByRole('button',{name:'采用 R01',exact:true}).waitFor();
  assert.match(await page.locator('.story-result:visible').innerText(),/待采用/);
  assert.deepEqual(errors,[]);
  console.log('首页提交、首条消息、工具收纳、询问不改稿、审查采用、随机澄清：浏览器验证通过');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
