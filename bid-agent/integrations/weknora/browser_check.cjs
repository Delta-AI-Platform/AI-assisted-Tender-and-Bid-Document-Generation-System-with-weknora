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
await call('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:out+'/browser-downloads'});
await evaluate(`(()=>{const row=[...document.querySelectorAll('.bid-files li')].find(x=>x.innerText.includes('quotation-agent-v3.docx'));if(!row)throw Error('Expected artifact missing');row.querySelector('button').click()})()`);await delay(1500);
const result=await evaluate(`({path:location.pathname,text:document.body.innerText.slice(-18000),buttons:[...document.querySelectorAll('button')].map(x=>({text:x.innerText,title:x.title,label:x.getAttribute('aria-label')}))})`);
fs.writeFileSync(out+'/browser-initial-check.json',JSON.stringify(result,null,2));
const shot=await call('Page.captureScreenshot',{format:'png'},sessionId);fs.writeFileSync(out+'/browser-initial.png',Buffer.from(shot.data,'base64'));
console.log(JSON.stringify({path:result.path,buttons:result.buttons.slice(-20)}));
}finally{browser.kill('SIGTERM')}})().catch(e=>{console.error(e.message);process.exitCode=1});
