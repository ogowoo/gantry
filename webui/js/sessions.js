// sessions.js — 
// 从原 webui/app.js 拆出的 ES 模块（该单体已退役，见 docs/ESM-MIGRATION.md）。
function toggleSessExpand(sid){
 if(expandedSess.has(sid))expandedSess.delete(sid);else expandedSess.add(sid);
 try{localStorage.setItem('gantry-expanded-sess',JSON.stringify([...expandedSess]))}catch(e){}
 sessSig='';drawSessions();
}
try{showSubagents=localStorage.getItem('gantry-show-subagents')==='1'}catch(e){}
let refreshSessionsInflight=false;   // AUD-P3（审核 ⑧-5）：与 runtime 同款的 in-flight 防护
async function refreshSessions(){
 if(serviceStopped)return;
 if(refreshSessionsInflight)return;   // 后端变慢时请求堆积、响应乱序会把旧列表写回界面
 refreshSessionsInflight=true;
 try{
  const q='?include_subagents=true';   // 全量：前端把子代理折叠到父会话下
  let d=await api('/v1/agent/sessions'+q);
  window.sessCache=d.sessions||[];
  subagentCount=d.subagent_count||0;
  drawSessions();
 }catch(e){}
 finally{refreshSessionsInflight=false}
}
function toggleSubagents(){
 // 全部展开 / 全部折叠（子代理会话归集在父会话下）
 const parents=new Set();
 window.sessCache.forEach(s=>{if(String(s.parent_id||''))parents.add(s.parent_id)});
 const anyCollapsed=[...parents].some(p=>!expandedSess.has(p));
 if(anyCollapsed)parents.forEach(p=>expandedSess.add(p));
 else parents.clear && parents.forEach(p=>expandedSess.delete(p));
 try{localStorage.setItem('gantry-expanded-sess',JSON.stringify([...expandedSess]))}catch(e){}
 sessSig='';drawSessions();
 toast(tr(anyCollapsed?'已展开 {n} 个子代理会话':'已折叠 {n} 个子代理会话',
            {n: parents.size}),'ok');
}
/* 列表签名：内容没变就不重建 DOM，避免每秒滚动回顶/点击错位 */
/* 会话行「⋯」菜单：设置标签 / 复制 ID / 删除会话。
   浮层挂 body 并 fixed 定位，避免被侧栏滚动容器裁掉。 */
let sessMenuEl=null;
/* 勾选导出改为设置页弹窗内做（spickMask），侧栏不再常驻勾选框 */
function closeSessMenu(){if(sessMenuEl){sessMenuEl.remove();sessMenuEl=null}}
function openSessMenu(btn,sid){
 closeSessMenu();
 const el=document.createElement('div');
 el.className='codemenu sessmenu';
 el.innerHTML=
  `<button type="button" class="cmitem" data-menu="tag">${esc(tr('设置标签'))}</button>`+
  `<button type="button" class="cmitem" data-menu="copy">${esc(tr('复制会话 ID'))}</button>`+
  `<button type="button" class="cmitem" data-menu="export">${esc(tr('导出会话'))}</button>`+
  `<button type="button" class="cmitem danger" data-menu="del">${esc(tr('删除会话'))}</button>`;
 document.body.appendChild(el);
 const r=btn.getBoundingClientRect();
 // AUD-P4：菜单默认开在按钮下方；靠近视口底部（列表最下方的会话）时翻到
 // 按钮上方，否则被窗口底边裁掉无法点到最后几项。
 let top=Math.round(r.bottom+4);
 if(top+el.offsetHeight>window.innerHeight-4){top=Math.max(8,Math.round(r.top-el.offsetHeight-4))}
 el.style.top=top+'px';
 el.style.left=Math.round(Math.max(8,Math.min(r.right-el.offsetWidth,window.innerWidth-el.offsetWidth-8)))+'px';
 el.querySelector('[data-menu="tag"]').onclick=()=>{closeSessMenu();editTagById(sid)};
 el.querySelector('[data-menu="copy"]').onclick=()=>{closeSessMenu();
  try{navigator.clipboard.writeText(sid);toast(tr('已复制会话 ID')+' '+sid,'ok')}catch(e){toast(sid)}};
 el.querySelector('[data-menu="export"]').onclick=()=>{closeSessMenu();exportSessionById(sid)};
 el.querySelector('[data-menu="del"]').onclick=()=>{closeSessMenu();window.removeSession(false,sid)};
 sessMenuEl=el;
}
document.addEventListener('click',e=>{
 if(sessMenuEl&&!e.target.closest('.sessmenu')&&!e.target.closest('.sess-more'))closeSessMenu()});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeSessMenu()});
/* 工作区短名：只显示末两段（全路径放 title），避免会话列表被长路径撑开 */
function drawSessions(){
 let q=(document.getElementById('sessFilter').value||'').toLowerCase();
 let box=document.getElementById('sessions');
 let all=window.sessCache.filter(s=>!q||(s.title||'').toLowerCase().includes(q)||(s.id||'').toLowerCase().includes(q)||(s.state||'').includes(q)||(s.tag||'').toLowerCase().includes(q)||(s.workspace||'').toLowerCase().includes(q));
 // 父子归集：子代理会话挂在父会话下（可展开），不再单占一行
 const byId=new Map(all.map(s=>[s.id,s]));
 const kidsOf=new Map();
 all.forEach(s=>{const p=String(s.parent_id||'');if(p&&byId.has(p)){if(!kidsOf.has(p))kidsOf.set(p,[]);kidsOf.get(p).push(s)}});
 const isKid=s=>{const p=String(s.parent_id||'');return !!p&&byId.has(p)};
 let list=all.filter(s=>!isKid(s));
 let sig=q+'|'+all.map(s=>s.id+s.state+s.total_steps+s.estimated_tokens+(s.subagent_steps||0)+(s.subagent_tokens||0)+(s.subagent_count||0)+(s.subagent_running||0)+(s.conversation_index??'')+(s.title||'')+(s.attachment_count??'')+(s.tag||'')+(s.workspace||'')+(s.parent_id||'')+(s.cleanup_suggested??'')+((s.conversation_urls||[]).length)+((s.current_url)||'')+(s.updated||0)).join('|')+'|'+window.current+'|'+[...expandedSess].join(',');
 if(sig===sessSig)return;
 sessSig=sig;
 box.innerHTML='';
 if(!list.length){box.innerHTML='<div class="sess-empty">'+esc(tr('没有匹配的 Session'))+'</div>';return}
 // 分组：有 tag（teams-bot=Teams / DSH 工具调用=DSH）按 tag 成组，
 // 其余仍按工作区分组；组头可直接批量删除整组。
 const tagOf=(s)=>{
  const t=String(s.tag||'').trim();
  if(t)return t;
  const id=String(s.id||'');
  return id.startsWith('teams-')?'Teams':(id.startsWith('toolcall-')?'DSH':'');
 };
 const groups=new Map();
 list.forEach(s=>{
  const t=tagOf(s);
  const key=t?'tag:'+t:'ws:'+(s.workspace||'').trim();
  if(!groups.has(key))groups.set(key,[]);
  groups.get(key).push(s)});
 const _maxUpd=(arr)=>Math.max(...arr.map(x=>Number(x.updated)||0));
 // 标签组（Teams/DSH）排在最上面，工作区组随后；组内按最近活跃
 const keys=[...groups.keys()].sort((a,b)=>{
  const at=a.startsWith('tag:')?0:1, bt=b.startsWith('tag:')?0:1;
  return at-bt || (_maxUpd(groups.get(b))-_maxUpd(groups.get(a)))});
 const wsName=(p)=>{const parts=String(p||'').replace(/\\/g,'/').split('/').filter(Boolean);
   return parts.length?parts[parts.length-1]:tr('默认工作区')};
 const renderCard=(s,opts)=>{
  opts=opts||{};
  // 子代理不再列进侧栏（用户要求），因此父行也不该再有 ▸ 折叠图标。
  const kids=[];
  let e=document.createElement('div');
  e.className='sess'+(s.id===window.current?' sel':'')+(opts.child?' child':'');
  e.setAttribute('role','button');e.setAttribute('aria-pressed',s.id===window.current?'true':'false');
  // 会话标识必须显示：标题可能相似（甚至被 Copilot 改名），删错代价很大
  const _kind=String(s.id||'').startsWith('cron-')?'⏱':String(s.id||'').startsWith('research-')?'🔎':String(s.id||'').startsWith('sub-')?'子':'';
  // 步数/Token 与详情页 KPI 必须同一口径：主会话 + 子代理（含孙）。
  // 以前这里只显示 s.total_steps / s.estimated_tokens，父任务跑了子代理后
  // 侧栏与页面会差出几百步/几千 token（用户实测"列表跟会话页数据不相符"）。
  const _subSteps=Number(s.subagent_steps||0), _subTk=Number(s.subagent_tokens||0),
        _subN=Number(s.subagent_count||0);
  const _ownSteps=Number(s.total_steps??0), _ownTk=Number(s.estimated_tokens??0);
  const _sumTip=_subN?tr('本会话 {a} + 子代理 {b}（{n} 个）',{a:_ownSteps.toLocaleString(),
    b:_subSteps.toLocaleString(),n:_subN}):'';
  const _num=(v,extra)=>extra>0
    ?`<span class="ssub" title="${esc(_sumTip)}">${esc(v)}</span>`
    :esc(v);
  // AUD-P4：任务完成后清理 Copilot 会话（保留本地记录）——有远端 URL 映射
  // 或后端给了 cleanup_suggested 标记时，行内放一个 🧹 按钮。
  const _cleanable=(Array.isArray(s.conversation_urls)&&s.conversation_urls.length>0)||Number(s.cleanup_suggested||0)>0;
  e.innerHTML=`<span class="sstat ${stateClassOf(s)}" title="${esc(stateLabel(s))}" aria-label="${esc(stateLabel(s))}">${stateIcon(s.state)}</span><div class="smeta"><b>${_kind?`<span class="skind">${_kind}</span>`:''}${esc(s.title)}</b><small><code class="sid" title="点击复制会话 ID">${esc(s.id)}</code><br>${tr('步骤')} ${_num(_ownSteps+_subSteps,_subSteps)} · ${tr('会话')} ${esc((s.conversation_index??0)+1)} · Token ${_num(_ownTk+_subTk,_subTk)}</small></div>${(s.attachment_count||0)>0?`<span class="attchip" title="含 ${s.attachment_count} 条带图片附件的消息">📎${s.attachment_count}</span>`:''}${_cleanable?`<button type="button" class="sess-clean" title="${esc(tr('清理 Copilot 会话（保留本地记录）'))}" aria-label="${esc(tr('清理 Copilot 会话（保留本地记录）'))}">🧹</button>`:''}<button type="button" class="sess-more" title="${esc(tr('更多操作'))}" aria-haspopup="menu">⋯</button>`;
  // 子代理不再列进侧栏 → 前导位（▸ 的那个位置）也就没用了：
  // 空着那 12px + gap 会把整行往右推，看起来跟工作区分组头"跳格"（用户实测）。
  let chip=e.querySelector('.sid');
  if(chip)chip.onclick=ev=>{ev.stopPropagation();try{navigator.clipboard.writeText(s.id);chip.textContent=tr('已复制 ✓');setTimeout(()=>chip.textContent=s.id,900)}catch(_){}};
  // 行内「⋯」菜单：必须 stopPropagation，否则先触发整行 onclick=load(s.id)
  const more=e.querySelector('.sess-more');
  if(more)more.onclick=ev=>{ev.stopPropagation();openSessMenu(more,s.id)};
  // AUD-P4：🧹 清理 Copilot 会话（先总结、再远端删除，本地记录保留）
  const clean=e.querySelector('.sess-clean');
  if(clean)clean.onclick=async ev=>{
   ev.stopPropagation();
   const ok=await window.dlgConfirm(
    tr('清理该会话在 Copilot 里的对话？本地记录与步骤都会保留；删除前会先让 Copilot 写一份任务总结。'),
    tr('清理 Copilot 会话'),false);
   if(!ok)return;
   clean.disabled=true;clean.textContent='…';
   try{
    const r=await api(`/v1/agent/sessions/${encodeURIComponent(s.id)}/copilot-cleanup`,
      {method:'POST',headers:{'Content-Type':'application/json'},
       body:JSON.stringify({summarize:true})});
    if(r.ok){toast(tr('已清理 Copilot 会话：{n} 个（本地记录已保留）',{n:r.deleted}),'ok')}
    else{toast(tr('清理失败: ')+((r.error||r.detail)||''),'err')}
    await refreshSessions();
   }catch(err){clean.disabled=false;clean.textContent='🧹';
    toast(tr('清理失败: ')+((err&&(err.detail||err.message))||String(err)),'err')}
   finally{
    // HTTP 200 但 ok:false（总结失败/部分删除失败）时上面不会走 catch：
    // 必须无条件恢复按钮，否则 🧹 一直停在 … 无法重试（审核 2026-11-26）
    clean.disabled=false;clean.textContent='🧹';
   }
  e.onclick=()=>load(s.id);
  // 键盘可达：会话切换是主入口，纯 div+onclick 对键盘用户等于不可用
  e.tabIndex=0;
  e.onkeydown=ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();load(s.id)}};
  box.appendChild(e)};
 keys.forEach(k=>{
  const isTag=k.startsWith('tag:');
  const label=isTag?k.slice(4):wsName(k.slice(3));
  const h=document.createElement('div');h.className='taghead wshead';
  h.title=isTag?label:k.slice(3);
  const caret=document.createElement('span');caret.className='wscaret';
  caret.textContent=wsIsCollapsed(k)?'▸':'▾';
  h.appendChild(caret);
  if(isTag){
   const tagico=document.createElement('span');tagico.className='gtagico';tagico.textContent='🏷';
   h.appendChild(tagico);
  }else{
   const ico=document.createElementNS('http://www.w3.org/2000/svg','svg');
   ico.setAttribute('viewBox','0 0 24 24');ico.setAttribute('width','13');ico.setAttribute('height','13');
   ico.setAttribute('fill','none');ico.setAttribute('stroke','currentColor');
   ico.setAttribute('stroke-width','1.8');ico.setAttribute('stroke-linecap','round');
   ico.setAttribute('stroke-linejoin','round');ico.setAttribute('aria-hidden','true');
   ico.innerHTML='<path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l1.6 2h9.4A1.5 1.5 0 0 1 21 9.5v8A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5z"/>';
   h.appendChild(ico);
  }
  const nm=document.createElement('span');nm.className='wsname';nm.textContent=label;
  nm.setAttribute('data-i18n-skip','1');   // 标签/文件夹名是数据
  const cnt=document.createElement('span');cnt.className='wscount';cnt.textContent=String(groups.get(k).length);
  h.appendChild(nm);h.appendChild(cnt);
  if(isTag){
   const del=document.createElement('button');
   del.type='button';del.className='wsdel';del.title=tr('删除本组会话');
   del.setAttribute('aria-label',tr('删除本组会话'));del.textContent='🗑';
   del.onclick=(ev)=>{ev.stopPropagation();removeGroupSessions(label,groups.get(k))};
   h.appendChild(del);
  }else{
   const add=document.createElement('button');
   add.type='button';add.className='wsadd';add.title=tr('在此工作区新建会话');
   add.setAttribute('data-i18n-title','在此工作区新建会话');
   add.textContent='＋';
   add.onclick=(ev)=>{ev.stopPropagation();newSessionInWorkspace(k.slice(3))};
   h.appendChild(add);
  }
  h.classList.toggle('collapsed', wsIsCollapsed(k));
  h.onclick=()=>wsToggleCollapse(k);
  box.appendChild(h);
  if(!wsIsCollapsed(k)) groups.get(k).forEach((s)=>{
   renderCard(s);
  })});
 try{translateAttrs(box)}catch(e){}}

/* 批量删除核心（不含确认/过滤，调用方负责）：先快速删本地，再按标记尽力
   清理远端 Copilot 对话。设置页「删除所选」与侧栏标签组批量删除共用。 */
async function deleteSessionsBulk(sids){
 let ok=0,fail=0;
 for(const sid of sids){
  try{
   await api('/v1/agent/sessions/'+encodeURIComponent(sid)+'?force_local=true',{method:'DELETE'});
   ok++;
   if(sid===window.current){
    stopEvents();window.current=null;
    document.getElementById('sessionView').style.display='none';
    document.getElementById('emptyState').style.display='flex';}
  }catch(e){fail++}
 }
 // 远端按 [GANTRY-AGENT][sid] 标记清理；一次最多 50 个 sid
 for(let i=0;i<sids.length;i+=50){
  try{
   await api('/v1/agent/copilot/cleanup-marked',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({sids:sids.slice(i,i+50),delete_matching:true})});
  }catch(e){}
 }
 await refreshSessions();
 return {ok,fail};
}

/* 批量删除一个标签组：先快速删本地，再按标记尽力清理远端 Copilot 对话。 */
async function removeGroupSessions(label, arr){
 const all=(arr||[]).filter(Boolean);
 const live=new Set(['running','waiting','queued']);
 const sids=all.filter(s=>!live.has(String(s.state||''))).map(s=>s.id).filter(Boolean);
 const skipped=all.length-sids.length;
 if(!sids.length){
  if(skipped)toast(tr('该组会话都在运行中，先停止再删除'),'err');
  return}
 if(!await dlgConfirm(tr('删除「{label}」组的 {n} 个会话？\n\n会先删本地记录，再尽力清理对应的 Copilot 网页对话。此操作不可撤销。',
                        {label:label,n:sids.length}),tr('批量删除'),true))return;
 const r=await deleteSessionsBulk(sids);
 toast(tr('批量删除完成：成功 {ok}，失败 {fail}',{ok:r.ok,fail:r.fail}),r.fail?'err':'ok');
 if(skipped)toast(tr('已跳过运行中的 {n} 个会话',{n:skipped}),'err');
}
/* 会话标签：纯本地组织手段（不参与 Agent 逻辑），列表按标签分组 */
async function editTag(btn){return editTagById(btn&&btn.dataset?btn.dataset.arg:'')}
async function editTagById(sid){
 if(!sid)return;
 const cur=((window.sessCache||[]).find(s=>s.id===sid)||{}).tag||'';
 const tag=await dlgPrompt(tr('设置会话标签（列表按标签分组；留空取消标签）'),cur,tr('保存'),tr('会话标签'));
 if(tag===null)return;
 try{
  await api(`/v1/agent/sessions/${encodeURIComponent(sid)}/tag`,
   {method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({tag:String(tag).trim()})});
  toast(tag.trim()?(tr('标签已设为')+'「'+tag.trim()+'」'):tr('已取消标签'),'ok');
  await refreshSessions();
 }catch(e){toast(tr('设置标签失败: ')+(e.text||e),'err')}}

/* ── 时间线：增量渲染，不重建已有 DOM，展开状态不丢失 ── */
/* 工具结果的语言猜测：优先按参数里的文件扩展名，其次按工具名 */

/* ── 会话导入导出 ──
   导出 = GET /v1/agent/sessions/export?sids=（留空=全部）；导入 = POST /import。
   导入一律生成 imp-* 新 sid，不撞本机；运行中/排队态落为 ready。 */
function _downloadJson(payload, filename){
 const blob=new Blob([JSON.stringify(payload,null,2)],{type:'application/json'});
 const a=document.createElement('a');
 a.href=URL.createObjectURL(blob);
 a.download=filename;
 document.body.appendChild(a); a.click(); a.remove();
 setTimeout(()=>URL.revokeObjectURL(a.href), 5000);
}
async function exportSessionById(sid){
 try{
  const d=await api('/v1/agent/sessions/export?sids='+encodeURIComponent(sid));
  _downloadJson(d, 'gantry-session-'+sid+'.json');
  toast(tr('已导出会话')+' '+sid,'ok');
 }catch(e){toast(tr('导出失败: ')+(e.text||e),'err')}
}
async function exportSessions(sids){
 sids=(sids||[]).filter(Boolean);
 if(!sids.length){toast(tr('未选择要导出的会话'),'err');return}
 try{
  const d=await api('/v1/agent/sessions/export?sids='+encodeURIComponent(sids.join(',')));
  _downloadJson(d, 'gantry-sessions-'+(d&&d.exported_at||'').slice(0,10)+'.json');
  toast(tr('已导出 {n} 个会话',{n:(d&&d.count)||sids.length}),'ok');
 }catch(e){toast(tr('导出失败: ')+(e.text||e),'err')}
}
async function importSessions(){
 const input=document.createElement('input');
 input.type='file'; input.accept='.json,application/json';
 input.onchange=async()=>{
  const file=input.files&&input.files[0];
  if(!file)return;
  let data;
  try{data=JSON.parse(await file.text())}
  catch(e){toast(tr('导入失败: JSON 解析错误'),'err');return}
  try{
   const r=await api('/v1/agent/sessions/import',
    {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
   toast(tr('导入完成：成功 {ok}、跳过 {skip}',{ok:r.added,skip:r.skipped}),
    (r.skipped&&!r.added)?'err':'ok');
   if(r.errors&&r.errors.length)toast(tr('跳过项：')+r.errors.slice(0,3).join('；'),'err');
   await refreshSessions();
  }catch(e){toast(tr('导入失败: ')+(e.text||e.detail||e),'err')}
 };
 input.click();
}

export { toggleSessExpand, refreshSessions, toggleSubagents, drawSessions, editTag,
         exportSessionById, exportSessions, importSessions, deleteSessionsBulk, copilotScan };

/* ── AUD-P4：盘点并批量删除所有 Gantry 标记的 Copilot 会话 ─────────────
   盘点只读；删除以本地记录的 URL 为索引逐条复核（Copilot 会自动改写标题，
   侧栏标题里的标记通常已不可见，本地 URL 才是可靠索引）。 */
async function copilotScan(){
 let local, sidebarN, localUrlN;
 try{
  const r=await api('/v1/agent/copilot/cleanup-marked',
   {method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({scan_only:true})});
  local=r.local||[];sidebarN=r.count||0;localUrlN=r.local_urls||0;
 }catch(e){toast(tr('盘点失败: ')+((e&&(e.detail||e.message))||String(e)),'err');return}
 if(!localUrlN&&!sidebarN){toast(tr('没有发现 Gantry 标记的 Copilot 会话'),'ok');return}
 const names=local.slice(0,8).map(x=>`· ${x.title||x.sid}（${x.count}）`).join('\n');
 const more=local.length>8?('\n…'):'';
 const ok=await window.dlgConfirm(
  tr('本地记录索引到 {u} 个 Copilot 会话（{s} 个会话；侧栏标题带标记的另有 {r} 个）：\n{l}{m}\n\n统一删除？本地记录不受影响；逐条复核约需数秒到数十秒。',
     {u:localUrlN,s:local.length,r:sidebarN,l:names,m:more}),
  tr('批量删除 Copilot 会话'),true);
 if(!ok)return;
 toast(tr('开始删除 {n} 个会话，请稍候…',{n:localUrlN}),'ok',30000);
 try{
  const r=await api('/v1/agent/copilot/cleanup-marked',
   {method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({all:true})});
  toast(tr('删除完成：成功 {a}，失败 {b}（{s} 秒）',{a:r.deleted,b:r.failed,s:r.total_seconds}),
        r.failed?'err':'ok');
  await refreshSessions();
 }catch(e){toast(tr('删除失败: ')+((e&&(e.detail||e.message))||String(e)),'err')}
}
