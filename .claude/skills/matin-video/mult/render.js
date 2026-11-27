// node render.js data.json mode out  (mode: frames "t1,t2,.." -> out dir jpgs | video -> stdout jpeg stream)
const {chromium}=require('playwright');const fs=require('fs');const path=require('path');
(async()=>{
 const [,,dataF,mode,arg,out]=process.argv; const data=JSON.parse(fs.readFileSync(dataF,'utf8'));
 const b=await chromium.launch(); const p=await b.newPage({viewport:{width:360,height:640},deviceScaleFactor:3});
 await p.goto('file://'+path.join(__dirname,'page.html')); await p.evaluate(async()=>{for(const f of ['800 21px MS','italic 900 21px MS','600 12px MS']) await document.fonts.load(f,'Aaәғқңөұүһі'); await document.fonts.ready;});
 await p.evaluate(d=>window.setup(d),data); await p.waitForTimeout(300);
 if(mode==='frames'){ const ts=arg.split(',').map(Number); let i=0;
   for(const t of ts){ await p.evaluate(t=>window.draw(t),t); await p.screenshot({path:path.join(out,`f${String(i++).padStart(2,'0')}.jpg`),type:'jpeg',quality:92}); } }
 else { const n=Math.ceil(data.dur*data.fps);
   for(let i=0;i<n;i++){ await p.evaluate(t=>window.draw(t),i/data.fps); const buf=await p.screenshot({type:'jpeg',quality:93});
     if(!process.stdout.write(buf)) await new Promise(r=>process.stdout.once('drain',r));
     if(i%150===0) process.stderr.write(`кадр ${i}/${n}\n`); } }
 await b.close();
})();
