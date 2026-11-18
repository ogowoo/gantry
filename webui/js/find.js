// find.js — 
// 从原 webui/app.js 拆出的 ES 模块（该单体已退役，见 docs/ESM-MIGRATION.md）。
let findHits=[],findCur=-1;   // window.findQuery 在 app.js（var，挂 window），跨块共享
function chatFindInput(){
 let q=(document.getElementById('chatFind').value||'').trim();
 window.findQuery=q;
 // 重渲染拿回干净 DOM（renderMessages 内部会按 window.findQuery 重新高亮）。
 // 不要再单独调 applyFind()：那会在已标记的 DOM 上再标一遍（嵌套 mark、计数翻倍）。
 renderMessages(window.lastMessages);
}
function applyFind(){
 let box=document.getElementById('chatlog');findHits=[];findCur=-1;
 let cnt=document.getElementById('findCount');
 if(!window.findQuery){if(cnt)cnt.textContent='';return}
 // 文本节点快照后逐个替换，避免边遍历边改 DOM；跳过按钮与已有标记
 const nodes=[];
 (function walk(n){for(let c=n.firstChild;c;c=c.nextSibling){
   if(c.nodeType===3){if(c.nodeValue&&c.nodeValue.trim())nodes.push(c)}
   else if(c.nodeType===1&&c.tagName!=='BUTTON'&&c.tagName!=='MARK'
           &&!c.classList.contains('msg-fold')
           &&!c.classList.contains('ln'))walk(c)}})(box);
 const q=window.findQuery.toLowerCase();
 nodes.forEach(node=>{
  const text=node.nodeValue,low=text.toLowerCase();
  let idx=low.indexOf(q);if(idx<0)return;
  const frag=document.createDocumentFragment();let last=0;
  while(idx>=0){
   frag.appendChild(document.createTextNode(text.slice(last,idx)));
   const mk=document.createElement('mark');mk.className='gantry-hit';mk.textContent=text.substr(idx,window.findQuery.length);
   frag.appendChild(mk);findHits.push(mk);
   last=idx+window.findQuery.length;idx=low.indexOf(q,last)}
  frag.appendChild(document.createTextNode(text.slice(last)));
  node.parentNode.replaceChild(frag,node)});
 if(cnt)cnt.textContent=findHits.length?`0/${findHits.length}`:'无匹配';
 if(findHits.length){findCur=0;focusHit()}}
function focusHit(){
 findHits.forEach((h,i)=>h.classList.toggle('cur',i===findCur));
 if(findCur<0)return;
 const h=findHits[findCur];
 h.scrollIntoView({block:'center',behavior:'smooth'});
 let cnt=document.getElementById('findCount');
 if(cnt)cnt.textContent=`${findCur+1}/${findHits.length}`}
function findStep(delta){
 if(!findHits.length)return;
 findCur=(findCur+delta+findHits.length)%findHits.length;focusHit()}
function toggleChatFind(){
 let bar=document.getElementById('chatFindBar');if(!bar)return;
 let show=bar.style.display==='none'||!bar.style.display;
 bar.style.display=show?'flex':'none';
 let inp=document.getElementById('chatFind');
 if(show){inp.focus();inp.select()}
 else{window.findQuery='';findHits=[];findCur=-1;if(inp)inp.value='';
      let cnt=document.getElementById('findCount');if(cnt)cnt.textContent='';
      renderMessages(window.lastMessages)}}   // 重渲染：把高亮标记一起清掉
/* ── 代码语法高亮（零依赖，手写）───────────────────────────────────
   输入是**已经 HTML 转义**的代码；做法：先把注释/字符串抽成占位符（占位符只用
   字母 z，绝不含数字，因此不会被后面的数字/关键字正则误匹配），再给关键字、
   数字、属性上色，最后还原占位符。未知语言原样返回（只吃 monospace 样式）。 */
const HL_ALIAS={py:'python',python:'python',python3:'python',
 js:'js',javascript:'js',mjs:'js',cjs:'js',ts:'js',typescript:'js',jsx:'js',tsx:'js',
 json:'json',jsonc:'json',sh:'bash',bash:'bash',shell:'bash',zsh:'bash',console:'bash',
 ps1:'powershell',powershell:'powershell',pwsh:'powershell',
 sql:'sql',yml:'yaml',yaml:'yaml',html:'html',xml:'html',htm:'html',vue:'html',
 css:'css',scss:'css',less:'css',diff:'diff',patch:'diff',udiff:'diff',
 wdl:'wdl',workflow:'wdl',expression:'wdl','power-automate':'wdl',pa:'wdl',
 go:'go',golang:'go',rs:'rust',rust:'rust',java:'java',kt:'kotlin',kotlin:'kotlin',
 c:'c',h:'c',cpp:'cpp','c++':'cpp',cc:'cpp',cxx:'cpp',hpp:'cpp',cs:'csharp',csharp:'csharp',
 rb:'ruby',ruby:'ruby',php:'php',swift:'swift',scala:'scala',dart:'dart',lua:'lua',
 r:'r',perl:'perl',pl:'perl',pm:'perl',hs:'haskell',haskell:'haskell',
 clj:'clojure',clojure:'clojure',ex:'elixir',elixir:'elixir',erl:'erlang',erlang:'erlang',
 md:'markdown',markdown:'markdown',dockerfile:'dockerfile',docker:'dockerfile',
 tf:'hcl',hcl:'hcl',toml:'ini',ini:'ini',cfg:'ini',env:'ini',
 graphql:'graphql',gql:'graphql',proto:'protobuf',protobuf:'protobuf',
 svelte:'html',astro:'html'};
const HL_KW={
 python:'def class return if elif else for while import from as try except finally with lambda yield async await None True False self in not and or is pass raise global nonlocal assert del break continue',
 js:'function const let var return if else for while class new import export from async await try catch finally throw typeof instanceof null undefined true false this extends super of in do switch case break default delete void yield static get set',
 json:'true false null',
 bash:'if then else elif fi for do done while until case esac function return export local readonly echo cd set source unset alias trap',
 powershell:'if elseif else foreach for while do until switch function param return try catch finally throw in begin process end filter',
 sql:'select from where insert into update delete join left right inner outer full on group by order having limit offset create table alter drop index view values set as and or not null distinct count sum avg min max like between asc desc union all case when then end',
 yaml:'true false null',
 html:'DOCTYPE html head body div span script style link meta title table tr td th a img input button form section header footer nav ul li p h1 h2 h3 h4 h5 h6 pre code br hr',
 css:'important media supports import charset',
 wdl:'true false null and or not if then else in'+
    ' items variables parameters outputs triggerBody triggerOutputs body'+
    ' concat formatDateTime utcNow addDays addHours addMinutes addSeconds addMonths addYears'+
    ' sub add mul div mod min max'+
    ' equals less lessOrEquals greater greaterOrEquals contains startsWith endsWith'+
    ' not empty length first last join split replace substring indexOf lastIndexOf'+
    ' toUpper toLower trim slice base64 base64ToBinary binary base64ToString decodeBase64 encodeUriComponent decodeUriComponent'+
    ' uriPath uriQuery uriHost uriPort uriScheme uriAbsolute'+
    ' createArray union intersection skip take filter select map reduce sort reverse'+
    ' int float string bool array object json xml xpath'+
    ' getFutureTime getPastTime subtractFromTime ticks convertTimeZone'+
    ' dayOfWeek dayOfMonth dayOfYear weekOfMonth weekOfYear startOfDay startOfHour startOfMonth'+
    ' rand guid'+
    ' coalesce if null',
 go:'package import func return if else for range go defer panic recover select chan interface struct map slice nil true false iota',
 rust:'fn let mut if else match for while loop return impl struct enum trait pub use mod crate self super true false nil Some None Ok Err i32 i64 u32 u64 usize String Vec Box',
 java:'public private protected static final class interface extends implements return if else for while do switch case break continue try catch finally throw throws new this super null true false void int long double float boolean char byte short',
 csharp:'public private protected internal static readonly class interface struct enum namespace using return if else for while do switch case break continue try catch finally throw new this base null true false void int long double float bool char string var',
 ruby:'def end return if else elsif unless while until for do begin rescue ensure raise require attr_accessor attr_reader attr_writer self nil true false and or not module class',
 php:'function return if else elseif for while do foreach switch case break continue try catch finally throw new class extends implements interface namespace use require include echo print fn match null true false',
 swift:'func return if else guard for while switch case break continue do catch throw try rethrows in out var let class struct enum protocol extension public private internal static mutating init deinit self super nil true false',
 scala:'def val var return if else for while match case break continue try catch finally throw new class object trait extends with implicit lazy sealed abstract override final import package yield forSome',
 dart:'void return if else for while do switch case break continue try catch finally throw new class extends implements interface abstract final static const var final late await async yield required this super null true false',
 lua:'function return local if else elseif for while do repeat until break end and or not nil true false then goto',
 r:'function return if else for while repeat break next tryCatch library require assign NULL TRUE FALSE NA Inf NaN',
 perl:'sub return my our local if else elsif unless for foreach while until do last next redo use require package bless strict warnings',
 haskell:'module import where let in do case of if then else return pure liftIO data type class instance newtype deriving foldl foldr map filter zip sum product show read putStrLn getLine Monad Functor Applicative Maybe Either IO',
 clojure:'defn def let if then else do quote var fn loop recur when when-not cond case try catch finally throw require ns in-ns import refer apply map filter reduce conj assoc dissoc get update merge into',
 elixir:'def defp defmodule defstruct return if else unless do end fn when cond case try rescue catch throw import require alias use IO Enum String Map List Tuple Kernel',
 erlang:'module export import fun receive after case of if end begin catch try throw spawn spawn_link ! self PID atom list tuple binary',
 markdown:'',
 dockerfile:'FROM RUN CMD ENTRYPOINT COPY ADD WORKDIR EXPOSE ENV ARG VOLUME USER LABEL ONBUILD STOPSIGNAL HEALTHCHECK SHELL',
 hcl:'resource variable output provider module locals data terraform var var map list string number bool any true false null',
 ini:'',
 graphql:'type interface union enum input schema query mutation subscription implements on fragment extend directive scalar',
 protobuf:'syntax package message enum service rpc returns option repeated optional required oneof map reserved extensions to extend'};
function hlKeyword(re){return new RegExp('\\b('+re+')\\b','g')}
function hlWrap(store){return(html)=>{store.push(html);return '\u0000'+'z'.repeat(store.length)+'\u0000'}}
/* 入参是**原始代码**（未转义），返回值是已转义并上色的 HTML。
   顺序很关键：先在原文上抽出注释/字符串（存转义后的 HTML），再转义剩余部分，
   最后上色——若反过来（先转义再上色），引号变成 &quot;，字符串正则永远匹配不到。 */
function highlight(raw,lang){
 const fam=HL_ALIAS[String(lang||'').toLowerCase()];
 const text=String(raw==null?'':raw);
 if(!fam)return esc(text);
 // diff/patch：行首决定颜色（+新增/-删除/@@hunk/--- +++文件头），不做关键字分析
 if(fam==='diff'){
  return text.split('\n').map(l=>{
   let cls='dl-ctx';
   if(/^\+\+\+|^---/.test(l))cls='dl-meta';
   else if(l.startsWith('+'))cls='dl-add';
   else if(l.startsWith('-'))cls='dl-del';
   else if(l.startsWith('@@'))cls='dl-hunk';
   else if(/^(diff|index|new file|deleted file|similarity|rename)/.test(l))cls='dl-meta';
   return '<span class="'+cls+'">'+esc(l)+'</span>'}).join('\n')}
 const store=[],keep=hlWrap(store);
 // HTML 家族反过来：要匹配的是 &lt;tag 这种转义后的形态
 if(fam==='html'){
  let s=esc(text);
  s=s.replace(/(&lt;!--[\s\S]*?--&gt;)/g,m=>keep('<span class="hl-c">'+m+'</span>'));
  s=s.replace(/(&lt;\/?)([A-Za-z][\w:-]*)/g,(m,a,b)=>a+keep('<span class="hl-t">'+b+'</span>'));
  s=s.replace(/([A-Za-z-]+)(=)(&quot;[^&]*?&quot;)/g,(m,a,b,c)=>keep('<span class="hl-p">'+a+'</span>')+b+keep('<span class="hl-s">'+c+'</span>'));
  const kws=HL_KW[fam];
  if(kws)s=s.replace(hlKeyword(kws.replace(/ /g,'|')),'<span class="hl-k">$1</span>');
  return s.replace(/\u0000(z+)\u0000/g,(m,zs)=>store[zs.length-1]||m)}
 let s=text;
 const keepEsc=(m,cls)=>keep('<span class="'+cls+'">'+esc(m)+'</span>');
 if(fam==='python'){
  s=s.replace(/("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')/g,m=>keepEsc(m,'hl-s'));
  s=s.replace(/(#[^\n]*)/g,m=>keepEsc(m,'hl-c'));
 }else if(fam==='js'||fam==='css'){
  s=s.replace(/(\/\*[\s\S]*?\*\/|\/\/[^\n]*)/g,m=>keepEsc(m,'hl-c'));
  s=s.replace(/("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`)/g,m=>keepEsc(m,'hl-s'));
 }else if(fam==='json'){
  s=s.replace(/("(?:[^"\\]|\\.)*")(\s*:)?/g,(m,str,colon)=>colon?keepEsc(str,'hl-k')+colon:keepEsc(str,'hl-s'));
 }else if(fam==='sql'){
  s=s.replace(/(--[^\n]*)/g,m=>keepEsc(m,'hl-c'));
  s=s.replace(/("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')/g,m=>keepEsc(m,'hl-s'));
 }else if(fam==='wdl'){
  // Power Automate 表达式（Workflow Definition Language）：
  // 字符串、'?'属性访问、@函数前缀、括号颜色区分
  s=s.replace(/("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')/g,m=>keepEsc(m,'hl-s'));
  s=s.replace(/(\b[A-Za-z_]\w*(?=\s*\())/g,m=>keepEsc(m,'hl-k'));
  s=s.replace(/(\['[^']*'\]|\["[^"]*"\])/g,m=>keepEsc(m,'hl-t'));
  s=s.replace(/([()[\]{}])/g,m=>keepEsc(m,'hl-p'));
  }else if(fam==='go'||fam==='rust'||fam==='java'||fam==='csharp'||fam==='swift'||fam==='scala'||fam==='dart'||fam==='c'||fam==='cpp'){
  // C 家族（含 Go/Rust/Java/C#/Swift/Scala/Dart/C/C++）：// 行注释 + /* */ 块注释
  s=s.replace(/(\/\/[^\n]*|\/\*[\s\S]*?\*\/)/g,m=>keepEsc(m,'hl-c'));
  s=s.replace(/("(?:[^"\\\\\n]|\\\\.)*"|'(?:[^'\\\\\n]|\\\\.)*')/g,m=>keepEsc(m,'hl-s'));
 }else if(fam==='ruby'||fam==='perl'){
  // Ruby/Perl：# 注释
  s=s.replace(/(#[^\n]*)/g,m=>keepEsc(m,'hl-c'));
  s=s.replace(/("(?:[^"\\\\\n]|\\\\.)*"|'(?:[^'\\\\\n]|\\\\.)*')/g,m=>keepEsc(m,'hl-s'));
 }else if(fam==='php'){
  // PHP：// 和 # 行注释 + /* */ 块注释
  s=s.replace(/(\/\/[^\n]*|#[^\n]*|\/\*[\s\S]*?\*\/)/g,m=>keepEsc(m,'hl-c'));
  s=s.replace(/("(?:[^"\\\\\n]|\\\\.)*"|'(?:[^'\\\\\n]|\\\\.)*')/g,m=>keepEsc(m,'hl-s'));
 }else if(fam==='lua'){
  // Lua：-- 行注释 + --[[ ]] 块注释
  s=s.replace(/(--\[\[[\s\S]*?\]\]|--[^\n]*)/g,m=>keepEsc(m,'hl-c'));
  s=s.replace(/("(?:[^"\\\\\n]|\\\\.)*"|'(?:[^'\\\\\n]|\\\\.)*')/g,m=>keepEsc(m,'hl-s'));
 }else if(fam==='r'){
  // R：# 注释
  s=s.replace(/(#[^\n]*)/g,m=>keepEsc(m,'hl-c'));
  s=s.replace(/("(?:[^"\\\\\n]|\\\\.)*"|'(?:[^'\\\\\n]|\\\\.)*')/g,m=>keepEsc(m,'hl-s'));
 }else if(fam==='haskell'){
  // Haskell：-- 注释 + {- -} 块注释
  s=s.replace(/(--[^\n]*|\{-[\s\S]*?-\})/g,m=>keepEsc(m,'hl-c'));
  s=s.replace(/("(?:[^"\\\\\n]|\\\\.)*"|'(?:[^'\\\\\n]|\\\\.)*')/g,m=>keepEsc(m,'hl-s'));
 }else if(fam==='clojure'){
  // Clojure：;; 注释
  s=s.replace(/(;[^\n]*)/g,m=>keepEsc(m,'hl-c'));
  s=s.replace(/("(?:[^"\\\\\n]|\\\\.)*")/g,m=>keepEsc(m,'hl-s'));
 }else if(fam==='elixir'){
  // Elixir：# 注释
  s=s.replace(/(#[^\n]*)/g,m=>keepEsc(m,'hl-c'));
  s=s.replace(/("(?:[^"\\\\\n]|\\\\.)*"|'(?:[^'\\\\\n]|\\\\.)*')/g,m=>keepEsc(m,'hl-s'));
 }else if(fam==='erlang'){
  // Erlang：% 注释
  s=s.replace(/(%[^\n]*)/g,m=>keepEsc(m,'hl-c'));
  s=s.replace(/("(?:[^"\\\\\n]|\\\\.)*")/g,m=>keepEsc(m,'hl-s'));
 }else if(fam==='dockerfile'){
  // Dockerfile：# 注释
  s=s.replace(/(#[^\n]*)/g,m=>keepEsc(m,'hl-c'));
  s=s.replace(/("(?:[^"\\\\\n]|\\\\.)*")/g,m=>keepEsc(m,'hl-s'));
 }else if(fam==='graphql'){
  // GraphQL：# 注释 + """docstring"""
  s=s.replace(/(#[^\n]*)/g,m=>keepEsc(m,'hl-c'));
  s=s.replace(/("""[\s\S]*?"""|"(?:[^"\\\\\n]|\\\\.)*")/g,m=>keepEsc(m,'hl-s'));
 }else if(fam==='protobuf'){
  // Protobuf：// 注释
  s=s.replace(/(\/\/[^\n]*)/g,m=>keepEsc(m,'hl-c'));
  s=s.replace(/("(?:[^"\\\\\n]|\\\\.)*")/g,m=>keepEsc(m,'hl-s'));
 }else if(fam==='markdown'){
  // Markdown：**粗体** *斜体*
  s=s.replace(/(\*\*[^*]+\*\*)/g,m=>keepEsc(m,'hl-k'));
  s=s.replace(/(\*[^*\n]+\*)/g,m=>keepEsc(m,'hl-s'));
 }else if(fam==='ini'||fam==='hcl'){
  // INI/HCL：# 和 ; 注释
  s=s.replace(/(#[^\n]*|;[^\n]*)/g,m=>keepEsc(m,'hl-c'));
  s=s.replace(/("(?:[^"\\\\\n]|\\\\.)*"|'(?:[^'\\\\\n]|\\\\.)*')/g,m=>keepEsc(m,'hl-s'));
 }else{   // bash / powershell / yaml
  s=s.replace(/(#[^\n]*)/g,m=>keepEsc(m,'hl-c'));
  s=s.replace(/("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')/g,m=>keepEsc(m,'hl-s'));
 }
 s=esc(s);                                  // 剩余部分（关键字/数字所在）统一转义
 const kws=HL_KW[fam];
 if(kws)s=s.replace(hlKeyword(kws.replace(/ /g,'|')),'<span class="hl-k">$1</span>');
 s=s.replace(/\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b/g,'<span class="hl-n">$&</span>');
 return s.replace(/\u0000(z+)\u0000/g,(m,zs)=>store[zs.length-1]||m)}
/* ── 助手回复的可视化：JSON 动作 / 最终答复 / Markdown ────────────────
   问题：Agent 模式的回复常常是 JSON 动作原样落进对话（"final" 除外），
   以往按纯文本渲染就是一大坨（还超过折叠阈值被折起来），完全没法读。
   这里按内容形态分流渲染：
     {type:"final",content}      -> content 按 Markdown 渲染 + "最终答复" 标记
     {type:"tool",...}           -> 工具卡：工具名 + 原因 + 参数(JSON 视图)
     {type:"actions",actions:[]} -> 批量动作卡：逐个工具 + 参数
     其它 JSON                    -> JSON 视图（可读、可滚、键值着色）
     非 JSON                      -> Markdown
   CSP 下不能有内联事件，所有按钮走 data-act 事件委托。 */

export { chatFindInput, applyFind, focusHit, findStep, toggleChatFind, highlight, HL_ALIAS };
