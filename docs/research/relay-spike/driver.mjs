import { chromium } from 'playwright-core';
const [,, scenario, relay, page, room, daemonPub, psk, out] = process.argv;
const b = await chromium.launch({executablePath:'/home/jianlee/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome'});
const ctx = await b.newContext();
const p = await ctx.newPage();
p.on('console', m => { if (m.type()==='error') console.log('console.error', m.text()) });
await p.goto(page); await p.waitForFunction(()=>window.ready);
const res = {scenario, browser: await p.evaluate(()=>navigator.userAgent.match(/Chrome\/[\d.]+/)[0])};
res.key1 = await p.evaluate(()=>window.staticKeyInfo());
try {
  res.hs = await p.evaluate((a)=>window.connect(a), {relay, room, daemonPub, psk});
  if (scenario === 'good') {
    const r = await p.evaluate(async()=>{ const r = await window.__request('GET','/api/projects'); return {status:r.status, text:new TextDecoder().decode(r.bytes), ms:r.ms}});
    const j = JSON.parse(r.text); res.api = {status:r.status, ms:Math.round(r.ms), projects:(j.projects??j).map(x=>x.name)};
    // latency of 20 small calls
    res.small = await p.evaluate(async()=>{ const t=[]; for(let i=0;i<20;i++){const r=await window.__request('GET','/api/auth-mode'); t.push(r.ms)} t.sort((a,b)=>a-b); return {p50:+t[10].toFixed(1), p95:+t[18].toFixed(1)}});
    // throughput: 5 MiB
    res.bulk = await p.evaluate(async()=>{ const r=await window.__request('GET','/_blob/5242880'); return {bytes:r.bytes.length, ms:Math.round(r.ms), MiBps:+((r.bytes.length/1048576)/(r.ms/1000)).toFixed(1)}});
    res.bulkBin = await p.evaluate(async()=>{ const r=await window.__requestBin('/_blob/5242880'); return {bytes:r.bytes.length, ms:Math.round(r.ms), MiBps:+((r.bytes.length/1048576)/(r.ms/1000)).toFixed(1)}});
    res.bulkBin20 = await p.evaluate(async()=>{ const r=await window.__requestBin('/_blob/20971520'); return {bytes:r.bytes.length, ms:Math.round(r.ms), MiBps:+((r.bytes.length/1048576)/(r.ms/1000)).toFixed(1)}});
    // a tampered frame must be rejected, the same frame untampered must be accepted
    res.tamper = await p.evaluate(async()=>{ const T=window.__transport.constructor, k=window.__transport.sk; const ct=await new T(k,k).encrypt(new TextEncoder().encode('x'));
      let ok='rejected'; try{ await new T(k,k).decrypt(ct); ok='untampered accepted' }catch(e){ ok='untampered REJECTED' }
      const bad=ct.slice(); bad[3]^=1; let t2='REJECTED-not'; try{ await new T(k,k).decrypt(bad); t2='tampered ACCEPTED' }catch(e){ t2='tampered rejected' } return ok+', '+t2 });
    // reload: the static key must persist and stay non-extractable
    await p.reload(); await p.waitForFunction(()=>window.ready);
    res.key2 = await p.evaluate(()=>window.staticKeyInfo());
  }
} catch (e) { res.error = String(e.message||e).slice(0,160); }
console.log(JSON.stringify(res, null, 1));
await b.close();
