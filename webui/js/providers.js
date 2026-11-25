// providers.js — 设置页「模型提供商」：预设/自定义提供商的增删改查、连通性测试、默认模型。
// 语义：预设（openai/deepseek/kimi…）不能删，改它们等于写一层覆盖；「删除」对预设 = 恢复默认。
// API Key 只写不读：列表只显示"已配置/来自环境变量"，编辑时留空表示保持原值。
import { api, esc } from './apistate.js';

const KIND_LABEL = { preset: '预设', custom: '自定义', local: '本地进程内' };
const JSON_MODES = [['auto', '自动（先试 json_object）'], ['json_schema', 'json_schema'],
  ['json_object', 'json_object'], ['off', '关闭（用提示词约束）']];

let _providers = [];
let _editing = null;          // null=新建，否则为正在编辑的提供商名
let _presets = [];

const _provEl = (id) => document.getElementById(id);

// 提供商可用性变化后同步对话页的模型下拉：loadProviders 只刷设置页徽标，
// 不刷模型选择器——授权/保存后当前会话里看不到新模型，用户以为没生效
// （kimi-coding 授权成功、测试通过，但下拉里没有就是这个原因）。
async function _refreshModels() {
  try { await window.loadModels?.(); } catch (e) { /* 刷新失败不影响设置页 */ }
}

function _provBadge(p) {
  const kind = `<span class="prov-kind prov-kind-${esc(p.kind)}">${esc(tr(KIND_LABEL[p.kind] || p.kind))}</span>`;
  const key = p.kind === 'local'
    ? `<span class="prov-key ${p.configured ? 'ok' : 'warn'}">${esc(tr(p.configured ? '模型就绪' : '模型缺失'))}</span>`
    : p.backend === 'webchat'
      ? `<span class="prov-key ${p.configured ? 'ok' : 'warn'}" title="${esc(tr('网页登录会话，无需 API Key'))}">🌐 ${esc(tr('网页版'))}</span>`
      : p.oauth && p.oauth.supported && p.oauth.authorized
      ? `<span class="prov-key ok" title="${esc(tr('OAuth 授权有效'))}">✅ OAuth</span>`
      : p.api_key_env_active
        ? `<span class="prov-key ok" title="${esc(tr('环境变量 {n} 已设置').replace('{n}', p.api_key_env_active))}">🔑 ${esc(p.api_key_env_active)}</span>`
        : p.api_key_in_config
          ? `<span class="prov-key ok">🔑 ${esc(tr('配置文件'))}</span>`
          : `<span class="prov-key warn">${esc(tr('未配置 Key'))}</span>`;
  return kind + key;
}

function _provCard(p) {
  const models = (p.models || []).slice(0, 6)
    .map((m) => `<code class="prov-model">${esc(m)}</code>`).join('');
  const more = (p.models || []).length > 6
    ? `<span class="sethint">+${p.models.length - 6}</span>` : '';
  const where = p.kind === 'local'
    ? esc(p.model_path || tr('（未指定模型）'))
    : (p.backend === 'webchat'
      ? esc(p.web_url || tr('（未注册站点）'))
      : esc(p.base_url || tr('（未配置地址）')));
  const oauthBtn = (p.oauth && p.oauth.supported)
    ? (p.oauth.authorized
        ? `<button type="button" class="btn ghost danger" data-act="provOauthDisconnect" data-arg="${esc(p.name)}">${esc(tr('断开授权'))}</button>`
        : `<button type="button" class="btn ghost" data-act="provOauth" data-arg="${esc(p.name)}">${esc(tr('一键授权'))}</button>`)
    : (p.backend === 'webchat'
        ? `<button type="button" class="btn ghost" data-act="provWebchatOpen" data-arg="${esc(p.name)}">${esc(tr('打开登录页'))}</button>`
        : (p.name === 'deepseek'
          ? `<button type="button" class="btn ghost" data-act="provGetKey" data-arg="https://platform.deepseek.com/api_keys">${esc(tr('获取 Key（打开控制台）'))}</button>`
          : ''));
  const acts = p.kind === 'local'
    ? `<span class="sethint">${esc(tr('本地模型请在「模型」页管理'))}</span>`
    : `${oauthBtn}<button type="button" class="btn ghost" data-act="provEdit" data-arg="${esc(p.name)}">${esc(tr('编辑'))}</button>
       <button type="button" class="btn ghost" data-act="provTest" data-arg="${esc(p.name)}">${esc(tr('测试连接'))}</button>
       <button type="button" class="btn ghost${p.kind === 'custom' ? ' danger' : ''}" data-act="provDelete" data-arg="${esc(p.name)}">${esc(tr(p.kind === 'preset' ? '恢复默认' : '删除'))}</button>`;
  return `<div class="prov-card${p.configured ? '' : ' dim'}" data-prov="${esc(p.name)}">
    <div class="prov-head"><strong>${esc(p.name)}</strong>${_provBadge(p)}</div>
    <div class="prov-desc">${esc(p.description || '')}</div>
    <div class="prov-url" title="${where}">${where}</div>
    <div class="prov-models">${models}${more}</div>
    <div class="prov-acts">${acts}</div>
  </div>`;
}

function _provRenderList() {
  const box = _provEl('provList');
  if (!box) return;
  if (!_providers.length) {
    box.innerHTML = `<p class="sethint">${esc(tr('没有可用的提供商。点「新增提供商」创建一个。'))}</p>`;
    return;
  }
  box.innerHTML = _providers.map(_provCard).join('');
}

function _provRenderDefaults(selected) {
  const sel = _provEl('provDefaultModel');
  if (!sel) return;
  const cur = selected || sel.value || '';
  const opts = [];
  _providers.forEach((p) => {
    (p.models || []).forEach((m) => {
      opts.push(`<option value="${esc(p.name + '/' + m)}" ${p.name + '/' + m === cur ? 'selected' : ''}>${esc(p.name + ' / ' + m)}${p.configured ? '' : ' ' + esc(tr('（未配置）'))}</option>`);
    });
  });
  sel.innerHTML = `<option value="">${esc(tr('（不改变）'))}</option>` + opts.join('');
}

export async function loadProviders() {
  const box = _provEl('provList');
  if (!box) return;                       // 设置页没渲染这一页
  try {
    const r = await api('/v1/providers');
    _providers = r.providers || [];
    _presets = r.presets || [];
    _provRenderList();
    _provRenderDefaults(r.default_model || '');
  } catch (e) {
    const code = e && e.status;
    const msg = String((e && (e.detail || e.text || e.message)) || e) || '';
    const hint = code === 404
      ? tr('当前运行的服务端版本还没有提供商接口，重启服务后即可使用。')
      : msg;
    box.innerHTML = `<p class="sethint">${esc(tr('读取提供商失败: '))}${esc(hint)}</p>`;
  }
}

function _provOption(v, label, on) {
  return `<option value="${esc(v)}" ${on ? 'selected' : ''}>${esc(label)}</option>`;
}

function _provFormHtml(p) {
  const isEdit = !!p;
  const localOnly = isEdit && p.kind === 'local';
  if (localOnly) {
    return `<h5 class="setsection">${esc(tr('编辑本地提供商'))}：${esc(p.name)}</h5>
      <p class="sethint">${esc(tr('进程内 GGUF 的模型、上下文与线程数在「模型」页的本地模型子页配置；这里只能改描述。'))}</p>
      <label class="setrow"><span>${esc(tr('描述'))}</span>
        <input class="kbinput" id="provDesc" value="${esc(p.description || '')}"></label>
      <div class="setbtns">
        <button type="button" class="btn" data-act="provSave">${esc(tr('保存'))}</button>
        <button type="button" class="btn ghost" data-act="provCancel">${esc(tr('取消'))}</button>
      </div>`;
  }
  const name = isEdit ? p.name : '';
  const base = isEdit ? (p.base_url || '') : '';
  const apiKind = isEdit ? (p.api || 'openai') : 'openai';
  const envs = isEdit ? (p.api_key_env || []).join(', ') : '';
  const models = isEdit ? (p.models || []).join('\n') : '';
  const jsonMode = isEdit ? (p.json_mode || 'auto') : 'auto';
  const vision = isEdit ? !!p.vision : false;
  const maxTokens = isEdit ? (p.max_tokens || '') : '';
  const timeout = isEdit ? (p.timeout_seconds || 300) : 300;
  return `<h5 class="setsection">${isEdit ? esc(tr('编辑提供商')) + '：' + esc(p.name) : esc(tr('新增提供商'))}</h5>
    ${isEdit ? '' : `<label class="setrow"><span>${esc(tr('从预设开始'))}</span>
      <select class="kbinput" id="provPreset">${_provOption('', tr('（自定义）'), true)}${
        _presets.map((n) => _provOption(n, n, false)).join('')}</select></label>`}
    <label class="setrow"><span>${esc(tr('名称（小写字母数字，唯一）'))}</span>
      <input class="kbinput" id="provName" value="${esc(name)}" ${isEdit ? 'disabled' : ''}
        placeholder="mygateway"></label>
    <label class="setrow"><span>${esc(tr('接口地址（base_url）'))}</span>
      <input class="kbinput" id="provBase" value="${esc(base)}" placeholder="https://api.example.com/v1"></label>
    <label class="setrow"><span>${esc(tr('协议'))}</span>
      <select class="kbinput" id="provApi">
        ${_provOption('openai', 'OpenAI 兼容（/chat/completions）', apiKind === 'openai')}
        ${_provOption('anthropic', 'Anthropic Messages（/v1/messages）', apiKind === 'anthropic')}
      </select></label>
    <label class="setrow"><span>${esc(tr('API Key 环境变量（可留空）'))}</span>
      <input class="kbinput" id="provEnv" value="${esc(envs)}" placeholder="MYAPI_KEY"></label>
    <label class="setrow"><span>${esc(tr('API Key（写入 config.json）'))}</span>
      <input class="kbinput" id="provKey" type="password" autocomplete="off"
        placeholder="${isEdit && (p.api_key_in_config) ? esc(tr('已保存，留空保持不变')) : esc(tr('sk-…（也可只用环境变量）'))}"></label>
    ${isEdit && p.api_key_in_config ? `<div class="setbtns"><button type="button" class="btn ghost danger" data-act="provClearKey">${esc(tr('清除已保存的 Key'))}</button></div>` : ''}
    <label class="setrow"><span>${esc(tr('模型列表（每行一个或逗号分隔）'))}</span>
      <textarea class="kbinput" id="provModels" rows="3" placeholder="gpt-4o-mini">${esc(models)}</textarea></label>
    <div class="setbtns"><button type="button" class="btn ghost" data-act="provDetectModels"
      title="${esc(tr('用上面填的地址/Key 向提供商查询真实可用的模型，结果覆盖模型列表'))}">${esc(tr('检测可用模型'))}</button></div>
    <div class="setgrid2">
      <label class="setrow"><span>${esc(tr('max_tokens（0=不发送）'))}</span>
        <input class="kbinput" id="provMax" type="number" min="0" value="${esc(String(maxTokens))}"></label>
      <label class="setrow"><span>${esc(tr('超时（秒）'))}</span>
        <input class="kbinput" id="provTimeout" type="number" min="5" max="7200" value="${esc(String(timeout))}"></label>
    </div>
    <label class="setrow"><span>${esc(tr('结构化输出'))}</span>
      <select class="kbinput" id="provJson">${JSON_MODES
        .map(([v, t]) => _provOption(v, t, v === jsonMode)).join('')}</select></label>
    <label class="setrow"><input type="checkbox" id="provVision" ${vision ? 'checked' : ''}>
      <span>${esc(tr('支持图片输入（视觉模型）'))}</span></label>
    <label class="setrow"><span>${esc(tr('描述'))}</span>
      <input class="kbinput" id="provDesc" value="${esc(isEdit ? (p.description || '') : '')}"></label>
    <div class="setbtns">
      <button type="button" class="btn" data-act="provSave">${esc(tr('保存'))}</button>
      <button type="button" class="btn ghost" data-act="provTest">${esc(tr('测试连接'))}</button>
      <button type="button" class="btn ghost" data-act="provCancel">${esc(tr('取消'))}</button>
    </div>
    <p class="sethint">${esc(tr('保存后立即生效：/v1/models 会出现该提供商的全部模型，模型选择器无需重启。'))}</p>`;
}

function _provShowForm(p) {
  _editing = p ? p.name : null;
  const box = _provEl('provForm');
  if (!box) return;
  box.innerHTML = _provFormHtml(p || null);
  box.style.display = '';
  const presetSel = _provEl('provPreset');
  if (presetSel) {
    // 选预设：把 base_url / 协议 / 环境变量 / 模型先填进去，再按需改
    presetSel.onchange = async () => {
      const name = presetSel.value;
      if (!name) return;
      const preset = _providers.find((x) => x.name === name);
      if (!preset) return;
      const set = (id, v) => { const n = _provEl(id); if (n) n.value = v; };
      set('provName', preset.name);
      set('provBase', preset.base_url || '');
      set('provApi', preset.api || 'openai');
      set('provEnv', (preset.api_key_env || []).join(', '));
      set('provModels', (preset.models || []).join('\n'));
      set('provJson', preset.json_mode || 'auto');
      const mx = _provEl('provMax');
      if (mx) mx.value = preset.max_tokens || '';
      const tv = _provEl('provTimeout');
      if (tv) tv.value = preset.timeout_seconds || 300;
      const vs = _provEl('provVision');
      if (vs) vs.checked = !!preset.vision;
      const nm = _provEl('provName');
      if (nm) nm.disabled = true;         // 预设名固定，保存的是覆盖层
      toast(tr('已填入预设 {n}，可直接保存或修改').replace('{n}', name), 'ok');
    };
  }
  box.scrollIntoView({ block: 'nearest' });
}

export function provNew() {
  _provShowForm(null);
}

export function provEdit(node) {
  const name = node && node.dataset ? node.dataset.arg : '';
  const p = _providers.find((x) => x.name === name);
  if (!p) return;
  _provShowForm(p);
}

export function provCancel() {
  _editing = null;
  const box = _provEl('provForm');
  if (box) { box.innerHTML = ''; box.style.display = 'none'; }
}

function _provCollect() {
  const val = (id) => { const n = _provEl(id); return n ? String(n.value || '').trim() : ''; };
  const name = val('provName').toLowerCase();
  if (!name) { toast(tr('请填写名称'), 'err'); return null; }
  const body = {
    name,
    base_url: val('provBase'),
    api: val('provApi') || 'openai',
    api_key_env: val('provEnv'),
    models: val('provModels'),
    description: val('provDesc'),
    max_tokens: val('provMax') === '' ? 0 : Number(val('provMax')),
    timeout_seconds: Number(val('provTimeout') || 300),
    json_mode: val('provJson') || 'auto',
    vision: !!(_provEl('provVision') && _provEl('provVision').checked),
  };
  const key = val('provKey');
  if (key) body.api_key = key;            // 留空 = 保持原值（不把空串发过去）
  return body;
}

export async function provSave() {
  const body = _provCollect();
  if (!body) return;
  try {
    const r = await api('/v1/providers', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    toast(r.created ? tr('已新增提供商 {n}').replace('{n}', body.name)
                   : tr('已保存提供商 {n}').replace('{n}', body.name), 'ok');
    provCancel();
    await loadProviders();
    await _refreshModels();
  } catch (e) {
    toast(tr('保存失败: ') + ((e && (e.detail || e.text || e.message)) || String(e)), 'err');
  }
}

export async function provDelete(node) {
  const name = node && node.dataset ? node.dataset.arg : '';
  const p = _providers.find((x) => x.name === name);
  if (!p) return;
  const msg = p.kind === 'preset'
    ? tr('恢复 {n} 的预设默认配置（删除覆盖层）？').replace('{n}', name)
    : tr('删除提供商 {n}？模型选择器里的相关模型会立即消失。').replace('{n}', name);
  if (!confirm(msg)) return;
  try {
    const r = await api('/v1/providers/' + encodeURIComponent(name), { method: 'DELETE' });
    toast(r.restored_preset ? tr('已恢复预设 {n}').replace('{n}', name)
                            : tr('已删除 {n}').replace('{n}', name), 'ok');
    if (_editing === name) provCancel();
    await loadProviders();
    await _refreshModels();
  } catch (e) {
    toast(tr('删除失败: ') + ((e && (e.detail || e.text || e.message)) || String(e)), 'err');
  }
}

export async function provOauth(node) {
  // 「一键授权」：device 流程弹窗显示验证码；callback 流程直接打开浏览器回跳。
  // 发起要同步访问厂商授权端点（公司网络下可能数十秒），先提示让用户知道没卡死。
  const name = node && node.dataset ? (node.dataset.arg || '') : '';
  if (!name) return;
  toast(tr('正在发起授权…'), 'ok');
  try {
    const flow = await api('/v1/oauth/' + encodeURIComponent(name) + '/start',
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    if (flow.status === 'failed') {
      toast(tr('授权发起失败: ') + (flow.error || ''), 'err');
      return;
    }
    if (flow.authorize_url) {
      window.open(flow.authorize_url, '_blank');
      toast(tr('已在浏览器打开授权页，同意后会自动生效'), 'ok');
    } else if (flow.verification_uri_complete) {
      _oauthDeviceModal(name, flow);
    }
    if (flow.id) _pollOauthFlow(name, flow.id);
  } catch (e) {
    toast(tr('授权发起失败: ') + ((e && (e.detail || e.message)) || String(e)), 'err');
  }
}

let _oauthTimer = null;
function _pollOauthFlow(name, flowId) {
  if (_oauthTimer) clearInterval(_oauthTimer);
  _oauthTimer = setInterval(async () => {
    try {
      const st = await api('/v1/oauth/flow/' + encodeURIComponent(flowId));
      const el = document.getElementById('oauthFlowStatus');
      if (el) el.textContent = st.status === 'pending' ? tr('等待授权中…') : '';
      if (st.status === 'ok') {
        clearInterval(_oauthTimer); _oauthTimer = null;
        _closeOauthModal();
        toast(tr('{n} 授权成功').replace('{n}', name), 'ok');
        await loadProviders();
        await _refreshModels();
      } else if (st.status === 'failed' || st.status === 'cancelled') {
        clearInterval(_oauthTimer); _oauthTimer = null;
        _closeOauthModal();
        toast(tr('授权未成功: ') + (st.error || st.status), 'err');
      }
    } catch (e) { /* 轮询暂时失败，下一轮再试 */ }
  }, 2000);
}

function _oauthDeviceModal(name, flow) {
  _closeOauthModal();
  const wrap = document.createElement('div');
  wrap.id = 'oauthModal';
  wrap.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:9999;display:flex;align-items:center;justify-content:center';
  wrap.innerHTML = `<div style="background:var(--card-bg,#fff);color:var(--fg,#222);padding:24px 28px;border-radius:10px;max-width:420px;text-align:center">
    <h3 style="margin:0 0 12px">${esc(tr('授权 {n}').replace('{n}', name))}</h3>
    <div style="font-size:32px;letter-spacing:6px;font-weight:700;margin:8px 0;cursor:pointer" id="oauthCode"
      title="${esc(tr('点击复制'))}">${esc(flow.user_code || '')}</div>
    <p class="sethint">${esc(tr('1. 点击验证码复制 2. 点击下方链接打开授权页 3. 粘贴验证码并同意'))}</p>
    <p><a href="${esc(flow.verification_uri_complete || flow.verification_uri || '#')}" target="_blank" rel="noopener"
      style="word-break:break-all">${esc(flow.verification_uri_complete || flow.verification_uri || '')}</a></p>
    <p id="oauthFlowStatus" class="sethint"></p>
    <button type="button" class="btn ghost" data-act="provOauthCancel">${esc(tr('取消'))}</button>
  </div>`;
  document.body.appendChild(wrap);
  wrap.addEventListener('click', (e) => { if (e.target === wrap) provOauthCancel(); });
  const code = wrap.querySelector('#oauthCode');
  if (code) code.addEventListener('click', () => {
    try { navigator.clipboard.writeText(flow.user_code || ''); toast(tr('验证码已复制'), 'ok'); } catch (e) {}
  });
}

function _closeOauthModal() {
  const m = document.getElementById('oauthModal');
  if (m) m.remove();
}

export function provOauthCancel() {
  // 通过当前轮询的 flow id 取消：简单起见直接关弹窗+停止轮询，后端流程自然超时
  if (_oauthTimer) { clearInterval(_oauthTimer); _oauthTimer = null; }
  _closeOauthModal();
}

export async function provOauthDisconnect(node) {
  const name = node && node.dataset ? (node.dataset.arg || '') : '';
  if (!name) return;
  try {
    await api('/v1/oauth/' + encodeURIComponent(name) + '/disconnect',
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    toast(tr('{n} 已断开授权').replace('{n}', name), 'ok');
    await loadProviders();
    await _refreshModels();
  } catch (e) {
    toast(tr('断开失败: ') + ((e && (e.detail || e.message)) || String(e)), 'err');
  }
}

export function provGetKey(node) {
  const url = node && node.dataset ? (node.dataset.arg || '') : '';
  if (url) window.open(url, '_blank');
}

export async function provWebchatOpen(node) {
  const name = node && node.dataset ? (node.dataset.arg || '') : '';
  if (!name) return;
  // 起受管浏览器要好几秒，先提示；后端现在后台导航，接口会很快返回
  toast(tr('正在启动受管浏览器，登录页马上弹出…'), 'ok', 8000);
  try {
    const r = await api('/v1/providers/' + encodeURIComponent(name) + '/webchat-open',
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    toast(r.hint || tr('已在受管浏览器打开登录页'), 'ok', 8000);
  } catch (e) {
    toast(tr('打开登录页失败: ') + ((e && (e.detail || e.message)) || String(e)), 'err');
  }
}

export async function provTest(node) {
  // 表单里的"测试连接"用当前填写的值先保存再测（未保存的配置测不出真问题）
  let name = node && node.dataset ? (node.dataset.arg || '') : '';
  if (!name) {
    const box = _provEl('provForm');
    if (box && box.style.display !== 'none') {
      const body = _provCollect();
      if (!body) return;
      try {
        await api('/v1/providers', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body) });
        name = body.name;
        await loadProviders();
        await _refreshModels();
      } catch (e) {
        toast(tr('保存失败: ') + ((e && (e.detail || e.text || e.message)) || String(e)), 'err');
        return;
      }
    }
  }
  if (!name) return;
  toast(tr('正在测试 {n}…').replace('{n}', name), 'ok');
  try {
    const r = await api('/v1/providers/' + encodeURIComponent(name) + '/test', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: '{}' });
    if (r.ok) {
      const extra = r.kind === 'local' ? r.path : `${r.latency_ms}ms · ${r.models_count || 0} 个模型`;
      toast(tr('{n} 连接正常（{x}）').replace('{n}', name).replace('{x}', extra), 'ok');
    } else {
      toast(tr('{n} 连接失败: {e}').replace('{n}', name).replace('{e}', r.error || ''), 'err');
    }
  } catch (e) {
    toast(tr('测试失败: ') + ((e && (e.detail || e.text || e.message)) || String(e)), 'err');
  }
}

export async function provDetectModels() {
  // 用表单当前值（可能还没保存）探测：探测本身不写 config.json，
  // 结果只填进「模型列表」，用户确认后再点保存。
  const body = _provCollect();
  if (!body) return;
  if (!body.base_url) { toast(tr('请先填写接口地址'), 'err'); return; }
  toast(tr('正在向 {n} 探测可用模型…').replace('{n}', body.name), 'ok', 8000);
  try {
    const r = await api('/v1/providers/detect-models', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: body.name, base_url: body.base_url, api: body.api,
                             api_key: body.api_key || '', api_key_env: body.api_key_env || '' }),
    });
    if (!r.ok) { toast(tr('探测失败: ') + (r.error || ''), 'err'); return; }
    const list = r.models || [];
    if (!list.length) { toast(tr('接口没有返回任何模型'), 'err'); return; }
    const ta = _provEl('provModels');
    if (ta) ta.value = list.join('\n');
    toast(tr('检测到 {n} 个模型，已填入模型列表（点保存生效）').replace('{n}', list.length), 'ok', 6000);
  } catch (e) {
    toast(tr('探测失败: ') + ((e && (e.detail || e.text || e.message)) || String(e)), 'err');
  }
}

export async function provClearKey(node) {
  const name = (node && node.dataset && node.dataset.arg) || _editing || '';
  if (!name) return;
  if (!confirm(tr('清除 {n} 保存在 config.json 里的 API Key？（环境变量不受影响）').replace('{n}', name))) return;
  try {
    await api('/v1/providers', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, api_key: '' }) });
    toast(tr('已清除保存的 Key'), 'ok');
    await loadProviders();
    await _refreshModels();
    const p = _providers.find((x) => x.name === name);
    if (p) _provShowForm(p);
  } catch (e) {
    toast(tr('清除失败: ') + ((e && (e.detail || e.text || e.message)) || String(e)), 'err');
  }
}

export async function provSetDefault() {
  const sel = _provEl('provDefaultModel');
  const model = sel ? String(sel.value || '') : '';
  if (!model) { toast(tr('请选择一个模型'), 'err'); return; }
  try {
    await api('/v1/providers/default-model', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model }),
    });
    toast(tr('默认模型已设为 {m}').replace('{m}', model), 'ok');
    await loadProviders();
    try { if (window.refreshModels) window.refreshModels(); } catch (e) {}
  } catch (e) {
    toast(tr('设置失败: ') + ((e && (e.detail || e.text || e.message)) || String(e)), 'err');
  }
}

export function providersPaneHtml() {
  return `<h4>${esc(tr('模型提供商'))}</h4>
  <p class="sethint">${esc(tr('提供商决定“请求发给谁”。预设可直接用环境变量里的 Key；自定义支持 OpenAI 兼容与 Anthropic Messages 协议。API Key 只写入不回显，保存后立即生效。'))}</p>
  <label class="setrow"><span>${esc(tr('默认模型'))}</span>
    <select class="kbinput" id="provDefaultModel" data-act-input="provSetDefault">
      <option value="">${esc(tr('加载中…'))}</option></select></label>
  <div class="setbtns">
    <button type="button" class="btn" data-act="provNew">＋ ${esc(tr('新增提供商'))}</button>
    <button type="button" class="btn ghost" data-act="provRefresh">↻ ${esc(tr('刷新'))}</button>
  </div>
  <div id="provList" class="provlist"><p class="sethint">${esc(tr('加载中…'))}</p></div>
  <div id="provForm" class="provform" style="display:none"></div>`;
}
