// settings.js — 
// 从原 webui/app.js 拆出的 ES 模块（该单体已退役，见 docs/ESM-MIGRATION.md）。
import { SETTINGS_GROUPS, SANDBOX_UI, ENUM_LABEL, openMemory } from './themeboot.js';
import { CODE_EXT } from './codeux.js';
import { stepFilterInput } from './steps.js';
import { readAnimPrefs, applyAnimPrefs, setAnimPref, animPrefLabel, ANIM_RANGES } from './animprefs.js';
import { openKb, closeKb, kbPick, kbRefresh, kbReindex, kbImportUrl, kbImportText, kbInbox, kbSearch, kbDelete, fmtErr } from './kb.js';
import { openModels, closeModels, renderModels, modelDelete, modelCopyPath, modelDownload } from './models.js';
import { kbSaveMsg as kbSaveMsgImpl } from './composer.js';

/* 设置左侧的“工具页”：不弹窗，直接把对应面板的 .dlg 移进设置右侧渲染。 */
const TOOL_TABS = [
 {id:'manual', label:'使用手册', mask:'manualMask', run:()=>window.openManual()},
 {id:'models', label:'本地模型', mask:'modelsMask', run:()=>openModels()},
 {id:'plugins', label:'插件管理', mask:'pluginsMask', run:()=>window.openPlugins()},
 {id:'kb', label:'知识库', mask:'kbMask', run:()=>openKb()},
 {id:'panels', label:'插件面板', mask:'panelMask', run:()=>window.openPanels()},
 {id:'workspace', label:'工作区浏览', mask:'wsMask', run:()=>window.openWsBrowser('')},
 {id:'memory', label:'记忆面板', mask:'memMask', run:()=>window.openMemory()},
];
function renderSandbox(){
 const mode=SANDBOX_UI[sandboxMode]?sandboxMode:'workspace-write';
 const meta=SANDBOX_UI[mode];
 // 两处实例（会话内 + 新建任务）都要同步，所以按 class 遍历而不是按 id
 document.querySelectorAll('.sandboxchip').forEach(chip=>{
  const ico=chip.querySelector('.sb-ico');
  const label=chip.querySelector('[data-sandbox-label],#sandboxLabel');
  if(ico)ico.textContent=meta.ico;
  if(label)label.textContent=tr(meta.label);
  chip.dataset.mode=mode;
  chip.classList.toggle('warn',mode==='danger-full-access');
  const host=chip.closest('.menu');
  const menu=host?host.querySelector('.menuList'):null;
  chip.setAttribute('aria-expanded',menu&&menu.classList.contains('open')?'true':'false');
 });
 document.querySelectorAll('.sandboxmenu .sbopt').forEach(b=>{
  const on=b.dataset.arg===mode;
  b.classList.toggle('on',on);
  const c=b.querySelector('.sb-check');
  if(c)c.textContent=on?'✓':'';
 });
}
async function loadSandbox(){
 try{
  const r=await api('/v1/agent/sandbox');
  sandboxMode=String(r.mode||'workspace-write');
 }catch(e){}
 renderSandbox();
}
async function pickSandbox(mode){
 if(!SANDBOX_UI[mode])return;
 try{
  const r=await api('/v1/agent/sandbox',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({mode,persist:true})});
  sandboxMode=String(r.mode||mode);
  renderSandbox();
  document.querySelectorAll('.sandboxmenu.open').forEach(m=>m.classList.remove('open'));
  toast(tr('权限已切换为 {m}',{m:tr(SANDBOX_UI[sandboxMode].label)}),'ok');
 }catch(e){ toast(tr('切换失败: ')+((e&&(e.text||e.detail))||e),'err') }
}

async function openSettings(){
 const mask=document.getElementById('settingsMask');
 if(!mask)return;
 _localSnap=_localSnapshot();      // 本次打开时的本地设置，保存时用来判断"改了什么"
 mask.classList.add('open');
 await renderSettings();
}
function closeSettings(){
 unmountAllTools();
 const mask=document.getElementById('settingsMask');
 if(mask)mask.classList.remove('open');
}
/* 一行滑杆：标签 + range + 实时数值。data-anim 是参数名（animprefs.js 的键），
   改动即时生效并落库（不用等「保存」——调动画时看不到变化等于没有）。 */
function _animRow(key, label, hint, prefs){
 const rng=ANIM_RANGES[key]||[0,1];
 const step=rng[1]-rng[0]>20?1:(rng[1]-rng[0]>4?0.1:0.05);
 const cur=prefs&&prefs[key]!==undefined?prefs[key]:rng[0];
 return `<label class="setrow animrow" title="${esc(tr(hint))}"><span>${esc(tr(label))}</span>
   <input type="range" id="set_${key}" data-anim="${key}" min="${rng[0]}" max="${rng[1]}"
     step="${step}" value="${cur}" aria-label="${esc(tr(label))}">
   <b class="animval" id="val_${key}">${esc(animPrefLabel(key,prefs))}</b></label>`;
}

/* 动画参数即时生效：设置页里的滑杆/开关一动就写 localStorage + 应用（并刷新右侧数值）。 */
function _onAnimInput(el){
 const key=el&&el.dataset?el.dataset.anim:'';
 if(!key)return;
 const v=el.type==='checkbox'?(el.checked?'1':'0'):el.value;
 const prefs=setAnimPref(key,v);
 const out=document.getElementById('val_'+key);
 if(out)out.textContent=animPrefLabel(key,prefs);
}
document.addEventListener('input',ev=>{if(ev.target&&ev.target.dataset&&ev.target.dataset.anim)_onAnimInput(ev.target)});
document.addEventListener('change',ev=>{if(ev.target&&ev.target.dataset&&ev.target.dataset.anim)_onAnimInput(ev.target)});

function _cfgInput(key,spec,val){
 const id='set_'+key.replace(/\./g,'_');
 if(spec.kind==='bool'){
  return `<label class="setrow" for="${id}"><input type="checkbox" id="${id}" data-cfg="${esc(key)}" ${val?'checked':''}>
    <span>${esc(tr(spec.label))}</span></label>`;
 }
 if(spec.kind==='enum'){
  const opts=spec.values.map(v=>{
   const label=ENUM_LABEL[v]||v;
   return `<option value="${esc(v)}" ${String(val)===v?'selected':''}>${esc(tr(label))}</option>`;
  }).join('');
  return `<label class="setrow" for="${id}"><span>${esc(tr(spec.label))}</span>
    <select id="${id}" data-cfg="${esc(key)}">${opts}</select></label>`;
 }
 const t=spec.kind==='int'?'number':'text';
 // 没配过的键显示"默认"占位：空输入框会让人以为坏了/以为必须填
 const ph=(val===undefined||val===null||val==='')?`placeholder="${esc(tr('默认'))}"`:'';
 return `<label class="setrow" for="${id}"><span>${esc(tr(spec.label))}</span>
   <input type="${t}" id="${id}" data-cfg="${esc(key)}" value="${esc(val??'')}" ${ph}
     ${spec.min!==undefined?`min="${spec.min}"`:''} ${spec.max!==undefined?`max="${spec.max}"`:''}></label>`;
}

/* ── 原始 config.json 图形树编辑器 ───────────────────────── */
function _cfgLeafHtml(val, path) {
 const key = path[path.length - 1] || '';
 const fullPath = path.join('.');
 const pad = Math.max(0, path.length - 1) * 12;
 if (typeof val === 'boolean') {
  return `<label class="setrow" style="padding-left:${pad}px"><span>${esc(key)}</span>
   <input type="checkbox" data-cfg-path="${esc(fullPath)}" ${val ? 'checked' : ''}></label>`;
 }
 const t = typeof val === 'number' ? 'number' : 'text';
 return `<label class="setrow" style="padding-left:${pad}px"><span>${esc(key)}</span>
  <input type="${t}" data-cfg-path="${esc(fullPath)}" value="${esc(val === null ? '' : val)}" style="flex:1"></label>`;
}

function _cfgTreeHtml(obj, path = []) {
 if (obj === null || typeof obj !== 'object') {
  return _cfgLeafHtml(obj, path);
 }
 if (Array.isArray(obj)) {
  const key = path[path.length - 1] || '';
  const fullPath = path.join('.');
  const pad = Math.max(0, path.length - 1) * 12;
  return `<label class="setrow" style="padding-left:${pad}px"><span>${esc(key)}</span>
   <textarea data-cfg-path="${esc(fullPath)}" class="code" spellcheck="false" style="flex:1;min-height:60px;font-size:12px">${esc(JSON.stringify(obj, null, 2))}</textarea></label>`;
 }
 return Object.entries(obj).map(([k, v]) => {
  const childPath = [...path, k];
  const isLeaf = v === null || typeof v !== 'object';
  if (isLeaf) return _cfgLeafHtml(v, childPath);
  const pad = Math.max(0, path.length) * 12;
  return `<details style="margin:2px 0;padding-left:${pad}px" open>
    <summary style="cursor:pointer;font-weight:600;color:var(--accent)">${esc(k)}</summary>
    <div style="margin-left:8px;border-left:2px solid var(--line);padding-left:6px">${_cfgTreeHtml(v, childPath)}</div>
  </details>`;
 }).join('');
}

function collectConfigTree() {
 const out = {};
 const box = document.getElementById('settingsBody');
 if (!box) return out;
 box.querySelectorAll('[data-cfg-path]').forEach(el => {
  const path = el.dataset.cfgPath.split('.');
  const isTextarea = el.tagName === 'TEXTAREA';
  const isCheck = el.type === 'checkbox';
  let val;
  if (isTextarea) {
   try { val = JSON.parse(el.value); } catch (_) { val = el.value; }
  } else if (isCheck) {
   val = el.checked;
  } else if (el.type === 'number') {
   const n = Number(el.value);
   val = Number.isNaN(n) ? el.value : n;
  } else {
   val = el.value;
  }
  let cur = out;
  for (let i = 0; i < path.length - 1; i++) {
   const k = path[i];
   if (!(k in cur) || typeof cur[k] !== 'object' || cur[k] === null) cur[k] = {};
   cur = cur[k];
  }
  cur[path[path.length - 1]] = val;
 });
 return out;
}

async function renderSettings(){
 const box=document.getElementById('settingsBody');
 const nav=document.getElementById('settingsNav');
 if(!box||!nav)return;
 unmountAllTools();
 if(!window.settingsValues){
  try{
   const r=await api('/v1/config');
   window.settingsValues=r.values||{}; window.settingsSchema=r.schema||{};
  }catch(e){ window.settingsValues={}; window.settingsSchema={}; }
 }
 const groups={};
 Object.entries(window.settingsSchema).forEach(([k,spec])=>{
  const g=spec.group||'exec';
  (groups[g]=groups[g]||[]).push([k,spec]);
 });
 const has=(gid)=>gid==='ui'||gid==='danger'||(groups[gid]||[]).length;
 const _AP=readAnimPrefs();                  // 动画参数（模板里铺滑杆的当前值）
 const visible=SETTINGS_GROUPS.filter(([gid])=>has(gid));
 const allTabs=visible.map(([gid])=>gid).concat(TOOL_TABS.map(t=>'tool-'+t.id));
 if(!allTabs.includes(window.settingsTab))window.settingsTab=visible[0][0];

 // 左导航：配置页签 + 工具动作（全部纯文字，无图标）
 const tabsHtml=visible.map(([gid,label])=>{
  const on=gid===window.settingsTab;
  return `<button type="button" class="setnavbtn${on?' on':''}${gid==='danger'?' danger':''}"
     role="tab" aria-selected="${on?'true':'false'}" data-act="settingsTab" data-arg="${gid}">
     ${esc(tr(label))}</button>`;
 }).join('');
 const toolsHtml=TOOL_TABS.map((t)=>{
  const id='tool-'+t.id;
  const on=id===window.settingsTab;
  return `<button type="button" class="setnavbtn setnavact${on?' on':''}"
     role="tab" aria-selected="${on?'true':'false'}" data-act="settingsTab" data-arg="${id}">
     ${esc(tr(t.label))}</button>`;
 }).join('');
 nav.innerHTML=tabsHtml+`<div class="setnavsep"></div>`+toolsHtml;

 // 右内容：所有分组都渲染，非当前的隐藏（值仍留在 DOM，保存不会丢）
 const pane=(gid,html)=>`<section class="setgroup${gid==='danger'?' danger':''}" data-pane="${gid}"
    style="${gid===window.settingsTab?'':'display:none'}">${html}</section>`;

 const uiHtml=`<h4>${esc(tr('外观'))}</h4>
   <label class="setrow"><span>${esc(tr('主题'))}</span>
     <select id="setTheme" data-ui="theme">${[['auto','跟随系统'],['light','浅色'],['dark','深色'],['solarized','护眼'],['mint','薄荷'],['amber','琥珀']]
       .map(([v,t])=>`<option value="${v}" ${_settingsTheme()===v?'selected':''}>${esc(tr(t))}</option>`).join('')}</select></label>
   <label class="setrow"><span>${esc(tr('语言'))}</span>
     <select id="setLang" data-ui="lang">${[['zh','中文'],['en','English'],['tw','繁體中文']]
       .map(([v,t])=>`<option value="${v}" data-i18n-skip="1" ${currentLang()===v?'selected':''}>${esc(t)}</option>`).join('')}</select></label>
   <h4>${esc(tr('动画'))}</h4>
   <label class="setrow"><input type="checkbox" id="setTeamScopeHeartbeat" data-ui="scope" data-anim="scopeOn"
     ${localStorage.getItem('gantry-teamscope-heartbeat')==='0'?'':'checked'}>
     <span>${esc(tr('任务进行时的心跳动画（卡片上沿的示波器）'))}</span></label>
   ${_animRow('scopeAmp', '示波器振幅', '波形上下摆动的幅度（100% 是原设计）', _AP)}
   ${_animRow('scopeLine', '示波器线宽', '线的粗细（px）', _AP)}
   ${_animRow('scopeOpacity', '示波器透明度', '线的不透明度，调低更含蓄', _AP)}
   ${_animRow('scopeSpeed', '示波器速度', '波形横向流动的快慢', _AP)}
   <h4>${esc(tr('输入框呼吸'))}</h4>
   <label class="setrow"><input type="checkbox" id="setBreatheOn" data-anim="breatheOn"
     ${_AP.breatheOn?'checked':''}>
     <span>${esc(tr('任务运行时输入框边框呼吸'))}</span></label>
   ${_animRow('breatheDur', '呼吸时长', '一个呼吸周期的秒数', _AP)}
   ${_animRow('breatheStrength', '呼吸强度', '边框强调色与光晕的强度（100% 是原设计）', _AP)}`;

 const dangerHtml=`<h4>${esc(tr('危险区'))}</h4>
   <div class="setbtns">
    <button type="button" class="btn ghost" data-act="restartService">↻ ${esc(tr('重启服务'))}</button>
    <button type="button" class="btn ghost danger" data-act="shutdownService">⏻ ${esc(tr('终止服务 / 退出'))}</button>
   </div>
   <p class="sethint">${esc(tr('重启/终止都会中断正在跑的任务；状态已落库，重启后可恢复。'))}</p>`;

 const cfgPane=(gid,label)=>{
  const rows=(groups[gid]||[]).map(([k,spec])=>_cfgInput(k,spec,window.settingsValues[k])).join('');
  return rows?`<h4>${esc(tr(label))}</h4>${rows}`:'';
 };

 box.innerHTML=visible.map(([gid,label])=>{
  if(gid==='ui')return pane('ui',uiHtml);
  if(gid==='danger'){
   // 危险区：先渲染 schema 里登记的高风险开关（如"允许自升级"），再放操作按钮
   const rows=(groups[gid]||[]).map(([k,spec])=>_cfgInput(k,spec,window.settingsValues[k])).join('');
   return pane('danger',(rows?`<h4>${esc(tr('高风险设置'))}</h4>${rows}`:'')+dangerHtml);
  }
  return pane(gid,cfgPane(gid,label));
 }).join('')+TOOL_TABS.map((t)=>{
  const id='tool-'+t.id;
  return `<section class="setgroup settings-toolpane" data-pane="${id}"
    style="${id===window.settingsTab?'':'display:none'}"><div class="settings-embed" id="embed-${id}"></div></section>`;
 }).join('');
 try{translateAttrs(box);translateAttrs(nav)}catch(e){}
 if(String(window.settingsTab||'').startsWith('tool-')) mountTool(window.settingsTab);
}
function _settingsTheme(){
 try{const t=localStorage.getItem('gantry-theme');return (!t||t==='auto')?'auto':t}catch(e){return 'auto'}
}
function _toolById(id){return TOOL_TABS.find(t=>'tool-'+t.id===id)||null}

function unmountTool(id){
 const t=_toolById(id);if(!t)return;
 const host=document.getElementById('embed-'+id);
 const dlg=host&&host.querySelector('.dlg');
 const mask=document.getElementById(t.mask);
 if(dlg&&mask)mask.appendChild(dlg);
 if(mask)mask.classList.remove('open');
}

function unmountAllTools(){
 TOOL_TABS.forEach(t=>unmountTool('tool-'+t.id));
}

function mountTool(id){
 const t=_toolById(id);if(!t)return;
 unmountAllTools();
 let mask=document.getElementById(t.mask);
 if(!mask){try{t.run()}catch(e){}}
 mask=document.getElementById(t.mask);
 const dlg=mask&&mask.querySelector('.dlg');
 const host=document.getElementById('embed-'+id);
 if(dlg&&host)host.appendChild(dlg);
 if(dlg)dlg.classList.add('settings-embed-dlg');
 try{t.run()}catch(e){}
 if(mask)mask.classList.remove('open');
}

function setSettingsTab(id){
 const known=SETTINGS_GROUPS.map(g=>g[0]);
 const isTool=String(id).startsWith('tool-');
 if(!known.includes(id)&&!isTool)return;
 window.settingsTab=id;
 document.querySelectorAll('#settingsNav .setnavbtn').forEach(b=>{
  const on=b.dataset.arg===id;
  b.classList.toggle('on',on);b.setAttribute('aria-selected',on?'true':'false');
 });
 document.querySelectorAll('#settingsBody .setgroup[data-pane]').forEach(p=>{
  p.style.display=(p.dataset.pane===id)?'':'none';
 });
 if(isTool)mountTool(id);else unmountAllTools();
}

function collectSettings(){
 const out={};
 const box=document.getElementById('settingsBody');
 if(!box)return out;
 box.querySelectorAll('[data-cfg]').forEach(el=>{
  const key=el.dataset.cfg;
  if(!key)return;
  out[key]=el.type==='checkbox'?el.checked:el.value;
 });
 return out;
}

/* 本地设置（不走 /v1/config）：主题、语言、动画参数。
   保存按钮必须把它们也算进"有没有改动"，否则只调了它们时会误报"没有改动"
   ——用户实测就撞到了：TeamScope 参数明明生效了，提示却说没改动。 */
let _localSnap=null;
function _localSnapshot(){
 const anim=readAnimPrefs();
 let theme=''; try{theme=localStorage.getItem('gantry-theme')||''}catch(e){}
 let lang='zh'; try{lang=currentLang()}catch(e){}
 return {theme, lang, anim};
}
/* 与快照比，列出改动了哪些本地项（名字给人看，用于提示里的"本地 N 项"） */
function _localChanges(){
 const now=_localSnapshot();
 const old=_localSnap||now;
 const out=[];
 if(String(now.theme||'')!==String(old.theme||''))out.push('theme');
 if(String(now.lang||'')!==String(old.lang||''))out.push('lang');
 Object.keys(ANIM_RANGES).concat(['scopeOn','breatheOn']).forEach(k=>{
  const a=old.anim?old.anim[k]:undefined, b=now.anim?now.anim[k]:undefined;
  if(String(a)!==String(b))out.push('anim:'+k);
 });
 return out;
}

async function saveSettings(){
 const themeSel=document.getElementById('setTheme');
 const langSel=document.getElementById('setLang');
 if(themeSel&&themeSel.value!==themeChoice()){try{localStorage.setItem('gantry-theme',themeSel.value)}catch(e){};applyTheme(themeSel.value,false);applyLangButton()}
 if(langSel&&langSel.value!==currentLang()){setLang(langSel.value)}
 const teamScopeHeartbeat=document.getElementById('setTeamScopeHeartbeat');
 if(teamScopeHeartbeat){setAnimPref('scopeOn',teamScopeHeartbeat.checked?'1':'0')}
 const breatheOnEl=document.getElementById('setBreatheOn');
 if(breatheOnEl){setAnimPref('breatheOn',breatheOnEl.checked?'1':'0')}
 // 滑杆：值可能还是模板初始值（用户没拖），也要落库，否则刷新后回到默认
 ['scopeAmp','scopeLine','scopeOpacity','scopeSpeed','breatheDur','breatheStrength'].forEach(k=>{
  const el=document.getElementById('set_'+k);
  if(el)setAnimPref(k,el.value);
 });
 applyAnimPrefs();
 // 只提交**改动过**的字段：整表提交时，空的文本/枚举控件会被后端校验拒掉
 // （实测 ``default_model: ""`` 让整批保存 400），也会把没动过的键来回写文件。
 const raw=collectSettings();
 const cur=window.settingsValues||{};
 const values={};
 Object.keys(raw).forEach(k=>{
  const before=(cur[k]===undefined||cur[k]===null)?'':String(cur[k]);
  const now=String(raw[k]);
  if(now!==before)values[k]=raw[k];
 });
 const serverN=Object.keys(values).length;
 const localChanges=_localChanges();       // 主题/语言/动画：都已经即时生效并落库了
 if(!serverN){
  // 只有本地改动：说清楚"存了什么"，不要再说"没有改动"
  if(localChanges.length){
   _localSnap=_localSnapshot();
   toast(tr('本地设置已保存（{n} 项）',{n:localChanges.length}),'ok');
  }else{
   toast(tr('没有改动'),'ok');
  }
  return;
 }
 try{
  const r=await api('/v1/config',{method:'POST',
   headers:{'Content-Type':'application/json'},
   body:JSON.stringify({values,persist:true})});
  window.settingsValues=Object.assign({},window.settingsValues,r.applied||{});
  const appliedN=Object.keys(r.applied||{}).length;
  _localSnap=_localSnapshot();
  toast(localChanges.length
    ? tr('设置已保存（服务端 {n} 项 · 本地 {m} 项）',{n:appliedN,m:localChanges.length})
    : tr('设置已保存（{n} 项）',{n:appliedN}),'ok');
 }catch(e){ toast(tr('保存失败: ')+((e&&(e.text||e.detail||e.message))||String(e)),'err') }
}

const ACTIONS={
 /* 复制文件卡片里的正文（按卡片 data-path 找到对应 pre） */
 copyFile:async(el)=>{
  const path=el.dataset.arg||'';
  const pre=(path?document.querySelector('.fc-body[data-path="'+CSS.escape(path)+'"]'):null)
    ||el.closest('.filecard').querySelector('.fc-body');
  if(!pre)return;
  const text=[...pre.childNodes].length?pre.innerText.replace(/^\s*\d+\s/gm,''):pre.innerText;
  try{await navigator.clipboard.writeText(text);toast(tr('已复制'),'ok')}
  catch(e){toast(tr('复制失败'),'err')}
 },

 toggleRail:()=>toggleRail(),
 // 窄屏抽屉的遮罩：点一下收起（只有窄屏才有意义，宽屏没有遮罩）
 closeRail:()=>{const r=document.documentElement;
  if(matchMedia('(max-width:860px)').matches){r.dataset.rail='closed';
   try{localStorage.setItem('gantry-rail','closed')}catch(e){}}},
 openTask:()=>openTask(),
 openManual:()=>openManual(),
 toggleBrowser:()=>toggleBrowser(),
 toggleTheme:()=>toggleTheme(),
 cycleLang:()=>cycleLang(),
 drawSessions:()=>drawSessions(),
 chatFindInput:()=>chatFindInput(),
 gsInput:()=>gsInput(),
 findStepKey:(el,e)=>{if(e.key==='Enter'){e.preventDefault();findStep(e.shiftKey?-1:1)}},
 gsKey:(el,e)=>{if(e.key==='Escape'){e.preventDefault();toggleGlobalSearch()}},
 findStep:(el)=>findStep(Number(el.dataset.arg||1)),
 control:(el)=>control(el.dataset.arg),
 continueAgent:()=>continueAgent(),
 activate:()=>activate(),
 openCopilot:()=>openCopilot(),
 toggleMore:(el,e)=>toggleMore(el, e),
 toggleToolLog:()=>toggleToolLog(),
 openSettings:()=>openSettings(),
 swapModelResume:(el)=>swapModelResume(el&&el.dataset&&el.dataset.arg),
 dismissRetry:()=>dismissRetryOverlay(),
 reopenRetry:()=>{retryDismissedAt=null;updateRetryCountdown(lastRuntime)},
 pickSandbox:(el)=>pickSandbox(el&&el.dataset?el.dataset.arg:''),
 settingsTab:(el)=>{setSettingsTab(el&&el.dataset?el.dataset.arg:'');},
 closeSettings:(el)=>{closeSettings();if(el&&el.dataset&&el.dataset.arg==='memory'){try{openMemory()}catch(e){}}},
 saveSettings:()=>saveSettings(),
 rollbackSession:()=>rollbackSession(),
 rollbackSelfUpgrade:()=>rollbackSelfUpgrade(),
 removeSession:(el)=>removeSession(el.dataset.arg==='true'),
 liveTab:(el)=>liveTab(el.dataset.arg),
 toggleStreamDetail:()=>toggleStreamDetail(),
 cardCollapse:(el)=>toggleCard(el.dataset.arg||'live'),
 topbarCollapse:()=>toggleTopbar(),
 showLastPrompt:()=>showLastPrompt(),
 openSubagent:(el)=>load(el.dataset.arg),
 openPlugins:()=>openPlugins(),
 closeKb:()=>closeKb(),
 kbPick:()=>kbPick(),
 kbRefresh:()=>kbRefresh(),
 kbReindex:()=>kbReindex(),
 kbUrl:()=>kbImportUrl(),
 kbText:()=>kbImportText(),
 kbInbox:()=>kbInbox(),
 kbSearch:()=>kbSearch(),
 kbDelete:(el)=>kbDelete(el.dataset.arg||''),
 openPanels:()=>openPanels(),
 refreshPanels:()=>refreshPanels(),
 closePanels:()=>closePanels(),
 deepResearch:()=>deepResearchNow(),
 pickWorkspace:()=>pickWorkspace(),
 newWorkspace:()=>newWorkspace(),
 wsMkdir:()=>wsMkdir(),
 wsUp:()=>wsUp(),
 wsDefault:()=>wsDefault(),
 wsChoose:()=>wsChoose(),
 closeWs:()=>closeWs(),
 closePlugins:()=>closePlugins(),
 toggleSubagents:()=>toggleSubagents(),
 showPromptView:()=>showPromptView(),
 expandLive:()=>expandLive(),
 toggleChatFind:()=>toggleChatFind(),
 injectMessage:()=>injectMessage(),
 setFollowupMode:(el)=>setFollowupMode(el.dataset.arg),
 setTaskMode:(el)=>setTaskMode(el.dataset.arg),
 sendFollowup:()=>sendFollowup(),
 stopTask:()=>stopTask(),
 exportTimeline:()=>exportTimeline(),
 refreshModels:()=>refreshModels(),
 runTask:()=>runTask(),
 taskKey:(el,e)=>{if(slashKey(e,el))return;if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();if(slashRun(el.value))return;runTask()}},
 taskInput:(el)=>slashUpdate(false,el),
 // 「继续对话」输入框：/ 菜单与 ↑↓ 选择；Enter 在 composer.js 里先问 slashKey，
 // 只有菜单没接管时才发送（所以这里无需处理 Enter）。
 slashFollowInput:(el)=>slashUpdate(false,el),
 slashFollowKey:(el,e)=>{slashKey(e,el)}, 
 closeDlg:()=>closeDlg(),
 closeManual:()=>closeManual(),
 closeLive:()=>closeLive(),

 // 点击遮罩**背景**才关闭（点弹窗内部不关）：原来写在内联里的 if(event.target===this)
 mask:(el,e)=>{if(e.target!==el)return;if(el&&el.id==='settingsMask'){try{closeSettings()}catch(_){};return}
  ({dlg:closeDlg,manual:closeManual,live:closeLive,plugins:closePlugins,ws:closeWs,panel:closePanels}[el.dataset.arg]||(()=>{}))()},
 maskGm:(el,e)=>{if(e.target===el)gmClose(null)},
 // 动态生成内容里的处理器（消息复制/代码折叠/附件移除）
 copyMsg:(el)=>copyMsg(el),
 copyCode:(el)=>copyCode(el),
 copyRaw:(el)=>copyText(el.dataset.raw||'',el),
 codeMenu:(el)=>codeMenu(el),
 codeLineSel:(el)=>codeLineSel(el),
 copySelLines:(el)=>copySelLines(el),
 stepView:(el)=>stepView(el),
 stepFilter:(el)=>stepFilterInput(el),
 exportChatMarkdown:()=>exportChatMarkdown(),
 editMsg:(el)=>editMsg(el),
 delMsg:(el)=>delMsg(el),
 kbSaveMsg:(el)=>kbSaveMsgImpl(el),
 toggleGlobalSearch:()=>toggleGlobalSearch(),
 shutdownService:()=>shutdownService(),
 restartService:()=>restartService(),
 toggleTodos:()=>toggleTodos(),
 imgZoom:(el)=>imgZoom(el),
 mCopy:(el)=>{const m=codeMenuEl;if(m)copyText(m.dataset.code||'',el);closeCodeMenu()},
 mDownload:(el)=>{
  const m=codeMenuEl;if(!m){return}
  const text=m.dataset.code||'';
  const ext=CODE_EXT[m.dataset.lang]||'txt';
  try{
   const blob=new Blob([text],{type:'text/plain;charset=utf-8'});
   const a=document.createElement('a');
   a.href=URL.createObjectURL(blob);a.download='code.'+ext;
   document.body.appendChild(a);a.click();a.remove();
   setTimeout(()=>URL.revokeObjectURL(a.href),2000);
  }catch(e){}
  closeCodeMenu()},
 mLines:()=>{
  // 偏好作用于所有代码块，不能只改当前块
  setCodePref('lines',!codePref('lines',true));
  applyCodePrefs();
  closeCodeMenu()},
 mWrap:()=>{
  setCodePref('wrap',!codePref('wrap',true));
  applyCodePrefs();
  closeCodeMenu()},
 mFold:()=>{
  const wrap=lastCodeMenuTarget;
  closeCodeMenu();
  if(!wrap)return;
  const folded=wrap.classList.toggle('collapsed');
  const b=wrap.querySelector('.md-fold');
  if(b)b.textContent=tr(folded?'展开':'收起')},
 toggleCodeFold:(el)=>toggleCodeFold(el),
 removeAttachment:(el)=>removeAttachment(el.dataset.store,Number(el.dataset.arg)),
 openModels:()=>openModels(),
 closeModels:()=>closeModels(),
 modelDelete:(el)=>modelDelete(el),
 modelCopyPath:(el)=>modelCopyPath(el),
 modelDownload:()=>modelDownload(),
};
document.addEventListener('click',e=>{
 const el=e.target.closest?e.target.closest('[data-act]'):null;
 if(!el)return;const fn=ACTIONS[el.dataset.act];if(fn)fn(el,e)});
document.addEventListener('input',e=>{
 const el=e.target.closest?e.target.closest('[data-act-input]'):null;
 if(!el)return;const fn=ACTIONS[el.dataset.actInput];if(fn)fn(el,e)});
document.addEventListener('keydown',e=>{
 const el=e.target.closest?e.target.closest('[data-act-key]'):null;
 if(!el)return;const fn=ACTIONS[el.dataset.actKey];if(fn)fn(el,e)});

/* 输入框显隐的启动同步在 main.js 的 bootInit 里做——这里**不能**调：
   本模块在 import 阶段执行，那时 window.syncComposer 还没绑上，会静默抛错。 */

/* 版本信息：侧栏底部那颗淡淡的小标签。
   数据来自 GET /（后端 app 的 version），拿不到就保持隐藏——不喧哗。 */
/* 页签标题 = 基础名 + 真实版本；幂等（先把旧的 " - v…" 尾巴去掉再拼）。 */

export { renderSandbox, loadSandbox, pickSandbox, openSettings, closeSettings, renderSettings, setSettingsTab, collectSettings, saveSettings };
