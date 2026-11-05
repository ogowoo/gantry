"""Teams 的鉴权/令牌抓取、chatsvc 直发，以及全部只读排障接口。

`_HOOK_JS` 在页面里挂 fetch/XHR 钩子；authz 换 skype 令牌后走 chatsvc 发送。
所有 `api_*` 都是诊断/实验接口（供插件 HTTP 面板与排障脚本调用），默认只读。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import html as _html
import json
import re
import time
from pathlib import Path
from typing import Any

from . import bridge as _b
from .bridge import diagnostics
from .teams_common import (  # noqa: F401
    PAGE_LOCK_TIMEOUT, REPLY_TIMEOUT, TEAMS_URL, TeamsBusyError, TeamsError, _PAGE_LOCK,
    _norm_identity, _plain_text, acquire_page_lock, page_lock_stats, release_page_lock,
    verify_conversation_identity,
)

from .teams_dom import (  # noqa: F401
    _SEL, _existing_teams_page, _first_visible, _warm_ready, chat_kind,
    chat_participants, dom_summary, list_chats, recent_messages,
    signed_in_name, teams_open, teams_page,
)
_HOOK_JS = r"""
(() => {
  if (window.__gantryHookInstalled) return;
  window.__gantryHookInstalled = true;
  window.__gantryHookVer = 3;
  window.__gantryCaptured = window.__gantryCaptured || [];
  window.__gantryResponses = window.__gantryResponses || [];
  const interest = /chatsvc|ic3|authz|substrate|csa|messaging/i;
  const norm = (h) => {
    const o = {};
    try {
      if (!h) return o;
      if (typeof h.forEach === 'function' && !Array.isArray(h)) {
        h.forEach((v, k) => { o[String(k).toLowerCase()] = String(v); });
      } else if (Array.isArray(h)) {
        h.forEach((p) => { if (p && p.length === 2) o[String(p[0]).toLowerCase()] = String(p[1]); });
      } else if (typeof h === 'object') {
        for (const k of Object.keys(h)) o[String(k).toLowerCase()] = String(h[k]);
      }
    } catch (e) { }
    return o;
  };
  const push = (arr, item, cap) => { arr.push(item); if (arr.length > cap) arr.shift(); };
  const recReq = (url, method, headers, body) => {
    try {
      if (!interest.test(String(url))) return;
      push(window.__gantryCaptured, {url: String(url).slice(0, 240), method: method || '',
        headers: norm(headers), body: body ? String(body).slice(0, 1500) : '',
        at: Date.now()}, 30);
    } catch (e) { }
  };
  const recResp = (url, text) => {
    try {
      if (!interest.test(String(url))) return;
      push(window.__gantryResponses, {url: String(url).slice(0, 240),
        body: String(text || '').slice(0, 20000), at: Date.now()}, 20);
    } catch (e) { }
  };
  const OF = window.fetch;
  if (OF) {
    window.fetch = function (input, init) {
      var url = '', method = '', body = '';
      try {
        url = typeof input === 'string' ? input : ((input && input.url) || '');
        method = (init && init.method) || (input && input.method) || 'GET';
        body = (init && init.body) || '';
        recReq(url, method, (init && init.headers) || (input && input.headers) || {}, body);
      } catch (e) { }
      var pr = OF.apply(this, arguments);
      try {
        if (interest.test(String(url))) {
          pr.then(function (r) {
            try { r.clone().text().then(function (t) { recResp(url, t); }).catch(function () { }); }
            catch (e) { }
          }).catch(function () { });
        }
      } catch (e) { }
      return pr;
    };
  }
  const XO = XMLHttpRequest.prototype.open;
  const XS = XMLHttpRequest.prototype.setRequestHeader;
  const XD = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, url) {
    try { this.__gUrl = url; this.__gMethod = m; this.__gH = {}; } catch (e) { }
    return XO.apply(this, arguments);
  };
  XMLHttpRequest.prototype.setRequestHeader = function (k, v) {
    try { this.__gH[String(k).toLowerCase()] = String(v); } catch (e) { }
    return XS.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function (body) {
    try {
      var self = this;
      recReq(self.__gUrl || '', self.__gMethod || '', self.__gH || {}, body);
      if (interest.test(String(self.__gUrl || ''))) {
        self.addEventListener('load', function () {
          try {
            var t = (self.responseType === '' || self.responseType === 'text')
              ? self.responseText : '';
            if (!t && self.response) { try { t = JSON.stringify(self.response); } catch (e) { } }
            recResp(self.__gUrl || '', t);
          } catch (e) { }
        });
      }
    } catch (e) { }
    return XD.apply(this, arguments);
  };
})();
"""

API_AUTHZ = "https://teams.microsoft.com/api/authsvc/v1.0/authz"

API_CHATSVC = "https://chatsvcagg.teams.microsoft.com/v1"

AUTHZ_TYPES = ("ExplicitLogin", "Chat", "ChatSvc", "Messaging", "Teams", "Calling",
               "Middleware", "SkypeSpaces", "Registration", "Presence")

HOST_CANDIDATES = (
    "https://chatsvcagg.teams.microsoft.com",
    "https://emea.ng.msg.teams.microsoft.com",
    "https://emea.msgapi.teams.microsoft.com:444",
    "https://emea.csa.svcs.teams.office.com",
    "https://eu-prod.asyncgw.teams.microsoft.com",
    "https://api-emea.flightproxy.teams.microsoft.com/api/v2/ep/api.conv.skype.com",
    "https://emea.substratesync.teams.microsoft.com",
    "https://presence.teams.microsoft.com",
)

_PATHS = (
    "/v1/users/ME/conversations?view=msnp24Equivalent&pageSize=2",
    "/v1/users/ME/conversations?pageSize=2",
)

def _jwt_claims(tok: str) -> dict[str, Any]:
    """只读解码 JWT payload（排查 token 身份用，不验签）。"""
    try:
        part = str(tok or "").split(".")[1]
        part += "=" * (-len(part) % 4)
        data = json.loads(base64.urlsafe_b64decode(part))
        keep = ("oid", "sub", "upn", "preferred_username", "tid", "aud",
                "scp", "roles", "ver")
        return {k: data.get(k) for k in keep if k in data}
    except Exception:                            # noqa: BLE001
        return {}

def _claims_summary(tok: str) -> dict[str, Any]:
    c = _jwt_claims(tok)
    if not c:
        return {"parsed": False}
    keep = {}
    for k in ("aud", "iss", "scp", "roles", "appid", "app_displayname", "tid", "oid",
              "unique_name", "name", "exp", "nbf", "ver", "xms_cc", "skypeid",
              "service", "region"):
        if k in c:
            v = c[k]
            if isinstance(v, list):
                v = [str(x)[:60] for x in v][:6]
            elif isinstance(v, str):
                v = v[:120]
            keep[k] = v
    keep["claimsCount"] = len(c)
    return {"parsed": True, "claims": keep}

async def _python_replay_authz(req: dict[str, Any]) -> dict[str, Any]:
    """把页面里 authz 请求原样重放（Python 侧无 CORS），返回响应与是否含 skypeToken。"""
    import httpx

    url = str(req.get("url") or "")
    headers = {k: v for k, v in (req.get("headers") or {}).items()}
    body = str(req.get("body") or "") or None
    if not url:
        return {"error": "没有记录的请求"}
    for drop in ("host", "content-length", "connection", "accept-encoding"):
        headers.pop(drop, None)
    async with httpx.AsyncClient(timeout=40, follow_redirects=True) as c:
        r = await c.request(str(req.get("method") or "POST").upper(), url,
                            headers=headers, content=body)
    text = r.text
    out: dict[str, Any] = {"url": url, "status": r.status_code, "ok": bool(r.is_success),
                           "head": text[:300], "skype_found": "skypeToken" in text}
    if out["skype_found"]:
        import re as _re

        m = _re.search(r'"skypeToken"\s*:\s*"([^"]+)"', text)
        if m:
            out["skype_len"] = len(m.group(1))
            out["skype_probe"] = await _python_chatsvc_probe(m.group(1))
    return out

async def _python_chatsvc_probe(bearer: str) -> dict[str, Any]:
    """用抓到的令牌在 Python 侧请求 chatsvcagg（只读 GET）。"""
    try:
        import httpx
    except Exception as exc:                     # noqa: BLE001
        return {"error": f"httpx 不可用: {exc}"}
    results: list[dict[str, Any]] = []
    hosts = ("https://chatsvcagg.teams.microsoft.com",
             "https://teams.microsoft.com/api/chatsvc/emea")
    for host in hosts:
        url = host + "/v1/users/ME/conversations?view=msnp24Equivalent&pageSize=2"
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
                r = await c.get(url, headers={"Authorization": "Bearer " + bearer,
                                              "Accept": "application/json"})
            body = r.text[:200]
            results.append({"host": host, "status": r.status_code,
                            "ok": bool(r.is_success), "head": body})
        except Exception as exc:                 # noqa: BLE001
            results.append({"host": host, "error": str(exc)[:160]})
    return {"results": results}

def truncated(url) -> str:
    url = str(url or "")
    return url[:90] + ("…" if len(url) > 90 else "")

async def _ensure_skype_token(page, *, capture: bool = False) -> str:
    """拿 chatsvc 用的 skypeToken；优先缓存，其次 authz。

    `capture=True` 才会重载页面抓 token（诊断用）；发送路径默认不抓：
    重载/装钩子可能几十秒，会把 Teams 发送卡死（实测 120s 超时）。
    """
    skype = str(_CAPTURED.get("skype") or "")
    if skype:
        return skype
    try:
        tok = await api_token(page)
        skype = str(tok.get("skype") or "")
    except Exception:                            # noqa: BLE001
        skype = ""
    if skype:
        _CAPTURED["skype"] = skype
        return skype
    if not capture:
        return ""
    try:
        await api_hook_capture(page=None, reload=True, seconds=45)
    except Exception:                            # noqa: BLE001
        pass
    return str(_CAPTURED.get("skype") or "")

async def _resolve_conversation_id(page, conv_id: str, peer: str, token: str,
                                   snippet: str = "", *,
                                   expected: dict[str, Any] | None = None
                                   ) -> tuple[str, dict[str, Any]]:
    """用 chatsvc 的 conversations 列表校正/核验 id。

    有 `expected` 时走**严格模式**：只接受 id 完全一致且身份核验通过的会话；
    绝不用 peer/snippet 模糊匹配到另一个 id（发错会话比不发更严重）。
    """
    out = await page.evaluate("""async ({origin, token, targetId}) => {
      const mapConv = (c) => ({
        id: c.id || '',
        type: c.type || '',
        threadType: (c.threadProperties || {}).threadType || '',
        topic: (c.threadProperties || {}).topic || '',
        lastFrom: ((c.lastMessage || {}).imdisplayname
                   || (c.lastMessage || {}).fromDisplayName || ''),
        last: ((c.lastMessage || {}).content || '').slice(0, 240)
      });
      try {
        const r = await fetch(origin + '/api/chatsvc/emea/v1/users/ME/conversations'
                              + '?view=msnp24Equivalent&pageSize=50',
          {credentials: 'include',
           headers: {'X-Skypetoken': token, 'Accept': 'application/json'}});
        const text = await r.text();
        let j = {};
        try { j = JSON.parse(text); } catch (e) { }
        const conversations = (j.conversations || []).map(mapConv);
        // 列表可能只返回最近 50 条；按 id 直接取一次，避免把正常会话误判成"不可发送"
        let direct = null;
        let directStatus = null;
        if (targetId && !conversations.some(c => c.id === targetId)) {
          try {
            const dr = await fetch(origin + '/api/chatsvc/emea/v1/users/ME/conversations/'
                                   + encodeURIComponent(targetId) + '?view=msnp24Equivalent',
              {credentials: 'include',
               headers: {'X-Skypetoken': token, 'Accept': 'application/json'}});
            directStatus = dr.status;
            const dt = await dr.text();
            let dj = {};
            try { dj = JSON.parse(dt); } catch (e) { }
            if (dr.ok && dj.id) direct = mapConv(dj);
          } catch (e) { directStatus = 'error'; }
        }
        return {ok: r.ok, status: r.status, body: text.slice(0, 400),
                conversations, direct, directStatus};
      } catch (e) { return {ok: false, error: String(e)}; }
    }""", {"origin": "https://teams.cloud.microsoft", "token": token,
            "targetId": conv_id})
    convs = out.get("conversations") or []
    exact = next((c for c in convs if c.get("id") == conv_id), None)
    if exact is None and out.get("direct"):
        exact = out.get("direct")
    if expected:
        if exact is None:
            return conv_id, {"how": "not-found", "status": out.get("status"),
                             "direct_status": out.get("directStatus"),
                             "count": len(convs),
                             "verification": {"ok": False,
                                              "errors": ["会话 id 不在可发送列表里"]}}
        verification = verify_conversation_identity(exact, expected)
        return conv_id, {"how": "exact", "status": out.get("status"),
                         "conversation": exact, "verification": verification}
    # 1) id 完全一致
    for c in convs:
        if c.get("id") == conv_id:
            return conv_id, {"how": "same-id", "status": out.get("status"),
                             "conversation": c}
    p = (peer or "").strip().lower()
    tokens = [t for t in re.split(r"[^a-z0-9]+", p) if len(t) > 1]
    frag = " ".join((snippet or "").split())[:16].lower()

    def _plain(s: str) -> str:
        return " ".join(re.sub(r"<[^>]+>", " ", str(s or "")).split()).lower()

    def peer_ok(c: dict[str, Any]) -> bool:
        hay = _plain(str(c.get("topic") or "") + " " + str(c.get("lastFrom") or ""))
        return bool(tokens) and all(t in hay for t in tokens)
    # 2) peer + 最后消息片段（最稳，能区分同名/会议）
    if tokens and frag:
        for c in convs:
            if peer_ok(c) and frag in _plain(c.get("last")):
                return str(c.get("id") or conv_id), {
                    "how": "peer+frag", "status": out.get("status"),
                    "topic": c.get("topic"), "conversation": c}
    # 3) peer 且不是会议
    for c in convs:
        if peer_ok(c) and "meeting" not in str(c.get("type") or "").lower() \
                and "meeting" not in str(c.get("threadType") or "").lower() \
                and "会面" not in str(c.get("topic") or ""):
            return str(c.get("id") or conv_id), {
                "how": "peer-1to1", "status": out.get("status"), "topic": c.get("topic"),
                "conversation": c}
    # 没把握就不猜：交给 UI 回退，宁可慢也不要发错会话
    return conv_id, {"how": "not-found", "status": out.get("status"),
                     "count": len(convs), "head": out.get("body")}

async def api_send_message(page, conv_id: str, text: str,
                           peer: str = "", snippet: str = "", *,
                           expected: dict[str, Any] | None = None,
                           lock: bool = True) -> dict[str, Any]:
    """用 chatsvc API 直接发消息（完全绕开 UI）。

    有 `expected` 时先做**严格身份核验**（id/标题/对端/最后消息），任一不一致直接
    `reason=identity_mismatch` 返回，绝不发送，也不会回退到 UI 猜会话。

    关键：chatsvc 要 **`X-Skypetoken` 头**，不是 `Authorization: Bearer`；
    同源 `/api/chatsvc/emea/...` 才能 200（实测 401 都是认证方式不对）。
    """
    if lock:
        await acquire_page_lock()
        try:
            return await _api_send_message(page, conv_id, text, peer=peer,
                                           snippet=snippet, expected=expected)
        finally:
            release_page_lock()
    return await _api_send_message(page, conv_id, text, peer=peer,
                                   snippet=snippet, expected=expected)

async def _api_send_message(page, conv_id: str, text: str, *,
                            peer: str = "", snippet: str = "",
                            expected: dict[str, Any] | None = None) -> dict[str, Any]:
    page = page or await teams_open()
    skype = await _ensure_skype_token(page)
    if not skype:
        return {"ok": False, "reason": "api_unavailable",
                "error": "拿不到 skypetoken（发送路径不重载页面抓 token，改走 UI）"}
    page = teams_page or page
    claims = _jwt_claims(skype)
    conv_id, resolved = await _resolve_conversation_id(
        page, conv_id, peer, skype, snippet, expected=expected)
    verification = resolved.get("verification") or {"ok": True, "errors": []}
    if expected and (resolved.get("how") != "exact" or not verification.get("ok")):
        reason = "，".join(verification.get("errors") or ["会话 id 不在可发送列表里"])
        if resolved.get("how") == "not-found":
            # 不在列表 ≠ 身份不一致：可能只是 API 列表不包含这种 1:1。
            # 交给 reply_to 决定是否用 UI 回退（回退仍校验标题/对端）。
            return {"ok": False, "reason": "api_not_found",
                    "error": "API 发送不可用：" + reason,
                    "verification": verification, "resolved": resolved, "claims": claims}
        return {"ok": False, "reason": "identity_mismatch",
                "error": "发送前校验失败，已停止发送：" + reason,
                "verification": verification, "resolved": resolved, "claims": claims}
    if resolved.get("how") == "not-found":
        return {"ok": False, "error": "无法确认可发送的会话 id",
                "resolved": resolved, "claims": claims}
    body_text = _html.escape(str(text or "")).replace("\r\n", "\n").replace("\n", "<br>")
    body_html = f"<p>{body_text}</p>"
    # clientmessageid 稳定化：同一 (conv,message) 的自动重试在服务端也尽量去重
    seed = ""
    if expected:
        seed = f"{conv_id}|{expected.get('message_id') or ''}|{text or ''}"
    if seed:
        client_id = "gantry-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:24]
    else:
        client_id = str(int(time.time() * 1000))
    result = await page.evaluate("""async ({origin, token, id, html, client}) => {
      const urls = [
        origin + '/api/chatsvc/emea/v1/users/ME/conversations/'
               + encodeURIComponent(id) + '/messages',
        'https://chatsvcagg.teams.microsoft.com/v1/users/ME/conversations/'
               + encodeURIComponent(id) + '/messages'
      ];
      let last = {ok: false, error: 'no endpoint'};
      const attempts = [];
      for (const url of urls) {
        try {
          const r = await fetch(url, {
            method: 'POST',
            credentials: 'include',
            headers: {'X-Skypetoken': token,
                      'Content-Type': 'application/json',
                      'Accept': 'application/json'},
            body: JSON.stringify({
              content: html,
              messagetype: 'RichText/Html',
              contenttype: 'text',
              clientmessageid: client,
              imdisplayname: '',
              properties: {importance: '', subject: '', title: '',
                           links: '[]', files: '[]'}
            })
          });
          const text = await r.text();
          let j = {};
          try { j = JSON.parse(text); } catch (e) { }
          last = {ok: r.ok, status: r.status, id: (j.id || j.sequenceId || ''),
                  body: text.slice(0, 500), url: url.slice(0, 80)};
          attempts.push(last);
          if (r.ok) return {...last, attempts, pageUrl: location.href};
        } catch (e) {
          last = {ok: false, error: String(e), url: url.slice(0, 80)};
          attempts.push(last);
        }
      }
      return {...last, attempts, pageUrl: location.href};
    }""", {"origin": "https://teams.cloud.microsoft", "token": skype,
            "id": conv_id, "html": body_html, "client": client_id})
    result["claims"] = claims
    result["resolved"] = resolved
    return result

async def api_loki_probe(page) -> dict[str, Any]:
    """探测：从 sessionStorage 取 LokiAuthToken，逐个 region 试同源 chatsvc 接口。"""
    return await page.evaluate("""async () => {
      const readTok = () => {
        const keys = [];
        for (let i = 0; i < sessionStorage.length; i++) keys.push(sessionStorage.key(i));
        const hit = keys.find(k => /^LokiAuthToken$/i.test(k))
                 || keys.find(k => /LokiAuthToken/i.test(k));
        return hit ? {key: hit, token: sessionStorage.getItem(hit)} : null;
      };
      const tok = readTok();
      const origin = location.origin;
      const out = {origin, tokenKey: tok ? tok.key : '', tokenLen: tok ? tok.token.length : 0,
                   regions: []};
      if (!tok) return Object.assign(out, {error: 'sessionStorage 里没有 LokiAuthToken'});
      for (const region of ['emea', 'amer', 'apac', 'asia', 'india', 'gcc']) {
        try {
          const r = await fetch(origin + '/api/chatsvc/' + region +
                                '/v1/users/ME/conversations?view=msnp24Equivalent&pageSize=3',
                                {credentials: 'include',
                                 headers: {'Authorization': 'Bearer ' + tok.token,
                                           'Accept': 'application/json'}});
          const t = await r.text();
          out.regions.push({region, status: r.status, ok: r.ok, head: t.slice(0, 200)});
        } catch (e) {
          out.regions.push({region, error: String(e)});
        }
      }
      return out;
    }""")

async def api_chain_probe(page) -> dict[str, Any]:
    """诊断：Loki 令牌 → authz → skype 令牌 → chatsvc 是否可用（全只读）。"""
    return await page.evaluate("""async () => {
      const masks = (t) => t ? (t.slice(0, 8) + '…(' + t.length + ')') : '';
      const ss = {};
      for (let i = 0; i < sessionStorage.length; i++) {
        const k = sessionStorage.key(i);
        if (/LokiAuthToken/i.test(k)) ss[k] = sessionStorage.getItem(k);
      }
      const loki = ss['LokiAuthToken'] || Object.values(ss)[0] || '';
      const origin = location.origin;
      const out = {origin, loki: masks(loki), steps: []};
      const call = async (url, opts, label) => {
        try {
          const r = await fetch(url, Object.assign({credentials: 'include'}, opts));
          const t = await r.text();
          let j = null; try { j = JSON.parse(t); } catch (e) { }
          return {label, url, status: r.status, ok: r.ok,
                  head: t.slice(0, 160),
                  keys: j ? Object.keys(j) : [],
                  skype: (j && j.tokens && j.tokens.skypeToken) || '',
                  ice: (j && j.tokens && j.tokens.iceToken) || ''};
        } catch (e) { return {label, url, error: String(e)}; }
      };
      const authz = origin + '/api/authsvc/v1.0/authz';
      const variants = [
        ['authz: Bearer loki + {}', {method: 'POST', headers: {'Authorization': 'Bearer ' + loki,
          'Content-Type': 'application/json'}, body: '{}'}],
        ['authz: Bearer loki + payload', {method: 'POST', headers: {'Authorization': 'Bearer ' + loki,
          'Content-Type': 'application/json'}, body: JSON.stringify({parameters: {}}) }],
        ['authz: no auth + payload', {method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({parameters: {}}) }],
        ['authz: GET Bearer loki', {method: 'GET', headers: {'Authorization': 'Bearer ' + loki}}],
      ];
      let skype = '';
      for (const [label, opts] of variants) {
        const r = await call(authz, opts, label);
        r.skypeMasked = masks(r.skype);
        delete r.skype;
        out.steps.push(r);
        if (r.skypeMasked) { skype = r.skypeMasked; }
      }
      // 用 Loki 当 skypetoken 试 chatsvc（有些租户直接认 Loki）
      for (const region of ['emea', 'amer', 'apac']) {
        const r = await call(origin + '/api/chatsvc/' + region +
          '/v1/users/ME/conversations?view=msnp24Equivalent&pageSize=2',
          {headers: {'Authorization': 'Bearer ' + loki, 'Accept': 'application/json'}},
          'chatsvc/' + region + ' (loki)');
        out.steps.push(r);
      }
      return out;
    }""")

async def api_endpoints(page) -> dict[str, Any]:
    """页面自己请求过哪些内部接口（performance 条目）+ 同源候选探测。

    CORS 说明：从 `teams.cloud.microsoft` 里 fetch `teams.microsoft.com/api/...` 会被
    CORS 挡掉（实测 "TypeError: Failed to fetch"），所以只能调**同源**路径，
    或者从页面实际用过的 host 上找。
    """
    return await page.evaluate("""async () => {
      const res = performance.getEntriesByType('resource')
        .map(e => e.name)
        .filter(n => new RegExp('chatsvc|authsvc|csa|/api/', 'i').test(n));
      const uniq = [...new Set(res)];
      const hosts = [...new Set(uniq.map(u => { try { return new URL(u).host; } catch (e) { return ''; } }))];
      const origin = location.origin;
      // 同源候选：authz + chatsvc（region 试几种）
      const probes = [];
      const tryFetch = async (url, opts) => {
        try {
          const r = await fetch(url, Object.assign({credentials: 'include'}, opts || {}));
          const t = await r.text();
          return {url, status: r.status, ok: r.ok, head: t.slice(0, 120)};
        } catch (e) { return {url, error: String(e)}; }
      };
      probes.push(await tryFetch(origin + '/api/authsvc/v1.0/authz',
                                 {method: 'POST', headers: {'Content-Type': 'application/json'},
                                  body: '{}'}));
      for (const region of ['emea', 'amer', 'apac', 'asia', 'india']) {
        probes.push(await tryFetch(origin + '/api/chatsvc/' + region +
                                   '/v1/users/ME/conversations?view=msnp24Equivalent&pageSize=2'));
      }
      return {origin, hosts, sample_urls: uniq.slice(0, 15), probes};
    }""")

async def api_page_mimic(page=None) -> dict[str, Any]:
    """页面内请求同源 chatsvc：cookie + 令牌 + 客户端头（只读 GET）。"""
    import httpx

    page = page or await teams_open()
    cap = await page.evaluate("() => (window.__gantryCaptured || []).slice(-20)")
    req = None
    for c in reversed(cap or []):
        if "authsvc" in str(c.get("url") or ""):
            req = c
            break
    if not req:
        return {"error": "没有捕获到 authz 请求"}
    base = {k: v for k, v in (req.get("headers") or {}).items()
            if k in ("claimschallengecapable", "x-ms-client-type", "x-ms-client-version",
                     "x-ms-migration")}
    tok = str(_CAPTURED.get("skype") or "")
    if not tok:
        async with httpx.AsyncClient(timeout=45, follow_redirects=True) as c:
            r = await c.post(str(req.get("url") or ""),
                             headers=dict(req.get("headers") or {}),
                             content=(req.get("body") or "").encode() or None)
        m = re.search(r'"skypeToken"\s*:\s*"([^"]+)"', r.text)
        tok = m.group(1) if m else ""
    out: dict[str, Any] = {"hasToken": bool(tok), "clientHeaders": base}
    if not tok:
        return {**out, "error": "没有令牌"}
    out["results"] = await page.evaluate("""async ({token, extra}) => {
      const urls = [
        '/api/chatsvc/emea/v1/users/ME/conversations?view=msnp24Equivalent&pageSize=3',
        '/api/chatsvc/emea/v1/users/ME/conversations?pageSize=3',
        '/api/chatsvc/unified/v1/users/ME/conversations?view=msnp24Equivalent&pageSize=3'
      ];
      const out = [];
      for (const url of urls) {
        for (const [label, hdrs] of [
            ['token+client', Object.assign({'Authorization': 'Bearer ' + token}, extra)],
            ['token', {'Authorization': 'Bearer ' + token}],
            ['client', Object.assign({}, extra)]]) {
          try {
            const r = await fetch(url, {credentials: 'include',
              headers: Object.assign({'Accept': 'application/json'}, hdrs)});
            const t = await r.text();
            out.push({url: url.slice(0, 60), label, status: r.status, ok: r.ok,
                      head: t.slice(0, 160)});
          } catch (e) { out.push({url: url.slice(0, 60), label, error: String(e)}); }
        }
      }
      return out;
    }""", {"token": tok, "extra": base})
    return out

async def api_mimic_probe(page=None) -> dict[str, Any]:
    """用 authz 的完整头 + 新令牌去请求 chatsvcagg（尽量模仿真客户端）。"""
    import httpx

    page = page or await teams_open()
    cap = await page.evaluate("() => (window.__gantryCaptured || []).slice(-20)")
    req = None
    for c in reversed(cap or []):
        if "authsvc" in str(c.get("url") or ""):
            req = c
            break
    if not req:
        return {"error": "没有捕获到 authz 请求"}
    base = {k: v for k, v in (req.get("headers") or {}).items()
            if k not in ("host", "content-length", "connection", "accept-encoding",
                         "authorization", "cookie")}
    url = str(req.get("url") or "")
    body = (req.get("body") or "").encode() or None
    out: dict[str, Any] = {"mimicHeaders": base}
    tokens: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=45, follow_redirects=True) as c:
        try:
            r = await c.post(url, headers={**base, **({"authorization": (req.get("headers") or {}).get("authorization", "")})},
                             content=body)
            for m in re.finditer(r'"([A-Za-z0-9_]*[Tt]oken)"\s*:\s*"([^"]{40,})"', r.text):
                tokens.setdefault(m.group(1), m.group(2))
            out["mint_status"] = r.status_code
        except Exception as exc:                 # noqa: BLE001
            return {**out, "error": f"铸令牌失败: {exc}"}
        out["tokenKeys"] = list(tokens)
        results = []
        targets = [
            "https://chatsvcagg.teams.microsoft.com/v1/users/ME/conversations"
            "?view=msnp24Equivalent&pageSize=3",
            "https://emea.msgapi.teams.microsoft.com:444/v1/users/ME/conversations"
            "?view=msnp24Equivalent&pageSize=3",
        ]
        for name, tok in tokens.items():
            for hero in ("Bearer " + tok, tok):
                for target in targets:
                    hdrs = dict(base)
                    hdrs["authorization"] = hero
                    try:
                        rr = await c.get(target, headers=hdrs)
                        results.append({"token": name,
                                        "auth_style": "bearer" if hero.startswith("Bearer") else "raw",
                                        "host": target.split("/v1/")[0],
                                        "status": rr.status_code,
                                        "ok": bool(rr.is_success),
                                        "head": rr.text[:120]})
                        if rr.is_success:
                            _CAPTURED["chatsvc_token"] = tok
                            _CAPTURED["chatsvc_host"] = target.split("/v1/")[0]
                            out["winner"] = results[-1]
                            out["results"] = results
                            return out
                    except Exception as exc:     # noqa: BLE001
                        results.append({"host": target.split("/v1/")[0],
                                        "error": str(exc)[:100]})
    out["results"] = results
    return out

async def api_authz_types(page=None) -> dict[str, Any]:
    """对每个 authz type 铸令牌并试 chatsvcagg，返回矩阵（只读）。"""
    import httpx

    page = page or await teams_open()
    cap = await page.evaluate("() => (window.__gantryCaptured || []).slice(-20)")
    req = None
    for c in reversed(cap or []):
        if "authsvc" in str(c.get("url") or ""):
            req = c
            break
    if not req:
        return {"error": "没有捕获到 authz 请求"}
    base = dict(req.get("headers") or {})
    url = str(req.get("url") or "")
    body = (req.get("body") or "").encode() or None
    for drop in ("host", "content-length", "connection", "accept-encoding"):
        base.pop(drop, None)
    out: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=45, follow_redirects=True) as c:
        for t in AUTHZ_TYPES:
            hdrs = dict(base)
            hdrs["ms-teams-authz-type"] = t
            rec: dict[str, Any] = {"type": t}
            try:
                r = await c.post(url, headers=hdrs, content=body)
                rec["status"] = r.status_code
                toks = {}
                for m in re.finditer(r'"([A-Za-z0-9_]*[Tt]oken)"\s*:\s*"([^"]{40,})"',
                                     r.text):
                    toks.setdefault(m.group(1), m.group(2))
                rec["tokenKeys"] = list(toks)
                rec["probes"] = []
                for name, tok in toks.items():
                    pr = await _python_chatsvc_probe(tok)
                    for item in pr.get("results") or []:
                        rec["probes"].append({"token": name, "host": item.get("host"),
                                              "status": item.get("status"),
                                              "head": str(item.get("head"))[:80]})
                        if item.get("ok"):
                            _CAPTURED["chatsvc_token"] = tok
                            _CAPTURED["chatsvc_host"] = item.get("host")
                            _CAPTURED["chatsvc_authz_type"] = t
                            rec["WINNER"] = True
                            out.append(rec)
                            return {"results": out, "winner": {"type": t,
                                                              "token": name,
                                                              "host": item.get("host")}}
            except Exception as exc:             # noqa: BLE001
                rec["error"] = str(exc)[:120]
            out.append(rec)
    return {"results": out}

async def api_replay_authz(page=None) -> dict[str, Any]:
    """重放页面捕获的 authz 请求（Python 侧），拿新令牌并试 chatsvcagg。"""
    import httpx

    page = page or await teams_open()
    cap = await page.evaluate("() => (window.__gantryCaptured || []).slice(-20)")
    req = None
    for c in reversed(cap or []):
        if "authsvc" in str(c.get("url") or ""):
            req = c
            break
    if not req:
        return {"error": "没有捕获到 authz 请求（先跑 hook-capture 或 scroll-capture）"}
    headers = dict(req.get("headers") or {})
    body = req.get("body") or ""
    url = str(req.get("url") or "")
    method = str(req.get("method") or "POST").upper()
    interesting = {k: v for k, v in headers.items()
                   if k not in ("authorization", "cookie")}
    out: dict[str, Any] = {"url": url, "method": method,
                           "headers": interesting, "bodyLen": len(body),
                           "hasAuth": bool(headers.get("authorization"))}
    for drop in ("host", "content-length", "connection", "accept-encoding"):
        headers.pop(drop, None)
    try:
        async with httpx.AsyncClient(timeout=45, follow_redirects=True) as c:
            r = await c.request(method, url, headers=headers,
                                content=body.encode() if body else None)
        text = r.text
        out["status"] = r.status_code
        out["head"] = text[:200]
        tokens = {}
        for m in re.finditer(r'"([A-Za-z0-9_]*[Tt]oken)"\s*:\s*"([^"]{40,})"', text):
            tokens.setdefault(m.group(1), m.group(2))
        out["tokenKeys"] = list(tokens)
        probes = []
        for name, tok in tokens.items():
            pr = await _python_chatsvc_probe(tok)
            probes.append({"token": name, "results": pr.get("results")})
            for item in pr.get("results") or []:
                if item.get("ok"):
                    _CAPTURED["chatsvc_token"] = tok
                    _CAPTURED["chatsvc_token_name"] = name
                    _CAPTURED["chatsvc_host"] = item.get("host")
                    out["winner"] = {"token": name, "host": item.get("host")}
        out["per_token"] = probes
    except Exception as exc:                     # noqa: BLE001
        out["error"] = str(exc)[:200]
    return out

async def api_captured(page=None, limit: int = 8) -> dict[str, Any]:
    """读当前页面 `__gantryCaptured` / `__gantryResponses` 的摘要（不刷新、不点击）。"""
    page = page or await teams_open()
    return await page.evaluate("""({limit}) => {
      const mask = (v) => v ? (String(v).slice(0, 20) + '…') : '';
      const reqs = (window.__gantryCaptured || []).slice(-limit).map(c => ({
        url: (c.url || '').slice(0, 160), method: c.method || '',
        headerNames: Object.keys(c.headers || {}),
        auth: mask((c.headers || {}).authorization || ''),
        formTo: (c.headers || {})['x-skypetoken'] ? 'x-skypetoken' : '',
        bodyLen: (c.body || '').length
      }));
      const resps = (window.__gantryResponses || []).slice(-limit).map(r => ({
        url: (r.url || '').slice(0, 160), len: (r.body || '').length,
        head: (r.body || '').slice(0, 100)
      }));
      return {hookVer: window.__gantryHookVer || 0, requests: reqs, responses: resps};
    }""", {"limit": limit})

async def api_scroll_capture(page=None, rounds: int = 4,
                             seconds: float = 60.0) -> dict[str, Any]:
    """在消息区向上滚动，触发 App 加载历史消息，然后看钩子抓到了什么。"""
    page = page or await teams_open()
    out: dict[str, Any] = {"scrolled": 0, "before": None, "after": None}
    out["before"] = await api_captured(page, 5)
    pane = None
    for sel in ('[data-tid="message-pane-list-viewport"]',
                '[data-tid="message-pane-list-runway"]',
                '[data-tid="message-pane-body"]'):
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                pane = loc
                break
        except Exception:                        # noqa: BLE001
            continue
    if pane is None:
        out["error"] = "找不到消息区"
        return out
    try:
        box = await pane.bounding_box()
        if box:
            await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            for _ in range(max(1, rounds)):
                await page.mouse.wheel(0, -900)   # 向上翻历史
                out["scrolled"] += 1
                await asyncio.sleep(1.2)
    except Exception as exc:                     # noqa: BLE001
        out["error"] = f"滚动失败: {str(exc)[:120]}"
    deadline = time.monotonic() + max(10.0, seconds)
    while time.monotonic() < deadline:
        cap = await api_captured(page, 5)
        if any("chatsvc" in (r.get("url") or "") or "/messages" in (r.get("url") or "")
               for r in cap.get("requests") or []):
            out["after"] = cap
            return out
        await asyncio.sleep(2.0)
    out["after"] = await api_captured(page, 5)
    return out

async def api_try_headers() -> dict[str, Any]:
    """用已保存的 skypeToken 试多种头组合（只读 GET）。"""
    import httpx

    tok = str(_CAPTURED.get("skype") or "")
    if not tok:
        return {"error": "没有已抓到的 skypeToken（先跑 api/hook-capture）"}
    base_headers = {
        "Origin": "https://teams.cloud.microsoft",
        "Referer": "https://teams.cloud.microsoft/",
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"),
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
    }
    styles = {
        "bearer": {"Authorization": "Bearer " + tok},
        "bearer-cf": {"authorization": "Bearer " + tok},
        "skypetoken": {"X-Skypetoken": tok},
        "bearer-prefixed": {"Authorization": "Bearer skypetoken=" + tok},
        "authorization-raw": {"Authorization": tok},
    }
    urls = [
        "https://chatsvcagg.teams.microsoft.com/v1/users/ME/conversations"
        "?view=msnp24Equivalent&pageSize=2",
        "https://chatsvcagg.teams.microsoft.com/v1/users/ME/conversations?pageSize=2",
    ]
    out = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        for url in urls:
            for name, hdr in styles.items():
                try:
                    r = await c.get(url, headers={**base_headers, **hdr})
                    out.append({"url": url.split("?")[0].split("/v1/")[-1], "style": name,
                                "status": r.status_code, "ok": bool(r.is_success),
                                "head": r.text[:140]})
                except Exception as exc:         # noqa: BLE001
                    out.append({"style": name, "error": str(exc)[:120]})
    return {"results": out}

async def api_tokens_all(page=None, seconds: float = 150.0) -> dict[str, Any]:
    """新开页面抓 authz 响应 → 列出所有令牌 → 逐个试 chatsvcagg。"""
    cap = await api_hook_capture(page=None, reload=True, seconds=seconds)
    out: dict[str, Any] = {"hook": {k: cap.get(k) for k in
                                    ("new_page", "hook_ver", "skype_found", "url")}}
    page = teams_page
    if page is None:
        return {**out, "error": "没有页面"}
    try:
        resps = await page.evaluate("() => (window.__gantryResponses || [])")
    except Exception as exc:                     # noqa: BLE001
        return {**out, "error": f"读响应失败: {exc}"}
    tokens: dict[str, str] = {}
    for r in resps or []:
        body = str(r.get("body") or "")
        if "skypeToken" not in body and "Token" not in body:
            continue
        # 用正则抽（响应可能仍被截断，json.loads 会失败）
        for m in re.finditer(r'"([A-Za-z0-9_]*[Tt]oken)"\s*:\s*"([^"]{40,})"', body):
            tokens.setdefault(m.group(1), m.group(2))
    out["tokenKeys"] = {k: len(v) for k, v in tokens.items()}
    if not tokens:
        return {**out, "error": "authz 响应里没解析到令牌"}
    results = []
    for name, tok in tokens.items():
        probe = await _python_chatsvc_probe(tok)
        results.append({"token": name, "len": len(tok),
                        "results": probe.get("results")})
    out["per_token"] = results
    # 保留一个可用的
    for item in results:
        for r in item.get("results") or []:
            if r.get("ok"):
                _CAPTURED["chatsvc_token"] = tokens[item["token"]]
                _CAPTURED["chatsvc_token_name"] = item["token"]
                _CAPTURED["chatsvc_host"] = r.get("host")
                out["winner"] = {"token": item["token"], "host": r.get("host")}
                return out
    return out

async def api_hosts_probe(page=None) -> dict[str, Any]:
    """用当前 skype 令牌逐个 host 试（只读 GET），找出真正可用的那个。"""
    import httpx

    tok = str(_CAPTURED.get("skype") or "")
    if not tok and page is not None:
        await api_hook_capture(page=page, reload=False, seconds=5)
        tok = str(_CAPTURED.get("skype") or "")
    if not tok:
        return {"error": "没有 skype 令牌（先 hook-capture）"}
    headers = {
        "Authorization": "Bearer " + tok,
        "Accept": "application/json",
        "x-ms-client-type": "web",
        "x-ms-client-version": "1415/26100212018",
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"),
        "Origin": "https://teams.cloud.microsoft",
        "Referer": "https://teams.cloud.microsoft/",
    }
    results = []
    async with httpx.AsyncClient(timeout=25, follow_redirects=True, verify=False) as c:
        for host in HOST_CANDIDATES:
            for path in _PATHS:
                url = host + path
                try:
                    r = await c.get(url, headers=headers)
                    results.append({"url": url[:110], "status": r.status_code,
                                    "ok": bool(r.is_success),
                                    "head": r.text[:110]})
                    if r.is_success:
                        _CAPTURED["chatsvc_token"] = tok
                        _CAPTURED["chatsvc_host"] = host
                        return {"results": results, "winner": {"url": url,
                                                               "host": host}}
                except Exception as exc:         # noqa: BLE001
                    results.append({"url": url[:110], "error": str(exc)[:80]})
    return {"results": results}

async def api_decode_tokens() -> dict[str, Any]:
    """解码当前保存的令牌（skype / loki），给出 aud/scp 等声明。"""
    out: dict[str, Any] = {}
    skype = str(_CAPTURED.get("skype") or "")
    loki = str(_CAPTURED.get("auth") or "")
    loki = loki.split(" ", 1)[1] if loki.lower().startswith("bearer ") else loki
    out["skype"] = _claims_summary(skype) if skype else {"missing": True}
    out["loki"] = _claims_summary(loki) if loki else {"missing": True}
    if skype:
        # 顺便按 aud 里的主机再试一次（如果 aud 本身是个 URL）
        aud = str((_jwt_claims(skype) or {}).get("aud") or "")
        out["aud"] = aud
        hosts = []
        for cand in aud.split(" "):
            cand = cand.strip().rstrip("/")
            if cand.startswith("http"):
                hosts.append(cand)
        out["audHosts"] = hosts
        if hosts:
            out["aud_probe"] = await _python_chatsvc_probe(skype)
    return out

async def api_typing_capture(seconds: float = 30.0) -> dict[str, Any]:
    """新开页面装钩子 → 在输入框打字（不回车）→ 读抓到的请求 → 清空输入框。"""
    out: dict[str, Any] = {}
    cap = await api_hook_capture(reload=True, seconds=5)
    out["hook"] = {k: cap.get(k) for k in ("new_page", "hook_ver", "ready", "url")}
    page = teams_page
    if page is None:
        return {**out, "error": "没有页面"}
    # 等聊天区可用
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            if await _warm_ready(page):
                break
        except Exception:                        # noqa: BLE001
            pass
        await asyncio.sleep(2)
    box = None
    for sel in _SEL["composer"]:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                box = loc
                break
        except Exception:                        # noqa: BLE001
            continue
    if box is None:
        return {**out, "error": "找不到输入框"}
    typed = False
    try:
        await box.click()
        await page.keyboard.type("x", delay=60)   # 只打一个字符，绝不回车
        typed = True
        await asyncio.sleep(2.0)
    except Exception as exc:                     # noqa: BLE001
        out["type_error"] = str(exc)[:120]
    # 等抓包
    deadline = time.monotonic() + max(10.0, seconds)
    hits: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        try:
            cap2 = await api_captured(page, 8)
        except Exception:                        # noqa: BLE001
            cap2 = {}
        reqs = cap2.get("requests") or []
        hits = [r for r in reqs
                if any(k in str(r.get("url") or "")
                       for k in ("chatsvc", "messaging", "substrate", "msgapi", "ic3"))]
        if hits:
            break
        await asyncio.sleep(1.5)
    out["typed"] = typed
    out["hits"] = hits[:6]
    out["all"] = (cap2.get("requests") or [])[:6] if "cap2" in dir() else []
    # 清理输入框（全选 + 删除，不回车）
    try:
        await box.click()
        await page.keyboard.press("Control+a")
        await page.keyboard.press("Backspace")
    except Exception:                            # noqa: BLE001
        pass
    return out

async def api_sw_capture2(page=None, seconds: float = 45.0,
                          reload_page: bool = True) -> dict[str, Any]:
    """经由 Target 转发会话抓 service worker 的网络请求（URL + 头）。"""
    import json as _json

    page = page or await teams_open()
    ctx = _b.ctx
    out: dict[str, Any] = {"targets": 0, "sessionIds": [], "hits": [], "errors": []}
    try:
        cdp = await ctx.new_cdp_session(page)
    except Exception as exc:                     # noqa: BLE001
        return {"error": f"无法建立 CDP 会话: {exc}"}
    hit_list: list[dict[str, Any]] = []
    captured_auth: dict[str, Any] = {}

    def on_msg(params):
        try:
            raw = params.get("message") or "{}"
            data = _json.loads(raw)
            if data.get("method") != "Network.requestWillBeSent":
                return
            req = (data.get("params") or {}).get("request") or {}
            url = str(req.get("url") or "")
            if not any(k in url for k in ("chatsvc", "msgapi", "substrate", "ic3",
                                          "messaging", "conversations")):
                return
            hdr = {str(k).lower(): str(v) for k, v in (req.get("headers") or {}).items()}
            hit_list.append({"url": url[:190], "method": req.get("method"),
                             "headerNames": sorted(hdr.keys())[:16],
                             "auth": (hdr.get("authorization") or "")[:30],
                             "hasCookie": bool(hdr.get("cookie"))})
            if hdr.get("authorization") and not captured_auth:
                captured_auth.update(url=url, headers=hdr,
                                     method=req.get("method") or "GET")
        except Exception:                        # noqa: BLE001
            pass

    cdp.on("Target.receivedMessageFromTarget", on_msg)
    try:
        tg = await cdp.send("Target.getTargets")
        infos = (tg or {}).get("targetInfos") or []
        out["targets"] = len(infos)
        sws = [t for t in infos if t.get("type") == "service_worker"
               and "teams" in str(t.get("url") or "").lower()]
        if not sws:
            sws = [t for t in infos if t.get("type") == "service_worker"][:3]
        next_id = 1
        for sw in sws[:4]:
            try:
                res = await cdp.send("Target.attachToTarget",
                                     {"targetId": sw["targetId"], "flatten": False})
                sid = (res or {}).get("sessionId")
                if not sid:
                    continue
                out["sessionIds"].append(sid)
                for method in ("Network.enable",):
                    await cdp.send("Target.sendMessageToTarget", {
                        "sessionId": sid,
                        "message": _json.dumps({"id": next_id, "method": method}),
                    })
                    next_id += 1
            except Exception as exc:             # noqa: BLE001
                out["errors"].append(f"attach 失败: {str(exc)[:100]}")
        # 触发流量：先重载页面（App 启动同步必然请求会话列表），再滚一下
        try:
            if reload_page:
                await page.reload(wait_until="domcontentloaded", timeout=180_000)
                await asyncio.sleep(3)
        except Exception as exc:                 # noqa: BLE001
            out["errors"].append(f"reload 失败: {str(exc)[:100]}")
        try:
            pane = page.locator('[data-tid="message-pane-list-viewport"]').first
            if await pane.count():
                box = await pane.bounding_box()
                if box:
                    await page.mouse.move(box["x"] + box["width"] / 2,
                                          box["y"] + box["height"] / 2)
                    for _ in range(3):
                        await page.mouse.wheel(0, -900)
                        await asyncio.sleep(1.5)
        except Exception:                        # noqa: BLE001
            pass
        deadline = time.monotonic() + max(10.0, seconds)
        while time.monotonic() < deadline and not hit_list:
            await asyncio.sleep(1.5)
    finally:
        try:
            await cdp.detach()
        except Exception:                        # noqa: BLE001
            pass
    out["hits"] = hit_list[:10]
    if captured_auth:
        _SW2["url"] = captured_auth.get("url")
        _SW2["headers"] = captured_auth.get("headers") or {}
        _SW2["method"] = captured_auth.get("method")
        out["captured"] = {"url": truncated(_SW2["url"]),
                           "headerNames": sorted((_SW2["headers"] or {}).keys())[:16],
                           "auth": str((_SW2["headers"] or {}).get("authorization") or "")[:20]}
    return out

async def api_sw_capture(page=None, seconds: float = 60.0) -> dict[str, Any]:
    """对每个 service worker 开 CDP Network，抓 chatsvc 请求的 URL 与头部。"""
    page = page or await teams_open()
    ctx = _b.ctx
    out: dict[str, Any] = {"workers": 0, "attached": 0, "hits": [], "errors": []}
    sessions = []
    hits: list[dict[str, Any]] = []

    def make_handler(label):
        def handler(params):
            try:
                req = params.get("request") or {}
                url = str(req.get("url") or "")
                if not any(k in url for k in ("chatsvc", "ic3", "msgapi", "conversations",
                                              "authz")):
                    return
                hdr = {str(k).lower(): str(v) for k, v in (req.get("headers") or {}).items()}
                rec = {"via": label, "url": url[:200], "method": req.get("method"),
                       "headerNames": sorted(hdr.keys())[:14],
                       "auth": (hdr.get("authorization") or "")[:26],
                       "hasCookie": bool(hdr.get("cookie")),
                       "xclient": {k: v[:40] for k, v in hdr.items()
                                   if k.startswith("x-client")}}
                if len(hits) < 12:
                    hits.append(rec)
                # 记录最新一条完整头，供后续照抄调用
                if hdr.get("authorization") or hdr.get("cookie"):
                    _SW_LAST["url"] = url
                    _SW_LAST["headers"] = hdr
                    _SW_LAST["method"] = req.get("method") or "GET"
            except Exception:                    # noqa: BLE001
                pass
        return handler

    try:
        workers = list(ctx.service_workers)
    except Exception as exc:                     # noqa: BLE001
        out["errors"].append(f"读取 service_workers 失败: {exc}")
        workers = []
    out["workers"] = len(workers)
    for i, w in enumerate(workers):
        try:
            cdp = await ctx.new_cdp_session(w)
            await cdp.send("Network.enable")
            cdp.on("Network.requestWillBeSent", make_handler(f"sw{i}"))
            sessions.append(cdp)
            out["attached"] += 1
        except Exception as exc:                 # noqa: BLE001
            out["errors"].append(f"sw{i} attach 失败: {str(exc)[:120]}")
    # 也给页面挂一份（SW 之外的请求）
    try:
        pcdp = await ctx.new_cdp_session(page)
        await pcdp.send("Network.enable")
        pcdp.on("Network.requestWillBeSent", make_handler("page"))
        sessions.append(pcdp)
    except Exception as exc:                     # noqa: BLE001
        out["errors"].append(f"page attach 失败: {str(exc)[:120]}")
    deadline = time.monotonic() + max(10.0, seconds)
    while time.monotonic() < deadline and not hits:
        await asyncio.sleep(1.5)
    out["hits"] = hits[:10]
    out["lastUrl"] = truncated(_SW_LAST.get("url"))
    out["lastHeaderNames"] = sorted((_SW_LAST.get("headers") or {}).keys())[:16]
    for cdp in sessions:
        try:
            await cdp.detach()
        except Exception:                        # noqa: BLE001
            pass
    return out

async def api_cookie_probe(page=None) -> dict[str, Any]:
    """同源 chatsvc 请求：分别试 只带 cookie / 带 skypeToken / 带 Loki，比较结果。"""
    page = page or await teams_open()
    skype = str(_CAPTURED.get("skype") or "")
    loki = ""
    try:
        loki = str(_CAPTURED.get("auth") or "")
        loki = loki.split(" ", 1)[1] if loki.lower().startswith("bearer ") else loki
    except Exception:                            # noqa: BLE001
        loki = ""
    return await page.evaluate("""async ({skype, loki}) => {
      const urls = [
        '/api/chatsvc/emea/v1/users/ME/conversations?view=msnp24Equivalent&pageSize=2',
        '/api/chatsvc/emea/v1/users/ME/conversations?pageSize=2',
        '/api/csa/emea/api/v1/teams/users/me'
      ];
      const variants = [
        ['cookie-only', {}],
        ['bearer-skype', skype ? {'Authorization': 'Bearer ' + skype} : null],
        ['bearer-loki', loki ? {'Authorization': 'Bearer ' + loki} : null],
        ['header-skypetoken', skype ? {'X-Skypetoken': skype} : null]
      ];
      const out = [];
      for (const url of urls) {
        for (const [label, extra] of variants) {
          if (extra === null) continue;
          try {
            const r = await fetch(url, {credentials: 'include',
              headers: Object.assign({'Accept': 'application/json'}, extra)});
            const t = await r.text();
            out.push({url, label, status: r.status, ok: r.ok, head: t.slice(0, 160)});
          } catch (e) {
            out.push({url, label, error: String(e)});
          }
        }
      }
      return {results: out};
    }""", {"skype": skype, "loki": loki})

async def api_gtm_hosts(page=None) -> dict[str, Any]:
    """从 tmp.auth.v1.*.Discover.DISCOVER-REGION-GTM 里提取所有 host（只读）。"""
    page = page or await teams_open()
    return await page.evaluate(r"""() => {
      const out = {hosts: [], urls: [], region: '', keys: []};
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (!/Discover\.DISCOVER-REGION-GTM$/.test(k)) continue;
        let j = null;
        try { j = JSON.parse(localStorage.getItem(k) || '{}'); } catch (e) { continue; }
        const item = j.item || {};
        out.keys.push(k.slice(-50));
        out.region = item.region || item.regionCode || out.region;
        const walk = (obj, path) => {
          if (!obj || typeof obj !== 'object') return;
          for (const key of Object.keys(obj)) {
            const v = obj[key];
            if (typeof v === 'string') {
              if (/^https?:\/\//i.test(v)) {
                out.urls.push({path: path + '.' + key, url: v.slice(0, 120)});
                try { out.hosts.push(new URL(v).host); } catch (e) { }
              }
            } else if (v && typeof v === 'object') {
              walk(v, path + '.' + key);
            }
          }
        };
        walk(item, '$');
        break;
      }
      out.hosts = [...new Set(out.hosts)];
      return out;
    }""")

async def api_hook_capture(page=None, reload: bool = True,
                           seconds: float = 150.0) -> dict[str, Any]:
    """装钩子（context 级）→ 新开一个 Teams 页 → 抓 authz 响应里的 skypeToken。"""
    global teams_page
    ctx = _b.ctx
    out: dict[str, Any] = {"at": time.strftime("%H:%M:%S")}
    try:
        await ctx.add_init_script(_HOOK_JS)
        out["hook_registered"] = "context"
    except Exception as exc:                     # noqa: BLE001
        out["hook_registered"] = f"失败: {str(exc)[:120]}"
    target = None
    if reload:
        try:
            # 优先重载现有 Teams 页；每次抓令牌都新开标签会把窗口堆满
            target = teams_page if (teams_page and not teams_page.is_closed()) else None
            if target is None:
                target = await _existing_teams_page(ctx)
            if target is None:
                target = await ctx.new_page()
                out["new_page"] = True
            await target.goto(TEAMS_URL, wait_until="domcontentloaded", timeout=180_000)
        except Exception as exc:                 # noqa: BLE001
            out["open_error"] = str(exc)[:200]
            target = None
    if target is None:
        target = page or await teams_open()
        try:
            await target.reload(wait_until="domcontentloaded", timeout=180_000)
        except Exception as exc:                 # noqa: BLE001
            out["reload_error"] = str(exc)[:160]
    teams_page = target
    out["url"] = truncated(target.url)
    # 等钩子就位
    ver = 0
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            ver = int(await target.evaluate("() => window.__gantryHookVer || 0") or 0)
        except Exception:                        # noqa: BLE001
            ver = 0
        if ver >= 3:
            break
        await asyncio.sleep(1.5)
    out["hook_ver"] = ver
    # 等 authz 响应里出现 skypeToken
    skype = ""
    resps: list[dict[str, Any]] = []
    deadline = time.monotonic() + max(20.0, seconds)
    while time.monotonic() < deadline:
        try:
            resps = await target.evaluate("() => (window.__gantryResponses || []).slice(-10)")
        except Exception:                        # noqa: BLE001
            resps = []
        for r in resps or []:
            # 只认 Teams 域的 authz：Copilot/m365 页也会发 authz，抓到它的
            # token 去发 Teams 消息会 403（not a member of roster，实测）。
            if "teams." not in str(r.get("url") or "").lower():
                continue
            body = str(r.get("body") or "")
            m = re.search(r'"skypeToken"\s*:\s*"([^"]+)"', body)
            if m:
                skype = m.group(1)
                break
        if skype:
            break
        await asyncio.sleep(2.5)
    out["responses"] = [{"url": r.get("url"), "head": str(r.get("body") or "")[:100]}
                        for r in (resps or [])[-5:]]
    out["skype_found"] = bool(skype)
    if skype:
        _CAPTURED["skype"] = skype
        _CAPTURED["skype_at"] = time.time()
        out["skype_len"] = len(skype)
        out["probe"] = await _python_chatsvc_probe(skype)
    return out

async def api_cdp_capture(page, seconds: float = 60.0) -> dict[str, Any]:
    """CDP 抓包：记录 chatsvc/IC3 请求的 URL 与 Authorization，并用抓到的令牌重放。"""
    ctx = _b.ctx
    hits: list[dict[str, Any]] = []
    auth = {"value": "", "url": "", "headers": {}}
    try:
        cdp = await ctx.new_cdp_session(page)
        await cdp.send("Network.enable")
    except Exception as exc:                     # noqa: BLE001
        return {"error": f"CDP 不可用: {exc}"}

    def on_request(params):
        try:
            req = params.get("request") or {}
            url = str(req.get("url") or "")
            if not any(k in url for k in ("chatsvc", "ic3", "authz", "/api/csa", "substrate")):
                return
            hdr = {str(k).lower(): str(v) for k, v in (req.get("headers") or {}).items()}
            rec = {"url": url[:170], "method": req.get("method"),
                   "auth": (hdr.get("authorization") or "")[:26],
                   "hasCookies": bool(hdr.get("cookie"))}
            if len(hits) < 25:
                hits.append(rec)
            if not auth["value"] and hdr.get("authorization"):
                auth.update(value=hdr["authorization"], url=url,
                            headers={k: v for k, v in hdr.items()
                                     if k in ("authorization", "x-client-info", "x-client-version",
                                              "x-skypetoken", "content-type", "accept")})
        except Exception:                        # noqa: BLE001
            pass

    cdp.on("Network.requestWillBeSent", on_request)
    try:
        # 触发懒加载：把鼠标移到会话列表上滚几下（不点任何会话项）
        try:
            first = page.locator(_SEL["chat_item"][0]).first
            if await first.count():
                box = await first.bounding_box()
                if box:
                    await page.mouse.move(box["x"] + box["width"] * 0.6, box["y"] + 10)
                    for _ in range(3):
                        await page.mouse.wheel(0, 500)
                        await asyncio.sleep(1.0)
                    for _ in range(3):
                        await page.mouse.wheel(0, -500)
                        await asyncio.sleep(1.0)
        except Exception:                        # noqa: BLE001
            pass
        deadline = time.monotonic() + max(10.0, seconds)
        while time.monotonic() < deadline and not auth["value"]:
            await asyncio.sleep(1.0)
    finally:
        try:
            await cdp.detach()
        except Exception:                        # noqa: BLE001
            pass
    out: dict[str, Any] = {"captured": bool(auth["value"]), "authLen": len(auth["value"]),
                           "authHead": auth["value"][:16],
                           "fromUrl": truncated(auth["url"]),
                           "headers": auth.get("headers") or {}, "hits": hits[:12]}
    if auth["value"]:
        _CAPTURED["auth"] = auth["value"]
        _CAPTURED["at"] = time.time()
        tok = auth["value"]
        bearer = tok.split(" ", 1)[1] if tok.lower().startswith("bearer ") else tok
        probes = []
        for region in ("emea", "amer", "apac"):
            try:
                r = await page.evaluate("""async ({region, token}) => {
                  const r = await fetch(location.origin + '/api/chatsvc/' + region +
                    '/v1/users/ME/conversations?view=msnp24Equivalent&pageSize=2',
                    {credentials: 'include', headers: {'Authorization': 'Bearer ' + token,
                                                       'Accept': 'application/json'}});
                  const t = await r.text();
                  return {status: r.status, ok: r.ok, head: t.slice(0, 200)};
                }""", {"region": region, "token": bearer})
                probes.append({"region": region, **r})
            except Exception as exc:             # noqa: BLE001
                probes.append({"region": region, "error": str(exc)[:120]})
        out["probes"] = probes
    return out

async def api_capture(page, seconds: float = 45.0, force_ping: bool = True) -> dict[str, Any]:
    """监听页面请求，抓一条带 Authorization 的 chatsvc 请求头。"""
    state: dict[str, Any] = {"hits": [], "auth": None, "url": None}

    def on_request(req):
        try:
            url = req.url or ""
        except Exception:                        # noqa: BLE001
            return
        if not any(k in url for k in ("chatsvc", "ic3", "/api/csa", "authz", "substrate")):
            return
        hdrs = dict(req.headers or {})
        auth = hdrs.get("authorization") or ""
        rec = {"url": url[:180], "method": req.method,
               "auth": (auth[:24] + "…") if auth else "",
               "keys": [k for k in hdrs if k.lower().startswith(("x-", "authorization"))][:8]}
        if len(state["hits"]) < 20:
            state["hits"].append(rec)
        if auth and not state["auth"]:
            state["auth"] = auth
            state["url"] = url
            state["headers"] = {k: v for k, v in hdrs.items()
                                if k.lower() in ("x-client-info", "x-client-version",
                                                 "x-skypetoken", "authorization",
                                                 "content-type", "accept")}

    # ⚠ 必须挂 context：Teams 的聊天同步由 service worker 发出，page 级事件收不到
    ctx = _b.ctx
    ctx.on("request", on_request)
    try:
        # 触发一点流量：让页面自己认为"有活动"（不要改消息，只是读接口）
        if force_ping:
            try:
                # 用真实鼠标悬停导航栏的 Chat（不发消息、不只点会话项）
                nav = page.locator('[aria-label="Chat"], [data-tid="app-bar-link-Chat"]').first
                if await nav.count():
                    box = await nav.bounding_box()
                    if box:
                        await page.mouse.move(box["x"] + box["width"] / 2,
                                              box["y"] + box["height"] / 2)
            except Exception:                    # noqa: BLE001
                pass
        deadline = time.monotonic() + max(5.0, seconds)
        while time.monotonic() < deadline and not state["auth"]:
            await asyncio.sleep(1.0)
    finally:
        try:
            ctx.remove_listener("request", on_request)
        except Exception:                        # noqa: BLE001
            pass
    out = {"captured": bool(state["auth"]),
           "authLen": len(state["auth"] or ""),
           "authHead": (state["auth"] or "")[:16],
           "fromUrl": truncated(state["url"]),
           "headers": state.get("headers") or {},
           "hits": state["hits"][:10]}
    if state["auth"]:
        _CAPTURED["auth"] = state["auth"]
        _CAPTURED["at"] = time.time()
        _CAPTURED["url"] = state["url"] or ""
        # 用抓到的令牌试同源 chatsvc
        tok = state["auth"]
        bearer = tok.split(" ", 1)[1] if tok.lower().startswith("bearer ") else tok
        probes = []
        for region in ("emea", "amer", "apac"):
            try:
                r = await page.evaluate("""async ({region, token}) => {
                  const r = await fetch(location.origin + '/api/chatsvc/' + region +
                    '/v1/users/ME/conversations?view=msnp24Equivalent&pageSize=2',
                    {credentials: 'include', headers: {'Authorization': 'Bearer ' + token,
                                                       'Accept': 'application/json'}});
                  const t = await r.text();
                  return {status: r.status, ok: r.ok, head: t.slice(0, 160)};
                }""", {"region": region, "token": bearer})
                probes.append({"region": region, **r})
            except Exception as exc:             # noqa: BLE001
                probes.append({"region": region, "error": str(exc)[:120]})
        out["probes"] = probes
    return out

async def api_shape_probe(page) -> dict[str, Any]:
    """只报结构：raw 长度、JSON 顶层键、item 的类型/键/前缀。"""
    return await page.evaluate(r"""() => {
      const out = [];
      const keys = [];
      for (let i = 0; i < localStorage.length; i++) keys.push(localStorage.key(i));
      const targets = keys.filter(k => /^tmp\.auth\.v1\./.test(k) &&
        /CHATSVCAGG|SKYPE-TOKEN|SKYPE-AUTHZ-URL|DISCOVER-REGION-GTM|EncryptionKey/i.test(k));
      for (const k of targets) {
        const raw = localStorage.getItem(k) || '';
        let top = null;
        try { top = JSON.parse(raw); } catch (e) { }
        const rec = {key: k.slice(-70), rawLen: raw.length,
                     topKeys: top ? Object.keys(top) : null,
                     rawHead: raw.slice(0, 100)};
        if (top && top.item !== undefined) {
          rec.itemType = typeof top.item;
          rec.itemLen = typeof top.item === 'string' ? top.item.length : null;
          rec.itemHead = typeof top.item === 'string' ? top.item.slice(0, 100)
                                                     : JSON.stringify(top.item).slice(0, 100);
          if (typeof top.item === 'string') {
            let inner = null;
            try { inner = JSON.parse(top.item); } catch (e) { }
            if (inner && typeof inner === 'object') {
              rec.innerKeys = Object.keys(inner);
              rec.innerHead = JSON.stringify(inner).slice(0, 120);
            }
          }
        }
        out.push(rec);
      }
      return {count: out.length, records: out};
    }""")

async def api_authcache_probe(page) -> dict[str, Any]:
    """解析 tmp.auth.v1.* 缓存，取出 region 与两个令牌，并逐个试同源 chatsvc（只读）。"""
    return await page.evaluate(r"""async () => {
      const mask = (v) => v ? (String(v).slice(0, 10) + '…(' + String(v).length + ')') : '';
      const parseSafe = (raw) => { try { return JSON.parse(raw); } catch (e) { return null; } };
      // 从 {"item": ...} 里挖出真正的字符串（最多三层）
      const unwrap = (obj, depth) => {
        if (depth > 4 || obj === null || obj === undefined) return null;
        if (typeof obj === 'string') return obj;
        if (typeof obj === 'object') {
          for (const k of ['item', 'value', 'token', 'Token', 'data']) {
            if (obj[k] !== undefined) {
              const v = unwrap(obj[k], depth + 1);
              if (v) return v;
            }
          }
        }
        return null;
      };
      const keys = [];
      for (let i = 0; i < localStorage.length; i++) keys.push(localStorage.key(i));
      const authKeys = keys.filter(k => /^tmp\.auth\.v1\./.test(k));
      const pick = (re) => authKeys.filter(k => re.test(k));
      const info = {};
      const dig = (keyRe, label) => {
        const hit = pick(keyRe);
        info[label] = {keys: hit, values: []};
        for (const k of hit.slice(0, 3)) {
          const raw = localStorage.getItem(k) || '';
          const j = parseSafe(raw);
          const token = unwrap(j, 0);
          info[label].values.push({key: k.slice(-60), parsedKeys: j ? Object.keys(j) : [],
                                   tokenPreview: mask(token)});
        }
        return info[label].values.map(v => v.tokenPreview);
      };
      const regionPreview = dig(/Discover\.DISCOVER-REGION-GTM$/, 'region');
      const authzPreview = dig(/Discover\.SKYPE-AUTHZ-URL$/, 'authzUrl');
      const skypePreview = dig(/Discover\.SKYPE-TOKEN$/, 'skypeToken');
      const svcPreview = dig(/Token\.HTTPS:\/\/CHATSVCAGG\.TEAMS/i, 'chatsvcToken');
      // 取实际值（只用于本次请求，不返回）
      const tokenOf = (re) => {
        for (const k of pick(re)) {
          const j = parseSafe(localStorage.getItem(k) || '');
          const t = unwrap(j, 0);
          if (t) return t;
        }
        return '';
      };
      const chatsvcTok = tokenOf(/Token\.HTTPS:\/\/CHATSVCAGG\.TEAMS/i);
      const skypeTok = tokenOf(/Discover\.SKYPE-TOKEN$/);
      const regionRaw = tokenOf(/Discover\.DISCOVER-REGION-GTM$/);
      let region = '';
      try {
        const rj = parseSafe(regionRaw);
        region = (rj && (rj.region || rj.regionCode || rj.Region || rj.value)) || '';
      } catch (e) { }
      if (!region) {
        const m = String(regionRaw || '').match(/"region(Code)?"\s*:\s*"([^"]+)"/i);
        region = m ? m[2] : '';
      }
      const out = {region, regionRawPreview: mask(regionRaw), authzUrl: authzPreview,
                   skype: skypePreview, chatsvc: svcPreview, info, probes: []};
      const regions = region ? [region.toLowerCase(),
                                region.toLowerCase() === 'emea' ? 'amer' : 'emea'] : ['emea'];
      const tryTok = async (label, tok, reg) => {
        if (!tok) return;
        try {
          const r = await fetch(location.origin + '/api/chatsvc/' + reg +
            '/v1/users/ME/conversations?view=msnp24Equivalent&pageSize=2',
            {credentials: 'include', headers: {'Authorization': 'Bearer ' + tok,
                                               'Accept': 'application/json'}});
          const t = await r.text();
          out.probes.push({label, region: reg, status: r.status, ok: r.ok, head: t.slice(0, 140)});
        } catch (e) { out.probes.push({label, region: reg, error: String(e)}); }
      };
      for (const reg of [...new Set(regions)]) {
        await tryTok('chatsvcagg-token', chatsvcTok, reg);
        await tryTok('skype-token', skypeTok, reg);
      }
      return out;
    }""")

async def api_deep_probe(page) -> dict[str, Any]:
    """盘点所有凭证材料，并逐个候选令牌试同源 chatsvc（只读）。"""
    return await page.evaluate("""async () => {
      const mask = (v) => v ? (String(v).slice(0, 8) + '…(' + String(v).length + ')') : '';
      const keys = (st) => { const r = []; for (let i = 0; i < st.length; i++) r.push(st.key(i)); return r; };
      const lsKeys = keys(localStorage), ssKeys = keys(sessionStorage);
      // 候选：名字里带 skype/auth/token/ica/grant 的
      const cand = [];
      const push = (where, k, v) => cand.push({where, key: k, preview: mask(v), raw: v});
      for (const k of lsKeys) if (/skype|auth|token|ica|grant|login/i.test(k)) push('ls', k, localStorage.getItem(k));
      for (const k of ssKeys) if (/skype|auth|token|ica|grant|login/i.test(k)) push('ss', k, sessionStorage.getItem(k));
      // IndexedDB 库名/表名（只看结构，不读值）
      let dbs = [];
      try {
        const list = indexedDB.databases ? await indexedDB.databases() : [];
        for (const d of list) {
          const meta = {name: d.name, version: d.version, stores: []};
          try {
            const db = await new Promise((res, rej) => {
              const req = indexedDB.open(d.name);
              req.onsuccess = () => res(req.result);
              req.onerror = () => rej(req.error);
            });
            meta.stores = [...db.objectStoreNames];
            db.close();
          } catch (e) { meta.error = String(e); }
          dbs.push(meta);
        }
      } catch (e) { dbs = [{error: String(e)}]; }
      // 逐个候选试 chatsvc（emea 一个 region 够判断）
      const probes = [];
      const url = location.origin + '/api/chatsvc/emea/v1/users/ME/conversations?view=msnp24Equivalent&pageSize=2';
      for (const c of cand.slice(0, 12)) {
        if (!c.raw || c.raw.length < 40) continue;
        try {
          const r = await fetch(url, {credentials: 'include',
            headers: {'Authorization': 'Bearer ' + c.raw, 'Accept': 'application/json'}});
          const t = await r.text();
          probes.push({key: c.where + ':' + c.key, status: r.status, ok: r.ok,
                       head: t.slice(0, 120)});
        } catch (e) { probes.push({key: c.where + ':' + c.key, error: String(e)}); }
      }
      return {lsKeys, ssKeys, candidates: cand.map(c => ({where: c.where, key: c.key,
                                                         preview: c.preview})),
              dbs, probes};
    }""")

async def api_storage(page) -> dict[str, Any]:
    """页面里可复用的会话材料：cookie 名、localStorage/sessionStorage 键。

    只报**名字与前缀/长度**，不打印完整令牌（避免日志泄漏）。
    """
    return await page.evaluate("""() => {
      const mask = (v) => v ? (v.slice(0, 6) + '…(' + v.length + ')') : '';
      const cookies = document.cookie.split(';').map(c => c.trim().split('=')[0]).filter(Boolean);
      const ls = {};
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (/token|auth|skype|msal|credential/i.test(k)) {
          ls['ls:' + k] = mask(localStorage.getItem(k));
        }
      }
      const ss = {};
      for (let i = 0; i < sessionStorage.length; i++) {
        const k = sessionStorage.key(i);
        if (/token|auth|skype|msal/i.test(k)) {
          ss['ss:' + k] = mask(sessionStorage.getItem(k));
        }
      }
      const skypeCookies = cookies.filter(c => /skype|token|auth/i.test(c));
      return {cookies: cookies.slice(0, 40), skypeCookies, localStorage: ls,
              sessionStorage: ss};
    }""")

async def api_token(page) -> dict[str, Any]:
    """从页面里换出 chatsvc 用的令牌（同源 fetch，自动带 cookie）。

    Teams 新版页面在 `teams.cloud.microsoft`，旧实现硬编码
    `teams.microsoft.com/api/authsvc` 会跨域失败（TypeError: Failed to fetch）。
    先试 `location.origin`，再退回旧域名。
    """
    return await page.evaluate("""async () => {
      const bases = [location.origin, 'https://teams.microsoft.com'];
      let last = {error: 'no authz endpoint'};
      for (const base of bases) {
        try {
          const r = await fetch(base + '/api/authsvc/v1.0/authz',
                                {method: 'POST', credentials: 'include',
                                 headers: {'Content-Type': 'application/json'},
                                 body: '{}'});
          const j = await r.json().catch(() => ({}));
          const t = (j && j.tokens) || {};
          if (t.skypeToken) {
            return {status: r.status, region: j.region || '',
                    skype: t.skypeToken, hasIce: !!(t.iceToken || ''),
                    keys: Object.keys(t), base};
          }
          last = {status: r.status, region: j.region || '', skype: '',
                  keys: Object.keys(t), base};
        } catch (e) {
          last = {error: String(e), base};
        }
      }
      return last;
    }""")

async def api_conversations(page, limit: int = 30) -> dict[str, Any]:
    """读会话列表（只读）。返回原始结构里我们关心的字段。"""
    tok = await api_token(page)
    if not tok.get("skype"):
        return {"ok": False, "error": "拿不到 skypetoken", "token": tok}
    return await page.evaluate("""async ({base, token, limit}) => {
      try {
        const url = base + '/users/ME/conversations?view=msnp24Equivalent&pageSize=' + limit;
        const r = await fetch(url, {headers: {'Authorization': 'Bearer ' + token,
                                              'Accept': 'application/json'},
                                   credentials: 'include'});
        const text = await r.text();
        let j = {};
        try { j = JSON.parse(text); } catch (e) { }
        const convs = (j.conversations || []).map(c => ({
          id: c.id || '', type: c.type || '',
          threadType: (c.threadProperties && c.threadProperties.threadType) || '',
          topic: (c.threadProperties && c.threadProperties.topic) || '',
          last: ((c.lastMessage || {}).content || '').slice(0, 120),
          lastFrom: ((c.lastMessage || {}).from || '').split('/').pop(),
          lastAt: (c.lastMessage || {}).composetime || '',
          members: ((c.threadProperties || {}).memberCount) || null
        }));
        return {ok: r.ok, status: r.status, count: convs.length,
                total: (j._metadata || {}).totalCount || null, conversations: convs,
                rawHead: text.slice(0, 200)};
      } catch (e) { return {ok: false, error: String(e)}; }
    }""", {"base": API_CHATSVC, "token": tok["skype"], "limit": limit})

async def api_messages(page, conv_id: str, limit: int = 20) -> dict[str, Any]:
    """读某个会话的消息（只读）。"""
    tok = await api_token(page)
    if not tok.get("skype"):
        return {"ok": False, "error": "拿不到 skypetoken", "token": tok}
    return await page.evaluate("""async ({base, token, id, limit}) => {
      try {
        const url = base + '/users/ME/conversations/' + encodeURIComponent(id)
                  + '/messages?view=msnp24Equivalent&pageSize=' + limit;
        const r = await fetch(url, {headers: {'Authorization': 'Bearer ' + token,
                                              'Accept': 'application/json'},
                                   credentials: 'include'});
        const j = await r.json().catch(() => ({}));
        const msgs = (j.messages || []).map(m => ({
          id: m.id || '', type: m.messagetype || '',
          from: (m.from || '').split('/').pop(),
          at: m.composetime || '',
          content: (m.content || '').replace(/<[^>]+>/g, '').slice(0, 300)
        }));
        return {ok: r.ok, status: r.status, count: msgs.length, messages: msgs};
      } catch (e) { return {ok: false, error: String(e)}; }
    }""", {"base": API_CHATSVC, "token": tok["skype"], "id": conv_id, "limit": limit})

async def api_probe() -> dict[str, Any]:
    """诊断：令牌是否拿到、会话列表能否读、1:1 与群聊怎么区分。"""
    page = await teams_open()
    out: dict[str, Any] = {}
    tok = await api_token(page)
    out["token"] = {k: (v if k != "skype" else ("***" if v else ""))
                    for k, v in (tok or {}).items()}
    out["endpoints"] = await api_endpoints(page)
    out["storage"] = await api_storage(page)
    out["loki"] = await api_loki_probe(page)
    out["chain"] = await api_chain_probe(page)
    out["deep"] = await api_deep_probe(page)
    out["authcache"] = await api_authcache_probe(page)
    out["shape"] = await api_shape_probe(page)
    out["capture"] = await api_capture(page, seconds=15)
    out["cdp"] = await api_cdp_capture(page, seconds=45)
    convs = await api_conversations(page, 30)
    out["conversations_ok"] = convs.get("ok")
    out["status"] = convs.get("status")
    out["total"] = convs.get("total")
    items = convs.get("conversations") or []
    out["count"] = len(items)
    out["sample"] = items[:12]
    one2one = [c for c in items if not str(c.get("id", "")).startswith("19:")]
    out["direct_like"] = one2one[:6]
    if one2one:
        first = one2one[0]["id"]
        out["messages_sample"] = await api_messages(page, first, 8)
    return out

async def probe(dump_dir: Path | None = None, max_html: int = 200_000) -> dict[str, Any]:
    """诊断：把"当前命中了哪些选择器 + 页面里有什么"dump 出来，用于校准选择器。

    换 Teams 版本后先跑这个（插件 API `GET api/probe` 也行），别靠猜。
    """
    page = await teams_open(timeout=120)
    out: dict[str, Any] = {"url": page.url, "title": await page.title(),
                           "at": time.strftime("%Y-%m-%d %H:%M:%S"), "counts": {},
                           "chats": [], "messages": [], "composer": None}
    for key in _SEL:
        hits = {}
        for sel in _SEL[key]:
            try:
                n = await page.locator(sel).count()
            except Exception:                   # noqa: BLE001
                n = -1
            if n:
                hits[sel] = n
        out["counts"][key] = hits
    try:
        out["chats"] = (await list_chats(10))[:10]
    except Exception as exc:                    # noqa: BLE001
        out["chats_error"] = str(exc)[:200]
    try:
        out["signed_in"] = await signed_in_name(page)
        out["participants"] = await chat_participants()
        out["kind"] = await chat_kind()
        out["messages"] = (await recent_messages(10))[-6:]
    except Exception as exc:                    # noqa: BLE001
        out["messages_error"] = str(exc)[:200]
    try:
        box = await _first_visible(page, "composer")
        out["composer"] = bool(box)
    except Exception:                           # noqa: BLE001
        out["composer"] = None
    out["dom"] = await dom_summary(page)
    if dump_dir is not None:
        dump_dir = Path(dump_dir)
        dump_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        try:
            html = await page.content()
            path = dump_dir / f"teams-{stamp}.html"
            path.write_text(html[:max_html], encoding="utf-8")
            out["html"] = str(path)
        except Exception:                       # noqa: BLE001
            pass
        try:
            body_text = (await page.inner_text("body"))[:5000]
            path2 = dump_dir / f"teams-{stamp}.txt"
            path2.write_text(body_text, encoding="utf-8")
            out["text"] = str(path2)
        except Exception:                       # noqa: BLE001
            pass
    return out

def probe_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


_CAPTURED: dict[str, Any] = {}

_SW2: dict[str, Any] = {}

_SW_LAST: dict[str, Any] = {}
