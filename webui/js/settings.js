// settings.js — 
// 从原 webui/app.js 拆出的 ES 模块（该单体已退役，见 docs/ESM-MIGRATION.md）。
import { SETTINGS_GROUPS, SANDBOX_UI, ENUM_LABEL, openMemory } from './themeboot.js';
import { toggleLiveView } from './liveview.js';
import { CODE_EXT } from './codeux.js';
import { stepFilterInput } from './steps.js';
import { exportSessions, importSessions, deleteSessionsBulk } from './sessions.js';
import { readAnimPrefs, applyAnimPrefs, setAnimPref, animPrefLabel, ANIM_RANGES } from './animprefs.js';
import { openKb, closeKb, kbPick, kbRefresh, kbReindex, kbImportUrl, kbImportText, kbInbox, kbSearch, kbDelete, fmtErr, wireKbSettings, kbLoadSettings, kbSaveSettings } from './kb.js';
import { openModels, closeModels, renderModels, modelDelete, modelCopyPath, modelDownload, modelPreset, modelStart, modelStop, modelUse, modelOrganize, modelStopAll } from './models.js';
import { kbSaveMsg as kbSaveMsgImpl, THEMES } from './composer.js';
import { openPreview, closePreview, reloadPreview, openPreviewExternal, downloadPreview, copyPreviewPath } from './preview.js';
import { providersPaneHtml, loadProviders, provNew, provEdit, provCancel, provSave, provDelete, provTest, provClearKey, provSetDefault, provOauth, provOauthCancel, provOauthDisconnect, provGetKey, provWebchatOpen } from './providers.js';

/* 设置左侧的“工具页”：不弹窗，直接把对应面板的 .dlg 移进设置右侧渲染。 */
const TOOL_TABS = [
 {id:'models', label:'模型', mask:'modelsMask', run:()=>openModels()},
 {id:'plugins', label:'插件管理', mask:'pluginsMask', run:()=>window.openPlugins(),
  extra:[{mask:'panelMask', run:()=>window.openPanels(), title:'插件面板'}]},
 {id:'kb', label:'知识库', mask:'kbMask', run:()=>openKb()},
 {id:'memory', label:'记忆面板', mask:'memMask', run:()=>window.openMemory()},
];

function _modelsExtraHtml(){
 return `<h5 class="setsection">${esc(tr('Embedding / OCR'))}</h5>
  <label class="setrow"><span>${esc(tr('Embedding 后端'))}</span>
   <select id="kbEmbBackend" class="kbinput">
    <option value="fastembed">fastembed</option>
    <option value="local">local</option>
    <option value="http">http</option>
    <option value="hash">hash</option>
    <option value="off">off</option>
   </select></label>
  <div class="setrow"><span>${esc(tr('Embedding 模型'))}</span>
   <div style="display:flex;gap:6px;min-width:0">
    <select id="kbEmbModel" class="kbinput" style="flex:1;min-width:0"></select>
    <input id="kbEmbModelCustom" class="kbinput" placeholder="${esc(tr('模型名 / GGUF 路径 / model id'))}" style="flex:1;min-width:0;display:none">
    <button type="button" class="btn ghost" id="kbEmbModelToggle">${esc(tr('自定义'))}</button>
   </div></div>
  <label class="setrow"><span>${esc(tr('OCR 引擎'))}</span>
   <select id="kbOcrEngine" class="kbinput">
    <option value="auto">auto</option>
    <option value="rapidocr">rapidocr</option>
    <option value="xiaomi">xiaomi</option>
   </select></label>
  <div id="kbSettingsHint" class="meta" style="margin-top:4px">${esc(tr('加载中…'))}</div>`;
}
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
  return `<label class="setrow cfgrow" for="${id}"><input type="checkbox" id="${id}" data-cfg="${esc(key)}" ${val?'checked':''}>
    <span>${esc(tr(spec.label))}</span></label>`;
 }
 if(spec.kind==='enum'){
  const opts=spec.values.map(v=>{
   const label=ENUM_LABEL[v]||v;
   return `<option value="${esc(v)}" ${String(val)===v?'selected':''}>${esc(tr(label))}</option>`;
  }).join('');
  return `<label class="setrow cfgrow" for="${id}"><span>${esc(tr(spec.label))}</span>
    <select id="${id}" data-cfg="${esc(key)}">${opts}</select></label>`;
 }
 const t=spec.kind==='int'?'number':'text';
 // 没配过的键显示"默认"占位：空输入框会让人以为坏了/以为必须填
 const ph=(val===undefined||val===null||val==='')?`placeholder="${esc(tr('默认'))}"`:'';
 return `<label class="setrow cfgrow" for="${id}"><span>${esc(tr(spec.label))}</span>
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
 const has=(gid)=>gid==='ui'||gid==='identity'||gid==='danger'||(groups[gid]||[]).length;
 const _AP=readAnimPrefs();                  // 动画参数（模板里铺滑杆的当前值）
 const visible=SETTINGS_GROUPS.filter(([gid])=>has(gid));
 const allTabs=visible.map(([gid])=>gid).concat(TOOL_TABS.map(t=>'tool-'+t.id)).concat(['sessions']);
 if(!allTabs.includes(window.settingsTab))window.settingsTab=visible[0][0];

 // 左导航：常规配置 + 工具页；危险区固定放最后
 const normal=visible.filter(([gid])=>gid!=='danger');
 const danger=visible.find(([gid])=>gid==='danger');
 const tabsHtml=normal.map(([gid,label])=>{
  const on=gid===window.settingsTab;
  return `<button type="button" class="setnavbtn${on?' on':''}"
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
 const dangerOn=danger&&danger[0]===window.settingsTab;
 const dangerNavHtml=danger?`<div class="setnavdanger-sep"></div><button type="button"
   class="setnavbtn setnavdanger danger${dangerOn?' on':''}" role="tab"
   aria-selected="${dangerOn?'true':'false'}" data-act="settingsTab" data-arg="${danger[0]}">
   ${esc(tr(danger[1]))}</button>`:'';
 const sessOn='sessions'===window.settingsTab;
 const sessNavHtml=`<button type="button" class="setnavbtn${sessOn?' on':''}"
    role="tab" aria-selected="${sessOn?'true':'false'}" data-act="settingsTab" data-arg="sessions">
    ${esc(tr('会话'))}</button>`;
 nav.innerHTML=tabsHtml+sessNavHtml+`<div class="setnavsep"></div>`+toolsHtml+dangerNavHtml;

 // 右内容：所有分组都渲染，非当前的隐藏（值仍留在 DOM，保存不会丢）
 const pane=(gid,html)=>`<section class="setgroup${gid==='danger'?' danger':''}" data-pane="${gid}"
    style="${gid===window.settingsTab?'':'display:none'}">${html}</section>`;

 const uiHtml=`<h4>${esc(tr('外观'))}</h4>
   <div class="theme-grid" id="themeGrid">${THEMES
     .map(([v,t])=>`<button type="button" class="theme-card${themeChoice()===v?' on':''}" data-theme-choice="${v}">
       <span class="theme-swatch theme-swatch-${v||'dark'}"></span>
       <span class="theme-name">${esc(tr(t))}</span>
     </button>`).join('')}</div>
   <input type="hidden" id="setTheme" data-ui="theme" value="${themeChoice()}">
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

 // 身份页：Soul（部署级自我）与 Persona（可切换表达风格）。
 // 数据来自 /v1/agent/soul 与 /v1/agent/personas；"本次会话"选择存 localStorage，
 // 由 apistate.api() 作为 X-Gantry-Persona 头带给后端（config 里的默认值走 /v1/config）。
 const identityHtml=`<h4>${esc(tr('Persona（表达风格）'))}</h4>
   <label class="setrow"><span>${esc(tr('默认 Persona（对所有任务生效）'))}</span>
     <select id="idPersonaDefault" class="kbinput"><option value="">${esc(tr('加载中…'))}</option></select></label>
   <label class="setrow"><span>${esc(tr('本次会话（只影响这个浏览器）'))}</span>
     <select id="idPersonaSession" class="kbinput"><option value="">${esc(tr('跟随默认'))}</option></select></label>
   <p class="sethint">${esc(tr('Persona 只改“怎么说”（语气/节奏/详略）；“信什么”由 Soul 决定，两者冲突以 Soul 为准。'))}</p>
   <h4>${esc(tr('Soul（部署级自我，SOUL.md）'))}</h4>
   <p class="sethint" id="idSoulPath"></p>
   <textarea id="idSoulText" class="kbinput" rows="10" spellcheck="false" placeholder="${esc(tr('留空 = 使用内置默认 Soul；填写后覆盖内置身份（建议 ≤40 行）'))}"></textarea>
   <p class="sethint" id="idSoulMeta"></p>
   <p class="sethint warn" id="idSoulLint"></p>
   <div class="setbtns">
     <button type="button" class="btn" data-act="soulSave">${esc(tr('保存 Soul'))}</button>
     <button type="button" class="btn ghost" data-act="soulReset">${esc(tr('恢复内置默认'))}</button>
   </div>
   <h5 class="setsection">${esc(tr('Soul 历史（改动前自动快照，可回滚）'))}</h5>
   <div id="idSoulHistory" class="meta">${esc(tr('加载中…'))}</div>
   <h4>${esc(tr('Persona 文本'))}</h4>
   <label class="setrow"><span>${esc(tr('编辑哪个'))}</span>
     <select id="idPersonaEdit" class="kbinput"></select></label>
   <label class="setrow"><span>${esc(tr('另存为（名称）'))}</span>
     <input id="idPersonaName" class="kbinput" placeholder="my-style"></label>
   <textarea id="idPersonaText" class="kbinput" rows="8" spellcheck="false"></textarea>
   <p class="sethint" id="idPersonaHint"></p>
   <div class="setbtns">
     <button type="button" class="btn" data-act="personaSaveWs">${esc(tr('保存到工作区'))}</button>
     <button type="button" class="btn ghost" data-act="personaSaveGlobal">${esc(tr('保存到全局'))}</button>
     <button type="button" class="btn ghost danger" data-act="personaDelete">${esc(tr('删除工作区副本'))}</button>
   </div>`;

 const dangerHtml=`<h4>${esc(tr('危险区'))}</h4>
   <div class="setbtns">
    <button type="button" class="btn ghost" data-act="restartService">↻ ${esc(tr('重启服务'))}</button>
    <button type="button" class="btn ghost danger" data-act="shutdownService">⏻ ${esc(tr('终止服务 / 退出'))}</button>
   </div>
   <p class="sethint">${esc(tr('重启/终止都会中断正在跑的任务；状态已落库，重启后可恢复。'))}</p>
   <h4>${esc(tr('更新'))}</h4>
   <div id="updatePanel">
    <div class="setbtns">
     <button type="button" class="btn ghost" data-act="checkUpdate">⟳ ${esc(tr('检查更新'))}</button>
     <button type="button" class="btn ghost danger" data-act="rollbackUpdate">↶ ${esc(tr('回滚最近更新'))}</button>
    </div>
    <p class="sethint" id="updateHint">${esc(tr('检查 GitHub 上的新版本；更新会先备份代码，可回滚'))}</p>
   </div>`;

 // 会话管理：直接列出现有会话（勾选/状态/标题/ID），工具条提供导出所选、
 // 导入、全选、清空。用户要求在这里能直接看到会话，而不是只有导出导入按钮。
 const sessionsHtml=`<h4>${esc(tr('会话导入导出'))}</h4>
   <p class="sethint">${esc(tr('导出为 JSON 文件，可导入本机或其它部署；导入一律生成 imp- 新会话，不覆盖现有数据。'))}</p>
   <div class="setbtns">
    <button type="button" class="btn" data-act="sessMgrExport">⇩ ${esc(tr('导出所选会话'))}</button>
    <button type="button" class="btn ghost" data-act="importSessions">⇧ ${esc(tr('导入会话'))}</button>
    <button type="button" class="btn ghost" data-act="sessMgrAll">${esc(tr('全选'))}</button>
    <button type="button" class="btn ghost" data-act="sessMgrNone">${esc(tr('清空'))}</button>
    <button type="button" class="btn ghost danger" data-act="sessMgrDelSel">🗑 ${esc(tr('删除所选'))}</button>
   </div>
   <div id="sessMgrList" class="sesspick" style="margin-top:10px;max-height:46vh;overflow:auto"></div>`;

 function _cfgSection(gid,path){
  if(gid==='exec'){
   const leaf=path.split('.').pop();
   if(/^allow_/.test(leaf)||path.includes('tools_extra'))return '工具权限';
   if(path.includes('approval'))return '审批';
   if(path.includes('memory')||path.includes('agent_md'))return '记忆与提示词';
   if(path.includes('session')||path.includes('compact')||path.includes('rollover')||
      path.includes('context')||path.includes('keep_recent'))return '上下文与会话';
   if(path.includes('subagent'))return '子代理';
   if(path.includes('run_code')||path.includes('python'))return '代码执行';
   if(path.includes('outlook'))return 'Outlook';
   if(path.includes('service_')||path.includes('copilot'))return 'Copilot 服务';
   return '';
  }
  if(gid==='browser'){
   if(path.includes('timeout')||path.includes('stable')||path.includes('poll'))return '超时与轮询';
   if(path.includes('startup')||path.includes('headless'))return '启动方式';
   if(path.includes('csrf'))return '安全';
   return '';
  }
  if(gid==='data')return '';
  return '';
 }
 const cfgPane=(gid,label)=>{
  const order=gid==='exec'
   ? ['','工具权限','审批','记忆与提示词','上下文与会话','子代理','代码执行','Outlook','Copilot 服务']
   : gid==='browser' ? ['','启动方式','超时与轮询','安全'] : [''];
  const bucket=new Map(order.map(s=>[s,[]]));
  (groups[gid]||[]).forEach(([k,spec])=>{
   const sec=_cfgSection(gid,k)||'';
   if(!bucket.has(sec))bucket.set(sec,[]);
   bucket.get(sec).push([k,spec]);
  });
  const rows=order.filter(s=>bucket.get(s)&&bucket.get(s).length).map(sec=>{
   const body=bucket.get(sec).map(([k,spec])=>_cfgInput(k,spec,window.settingsValues[k])).join('');
   return (sec?`<h5 class="setsection">${esc(tr(sec))}</h5>`:'')+body;
  }).join('');
  return rows?`<h4>${esc(tr(label))}</h4>${rows}`:'';
 };

 box.innerHTML=visible.map(([gid,label])=>{
  if(gid==='ui')return pane('ui',uiHtml);
  if(gid==='identity'){
   // 身份页 = 自定义编辑器 + 白名单开关（agent.persona 由上方下拉框负责，避免两个控件打架）
   const rows=(groups.identity||[]).filter(([k])=>k!=='agent.persona')
     .map(([k,spec])=>_cfgInput(k,spec,window.settingsValues[k])).join('');
   const extra=rows?`<h5 class="setsection">${esc(tr('子代理与来源'))}</h5>${rows}`:'';
   return pane('identity',identityHtml+extra);
  }
  if(gid==='danger'){
   // 危险区：先渲染 schema 里登记的高风险开关（如"允许自升级"），再放操作按钮
   const rows=(groups[gid]||[]).map(([k,spec])=>_cfgInput(k,spec,window.settingsValues[k])).join('');
   return pane('danger',(rows?`<h4>${esc(tr('高风险设置'))}</h4>${rows}`:'')+dangerHtml);
  }
  return pane(gid,cfgPane(gid,label));
 }).join('')+TOOL_TABS.map((t)=>{
  const id='tool-'+t.id;
  const extras=(t.extra||[]).map((x,i)=>
   `<h5 class="setsection">${esc(tr(x.title||''))}</h5><div class="settings-embed" id="embed-${id}-extra-${i}"></div>`
  ).join('');
  let inner=`<div class="settings-embed" id="embed-${id}"></div>`;
  if(t.id==='models'){
   // 提供商配置属于"模型这一节"：子页签挂在本地模型里，而不是另开一个顶层分组
   inner=`<div class="subtabs" id="modelsSubtabs">
    <button type="button" class="btn seg on" data-sub="local">${esc(tr('本地模型'))}</button>
    <button type="button" class="btn seg" data-sub="prov">${esc(tr('模型提供商'))}</button>
   </div>
   <div class="subpane" data-subpane="local"><div class="settings-embed" id="embed-${id}"></div><div class="settings-tool-extra" id="modelsEmbedOcr"></div></div>
   <div class="subpane" data-subpane="prov" style="display:none">${providersPaneHtml()}</div>`;
  }else if(t.id==='plugins'){
   inner=`<div class="subtabs" id="pluginsSubtabs">
    <button type="button" class="btn seg on" data-sub="plugins">${esc(tr('插件'))}</button>
    <button type="button" class="btn seg" data-sub="panels">${esc(tr('插件面板'))}</button>
   </div>
   <div class="subpane" data-subpane="plugins"><div class="settings-embed" id="embed-${id}"></div></div>
   <div class="subpane" data-subpane="panels" style="display:none"><div class="settings-embed" id="embed-${id}-extra-0"></div></div>`;
  }else if(t.extra){
   inner=`<div class="settings-embed" id="embed-${id}"></div>${extras}`;
  }
  return `<section class="setgroup settings-toolpane" data-pane="${id}"
    style="${id===window.settingsTab?'':'display:none'}">${inner}</section>`;
 }).join('')+pane('sessions',sessionsHtml);
 try{translateAttrs(box);translateAttrs(nav)}catch(e){}
 const themeGrid=document.getElementById('themeGrid');
 if(themeGrid){
  themeGrid.querySelectorAll('[data-theme-choice]').forEach(btn=>{
   btn.onclick=()=>{
    const v=btn.dataset.themeChoice;
    const hidden=document.getElementById('setTheme');
    if(hidden)hidden.value=v;
    try{applyTheme(v,true)}catch(e){}
    themeGrid.querySelectorAll('[data-theme-choice]').forEach(x=>x.classList.toggle('on',x===btn));
   };
  });
 }
 if(String(window.settingsTab||'').startsWith('tool-')) mountTool(window.settingsTab);
 // 身份页数据（persona 列表 / Soul 文本）：只在页签存在时拉，失败静默
 if(document.getElementById('idPersonaDefault')){ try{ loadIdentity() }catch(e){} }
 // 会话管理列表：设置页刚渲染时填一次（之后每次切到该页签由 setSettingsTab 刷新）
 if(document.getElementById('sessMgrList'))renderSessMgr();
}
/* ── 身份页：Soul / Persona ─────────────────────────────────────────
   Soul：部署级自我，只有一份（SOUL.md）；Persona：可切换表达风格（工作区可覆盖）。
   "本次会话"选择存 localStorage → apistate 的请求头 X-Gantry-Persona。 */
let _identityPersonas=[];
function _identityOpts(sel, withFollow){
 const items=_identityPersonas.map(it=>{
  const label=it.name+(it.source!=='builtin'?`（${it.source}）`:'');
  return `<option value="${esc(it.name)}" ${it.name===sel?'selected':''}>${esc(label)}</option>`;
 }).join('');
 return (withFollow?`<option value="" ${sel?'':'selected'}>${esc(tr('跟随默认'))}</option>`:'')+items;
}
async function loadIdentity(){
 const dflt=document.getElementById('idPersonaDefault');
 const sess=document.getElementById('idPersonaSession');
 const edit=document.getElementById('idPersonaEdit');
 try{
  const r=await api('/v1/agent/personas');
  _identityPersonas=r.items||[];
  const cur=r.selected||'default';
  const sessCur=(()=>{try{return localStorage.getItem('gantry-persona')||''}catch(e){return ''}})();
  if(dflt)dflt.innerHTML=_identityOpts(cur,false);
  // 有打开的会话时，"本次会话"显示的是会话记录里的 persona（权威值）
  let sessVal=sessCur;
  if(window.current){
   try{
    const st=(window.sessCache||[]).find(x=>x.id===window.current);
    if(st)sessVal=st.persona||'';
    else{const d=await api('/v1/agent/sessions/'+encodeURIComponent(window.current));sessVal=(d&&d.persona)||'';}
   }catch(e){sessVal=''}
  }
  if(sess){sess.innerHTML=_identityOpts(sessVal,true);
   sess.onchange=async()=>{
    // 有当前会话：写会话记录（权威来源，cron/续跑不漂移）；否则只记 localStorage，
    // 作为下次创建会话时的请求头选择。
    const sid=window.current||'';
    if(sid){
     try{
      const r=await api('/v1/agent/sessions/'+encodeURIComponent(sid)+'/persona',
       {method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({persona:sess.value||''})});
      toast(sess.value?tr('本会话使用 {m}',{m:sess.value}):tr('本会话跟随默认 Persona'),'ok');
      try{localStorage.removeItem('gantry-persona')}catch(e){}
      const _pc=document.getElementById('personaChip');
      if(_pc){_pc.textContent=(sess.value?('🎭 '+sess.value):('🎭 '+tr('默认')));}
      return r;
     }catch(e){toast(tr('切换失败: ')+fmtErr(e),'err');return}
    }
    try{
     if(sess.value)localStorage.setItem('gantry-persona',sess.value);
     else localStorage.removeItem('gantry-persona');
    }catch(e){}
    toast(sess.value?tr('新会话将使用 {m}',{m:sess.value}):tr('新会话跟随默认 Persona'),'ok');
   };}
  if(dflt){dflt.onchange=async()=>{
    try{
     await api('/v1/config',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({values:{'agent.persona':dflt.value},persist:true})});
     toast(tr('默认 Persona 已切换为 {m}',{m:dflt.value}),'ok');
    }catch(e){toast(tr('切换失败: ')+fmtErr(e),'err')}
   };}
  if(edit){edit.innerHTML=_identityOpts(cur,false);
   edit.onchange=()=>loadPersonaText(edit.value);}
 }catch(e){}
 try{
  const s=await api('/v1/agent/soul');
  const ta=document.getElementById('idSoulText');
  if(ta)ta.value=s.exists?(s.text||''):'';
  const hint=document.getElementById('idSoulPath');
  if(hint)hint.textContent=s.exists?(tr('当前来源：')+s.path):(tr('当前使用内置默认 Soul；保存后写入 ')+s.path);
  _renderSoulMeta(s); _renderLint('idSoulLint', s.warnings);
 }catch(e){}
 loadSoulHistory();
 if(edit&&edit.value)loadPersonaText(edit.value);
}
function _renderSoulMeta(s){
 const el=document.getElementById('idSoulMeta');
 if(!el)return;
 const eff=String(s&&s.effective_text||'').length;
 el.textContent=tr('生效文本 {n} 字符，约 {t} token/回合（上限 {m} 字符）',
  {n:String(eff),t:String(s&&s.tokens||0),m:String(s&&s.max_chars||0)});
}
function _renderLint(id, warns){
 const el=document.getElementById(id);
 if(!el)return;
 const list=(warns||[]);
 el.textContent=list.length?(tr('检查提示：')+list.join('；')):'';
 el.style.display=list.length?'':'none';
}
async function loadSoulHistory(){
 const host=document.getElementById('idSoulHistory');
 if(!host)return;
 try{
  const r=await api('/v1/agent/soul/history');
  const items=r.items||[];
  if(!items.length){host.textContent=tr('暂无历史（第一次改 Soul 时会自动快照）');return}
  host.innerHTML=items.slice(0,8).map(x=>
   `<div class="setrow"><span>${esc(x.id)} · ${esc(tr('{n} 字符',{n:String(x.chars)}))}</span>
    <button type="button" class="btn ghost" data-act="soulRestore" data-arg="${esc(x.id)}">${esc(tr('回滚'))}</button></div>`
  ).join('');
 }catch(e){host.textContent=''}
}
async function _soulRestore(id){
 if(!id)return;
 if(!confirm(tr('回滚到 {id}？当前版本会先自动快照，可再撤销。',{id})))return;
 try{
  const r=await api('/v1/agent/soul/restore',{method:'POST',
   headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});
  toast(tr('已回滚到 {id}',{id}),'ok');
  _renderLint('idSoulLint', r.warnings);
  loadIdentity();
 }catch(e){toast(tr('回滚失败: ')+fmtErr(e),'err')}
}
async function loadPersonaText(name){
 if(!name)return;
 try{
  const r=await api('/v1/agent/personas/'+encodeURIComponent(name));
  const ta=document.getElementById('idPersonaText');
  const hint=document.getElementById('idPersonaHint');
  if(ta)ta.value=r.text||'';
  const wsWarn=r.source==='workspace'?tr('来源：当前工作区（仓库里的这个文件会进入系统提示词，属提示词注入面，确认可信再用）'):
    tr('来源：{s}；注入约 {t} token；实际截断到 {n} 字符',{s:r.source,t:String(r.tokens||0),n:String(r.max_chars||0)});
  if(hint){hint.textContent=wsWarn;hint.classList.toggle('warn',r.source==='workspace')}
  _renderLint('idSoulLint', []);
  const fl=r.fields&&Object.keys(r.fields).length?tr('结构化：')+Object.entries(r.fields).map(([k,v])=>k+'='+v).join('，'):'';
  if(fl&&hint)hint.textContent=wsWarn+' · '+fl;
  if(r.source==='workspace'){
   const key='gantry-wspersona-ok:'+name;
   try{
    if(localStorage.getItem(key)!=='1'&&confirm(tr('{m} 来自当前工作区（可能是 clone 来的仓库文件）。确认可信并继续？',{m:name})))localStorage.setItem(key,'1');
   }catch(e){}
  }
 }catch(e){toast(tr('读取 persona 失败: ')+fmtErr(e),'err')}
}
async function _soulSave(restore){
 const ta=document.getElementById('idSoulText');
 const text=restore?'':(ta?ta.value:'');
 try{
  const r=await api('/v1/agent/soul',{method:'PUT',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({text})});
  toast(r.restored_default?tr('已恢复内置默认 Soul'):tr('Soul 已保存'),'ok');
  _renderLint('idSoulLint', r.warnings);
  if(restore&&ta)ta.value='';
  loadIdentity();
 }catch(e){toast(tr('保存 Soul 失败: ')+fmtErr(e),'err')}
}
async function _personaSave(scope){
 const nameEl=document.getElementById('idPersonaName');
 const editEl=document.getElementById('idPersonaEdit');
 const ta=document.getElementById('idPersonaText');
 const name=String((nameEl&&nameEl.value)||'').trim()||(editEl&&editEl.value)||'';
 if(!name){toast(tr('先填 persona 名称'),'err');return}
 try{
  await api('/v1/agent/personas/'+encodeURIComponent(name),{method:'PUT',
   headers:{'Content-Type':'application/json'},
   body:JSON.stringify({text:ta?ta.value:'',scope})});
  toast(tr('Persona 已保存到{s}',{s:scope==='global'?tr('全局'):tr('工作区')}),'ok');
  loadIdentity();
 }catch(e){toast(tr('保存 persona 失败: ')+fmtErr(e),'err')}
}
async function _personaDelete(){
 const editEl=document.getElementById('idPersonaEdit');
 const name=(editEl&&editEl.value)||'';
 if(!name)return;
 if(!confirm(tr('删除工作区里的 {m} 副本？内置与全局副本不受影响。',{m:name})))return;
 try{
  await api('/v1/agent/personas/'+encodeURIComponent(name),{method:'DELETE',
   headers:{'Content-Type':'application/json'},body:JSON.stringify({scope:'workspace'})});
  toast(tr('已删除工作区副本'),'ok'); loadIdentity();
 }catch(e){toast(tr('删除失败: ')+fmtErr(e),'err')}
}
function _settingsTheme(){
 try{const t=localStorage.getItem('gantry-theme');return (!t||t==='auto')?'auto':t}catch(e){return 'auto'}
}
function _toolById(id){return TOOL_TABS.find(t=>'tool-'+t.id===id)||null}

function _mountMask(maskId,hostId,run){
 let mask=document.getElementById(maskId);
 if(!mask){try{run()}catch(e){}}
 mask=document.getElementById(maskId);
 const dlg=mask&&mask.querySelector('.dlg');
 const host=document.getElementById(hostId);
 if(dlg&&host)host.appendChild(dlg);
 if(dlg){dlg.classList.add('settings-embed-dlg');dlg.classList.add('embed-'+maskId)}
 try{run()}catch(e){}
 if(mask){mask.classList.remove('open');mask.style.display='none'}
}

function _unmountMask(maskId,hostId){
 const host=document.getElementById(hostId);
 const dlg=host&&host.querySelector('.dlg');
 const mask=document.getElementById(maskId);
 if(dlg&&mask)mask.appendChild(dlg);
 if(mask){mask.classList.remove('open');mask.style.display='none'}
}

function unmountTool(id){
 const t=_toolById(id);if(!t)return;
 _unmountMask(t.mask,'embed-'+id);
 (t.extra||[]).forEach((x,i)=>_unmountMask(x.mask,'embed-'+id+'-extra-'+i));
}

function unmountAllTools(){
 TOOL_TABS.forEach(t=>unmountTool('tool-'+t.id));
}

function mountTool(id){
 const t=_toolById(id);if(!t)return;
 unmountAllTools();
 _mountMask(t.mask,'embed-'+id,t.run);
 (t.extra||[]).forEach((x,i)=>_mountMask(x.mask,'embed-'+id+'-extra-'+i,x.run));
 if(t.id==='models'){
  const host=document.getElementById('modelsEmbedOcr');
  if(host&&!host.dataset.ready){host.innerHTML=_modelsExtraHtml();host.dataset.ready='1'}
  try{wireKbSettings();kbLoadSettings()}catch(e){}
  // 子页签：本地模型 / 模型提供商
  const pane=document.querySelector('[data-pane="tool-models"]');
  const mTabs=[...(pane?pane.querySelectorAll('#modelsSubtabs [data-sub]'):[])];
  const mPanes=[...(pane?pane.querySelectorAll('.subpane'):[])];
  mTabs.forEach(b=>{b.onclick=()=>{
   mTabs.forEach(x=>x.classList.toggle('on',x===b));
   mPanes.forEach(p=>{p.style.display=(p.dataset.subpane===b.dataset.sub)?'':'none'});
   if(b.dataset.sub==='prov'){ try{ loadProviders() }catch(e){} }
   // 切子页签时回到顶部：否则会停在"本地模型"列表的滚动位置，看不到提供商页头
   try{const pn=b.closest('.setgroup');const host=pn&&(pn.closest('.dlg-body')||pn.parentElement);if(host)host.scrollTop=0}catch(e){}
  }});
  try{ loadProviders() }catch(e){}
 }
 if(t.id==='plugins'){
  const pane=document.querySelector('[data-pane="tool-plugins"]');
  const tabs=[...(pane?pane.querySelectorAll('#pluginsSubtabs [data-sub]'):[])];
  const subpanes=[...(pane?pane.querySelectorAll('.subpane'):[])];
  tabs.forEach(b=>{b.onclick=()=>{
   tabs.forEach(x=>x.classList.toggle('on',x===b));
   subpanes.forEach(p=>{p.style.display=(p.dataset.subpane===b.dataset.sub)?'':'none'});
  }});
 }
}

/* ── 设置页「会话」管理列表：直接列出顶层会话（勾选/状态/标题/ID） ──
   勾选只存在于设置页内，侧栏因此保持干净（无常驻勾选框/图标）。
   stateIcon/stateLabel/stateClassOf 是 main.js 挂到 window 的全局。 */
function renderSessMgr(){
 const box=document.getElementById('sessMgrList');
 if(!box)return;
 if(!(window.sessCache||[]).length){
  // 缓存为空时（罕见：侧栏还没刷出来）主动拉一次，别给用户看空列表
  api('/v1/agent/sessions?include_subagents=true')
   .then(d=>{window.sessCache=d.sessions||[];renderSessMgr()})
   .catch(()=>{});
  return;
 }
 // 只列顶层会话（无 parent_id）：子代理随父会话一起导出/删除，单列只会造成困惑。
 // 删除按钮放在 label 外面：放里面点击会连带切换勾选框。
 const items=window.sessCache.filter(s=>!String(s.parent_id||''));
 box.innerHTML=items.map(s=>
  `<div class="spickrow"><label class="spickmain"><input type="checkbox" value="${esc(s.id)}" data-sessmgr>
   <span class="sstat ${esc(stateClassOf(s))}" title="${esc(stateLabel(s))}" aria-label="${esc(stateLabel(s))}">${stateIcon(s.state)}</span>
   <span class="spicktitle">${esc(s.title||s.id)}</span><code>${esc(s.id)}</code></label>
   <button type="button" class="smdel" title="${esc(tr('删除会话'))}" data-act="sessMgrDel" data-arg="${esc(s.id)}">🗑</button></div>`).join('')
  ||`<p class="sethint">${esc(tr('没有可导出的会话'))}</p>`;
}
/* 批量删除勾选的会话：运行中/排队中的跳过。删除后由 deleteSessionsBulk
   里的 refreshSessions 更新缓存，这里重绘管理列表。 */
async function sessMgrDeleteSel(){
 const live=new Set(['running','waiting','queued']);
 const picked=[...document.querySelectorAll('#sessMgrList input[data-sessmgr]:checked')].map(c=>c.value);
 const all=(window.sessCache||[]).filter(s=>picked.includes(s.id));
 const sids=all.filter(s=>!live.has(String(s.state||''))).map(s=>s.id);
 const skipped=all.length-sids.length;
 if(!sids.length){toast(tr('该组会话都在运行中，先停止再删除'),'err');return}
 if(!await dlgConfirm(tr('删除「{label}」组的 {n} 个会话？\n\n会先删本地记录，再尽力清理对应的 Copilot 网页对话。此操作不可撤销。',
                        {label:tr('所选'),n:sids.length}),tr('批量删除'),true))return;
 const r=await deleteSessionsBulk(sids);
 toast(tr('批量删除完成：成功 {ok}，失败 {fail}',{ok:r.ok,fail:r.fail}),r.fail?'err':'ok');
 if(skipped)toast(tr('已跳过运行中的 {n} 个会话',{n:skipped}),'err');
 renderSessMgr();
}
function sessMgrSetAll(on){
 document.querySelectorAll('#sessMgrList input[data-sessmgr]').forEach(c=>{c.checked=on});
}
function sessMgrExport(){
 const sids=[...document.querySelectorAll('#sessMgrList input[data-sessmgr]:checked')]
  .map(c=>c.value);
 exportSessions(sids);
}
function setSettingsTab(id){
 const known=SETTINGS_GROUPS.map(g=>g[0]);
 const isTool=String(id).startsWith('tool-');
 if(id!=='sessions'&&!known.includes(id)&&!isTool)return;
 window.settingsTab=id;
 document.querySelectorAll('#settingsNav .setnavbtn').forEach(b=>{
  const on=b.dataset.arg===id;
  b.classList.toggle('on',on);b.setAttribute('aria-selected',on?'true':'false');
 });
 document.querySelectorAll('#settingsBody .setgroup[data-pane]').forEach(p=>{
  p.style.display=(p.dataset.pane===id)?'':'none';
 });
 if(isTool)mountTool(id);else unmountAllTools();
 if(id==='sessions')renderSessMgr();   // 每次进「会话」页都刷新列表
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
 // Embedding / OCR 控件在本地模型页里：跟随全局保存一起提交
 if(document.getElementById('kbEmbBackend')){
  try{await kbSaveSettings(true)}
  catch(e){toast(tr('保存失败: ')+fmtErr(e),'err',8000);return}
 }
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

async function _waitHealth(ms){
 const t0=Date.now();
 while(Date.now()-t0<ms){
  try{
   const r=await fetch('/health',{cache:'no-store'});
   if(r.ok){await r.json();return true}
  }catch(e){}
  await new Promise(r=>setTimeout(r,1000));
 }
 return false;
}

/* 更新遮罩：安装期间整屏阻塞（防重复点击/误操作），成功后靠页面刷新自然消失。 */
function _updateMask(text){
 let el=document.getElementById('updateMask');
 if(!el){el=document.createElement('div');el.id='updateMask';
  el.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.72);z-index:10000;display:flex;align-items:center;justify-content:center';
  document.body.appendChild(el)}
 el.innerHTML='<div style="text-align:center;color:#fff">'
  +'<div style="font-size:42px;margin-bottom:14px">⏳</div>'
  +'<div id="updateMaskText" style="font-size:16px;line-height:1.7"></div></div>';
 _updateMaskText(text);
 el.style.display='flex';
}
function _updateMaskText(t){const e=document.getElementById('updateMaskText');if(e)e.textContent=t}
function _updateMaskHide(){const el=document.getElementById('updateMask');if(el)el.style.display='none'}

/* 检查 GitHub 更新：只检查不自动装；用户确认后才安装并等 /health 恢复。 */
async function checkUpdate(){
 try{
  const r=await api('/v1/update/check');
  const hint=document.getElementById('updateHint');
  if(hint)hint.textContent=tr('当前版本 {a}，最新 {b}',{a:r.current||'?',b:r.latest||'?'});
  if(!r.update_available){toast(tr('已是最新版本'),'ok');return}
  const ok=await window.dlgConfirm(
   tr('发现新版本 {a} → {b}（{m}）。更新前会备份代码，服务将重启。确定更新？',
      {a:r.current,b:r.latest,m:r.method}),tr('发现新版本'),false);
  if(!ok)return;
  _updateMask(tr('正在下载并安装更新 {v}，请勿关闭页面…',{v:r.latest}));
  const d=await api('/v1/update/apply',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({version:r.latest,method:r.method,restart:true})});
  _updateMaskText(tr('已更新到 {v}，等待服务重启，完成后本页会自动刷新…',{v:d.target||r.latest}));
  if(await _waitHealth(120000)){location.reload();return}
  _updateMaskHide();
  toast(tr('服务未在 120 秒内恢复，请查看 data/logs/crash.log'),'err');
 }catch(e){_updateMaskHide();toast(tr('更新失败: ')+((e&&(e.text||e.detail||e.message))||e),'err')}
}

async function rollbackUpdate(){
 if(!await window.dlgConfirm(tr('确定回滚最近一次代码更新并重启服务？'),tr('回滚最近更新'),true))return;
 try{
  _updateMask(tr('正在回滚更新，请勿关闭页面…'));
  const d=await api('/v1/update/rollback',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({restart:true})});
  _updateMaskText(tr('已回滚：{b}，等待服务重启，完成后本页会自动刷新…',{b:d.backup||''}));
  if(await _waitHealth(120000)){location.reload();return}
  _updateMaskHide();
  toast(tr('服务未在 120 秒内恢复，请查看 data/logs/crash.log'),'err');
 }catch(e){_updateMaskHide();toast(tr('回滚失败: ')+((e&&(e.text||e.detail||e.message))||e),'err')}
}

/** 侧栏版本号点击：直接打开设置的危险区，并滚到更新面板。 */
async function openUpdateSettings(){
 try{await openSettings()}catch(e){return}
 window.settingsTab='danger';
 setSettingsTab('danger');
 const info=window.gantryUpdateInfo||null;
 const hint=document.getElementById('updateHint');
 if(hint&&info&&info.update_available){
  hint.textContent=tr('当前版本 {a}，最新 {b}',{a:info.current||'?',b:info.latest||'?'});
 }
 try{document.getElementById('updatePanel')?.scrollIntoView({block:'nearest'})}catch(e){}
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
 toggleLiveView:()=>toggleLiveView(),
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
 stepsLoadMore:()=>{if(window.stepLoadMore)window.stepLoadMore()},
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
 importSessions:()=>importSessions(),
 sessMgrExport:()=>sessMgrExport(),
 sessMgrAll:()=>sessMgrSetAll(true),
 sessMgrNone:()=>sessMgrSetAll(false),
 sessMgrDelSel:()=>sessMgrDeleteSel(),
 sessMgrDel:(el)=>{const sid=el&&el.dataset?el.dataset.arg:'';
  if(sid)window.removeSession(false,sid).then(()=>renderSessMgr())},
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
 checkUpdate:()=>checkUpdate(),
 rollbackUpdate:()=>rollbackUpdate(),
 openUpdateSettings:()=>openUpdateSettings(),
 // 身份页（Soul / Persona）
 soulSave:()=>_soulSave(false),
 soulReset:()=>_soulSave(true),
 personaSaveWs:()=>_personaSave('workspace'),
 personaSaveGlobal:()=>_personaSave('global'),
 personaDelete:()=>_personaDelete(),
 soulRestore:(el)=>_soulRestore(el.dataset.arg||''),
 openPersonaSettings:()=>{try{openSettings()}catch(e){};try{setSettingsTab('identity')}catch(e){}} ,
 toggleTodos:()=>toggleTodos(),
 imgZoom:(el)=>imgZoom(el),
 openPreview:(el)=>openPreview(el.dataset.arg||el.dataset.path||''),
 closePreview:()=>closePreview(),
 previewReload:()=>reloadPreview(),
 previewOpen:()=>openPreviewExternal(),
 previewDownload:()=>downloadPreview(),
 copyPreviewPath:()=>copyPreviewPath(),
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
 modelStart:(el)=>modelStart(el),
 modelStop:(el)=>modelStop(el),
 modelPreset:(el)=>modelPreset(el),
 modelUse:(el)=>modelUse(el),
 modelOrganize:()=>modelOrganize(),
 modelStopAll:()=>modelStopAll(),
 // 模型提供商（设置页）
 provNew:()=>provNew(),
 provEdit:(el)=>provEdit(el),
 provCancel:()=>provCancel(),
 provSave:()=>provSave(),
 provDelete:(el)=>provDelete(el),
 provTest:(el)=>provTest(el),
 provOauth:(el)=>provOauth(el),
 provOauthCancel:()=>provOauthCancel(),
 provOauthDisconnect:(el)=>provOauthDisconnect(el),
 provGetKey:(el)=>provGetKey(el),
 provWebchatOpen:(el)=>provWebchatOpen(el),
 provClearKey:(el)=>provClearKey(el),
 provSetDefault:()=>provSetDefault(),
 provRefresh:()=>loadProviders(),
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
