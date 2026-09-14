const CACHE="mandi-tracker-v1";
const SHELL=["/","/manifest.webmanifest","/static/style.css"];
self.addEventListener("install",event=>{event.waitUntil(caches.open(CACHE).then(c=>c.addAll(SHELL)).then(()=>self.skipWaiting()));});
self.addEventListener("activate",event=>{event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim()));});
self.addEventListener("fetch",event=>{
 if(event.request.method!=="GET") return;
 if(event.request.mode==="navigate"){
   event.respondWith(fetch(event.request).then(r=>{const copy=r.clone();caches.open(CACHE).then(c=>c.put(event.request,copy));return r;}).catch(()=>caches.match(event.request).then(r=>r||caches.match("/"))));
 } else {
   event.respondWith(caches.match(event.request).then(cached=>cached||fetch(event.request).then(r=>{if(r.ok && new URL(event.request.url).origin===location.origin){const copy=r.clone();caches.open(CACHE).then(c=>c.put(event.request,copy));}return r;})));
 }
});
