/* Lightweight DOM fixture: exercise the real search script without a browser. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const script = fs.readFileSync(path.join(__dirname, '../static/assets/knowledge.js'), 'utf8');
const notes = [
  {title:'KV Cache', path:'knowledge/infra/concepts/kv-cache.md', group:'knowledge', group_name:'沉淀知识', body:'缓存机制与 memory', url:'/knowledge/notes/kv.html'},
  {title:'Agent 实践', path:'agent/reports/demo.md', group:'agent', group_name:'Agent', body:'KV Cache in production', url:'/knowledge/notes/agent.html'},
  {title:'SSH', path:'knowledge/foundations/tools/ssh.md', group:'knowledge', group_name:'沉淀知识', body:'密钥认证', url:'/knowledge/notes/ssh.html'},
];
class Element {
  constructor() { this.children=[]; this.value=''; this.events={}; }
  append(...items) { for (const item of items) this.children.push(...(item.fragment ? item.children : [item])); }
  after(item) { this.next = item; }
  replaceChildren(...items) { this.children=items; }
  addEventListener(name, callback) { this.events[name]=callback; }
}
async function search(url, expected) {
  const fields = Object.fromEntries(['kb-query','kb-group','kb-search-state','kb-results'].map(id=>[id,new Element()]));
  const location = new URL(url);
  const context = {
    URL, URLSearchParams, AbortController, location, history:{replaceState(){}},
    document:{getElementById:id=>fields[id], querySelectorAll:()=>[], createElement:()=>new Element(),
      createDocumentFragment:()=>Object.assign(new Element(),{fragment:true})},
    fetch:async address=>{
      assert.ok(address.startsWith('/api/v1/knowledge/search?'));
      const p=new URL(address,'http://localhost').searchParams;
      const terms=(p.get('q')||'').toLowerCase().split(/\s+/).filter(Boolean);
      const selected=notes.filter(n=>(!p.get('group')||n.group===p.get('group')) &&
        (!p.get('path')||n.path.startsWith(p.get('path'))) && terms.every(t=>(n.title+' '+n.path+' '+n.body).toLowerCase().includes(t)));
      const start=Number(p.get('offset')||0), limit=Number(p.get('limit')||40);
      return {ok:true,json:async()=>({total:selected.length,results:selected.slice(start,start+limit)})};
    }, setTimeout, clearTimeout,
  };
  vm.runInNewContext(script, context);
  await new Promise(resolve=>setImmediate(resolve));
  const results=fields['kb-results'].children;
  assert.equal(results.length,expected.length);
  assert.deepEqual(results.map(e=>e.children[0].textContent), expected);
  return fields;
}
(async()=>{
  await search('http://localhost/knowledge?q=KV', ['KV Cache','Agent 实践']);
  await search('http://localhost/knowledge?q=KV&group=agent', ['Agent 实践']);
  await search('http://localhost/knowledge?q=密钥', ['SSH']);
  await search('http://localhost/knowledge?q=KV+memory', ['KV Cache']);
  await search('http://localhost/knowledge?path=knowledge/infra/', ['KV Cache']);
  await search('http://localhost/knowledge?q=not-found', []);
  const original=notes.splice(0);
  for (let i=0;i<51;i++) notes.push({...original[1],title:'Agent '+i});
  const fields=await search('http://localhost/knowledge?group=agent', Array.from({length:40},(_,i)=>'Agent '+i));
  assert.equal(fields['kb-results'].next.hidden,false);
  fields['kb-results'].next.events.click();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(fields['kb-results'].children.length,51);
  assert.equal(fields['kb-results'].next.hidden,true);
  console.log('知识库前端搜索验证通过：6 个查询用例及分页');
})().catch(error=>{console.error(error);process.exitCode=1;});
