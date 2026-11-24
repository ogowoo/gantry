// panels.js — 
// 从原 webui/app.js 拆出的 ES 模块（该单体已退役，见 docs/ESM-MIGRATION.md）。
function _panelUrl(p){ return '/v1/plugins/'+encodeURIComponent(p.plugin)+'/api/'+String(p.path||'').replace(/^\/+/, ''); }
function _panelData(p){
 return api(_panelUrl(p), {method:p.method||'GET'});
}
function _renderKv(obj){
 if(!obj||typeof obj!=='object'||Array.isArray(obj))return _renderJson(obj);
 const rows=Object.entries(obj).map(([k,v])=>{
  const val=(v&&typeof v==='object')?JSON.stringify(v):String(v===null||v===undefined?'':v);
  return '<tr><th>'+esc(k)+'</th><td>'+esc(val)+'</td></tr>';});
 return '<table class="ptable">'+rows.join('')+'</table>';
}
function _renderList(arr){
 if(!Array.isArray(arr))return _renderJson(arr);
 if(!arr.length)return '<div class="meta">'+esc(tr('（空）'))+'</div>';
 return '<ul class="plist">'+arr.map(x=>'<li>'+esc(typeof x==='object'?JSON.stringify(x):String(x))+'</li>').join('')+'</ul>';
}
function _renderTable(arr){
 if(!Array.isArray(arr))return _renderJson(arr);
 if(!arr.length)return '<div class="meta">'+esc(tr('（空）'))+'</div>';
 const cols=[...new Set(arr.flatMap(x=>Object.keys(x||{})))];
 const head='<tr>'+cols.map(c=>'<th>'+esc(c)+'</th>').join('')+'</tr>';
 const body=arr.map(x=>'<tr>'+cols.map(c=>{
  const v=x&&x[c]; const s=(v&&typeof v==='object')?JSON.stringify(v):(v===null||v===undefined?'':String(v));
  return '<td>'+esc(s)+'</td>';}).join('')+'</tr>').join('');
 return '<table class="ptable">'+head+body+'</table>';
}
function _renderJson(v){
 try{ return '<pre class="pjson">'+esc(JSON.stringify(v,null,2))+'</pre>' }
 catch(e){ return '<pre class="pjson">'+esc(String(v))+'</pre>' }
}
function _adminFieldHtml(f, values){
 const key=esc(f.key), val=values&&values[f.key]!==undefined?values[f.key]:(f.default!==undefined?f.default:'');
 if(f.type==='checkbox'){
  return `<label class="setrow"><input type="checkbox" data-admin-key="${key}" ${val?'checked':''}><span data-i18n-skip="1">${esc(f.label)}</span></label>`;
 }
 if(f.type==='select'){
  const opts=(f.options||[]).map(o=>`<option value="${esc(o.value)}" ${String(o.value)===String(val)?'selected':''}>${esc(o.label)}</option>`).join('');
  return `<label class="setrow"><span data-i18n-skip="1">${esc(f.label)}</span><select class="kbinput" data-admin-key="${key}">${opts}</select></label>`;
 }
 return `<label class="setrow"><span data-i18n-skip="1">${esc(f.label)}</span><input class="kbinput" type="${esc(f.type||'text')}" data-admin-key="${key}" value="${esc(val===undefined||val===null?'':val)}" placeholder="${esc(f.placeholder||'')}"></label>`;
}
function _renderAdmin(p,data){
 const body=document.getElementById('panelBody');
 const values=(data&&data.values)||{};
 const status=(data&&data.status)||data||{};
 let html='';
 if(p.hint)html+='<div class="meta">'+esc(tr(p.hint))+'</div>';
 html+='<div class="adminstatus">'+_renderKv(status)+'</div>';
 if((p.fields||[]).length){
  html+='<div class="adminform">'+(p.fields||[]).map(f=>_adminFieldHtml(f,values)).join('')+'</div>';
 }
 html+='<div class="setbtns">';
 (p.actions||[]).forEach(a=>{html+=`<button type="button" class="btn ${a.danger?'danger':'ghost'}" data-admin-action="${esc(a.id)}" data-i18n-skip="1">${esc(a.label)}</button>`});
 html+='</div>';
 if(p.search){
  html+=`<div class="setrow"><span data-i18n-skip="1">${esc(p.search.label)}</span><input class="kbinput" id="adminSearchInput" placeholder="${esc(p.search.placeholder||'')}"><button type="button" class="btn ghost" id="adminSearchBtn">${esc(tr('搜索'))}</button></div>`;
 }
 html+='<div id="adminResult" class="adminresult"></div>';
 body.innerHTML=html;
 const vals=()=>{
  const out={};
  body.querySelectorAll('[data-admin-key]').forEach(el=>{
   const k=el.dataset.adminKey;
   out[k]=el.type==='checkbox'?!!el.checked:el.value;
  });
  return out;
 };
 const run=async(a)=>{
  if(a.confirm&&!await window.dlgConfirm(tr(a.confirm),tr(a.label),!!a.danger))return;
  const url=_panelUrl({plugin:p.plugin,path:a.path});
  const opt={method:a.method||'POST'};
  if(opt.method==='POST'){
   opt.headers={'Content-Type':'application/json'};
   opt.body=JSON.stringify(a.send_values===false?{}:{values:vals()});
  }
  try{
   const d=await api(url,opt);
   toast(tr('完成')+': '+a.label,'ok');
   await loadPanel(p);
   const r=document.getElementById('adminResult');
   if(r)r.innerHTML=_renderJson(d);
  }catch(e){toast(tr('操作失败: ')+((e&&(e.text||e.detail))||e),'err')}
 };
 body.querySelectorAll('[data-admin-action]').forEach(btn=>{
  const a=(p.actions||[]).find(x=>x.id===btn.dataset.adminAction);
  if(a)btn.onclick=()=>run(a);
 });
 const sb=document.getElementById('adminSearchBtn');
 if(sb&&p.search){
  sb.onclick=async()=>{
   const q=(document.getElementById('adminSearchInput')||{}).value||'';
   try{
    const d=await api(_panelUrl({plugin:p.plugin,path:p.search.path})+(p.search.path.includes('?')?'&':'?')+'q='+encodeURIComponent(q));
    const r=document.getElementById('adminResult');if(r)r.innerHTML=_renderJson(d);
   }catch(e){toast(tr('搜索失败: ')+((e&&(e.text||e.detail))||e),'err')}
  };
 }
}
function _renderPanel(p,data){
 const body=document.getElementById('panelBody');
 if(!body)return;
 const box=(data&&typeof data==='object'&&!Array.isArray(data)&&Array.isArray(data[p.id])
            ? data[p.id] : (data&&data.items!==undefined?data.items:data));
 let html='';
 const v=p.view||'kv';
 if(v==='admin'){_renderAdmin(p,data);return}
 if(v==='table')html=_renderTable(Array.isArray(box)?box:_firstArray(data));
 else if(v==='list')html=_renderList(Array.isArray(box)?box:_firstArray(data));
 else if(v==='json')html=_renderJson(data);
 else if(v==='markdown')html='<div class="md-body">'+mdToHtml(String((data&&(data.text||data.markdown))||''))+'</div>';
 else html=_renderKv(box);
 body.innerHTML=html;
 if(p.hint)body.insertAdjacentHTML('afterbegin','<div class="meta">'+esc(tr(p.hint))+'</div>');
}
function _firstArray(data){
 if(Array.isArray(data))return data;
 if(data&&typeof data==='object'){
  for(const k of Object.keys(data)) if(Array.isArray(data[k])) return data[k];
 }
 return [];
}
async function openPanels(){
 openMask('panelMask');
 await refreshPanels(true);
}
async function refreshPanels(silent){
 const list=document.getElementById('panelList'), body=document.getElementById('panelBody');
 if(!list||!body)return;
 let panels=[];
 try{ const d=await api('/v1/plugins/panels'); panels=(d&&d.panels)||[] }
 catch(e){ list.innerHTML='<div class="meta">'+esc(tr('读取失败: ')+(e.text||e))+'</div>'; return }
 panelCache=panels;
 if(!panels.length){ list.innerHTML='<div class="meta">'+esc(tr('（没有面板）'))+'</div>'; body.innerHTML=''; return }
 list.innerHTML='';
 panels.forEach((p,i)=>{
  const row=document.createElement('div');
  row.className='panelrow'+(i===0?' on':'');
  row.setAttribute('data-i18n-skip','1');     // 面板标题来自插件（数据）
  row.textContent=tr(p.title||p.id);
  row.title=p.plugin+' · '+p.view;
  row.onclick=()=>{ [...list.children].forEach(c=>c.classList.remove('on')); row.classList.add('on'); loadPanel(p) };
  list.appendChild(row);
 });
 await loadPanel(panels[0]);
}
async function _injectTeamsBotModelControl(body, data, panel){
 const wrap=document.createElement('div');wrap.style.cssText='margin-top:10px;padding-top:10px;border-top:1px solid var(--line)';
 // ── 开关区 ──
 const swWrap=document.createElement('div');swWrap.style.cssText='display:flex;gap:14px;flex-wrap:wrap;margin-bottom:8px';
 const SWITCHES=[
  ['enabled','启用轮询'],['dry_run','试运行（不发真消息）'],
  ['send_enabled','允许发送'],['only_direct','仅 1:1 私聊'],
 ];
 async function postSwitch(patch){
  await api('/v1/plugins/teams-bot/api/settings',{method:'POST',
   headers:{'Content-Type':'application/json'},body:JSON.stringify(patch)});
 }
 function buildSwitches(st){
  swWrap.innerHTML='';
  SWITCHES.forEach(([key,label])=>{
   const lb=document.createElement('label');
   lb.style.cssText='display:inline-flex;align-items:center;gap:6px;font-size:12.5px;color:var(--text);cursor:pointer';
   const cb=document.createElement('input');cb.type='checkbox';cb.checked=!!(st&&st[key]);
   cb.onchange=async()=>{ cb.disabled=true; try{
     await postSwitch({[key]:cb.checked});
     toast(tr('设置已保存（{n} 项）').replace('{n}','1'),'ok');
     await loadPanel(panel);
    }catch(e){ toast(tr('保存失败: ')+(e.text||e),'err'); cb.checked=!cb.checked; cb.disabled=false; } };
   const sp=document.createElement('span');sp.textContent=tr(label);
   lb.appendChild(cb);lb.appendChild(sp);swWrap.appendChild(lb);
  });
 }
 // ── 模型选择 ──
 const row=document.createElement('div');row.style.cssText='display:flex;gap:6px;align-items:center';
 const lbl=document.createElement('span');lbl.className='hint';lbl.textContent=tr('模型')+':';row.appendChild(lbl);
 const sel=document.createElement('select');sel.className='kbinput';sel.style.flex='1';sel.id='teamsBotPanelModel';
 const save=document.createElement('button');save.type='button';save.className='btn';save.textContent=tr('保存');
 row.appendChild(sel);row.appendChild(save);wrap.appendChild(swWrap);wrap.appendChild(row);
 // ── 提示词编辑区 ──
 const taWrap=document.createElement('div');taWrap.style.cssText='margin-top:10px;display:flex;flex-direction:column;gap:6px';
 const TA_FIELDS=[
  ['prompt_with_kb','有资料时的提示词'],['prompt_no_kb','无资料时的提示词'],
  ['unknown_text','答不上时的兜底回复'],
 ];
 TA_FIELDS.forEach(([key,label])=>{
  const lb=document.createElement('label');lb.className='hint';lb.style.cssText='font-size:11px';
  lb.textContent=tr(label);
  const ta=document.createElement('textarea');
  ta.className='kbinput';ta.rows=3;ta.dataset.cfgKey=key;
  ta.style.cssText='width:100%;font-family:var(--mono);font-size:11.5px;padding:6px 8px;resize:vertical';
  taWrap.appendChild(lb);taWrap.appendChild(ta);
 });
 const taSave=document.createElement('button');taSave.type='button';taSave.className='btn ghost';taSave.textContent=tr('保存提示词');
 taSave.style.cssText='align-self:flex-end';
 taSave.onclick=async()=>{ taSave.disabled=true; try{
   const patch={};
   taWrap.querySelectorAll('textarea[data-cfg-key]').forEach(ta=>{
    const v=ta.value.trim();
    if(v) patch[ta.dataset.cfgKey]=v;
   });
   if(Object.keys(patch).length){
    await postSwitch(patch);
    toast(tr('设置已保存（{n} 项）').replace('{n}',String(Object.keys(patch).length)),'ok');
    await loadPanel(panel);
   }else{ toast(tr('没有改动'),'ok'); }
  }catch(e){ toast(tr('保存失败: ')+(e.text||e),'err'); } finally{ taSave.disabled=false; } };
 taWrap.appendChild(taSave);
 wrap.appendChild(taWrap);
 body.appendChild(wrap);
 try{
  const [models,status]=await Promise.all([api('/v1/models'),api('/v1/plugins/teams-bot/api/status')]);
  buildSwitches(status||{});
  TA_FIELDS.forEach(([key])=>{
   const ta=taWrap.querySelector('textarea[data-cfg-key="'+key+'"]');
   if(ta&&status&&status[key])ta.value=status[key];
  });
  const current=String((status&&status.model)||(data&&data.model)||'');
  const ids=(models&&models.data||[]).map(m=>m.id).filter(Boolean);
  if(!ids.length){sel.innerHTML='<option value="">-</option>';return;}
  sel.innerHTML=ids.map(id=>'<option value="'+esc(id)+'"'+(id===current?' selected':'')+'>'+esc(id)+'</option>').join('');
  save.onclick=async()=>{ save.disabled=true; try{
    await api('/v1/plugins/teams-bot/api/settings',{method:'POST',body:JSON.stringify({model:sel.value})});
    toast(tr('设置已保存（{n} 项）').replace('{n}','1'),'ok'); await loadPanel(panel); }catch(e){ toast(tr('保存失败: ')+(e.text||e),'err'); save.disabled=false; } };
 }catch(e){ sel.innerHTML='<option value="">'+esc(tr('读取失败: ')+(e.text||e))+'</option>'; }
}
async function _injectScheduleManager(body, data, panel){
 /* 定时任务面板：在只读表格下补管理区——新建/编辑表单 + 每行 运行/启停/编辑/删除。
 数据与操作全走插件自己的 api/add|update|remove|run，与 schedule_add 等工具等价。 */
 const wrap=document.createElement('div');
 // 上方只读表格与下面管理区信息重复（且列宽难看），管理区接管展示
 const oldTable=body.querySelector('table.ptable');
 if(oldTable) oldTable.remove();
 wrap.style.cssText='margin-top:12px;padding-top:10px;border-top:1px solid var(--line);display:flex;flex-direction:column;gap:8px';
 const list=(data&&data.schedules)||[];
 // ── 表单（新建 & 编辑共用） ──
 const form=document.createElement('div');
 form.style.cssText='display:flex;flex-direction:column;gap:6px;padding:8px;border:1px solid var(--line);border-radius:6px';
 form.innerHTML=
  '<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">'+
   '<input class="kbinput" id="schName" placeholder="'+esc(tr('任务名（必填）'))+'" style="flex:1;min-width:120px">'+
   '<select class="kbinput" id="schType">'+
    '<option value="every">'+esc(tr('按间隔'))+'</option>'+
    '<option value="daily">'+esc(tr('每天'))+'</option>'+
    '<option value="once">'+esc(tr('一次性'))+'</option>'+
   '</select>'+
   '<input class="kbinput" id="schWhen" placeholder="'+esc(tr('30m / 09:00 / 2026-01-01T09:00:00'))+'" style="width:190px">'+
   '<label style="display:inline-flex;align-items:center;gap:4px;font-size:12px"><input type="checkbox" id="schEnabled" checked>'+esc(tr('启用'))+'</label>'+
  '</div>'+
  '<textarea class="kbinput" id="schTask" rows="3" placeholder="'+esc(tr('到点执行的完整任务描述（必填）'))+'" style="width:100%;resize:vertical"></textarea>'+
  '<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">'+
   '<input class="kbinput" id="schModel" list="schModels" placeholder="'+esc(tr('模型（留空跟随配置）'))+'" style="flex:1;min-width:140px">'+
   '<datalist id="schModels"></datalist>'+
   '<input class="kbinput" id="schWs" placeholder="'+esc(tr('工作区（可选）'))+'" style="flex:1;min-width:140px">'+
  '</div>'+
  '<div style="display:flex;gap:6px;justify-content:flex-end">'+
   '<button type="button" class="btn ghost" id="schCancel" style="display:none">'+esc(tr('取消编辑'))+'</button>'+
   '<button type="button" class="btn" id="schSave">'+esc(tr('新建任务'))+'</button>'+
  '</div>';
 wrap.appendChild(form);
 // ── 导入导出 ──
 const ioRow=document.createElement('div');
 ioRow.style.cssText='display:flex;gap:6px;flex-wrap:wrap;align-items:center';
 const ioBtn=(label,cls)=>{
  const b=document.createElement('button'); b.type='button'; b.className='btn '+cls;
  b.textContent=tr(label); ioRow.appendChild(b); return b;
 };
 const ioResult=document.createElement('span');
 ioResult.className='meta';
 ioRow.appendChild(ioResult);
 // 导出：api/export → 存成 JSON 文件下载
 ioBtn('导出','ghost').onclick=async(e)=>{
  e.target.disabled=true;
  try{
   const d=await api(_panelUrl({plugin:panel.plugin, path:'api/export'}));
   const blob=new Blob([JSON.stringify(d,null,2)],{type:'application/json'});
   const a=document.createElement('a');
   a.href=URL.createObjectURL(blob);
   a.download='gantry-schedules-'+(d&&d.exported_at||'').slice(0,10)+'.json';
   document.body.appendChild(a); a.click(); a.remove();
   setTimeout(()=>URL.revokeObjectURL(a.href), 5000);
   ioResult.textContent=tr('已导出 ')+((d&&d.schedules||[]).length)+tr(' 个任务');
  }catch(err){ toast(tr('导出失败: ')+(err.text||err),'err'); }
  finally{ e.target.disabled=false; }
 };
 // 导入：文件选择 → api/import；replace 需二次确认
 const fileInput=document.createElement('input');
 fileInput.type='file'; fileInput.accept='.json,application/json';
 fileInput.style.display='none';
 ioRow.appendChild(fileInput);
 const doImport=async(mode)=>{
  const file=fileInput.files&&fileInput.files[0];
  if(!file) return;
  let data;
  try{ data=JSON.parse(await file.text()); }
  catch(err){ toast(tr('导入失败: JSON 解析错误'),'err'); return; }
  try{
   const r=await api(_panelUrl({plugin:panel.plugin, path:'api/import'}),
    {method:'POST', headers:{'Content-Type':'application/json'},
     body:JSON.stringify({data, mode})});
   ioResult.textContent=tr('导入完成：新增 {a}、更新 {u}、跳过 {s}',{a:r.added,u:r.updated,s:r.skipped});
   if(r.errors&&r.errors.length) toast(tr('跳过项：')+r.errors.slice(0,3).join('；'),'err');
   await loadPanel(panel);
  }catch(err){ toast(tr('导入失败: ')+(err.text||err.detail||err),'err'); }
 };
 ioBtn('导入（合并）','ghost').onclick=()=>{ fileInput.click(); };
 ioBtn('导入（覆盖）','danger').onclick=async()=>{
  const ok=await window.dlgConfirm(
   tr('覆盖导入会先清空现有全部定时任务，确定继续？'), tr('导入（覆盖）'), true);
  if(!ok) return;
  fileInput.dataset.mode='replace';
  fileInput.click();
 };
 // 覆盖模式的 mode 传递：在 doImport 前从 dataset 取
 const _origDoImport=doImport;
 fileInput.onchange=()=>{
  if(!fileInput.files||!fileInput.files[0]) return;
  const mode=fileInput.dataset.mode==='replace'?'replace':'merge';
  fileInput.dataset.mode='';
  _origDoImport(mode).finally(()=>{ fileInput.value=''; });
 };
 wrap.insertBefore(ioRow, form);
 const el=(id)=>form.querySelector('#'+id);
 let editing=null;   // 编辑中的任务名（null = 新建模式）
 function fillForm(it){
  editing=it?it.name:null;
  el('schName').value=it?it.name:'';
  el('schName').disabled=!!it;
  const spec=it?it.spec:{};const t=spec.every?'every':(spec.daily?'daily':'once');
  el('schType').value=t;
  el('schWhen').value=spec[t]||'';
  el('schEnabled').checked=it?!!it.enabled:true;
  el('schTask').value=it?it.task:'';
  el('schModel').value=it?it.model||'':'';
  el('schWs').value=it?it.workspace||'':'';
  el('schSave').textContent=tr(it?'保存修改':'新建任务');
  el('schCancel').style.display=it?'':'none';
 }
 el('schCancel').onclick=()=>fillForm(null);
 el('schSave').onclick=async()=>{
  const name=el('schName').value.trim(), when=el('schWhen').value.trim(), task=el('schTask').value.trim();
  if(!name||!task){ toast(tr('任务名与任务描述都不能为空'),'err'); return }
  const type=el('schType').value;
  const payload={name, spec:{[type]:when}, task, model:el('schModel').value.trim(),
                 workspace:el('schWs').value.trim(), enabled:el('schEnabled').checked};
  const btn=el('schSave'); btn.disabled=true;
  try{
   await api(_panelUrl({plugin:panel.plugin, path:editing?'api/update':'api/add'}),
             {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
   toast(tr('已保存: ')+name,'ok'); await loadPanel(panel);
  }catch(e){ toast(tr('保存失败: ')+(e.text||e.detail||e),'err'); btn.disabled=false }
 };
 // 模型候选
 try{
  const models=await api('/v1/models');
  const ids=(models&&models.data||[]).map(m=>m.id).filter(Boolean);
  el('schModels').innerHTML=ids.map(id=>'<option value="'+esc(id)+'">').join('');
 }catch(e){}
 // ── 任务行 + 操作 ──
 const rows=document.createElement('div');
 rows.style.cssText='display:flex;flex-direction:column;gap:6px';
 function specText(spec){
  if(!spec)return '';
  if(spec.every)return tr('每 ')+spec.every;
  if(spec.daily)return tr('每天 ')+spec.daily;
  if(spec.once)return tr('一次 ') + spec.once;
  return JSON.stringify(spec);
 }
 if(!list.length) rows.innerHTML='<div class="meta">'+esc(tr('（还没有定时任务，用上方表单创建）'))+'</div>';
 list.forEach(it=>{
  const row=document.createElement('div');
  row.style.cssText='display:flex;flex-direction:column;gap:3px;padding:7px 9px;border:1px solid var(--line);border-radius:6px';
  const head=document.createElement('div');
  head.style.cssText='display:flex;gap:8px;align-items:center;flex-wrap:wrap';
  const bad=it.last_error;
  head.innerHTML='<b>'+esc(it.name)+'</b>'+
   '<span class="meta">'+esc(specText(it.spec))+'</span>'+
   '<span class="meta">'+esc(tr('下次'))+': '+esc(it.next_run||'—')+'</span>'+
   '<span class="meta">'+esc(tr('已跑'))+': '+(it.runs||0)+'</span>'+
   (it.last_run?'<span class="meta">'+esc(tr('上次'))+': '+esc(it.last_run)+'</span>':'')+
   '<span class="pstat '+(it.enabled?'on':'')+'">'+(it.enabled?esc(tr('启用')):esc(tr('已停用')))+'</span>';
  if(it.model) head.innerHTML+='<span class="meta">'+esc(it.model)+'</span>';
  if(it.workspace) head.innerHTML+='<span class="meta">'+esc(it.workspace)+'</span>';
  const acts=document.createElement('div');
  acts.style.cssText='display:flex;gap:6px;flex-wrap:wrap';
  const mk=(label,cls,fn)=>{
   const b=document.createElement('button'); b.type='button'; b.className='btn '+cls;
   b.textContent=tr(label); b.onclick=fn; acts.appendChild(b); return b;
  };
  const post=async(path,payload,confirmMsg)=>{
   if(confirmMsg && !await window.dlgConfirm(confirmMsg, tr('定时任务'), true)) return false;
   await api(_panelUrl({plugin:panel.plugin, path}),{method:'POST',
    headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload||{})});
   return true;
  };
  mk('运行','ghost',async(e)=>{ e.target.disabled=true;
   try{ await post('api/run',{name:it.name}); toast(tr('已触发: ')+it.name,'ok'); await loadPanel(panel); }
   catch(err){ toast(tr('触发失败: ')+(err.text||err),'err'); e.target.disabled=false } });
  mk(it.enabled?'停用':'启用','ghost',async(e)=>{ e.target.disabled=true;
   try{ await post('api/update',{name:it.name, enabled:!it.enabled}); await loadPanel(panel); }
   catch(err){ toast(tr('操作失败: ')+(err.text||err),'err'); e.target.disabled=false } });
  mk('编辑','ghost',()=>{ fillForm(it); form.scrollIntoView({block:'nearest',behavior:'smooth'}); });
  if(it.last_session && typeof window.load==='function')
   mk('会话','ghost',()=>{ window.load(it.last_session); closeMask('panelMask'); });
  mk('删除','danger',async(e)=>{ e.target.disabled=true;
   try{
    if(await post('api/remove',{name:it.name}, tr('确定删除定时任务 {name}？',{name:it.name})))
     { toast(tr('已删除: ')+it.name,'ok'); await loadPanel(panel); }
    else e.target.disabled=false;
   }catch(err){ toast(tr('删除失败: ')+(err.text||err),'err'); e.target.disabled=false } });
  row.appendChild(head);
  if(it.task){ const t=document.createElement('div'); t.className='meta'; t.style.cssText='white-space:pre-wrap'; t.textContent=it.task; row.appendChild(t); }
  if(bad){ const d=document.createElement('div'); d.className='meta'; d.style.color='var(--bad,#d33)'; d.textContent=tr('上次失败: ')+bad; row.appendChild(d); }
  else if(it.last_result){ const d=document.createElement('div'); d.className='meta'; d.style.cssText='white-space:pre-wrap'; d.textContent=it.last_result; row.appendChild(d); }
  row.appendChild(acts);
  rows.appendChild(row);
 });
 wrap.appendChild(rows);
 body.appendChild(wrap);
}
async function loadPanel(p){
 const body=document.getElementById('panelBody');
 if(!body)return;
 body.innerHTML='<div class="meta">'+esc(tr('加载中…'))+'</div>';
 try{
  const data=await _panelData(p);
  _renderPanel(p,data);
  if(p.plugin==='teams-bot' && p.id==='status'){
   await _injectTeamsBotModelControl(body, data, p);
  }else if(p.plugin==='schedule' && p.id==='schedules'){
   await _injectScheduleManager(body, data, p);
  }
 }catch(e){
  body.innerHTML='<div class="meta">'+esc(tr('读取失败: ')+(e.text||e))+'</div>';
 }
}
function closePanels(){closeMask('panelMask')}

const PLUGINS_BASE=['power-apps','power-automate','power-platform','teams-bot','mcp','research','web-search','image-gen','telegram','schedule','kb','vector-index'];
function _pluginLabel(name){
 if(name==='power-apps')return 'Power Apps';
 if(name==='power-automate')return 'Power Automate';
 if(name==='power-platform')return 'Power Platform';
 if(name==='teams-bot')return 'Teams Bot';
 return name;
}
/* 插件启用/禁用/重载：UI 统一走这里，插件管理页按钮和 /plugin 斜杠命令共用。
   启用后写 data/plugins.json（永久生效），并提示合并插件 i18n。 */
async function setPluginEnabled(name, enabled){
 const d=await api('/v1/plugins/'+encodeURIComponent(name)+'/'+(enabled?'enable':'disable'),{method:'POST'});
 toast(tr('已')+(enabled?tr('启用'):tr('禁用'))+': '+name,'ok');
 try{ await mergePluginI18n(false); }catch(e){}
 return d;
}
async function togglePlugin(name){
 const d=await api('/v1/plugins');
 const p=(d.plugins||[]).find(x=>x.name===name);
 if(!p){ toast(tr('插件不存在: ')+name,'err'); return; }
 await setPluginEnabled(name, !p.enabled);
}
async function openPlugins(){
 openMask('pluginsMask');
 const box=document.getElementById('pluginsBody');
 if(!box) return;
 box.innerHTML='<div class="meta">'+esc(tr('加载中…'))+'</div>';
 let list=[];
 try{ const d=await api('/v1/plugins'); list=d.plugins||[] }catch(e){
  box.innerHTML='<div class="meta">'+esc(tr('读取插件失败: ')+(e.text||e))+'</div>';return}
 if(!list.length){ box.innerHTML='<div class="meta">'+esc(tr('没有安装插件（把插件放进 plugins/ 目录）'))+'</div>';return}
 box.innerHTML='';
 list.forEach(p=>{
  const row=document.createElement('div');row.className='pluginrow';
  const head=document.createElement('div');head.className='phead';
  const name=document.createElement('b');name.textContent=p.name+'  ';
  const ver=document.createElement('span');ver.className='meta';ver.textContent='v'+(p.version||'')+' · ';
  const st=document.createElement('span');st.className='pstat '+(p.status==='loaded'?'on':(p.status==='error'?'bad':''));
  st.textContent=stateText(p.status==='loaded'?'已启用':(p.status==='disabled'?'已禁用':'未加载'));
  head.appendChild(name);head.appendChild(ver);head.appendChild(st);
  const desc=document.createElement('div');desc.className='meta';
  desc.setAttribute('data-i18n-skip','1');   // 插件作者给的描述：数据，不参与覆盖率审计
  desc.textContent=p.description||'';
  const meta=document.createElement('div');meta.className='meta';
  meta.setAttribute('data-i18n-skip','1');   // 贡献点是结构化数据
  meta.textContent=tr('权限: ')+(p.permissions||[]).join(', ')+' · '+tr('贡献: ')+JSON.stringify(p.contributes||{});
  const acts=document.createElement('div');acts.className='pacts';
  const btn=document.createElement('button');btn.type='button';btn.className='btn';
  btn.textContent=p.enabled?tr('禁用'):tr('启用');
  btn.onclick=async()=>{ btn.disabled=true;
    try{ await setPluginEnabled(p.name, !p.enabled);
      await openPlugins(); }
    catch(e){ btn.disabled=false; toast(tr('操作失败: ')+(e.text||e),'err') } };
  const rb=document.createElement('button');rb.type='button';rb.className='btn ghost';rb.textContent=tr('重载');
  rb.onclick=async()=>{ try{ await api('/v1/plugins/'+encodeURIComponent(p.name)+'/reload',{method:'POST'});
      toast(tr('已重载: ')+p.name,'ok'); await openPlugins(); }catch(e){ toast(tr('重载失败: ')+(e.text||e),'err') } };
  acts.appendChild(btn);acts.appendChild(rb);
  if(p.removable){
   const ub=document.createElement('button');ub.type='button';ub.className='btn danger';ub.textContent=tr('卸载');
   ub.onclick=async()=>{
    const ok=await window.dlgConfirm(
      tr('确定卸载插件 {name}？会先摘除它的工具/API/面板，并保留备份。',{name:p.name}),
      tr('卸载插件'),true);
    if(!ok)return;
    ub.disabled=true;
    try{
     const r=await api('/v1/plugins/uninstall',{method:'POST',
       headers:{'Content-Type':'application/json'},
       body:JSON.stringify({name:p.name,keep_backup:true})});
     toast(tr('已卸载: {name}（备份: {backup}）',{name:p.name,backup:r.backup||'—'}),'ok');
     try{await mergePluginI18n(false)}catch(e){}
     await openPlugins();
    }catch(e){ub.disabled=false;toast(tr('卸载失败: ')+((e&&(e.text||e.detail))||e),'err')}
   };
   acts.appendChild(ub);
  }
  row.appendChild(head);row.appendChild(desc);row.appendChild(meta);row.appendChild(acts);
  if(p.error){ const er=document.createElement('div');er.className='perr';er.textContent=p.error; row.appendChild(er) }
  box.appendChild(row)});
}
function closePlugins(){closeMask('pluginsMask')}

/* ── 斜杠命令（/）：模式与工具的快捷入口 ─────────────────────────
   输入框以 "/" 开头时列出命令；↑↓ 选择、Tab/Enter 补全、Esc 关闭。
   带参数直接回车即执行（/research 问题…），不带参数则补全后等输入。 */
const SLASH_COMMANDS=[
 {id:'research',label:'research',aliases:['research','深研','dr'],
  desc:'深研：多角度搜索 + 抓正文，生成带来源的报告',
  run:(arg)=>{
   if(!arg){toast(tr('用法：/research 你的问题'),'err');return}
   const t=document.getElementById('task'); if(t)t.value='';
   deepResearchNow(arg)}},
 {id:'fanout',label:'fanout',aliases:['fanout','扇出','并行'],
  desc:'扇出：把多个互不依赖的任务各派一个子代理（用 | 分隔），不经模型判断',
  run:(arg)=>{
   const tasks=String(arg||'').split('|').map(x=>x.trim()).filter(Boolean);
   if(tasks.length<2){toast(tr('用法：/fanout 任务1 | 任务2 | 任务3（至少两个）'),'err');return}
   fanoutNow(tasks)}},
 {id:'audit',label:'audit',aliases:['audit','审计','对抗审计','review'],
  desc:'对抗式审计：多名审计员 + 裁判核验，循环到只剩小问题',
  run:(arg)=>{auditNow(arg)}},
 {id:'kb',label:'kb',aliases:['kb','知识库','入库'],
  desc:'把这段内容/附件整理后存入知识库（模型整理 → kb_add 入库）',
  run:(arg)=>{
   const t=document.getElementById('task');if(!t)return;
   const body=String(arg||'').trim();
   t.value=(body?body+'\n\n':'')+
    '[存入知识库] 请先把我上面的内容（文字/链接/附件里的文件）整理成一条知识库条目：'+
    '去掉寒暄与重复、按要点分条、补一个准确的小标题；然后调用 kb_add 工具入库'+
    '（文本用 text+title；文件用 paths；网页用 url）。完成后只回一句"已入库：<标题>"。';
   t.focus();toast(tr('已填入入库指令，确认后发送'),'ok')}},
 {id:'agent',label:'agent',aliases:['agent','执行'],
  desc:'切到 Agent 执行模式',run:()=>{setTaskMode('agent');toast(tr('已切到 Agent 执行'),'ok')}},
 {id:'chat',label:'chat',aliases:['chat','对话'],
  desc:'切到普通对话模式',run:()=>{setTaskMode('chat');toast(tr('已切到普通对话'),'ok')}},
 {id:'goal',label:'goal',aliases:['goal','目标'],
  desc:'目标模式：建立目标后多轮推进，未完成不会停',
  run:(arg)=>{
   if(!arg){toast(tr('用法：/goal 你的目标'),'err');return}
   const t=document.getElementById('task');
   if(t)t.value=arg+'\n\n'+'[目标模式] 请把上面这件事当作一个长期**目标**：'
    +'先调用 create_goal 记录下来（max_rounds 取 8-12），必要时用 todo_write 拆出 3-8 个可执行子步骤；'
    +'然后分多轮持续推进，每轮做完一部分就继续做下一部分，目标未达成时不要返回 final；'
    +'只有目标真正达成（或确实被外部条件阻塞）时，先调用 update_goal（complete / blocked）再返回 final。';
   runTask()}},
 {id:'plan',label:'plan',aliases:['plan','计划'],
  desc:'计划模式（只读）：探索后产出方案，exit_plan_mode 提交',
  run:(arg)=>{
   if(!arg){toast(tr('用法：/plan 你的任务'),'err');return}
   const t=document.getElementById('task');
   if(t)t.value=arg;
   planModeOnce=true;        // 由后端强制只读，不只靠提示词
   runTask()}},
 {id:'self-upgrade',label:'self-upgrade',aliases:['self-upgrade','自升级','改服务'],
  desc:'自升级任务模板：一键填进输入框（记得先在 设置→危险区 打开「允许自升级」）',
  run:(arg)=>{
   const t=document.getElementById('task');
   if(t){t.value=(arg?arg+'\n\n':'')+'[自升级任务]\n文件：gantry/<文件名>.py（安装目录相对路径，不要写绝对路径）\n现状：<你看到的现象，例如「重启后旧会话恢复失败」>\n改成：<具体要改什么；能贴函数名/字段名/代码片段最好>\n范围：只改上面的文件/逻辑，别动无关文件\n如果不记得文件名：先调 self_search_text 搜出现象里的关键词（服务代码在安装目录，工作区里没有），把定位到的文件/行号报给我确认后再改；不要猜。\n验收：self_validate + self_unittest 都通过\n交付：改了哪几行、验证结果、是否要重启、以及回滚命令';
    setTaskMode('agent');
    toast(tr('已载入自升级模板：把 <…> 部分填掉即可'),'ok');
    t.focus()}}},
 {id:'plugin',label:'plugin',aliases:['plugin','插件','plug'],
  desc:'启用或禁用插件：/plugin <名称> [on|off]，如 /plugin power-apps on',
  run:(arg)=>{
   const parts=String(arg||'').trim().split(/\s+/).filter(Boolean);
   let name=parts[0]||'', act=parts[1]||'';
   if(!name){
    const d=PLUGINS_BASE.map(n=>_pluginLabel(n)+' ('+n+')').join('  ');
    toast(tr('可用插件: ')+d,'ok'); return;
   }
   const real=PLUGINS_BASE.find(n=>n===name.toLowerCase())||PLUGINS_BASE.find(n=>_pluginLabel(n).toLowerCase()===name.toLowerCase());
   if(!real){ toast(tr('未知插件: ')+name,'err'); return; }
   if(act==='on'||act==='enable'||act==='启用'){ setPluginEnabled(real,true).catch(e=>toast(tr('操作失败: ')+(e.text||e),'err')); }
   else if(act==='off'||act==='disable'||act==='禁用'){ setPluginEnabled(real,false).catch(e=>toast(tr('操作失败: ')+(e.text||e),'err')); }
   else{ togglePlugin(real).catch(e=>toast(tr('操作失败: ')+(e.text||e),'err')); }
  }},
 {id:'help',label:'help',aliases:['help','?','帮助'],
  desc:'列出全部可用命令',run:()=>{const t=document.getElementById('task');
   if(t){t.value='/';slashUpdate(true);t.focus()}}},
];

export { openPanels, refreshPanels, loadPanel, closePanels, openPlugins, closePlugins, setPluginEnabled, togglePlugin, SLASH_COMMANDS };
