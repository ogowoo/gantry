/* 生成文件的右侧预览面板（参考 DSH / Kimi 的 artifact 面板）。
 *
 * 只做前端展示：文件内容从 /v1/agent/sessions/{sid}/file 读取，路径由后端
 * 会话工作区校验。HTML 用 sandbox iframe（禁脚本/同源），SVG 用 <img>，
 * 文本一律转义；不把文件内容直接塞进主页面 DOM。
 */
const IMAGE_EXT = new Set(['png','jpg','jpeg','gif','webp','bmp','avif','ico','svg']);
const TEXT_EXT = new Set(['txt','md','markdown','json','jsonc','csv','tsv','log','yaml','yml',
  'xml','html','htm','css','scss','less','js','mjs','cjs','ts','tsx','jsx','py','rb','php',
  'java','kt','kts','go','rs','c','h','cpp','hpp','cs','swift','scala','sh','bash','zsh',
  'ps1','bat','cmd','sql','toml','ini','cfg','conf','env','dockerfile','gitignore']);
const OFFICE_EXT = new Set(['docx','doc','xlsx','xls','xlsm','pptx','ppt','odt','ods','odp']);
let lastPath = '';
let lastMeta = '';

function trPreview(s){
  try{return window.tr ? window.tr(s) : s}catch(e){return s}
}
function escP(s){
  return String(s==null?'':s).replace(/[&<>"']/g,c=>({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function extOf(path){
  const base=String(path||'').split(/[\\/]/).pop()||'';
  if(base.toLowerCase()==='dockerfile')return 'dockerfile';
  const i=base.lastIndexOf('.');
  return i>=0?base.slice(i+1).toLowerCase():'';
}
function fileUrl(path, download){
  const sid=window.current||'';
  const tk=window.apiToken?window.apiToken():'';
  let u='/v1/agent/sessions/'+encodeURIComponent(sid)+'/file?path='+encodeURIComponent(path);
  if(download)u+='&download=1';
  if(tk)u+='&token='+encodeURIComponent(tk);
  return u;
}
function setMeta(text){lastMeta=String(text||'');const el=document.getElementById('pvMeta');if(el)el.textContent=lastMeta}
function setBody(html){
  const body=document.getElementById('pvBody');
  if(body)body.innerHTML=html;
}
function setTitle(path, kind){
  const nameEl=document.getElementById('pvName');
  const pathEl=document.getElementById('pvPath');
  const iconEl=document.getElementById('pvIcon');
  const base=String(path||'').split(/[\\/]/).pop()||path||'';
  if(nameEl)nameEl.textContent=base;
  if(pathEl)pathEl.textContent=path||'';
  if(iconEl)iconEl.textContent=kind==='image'?'🖼':kind==='html'?'🌐':kind==='pdf'?'📕':
    kind==='office'?'📄':kind==='binary'?'📦':'📄';
}
function previewKind(path){
  const ext=extOf(path);
  if(IMAGE_EXT.has(ext))return 'image';
  if(ext==='html'||ext==='htm')return 'html';
  if(ext==='pdf')return 'pdf';
  if(OFFICE_EXT.has(ext))return 'office';
  if(TEXT_EXT.has(ext)||!ext)return 'text';
  return 'binary';
}
async function loadText(path){
  const url=fileUrl(path,false);
  const tk=window.apiToken?window.apiToken():'';
  const r=await fetch(url,{headers:tk?{'X-Gantry-Token':tk}:{}});
  if(!r.ok)throw new Error('HTTP '+r.status);
  const text=await r.text();
  if(text.length>600000)return text.slice(0,600000)+'\n\n…（文件较大，已截断显示）';
  return text;
}
function renderText(path, text){
  const ext=extOf(path);
  if(ext==='json'||ext==='jsonc'){
    try{text=JSON.stringify(JSON.parse(text),null,2)}catch(e){}
  }
  if(ext==='md'||ext==='markdown'){
    try{
      const fn=window.mdToHtml;
      if(typeof fn==='function'){
        setBody('<div class="pv-markdown">'+fn(text)+'</div>');
        return;
      }
    }catch(e){}
  }
  setBody('<pre class="pv-text">'+escP(text)+'</pre>');
}
async function renderPreview(path){
  const kind=previewKind(path);
  setTitle(path,kind);
  setMeta(trPreview('读取中…'));
  const body=document.getElementById('pvBody');
  if(!body)return;
  if(!window.current){
    setMeta('');
    body.innerHTML='<div class="pv-empty">'+escP(trPreview('先选择一个任务，再预览文件。'))+'</div>';
    return;
  }
  if(kind==='image'){
    const url=fileUrl(path,false);
    body.innerHTML='<div class="pv-image-wrap"><img class="pv-image" alt="'+escP(path)+'" src="'+escP(url)+'"></div>';
    const img=body.querySelector('img');
    if(img)img.addEventListener('load',()=>setMeta(img.naturalWidth+' × '+img.naturalHeight+' px'));
    if(img)img.addEventListener('error',()=>setMeta(trPreview('图片加载失败')));
    return;
  }
  if(kind==='html'){
    setMeta('HTML · sandbox');
    body.innerHTML='<iframe class="pv-frame" sandbox="" referrerpolicy="no-referrer" '+
      'src="'+escP(fileUrl(path,false))+'"></iframe>';
    return;
  }
  if(kind==='pdf'){
    setMeta('PDF');
    body.innerHTML='<iframe class="pv-frame pv-pdf" src="'+escP(fileUrl(path,false))+'#toolbar=1"></iframe>';
    return;
  }
  if(kind==='office'){
    setMeta(trPreview('Office 文件 · 浏览器不能直接预览'));
    body.innerHTML='<div class="pv-empty pv-office"><div class="pv-empty-ico">📄</div>'+
      '<p>'+escP(trPreview('Office 文件需要下载后用桌面应用打开。'))+'</p>'+
      '<a class="btn accent" href="'+escP(fileUrl(path,true))+'">'+escP(trPreview('下载文件'))+'</a></div>';
    return;
  }
  if(kind==='binary'){
    setMeta(trPreview('二进制文件'));
    body.innerHTML='<div class="pv-empty"><div class="pv-empty-ico">📦</div>'+
      '<p>'+escP(trPreview('此类型暂无内置预览，可下载或在新窗口打开。'))+'</p></div>';
    return;
  }
  try{
    const text=await loadText(path);
    setMeta(extOf(path).toUpperCase()+' · '+text.length.toLocaleString()+' chars');
    renderText(path,text);
  }catch(e){
    setMeta(trPreview('读取失败'));
    setBody('<div class="pv-empty pv-error">'+escP(trPreview('读取文件失败：'))+escP(e&&e.message?e.message:e)+'</div>');
  }
}
function openPreview(path, opts){
  opts=opts||{};
  path=String(path||'').trim();
  if(!path)return;
  lastPath=path;
  const panel=document.getElementById('previewPanel');
  if(!panel)return;
  panel.hidden=false;
  panel.dataset.path=path;
  document.body.classList.add('preview-open');
  try{
    const p=renderPreview(path);
    if(p&&typeof p.catch==='function')p.catch(()=>{});
  }catch(e){}
}
function closePreview(){
  const panel=document.getElementById('previewPanel');
  if(panel)panel.hidden=true;
  document.body.classList.remove('preview-open');
  lastPath='';
}
function reloadPreview(){
  if(lastPath)renderPreview(lastPath);
}
function openPreviewExternal(){
  if(lastPath)window.open(fileUrl(lastPath,false),'_blank','noopener');
}
function downloadPreview(){
  if(!lastPath)return;
  const a=document.createElement('a');
  a.href=fileUrl(lastPath,true);a.target='_blank';a.rel='noopener';
  document.body.appendChild(a);a.click();a.remove();
}
function copyPreviewPath(){
  if(!lastPath)return;
  const done=()=>{try{window.toast&&window.toast(trPreview('路径已复制'),'ok')}catch(e){}};
  try{navigator.clipboard.writeText(lastPath).then(done,done)}catch(e){done()}
}
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'){
    const panel=document.getElementById('previewPanel');
    if(panel&&!panel.hidden){closePreview();}
  }
});

export { openPreview, closePreview, reloadPreview, openPreviewExternal, downloadPreview,
         copyPreviewPath, fileUrl, previewKind };
