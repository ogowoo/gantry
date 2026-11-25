/* 实时视图：右侧抽屉面板，SSE 接收受管浏览器画面；点击/滚轮/键盘转发回
   受管浏览器（v2 互动），坐标按帧元数据从显示尺寸换算成页面 CSS 像素。
   Agent 调 open_site 打开网站时自动展开；也可点顶栏按钮手动开关。 */
import { api } from './apistate.js';
import { apiToken } from './cards.js';
import { toast } from './toast.js';
/* tr 是 i18n.js（传统脚本，非模块）挂的全局函数，直接调用，不要 import */

let _es = null;          // EventSource
let _idx = -1;           // 当前观看的页面序号（ctx.pages 下标）
let _poll = null;        // 页面列表/自动展开轮询
let _dismissedAt = 0;    // 用户上次手动关掉面板的时间（自动展开不打扰）
let _seenOpenAt = 0;     // 已处理过的 last_open.at
let _vp = null;          // 帧元数据：页面视口的 CSS 像素尺寸 {w,h}

function _el(id){ return document.getElementById(id) }

function _build(){
 if(_el('livePanel')) return;
 const p = document.createElement('div');
 p.id = 'livePanel';
 p.style.cssText = 'position:fixed;top:0;right:0;bottom:0;width:min(430px,40vw);z-index:9000;'
  + 'display:none;flex-direction:column;background:var(--bg,#111);border-left:1px solid var(--line,#333)';
 p.innerHTML =
  '<div style="display:flex;align-items:center;gap:8px;padding:8px 10px;border-bottom:1px solid var(--line,#333)">'
  + '<b style="font-size:13px">' + tr('实时视图') + '</b>'
  + '<select id="livePageSel" style="flex:1;min-width:0" title="' + tr('选择要观看的页面') + '"></select>'
  + '<button type="button" class="iconbtn" id="liveClose" title="' + tr('关闭') + '">✕</button></div>'
  + '<div style="flex:1;overflow:auto;display:flex;align-items:flex-start;justify-content:center;background:#000">'
  + '<img id="liveImg" alt="" tabindex="0" draggable="false" '
  + 'style="width:100%;display:block;cursor:crosshair;user-select:none" '
  + 'title="' + tr('点击/滚动/打字，操作会同步到受管浏览器') + '">'
  + '<div id="liveEmpty" style="color:#888;font-size:13px;padding:24px;display:none"></div></div>';
 document.body.appendChild(p);
 _el('liveClose').onclick = () => hideLive(true);
 _el('livePageSel').onchange = (e) => _watch(parseInt(e.target.value, 10));
 _bindInput(_el('liveImg'));
}

/* 面板显示坐标 → 页面 CSS 像素（依赖帧元数据，首帧到达前忽略输入） */
function _toPage(img, cx, cy){
 if(!_vp || !_vp.w || !_vp.h) return null;
 const x = cx / (img.clientWidth || 1) * _vp.w;
 const y = cy / (img.clientHeight || 1) * _vp.h;
 return { x: Math.round(x * 10) / 10, y: Math.round(y * 10) / 10 };
}

function _sendInput(ev){
 if(_idx < 0) return;
 ev.index = _idx;
 api('/v1/live/input', { method: 'POST', body: JSON.stringify(ev) }).catch(() => {});
}

function _bindInput(img){
 img.addEventListener('click', (e) => {
  const pt = _toPage(img, e.offsetX, e.offsetY);
  if(pt) _sendInput({ kind: 'click', x: pt.x, y: pt.y, click_count: e.detail });
  img.focus();
 });
 img.addEventListener('wheel', (e) => {
  const pt = _toPage(img, e.offsetX, e.offsetY);
  if(pt) _sendInput({ kind: 'wheel', x: pt.x, y: pt.y, delta_x: e.deltaX, delta_y: e.deltaY });
  e.preventDefault();
 }, { passive: false });
 // 键盘：单字符走 insert_text（含中文输入），导航/编辑键走 press
 img.addEventListener('keydown', (e) => {
  if(e.ctrlKey || e.metaKey || e.altKey) return;       // 组合键不转发
  if(e.key.length === 1){ _sendInput({ kind: 'text', text: e.key }); }
  else if(e.key.length > 1){ _sendInput({ kind: 'key', key: e.key }); }
  else return;
  e.preventDefault();
 });
}

function _watch(idx){
 _idx = idx;
 _vp = null;
 if(_es){ try{ _es.close() }catch(e){} _es = null }
 const img = _el('liveImg'), empty = _el('liveEmpty');
 if(idx < 0){ img.style.display = 'none'; empty.style.display = ''; empty.textContent = tr('受管浏览器没有打开的页面'); return }
 empty.style.display = 'none'; img.style.display = '';
 let url = '/v1/live/stream?index=' + idx;
 const tk = apiToken(); if(tk) url += '&token=' + encodeURIComponent(tk);
 _es = new EventSource(url);
 _es.onmessage = (e) => {
  try{
   const f = JSON.parse(e.data);
   _vp = { w: f.w, h: f.h };
   img.src = 'data:image/jpeg;base64,' + f.img;
  }catch(_){}
 };
 _es.onerror = () => { /* EventSource 自动重连；页面被关掉时服务端会发 error 事件 */ };
 _es.addEventListener('error', (e) => {
  try{ const d = JSON.parse(e.data); empty.style.display = ''; empty.textContent = d.detail || ''; }catch(_){}
 });
}

async function _refreshPages(autoOpen){
 let d;
 try{ d = await api('/v1/live/pages') }catch(e){ return }
 const sel = _el('livePageSel'); if(!sel) return;
 const pages = d.pages || [];
 const visible = _el('livePanel').style.display !== 'none';
 sel.innerHTML = pages.map(p =>
  '<option value="' + p.index + '">' + (p.title || p.url || ('#' + p.index)).replace(/</g, '&lt;') + '</option>').join('');
 if(visible){
  if(pages.length && !pages.some(p => p.index === _idx)){
   _watch(pages[pages.length - 1].index);           // 当前页没了：切到最新一页
  } else if(pages.length){
   sel.value = String(_idx);
  } else { _watch(-1) }
 }
 // 自动展开：open_site 开了新页面。用户手动关过面板后冷却 4 小时不再自动打扰
 const lo = d.last_open || {};
 if(autoOpen && lo.at && lo.at > _seenOpenAt){
  _seenOpenAt = lo.at;
  const hit = pages.find(p => p.url === lo.url);
  const cooled = !_dismissedAt || (Date.now() / 1000 - _dismissedAt) > 4 * 3600;
  if(!visible && cooled) showLive();
  if(hit) _watch(hit.index);
 } else if(!autoOpen && lo.at){
  _seenOpenAt = Math.max(_seenOpenAt, lo.at);      // 启动基线：历史记录不触发自动展开
 }
}

export function showLive(){
 _build();
 _el('livePanel').style.display = 'flex';
 if(_idx >= 0) _watch(_idx);                        // 重开时恢复流
 if(!_poll){ _refreshPages(true); _poll = setInterval(() => _refreshPages(true), 5000) }
}

export function hideLive(manual){
 if(manual) _dismissedAt = Date.now() / 1000;
 if(_es){ try{ _es.close() }catch(e){} _es = null }
 if(_poll){ clearInterval(_poll); _poll = null }
 const p = _el('livePanel'); if(p) p.style.display = 'none';
 if(manual) toast(tr('已关闭实时视图（新页面打开时会重新展开）'));
}

export function toggleLiveView(){
 const p = _el('livePanel');
 if(p && p.style.display !== 'none') hideLive(true); else showLive();
}

/* 面板不可见时也低频轮询：发现 open_site 新页面就自动展开。
   启动时先无自动展开地记一次基线，避免把历史记录当成新页面。 */
export function startLiveAutoOpen(){
 _build();
 _refreshPages(false);
 setInterval(() => {
  if(_poll) return;                                  // 面板开着时由 showLive 的轮询处理
  _refreshPages(true);
 }, 8000);
}
