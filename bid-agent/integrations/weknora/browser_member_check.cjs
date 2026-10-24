const {spawn}=require('child_process');const fs=require('fs');
const base='/home/ubuntu/codex-workspace';const out=base+'/bid-agent/runs/real-project-20261022/analysis/browser-member-20261023';fs.mkdirSync(out,{recursive:true});
const account=JSON.parse(fs.readFileSync(base+'/weknora-build-tools/evaluation-account.json'));
const browser=spawn('/home/ubuntu/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell',['--headless','--no-sandbox','--disable-gpu','--remote-debugging-pipe','--user-data-dir='+base+'/weknora-build-tools/browser-member-profile'],{stdio:['ignore','ignore','ignore','pipe','pipe']});
let n=0,buf='',pending=new Map();
browser.stdio[4].on('data',chunk=>{buf+=chunk;let i;while((i=buf.indexOf('\0'))>=0){const msg=JSON.parse(buf.slice(0,i));buf=buf.slice(i+1);if(msg.id&&pending.has(msg.id)){const {resolve,reject}=pending.get(msg.id);pending.delete(msg.id);msg.error?reject(new Error(msg.error.message)):resolve(msg.result)}}});
function call(method,params={},sessionId){return new Promise((resolve,reject)=>{const id=++n;pending.set(id,{resolve,reject});browser.stdio[3].write(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})})+'\0')})}
const delay=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{try{
const {targetId}=await call('Target.createTarget',{url:'http://127.0.0.1/login'});const {sessionId}=await call('Target.attachToTarget',{targetId,flatten:true});
const evaluate=async expression=>{const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},sessionId);if(r.exceptionDetails)throw new Error(r.exceptionDetails.exception?.description || 'Browser evaluation failed');return r.result?.value};
await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false},sessionId);
await delay(1500);
const authData=JSON.parse(fs.readFileSync('/tmp/deta-user-browser-auth.json'));
await evaluate(`(()=>{localStorage.clear();localStorage.setItem('weknora_token',${JSON.stringify(authData.token)});localStorage.setItem('weknora_user',${JSON.stringify(JSON.stringify(authData.auth.user))});localStorage.setItem('weknora_tenant',${JSON.stringify(JSON.stringify(authData.auth.tenant))});localStorage.setItem('weknora_memberships',${JSON.stringify(JSON.stringify(authData.auth.memberships))})})()`);
await call('Page.navigate',{url:'http://127.0.0.1/platform/agents'},sessionId);await delay(4000);
await evaluate(`(()=>{document.querySelector('button[aria-label="跳过引导"]')?.click();const el=document.querySelector('.tenant-trigger');if(!el)throw Error('Member workspace selector missing');el.click()})()`);await delay(1200);
await evaluate(`(()=>{const el=[...document.querySelectorAll('.tenant-item')].find(e=>e.innerText.includes('德塔智能招投标系统'));if(!el)throw Error('Authorized workspace missing');el.click()})()`);await delay(3500);
await call('Page.navigate',{url:'http://127.0.0.1/platform/agents'},sessionId);await delay(2500);
await evaluate(`(()=>{if(!document.body.innerText.includes('第四标段完整标书Agent'))throw Error('Bid agent missing after switch')})()`);
const result=await evaluate(`({path:location.pathname,text:document.body.innerText.slice(-18000),artifactNames:[...document.querySelectorAll('.bid-files li span')].map(x=>x.innerText),buttons:[...document.querySelectorAll('button')].map(x=>({text:x.innerText,title:x.title,label:x.getAttribute('aria-label')}))})`);
fs.writeFileSync(out+'/browser-full-project-check.json',JSON.stringify(result,null,2));
const shot=await call('Page.captureScreenshot',{format:'png'},sessionId);fs.writeFileSync(out+'/browser-full-project.png',Buffer.from(shot.data,'base64'));
console.log(JSON.stringify({path:result.path,buttons:result.buttons.slice(-20)}));
}finally{browser.kill('SIGTERM')}})().catch(e=>{console.error(e.message);process.exitCode=1});
