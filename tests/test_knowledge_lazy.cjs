const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname,'../static/assets/knowledge.js'),'utf8');
class Element {
  constructor() { this.children=[]; this.events={}; }
  append(...items) { this.children.push(...items); for (const item of items) item.parentElement=this; }
  before(...items) { this.inserted=items; }
  addEventListener(name, fn) { this.events[name]=fn; }
  remove() {}
}
const tick=()=>new Promise(resolve=>setTimeout(resolve,15));
async function test(observed) {
  const head=new Element();
  const codes=Array.from({length:2},()=>{
    const code=new Element(); code.textContent='graph LR; A-->B';
    const pre=new Element(); pre.append(code); return code;
  });
  const rendered=[];
  let observer;
  const context={setTimeout,Promise,
    document:{getElementById:()=>null,querySelectorAll:()=>codes,head,createElement:()=>new Element()},
    mermaid:{initialize:options=>assert.equal(options.securityLevel,'strict'),
      render:async(id,text)=>{rendered.push(id);return {svg:'<svg>'+text+'</svg>'};}},
  };
  if (observed) context.IntersectionObserver=class {
    constructor(callback) {this.callback=callback;this.items=[];observer=this;}
    observe(item) {this.items.push(item);}
    unobserve() {}
  };
  vm.runInNewContext(source,context);
  assert.equal(head.children.length,0,'Do not load Mermaid until a diagram is visible/clicked');
  const first=observed?observer.items[0]:codes[0].parentElement.inserted[0];
  if (observed) observer.callback([{target:first,isIntersecting:true}]); else first.children[0].events.click();
  await tick();
  assert.equal(head.children.length,1);
  assert.equal(head.children[0].src,'/assets/mermaid-11.12.0.min.js');
  head.children[0].onload();
  await tick();
  assert.equal(rendered.length,1);
  assert.ok(first.innerHTML.startsWith('<svg>'));
  const second=observed?observer.items[1]:codes[1].parentElement.inserted[0];
  if (observed) observer.callback([{target:second,isIntersecting:true}]); else second.children[0].events.click();
  await tick();
  assert.equal(head.children.length,1,'Reuse the already-loaded library');
  assert.equal(rendered.length,2);
  assert.equal(codes[0].parentElement.parentElement.children[1],codes[0].parentElement,'Keep diagram source readable');
}
(async()=>{await test(true);await test(false);console.log('图表懒加载验证通过：可见性触发、点击回退、单次加载与源码保留');})()
  .catch(error=>{console.error(error);process.exitCode=1;});
