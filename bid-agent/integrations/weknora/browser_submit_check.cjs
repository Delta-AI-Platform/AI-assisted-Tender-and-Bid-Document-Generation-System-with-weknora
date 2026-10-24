const {spawn}=require('child_process');const fs=require('fs');
const base='/home/ubuntu/codex-workspace';const out=base+'/bid-agent/runs/weknora-bridge-validation';
const account=JSON.parse(fs.readFileSync(base+'/weknora-build-tools/evaluation-account.json'));
const browser=spawn('/home/ubuntu/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell',['--headless','--no-sandbox','--disable-gpu','--remote-debugging-pipe','--user-data-dir='+base+'/weknora-build-tools/browser-profile'],{stdio:['ignore','ignore','ignore','pipe','pipe']});
let n=0,buf='',pending=new Map();
browser.stdio[4].on('data',chunk=>{buf+=chunk;let i;while((i=buf.indexOf('\0'))>=0){const msg=JSON.parse(buf.slice(0,i));buf=buf.slice(i+1);if(msg.id&&pending.has(msg.id)){const {resolve,reject}=pending.get(msg.id);pending.delete(msg.id);msg.error?reject(new Error(msg.error.message)):resolve(msg.result)}}});
function call(method,params={},sessionId){return new Promise((resolve,reject)=>{const id=++n;pending.set(id,{resolve,reject});browser.stdio[3].write(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})})+'\0')})}
const delay=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{try{
const {targetId}=await call('Target.createTarget',{url:'http://127.0.0.1:18765/login'});const {sessionId}=await call('Target.attachToTarget',{targetId,flatten:true});
const evaluate=async expression=>{const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},sessionId);if(r.exceptionDetails)throw new Error('Browser evaluation failed');return r.result?.value};
await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false},sessionId);
await delay(3000);
await evaluate(`(()=>{const inputs=document.querySelectorAll('input');const email=[...inputs].find(x=>x.autocomplete==='email');const pass=[...inputs].find(x=>x.type==='password');if(!email||!pass)return;email.value=${JSON.stringify(account.email)};email.dispatchEvent(new Event('input',{bubbles:true}));pass.value=${JSON.stringify(account.password)};pass.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('button[type=submit]').click()})()`);
await delay(4000);await call('Page.navigate',{url:'http://127.0.0.1:18765/platform/chat/42c90d3b-65f8-4870-b7ff-075106f19528'},sessionId);await delay(4000);
await evaluate(`(()=>{document.querySelector('button[aria-label="跳过引导"]')?.click();document.querySelector('button[aria-label="沙箱终端"]')?.click()})()`);await delay(1500);
await evaluate(`(()=>{[...document.querySelectorAll('button[role=tab]')].find(x=>x.innerText==='产物')?.click()})()`);await delay(1000);
const query='页面贯通验证 UI-CHECK-20261022：请实际调用 inspect_docx_tables，path=quotation-agent-v3.docx，offset=0，count=1，确认报价表总行数和重复表头行数。不要读取全文，也不要修改文件。然后调用 remember 保存 evidence，key=ui-table-check-20261022，记录文件哈希、表格索引、行数及结果来源，明确未完成整卷验收。完成后用两句话汇报。';
await evaluate(`(()=>{const area=document.querySelector('.answers-input textarea');if(!area)throw Error('Composer missing');area.value=${JSON.stringify(query)};area.dispatchEvent(new Event('input',{bubbles:true}))})()`);await delay(300);
await evaluate(`(()=>{const box=document.querySelector('.answers-input');const send=box.querySelector('button[aria-label="发送"]');if(!send||send.disabled)throw Error('Send unavailable');send.click()})()`);
console.log('Submitted task through existing chat composer');
await delay(5000);
for(let i=0;i<36;i++){await delay(5000);const active=await evaluate(`!!document.querySelector('.answers-input button[aria-label*="停止"]')`);if(i>2&&!active)break;}
const result=await evaluate(`({path:location.pathname,text:document.body.innerText.slice(-18000),buttons:[...document.querySelectorAll('button')].map(x=>({text:x.innerText,title:x.title,label:x.getAttribute('aria-label')}))})`);
fs.writeFileSync(out+'/browser-submit-check.json',JSON.stringify(result,null,2));
const shot=await call('Page.captureScreenshot',{format:'png'},sessionId);fs.writeFileSync(out+'/browser-submit.png',Buffer.from(shot.data,'base64'));
console.log(JSON.stringify({path:result.path,buttons:result.buttons.slice(-20)}));
}finally{browser.kill('SIGTERM')}})().catch(e=>{console.error(e.message);process.exitCode=1});
