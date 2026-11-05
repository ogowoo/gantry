"""Teams 的 IndexedDB 读取（会话列表 / 最后一条消息 / 成员映射）。

只读：用 `page.evaluate` 在 Teams 页面里打开 `Teams:conversation-manager` 等库，
把 351 个会话、248 个私聊、2362 个 profile 映射读出来，供 teams-bot 判断"谁发了什么"。
不点 DOM、不需要 IC3 令牌。

`teams_open()` 用函数内惰性 import，避免 `teams.py` 与本模块的循环依赖。
"""
from __future__ import annotations

import asyncio
from typing import Any


async def api_idb_watch(page=None, limit: int = 400,
                        since: float = 0.0) -> dict[str, Any]:
    """会话监看视图：id / 是否 1:1 / 对方 mri / 最后一条消息（只读）。

    `since` > 0 时只返回 `lastTime` 之后的会话（增量扫描，减少跨进程传输）。
    """
    from .teams import teams_open  # 惰性 import：避免与 teams.py 循环
    page = page or await teams_open()
    return await page.evaluate(r"""async ({limit, since}) => {
      const openDb = (name) => new Promise((res, rej) => {
        const req = indexedDB.open(name);
        req.onsuccess = () => res(req.result);
        req.onerror = () => rej(req.error);
      });
      const readAll = (db, store, cap) => new Promise((res) => {
        const items = [];
        try {
          const tx = db.transaction(store, 'readonly');
          const req = tx.objectStore(store).openCursor();
          req.onsuccess = () => {
            const cur = req.result;
            if (!cur || items.length >= cap) return res(items);
            items.push({key: cur.key, value: cur.value});
            cur.continue();
          };
          req.onerror = () => res(items);
        } catch (e) { res(items); }
      });
      const names = (await indexedDB.databases()).map(d => d.name || '');
      const convDbName = names.find(n => /conversation-manager/i.test(n));
      if (!convDbName) return {error: '找不到 conversation-manager'};
      const cdb = await openDb(convDbName);
      const rows = (await readAll(cdb, 'conversations', limit)).map(r => r.value);
      cdb.close();

      // 人名牌：profiles 库（mri → 显示名）
      const prof = {};
      const profDbName = names.find(n => /:profiles:/i.test(n));
      if (profDbName) {
        try {
          const pdb = await openDb(profDbName);
          const stores = [...pdb.objectStoreNames];
          const store = stores.includes('profiles') ? 'profiles' : stores[0];
          const items = await readAll(pdb, store, 4000);
          pdb.close();
          for (const it of items) {
            const v = it.value || {};
            const mri = String(v.mri || v.id || it.key || '');
            const name = String(v.displayName || v.name || (v.profile && v.profile.name) || '');
            if (mri && name) prof[mri] = name;
          }
        } catch (e) { }
      }
      // Skype 租户 id：DB 名里 `skypexspaces-<guid>`；也是私聊 id 里两个 GUID 之一
      let skypeTenant = '';
      for (const n of names) {
        const mm = /skypexspaces-([0-9a-fA-F-]{36})/i.exec(n);
        if (mm) { skypeTenant = mm[1].toLowerCase(); break; }
      }
      const directRe = /^19:([0-9a-fA-F-]{36})_([0-9a-fA-F-]{36})@unq\.gbl\.spaces$/;
      const out = [];
      for (const v of rows) {
        if (since > 0 && (v.lastMessageTimeUtc || 0) <= since) continue;
        const tp = v.threadProperties || {};
        const lm = v.lastMessage || {};
        const id = String(v.id || '');
        const m = directRe.exec(id);
        let peer = '';
        if (m) {
          // 两个 GUID 里一个是 Skype 租户，另一个才是对方
          const a = m[1].toLowerCase(), b = m[2].toLowerCase();
          if (skypeTenant) peer = (a === skypeTenant) ? b : a;
          else peer = a === '5c899237-c5e3-44bf-8b5f-8f7e24b3e5d1' ? b : a;
        }
        out.push({
          id,
          isDirect: !!m,
          peerGuid: peer,
          peerName: peer ? (prof['8:orgid:' + peer] || prof[peer] || '') : '',
          memberKeys: Object.keys(v.memberProperties || {}).length,
          topic: tp.topic || '',
          threadType: tp.threadType || '',
          targetLink: String(v.targetLink || ''),
          lastText: String(lm.content || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 200),
          lastFrom: lm.imdisplayname || lm.fromDisplayNameInToken || '',
          lastAt: lm.composetime || '',
          lastTime: v.lastMessageTimeUtc || 0,
          lastId: String(lm.clientmessageid || ''),
          lastType: lm.messagetype || '',
          // @ 在 Teams 里不一定以 "@名字" 出现在 content 里（常见是纯文本 +
          // properties.mentions 元数据，或者 <at> 标签被上面剥掉）。
          // 原样带出来给插件判断，别只靠 lastText 里有没有 '@'。
          // mentions 有时是 JSON 字符串（"[]"），统一解析成数组，避免被当成名字。
          lastMentions: (() => {
            const raw = (lm.properties || {}).mentions;
            let arr = raw;
            if (typeof raw === 'string') {
              try { arr = JSON.parse(raw); } catch (e) { arr = []; }
            }
            return Array.isArray(arr) ? arr.slice(0, 8) : [];
          })(),
          // 未读：Teams 用 properties.consumptionhorizon 记录已读水位
          // （形如 "<msgId>;<...>;<clientmessageid>"）。水位里没有最后一条
          // 消息的 id → 未读。没有水位时按已读处理，避免首次全量误回。
          unread: (() => {
            const ch = String((v.properties || {}).consumptionhorizon || '').trim();
            if (!ch) return false;
            const ids = new Set(ch.split(';').map(s => s.trim()).filter(Boolean));
            const lastId = String(lm.id || '');
            const lastClient = String(lm.clientmessageid || '');
            return !((lastId && ids.has(lastId)) || (lastClient && ids.has(lastClient)));
          })()
        });
      }
      out.sort((a, b) => (b.lastTime || 0) - (a.lastTime || 0));
      const direct = out.filter(c => c.isDirect);
      return {total: out.length, direct: direct.length,
              group: out.length - direct.length, profiles: Object.keys(prof).length,
              skypeTenant,
              list: out.slice(0, 50),
              directSample: direct.slice(0, 6), recent: out.slice(0, 6)};
    }""", {"limit": max(50, limit), "since": float(since or 0)})

async def api_idb_record(page=None, skip: int = 0) -> dict[str, Any]:
    """取第 skip 条会话记录，输出字段类型与 threadProperties 全量（只读）。"""
    from .teams import teams_open  # 惰性 import：避免与 teams.py 循环
    page = page or await teams_open()
    return await page.evaluate(r"""async ({skip}) => {
      const openDb = (name) => new Promise((res, rej) => {
        const req = indexedDB.open(name);
        req.onsuccess = () => res(req.result);
        req.onerror = () => rej(req.error);
      });
      const names = (await indexedDB.databases()).map(d => d.name || '');
      const target = names.find(n => /conversation-manager/i.test(n));
      if (!target) return {error: 'no db'};
      const db = await openDb(target);
      const rows = await new Promise((res) => {
        const items = [];
        try {
          const tx = db.transaction('conversations', 'readonly');
          const req = tx.objectStore('conversations').openCursor();
          req.onsuccess = () => {
            const cur = req.result;
            if (!cur) return res(items);
            items.push(cur.value);
            if (items.length >= skip + 1) return res(items);
            cur.continue();
          };
          req.onerror = () => res(items);
        } catch (e) { res(items); }
      });
      db.close();
      const v = rows[skip];
      if (!v) return {error: 'not enough rows'};
      const types = {};
      for (const k of Object.keys(v)) {
        const t = v[k] === null ? 'null' : (Array.isArray(v[k]) ? 'array' : typeof v[k]);
        types[k] = t + (Array.isArray(v[k]) ? '(' + v[k].length + ')' : '');
      }
      const tp = v.threadProperties || {};
      const out = {id: String(v.id || ''), types,
                   targetLink: String(v.targetLink || '').slice(0, 300),
                   threadProperties: {},
                   lastMessage: null, messagesHead: null};
      for (const k of Object.keys(tp)) {
        const val = tp[k];
        out.threadProperties[k] = (typeof val === 'string')
          ? val.slice(0, 100) : (Array.isArray(val) ? 'array(' + val.length + ')'
          : (val && typeof val === 'object' ? 'object' : val));
      }
      try { out.lastMessage = JSON.stringify(v.lastMessage || {}).slice(0, 2000); } catch (e) { }
      try { out.properties = JSON.stringify(v.properties || {}).slice(0, 1200); } catch (e) { }
      try {
        const msgs = v.messages;
        if (Array.isArray(msgs)) out.messagesHead = JSON.stringify(msgs.slice(-2)).slice(0, 800);
        else out.messagesHead = typeof msgs + ':' + String(msgs).slice(0, 120);
      } catch (e) { }
      return out;
    }""", {"skip": skip})

async def api_idb_conversations(page=None, limit: int = 30) -> dict[str, Any]:
    """读 conversation-manager 的会话列表（只读）：id/主题/成员数/最后一条消息。"""
    from .teams import teams_open  # 惰性 import：避免与 teams.py 循环
    page = page or await teams_open()
    return await page.evaluate(r"""async ({limit}) => {
      const openDb = (name) => new Promise((res, rej) => {
        const req = indexedDB.open(name);
        req.onsuccess = () => res(req.result);
        req.onerror = () => rej(req.error);
      });
      let names = [];
      try { names = (await indexedDB.databases()).map(d => d.name || ''); } catch (e) { }
      const target = names.find(n => /conversation-manager/i.test(n));
      if (!target) return {error: '找不到 conversation-manager'};
      const db = await openDb(target);
      const store = 'conversations';
      const rows = await new Promise((res) => {
        const items = [];
        try {
          const tx = db.transaction(store, 'readonly');
          const req = tx.objectStore(store).openCursor();
          req.onsuccess = () => {
            const cur = req.result;
            if (!cur || items.length >= limit) return res(items);
            items.push(cur.value);
            cur.continue();
          };
          req.onerror = () => res(items);
        } catch (e) { res(items); }
      });
      db.close();
      const pick = (v) => {
        const tp = v.threadProperties || {};
        const lm = v.lastMessage || (Array.isArray(v.messages) ? v.messages[v.messages.length - 1] : null) || {};
        const mp = v.memberProperties || [];
        return {
          id: String(v.id || ''),
          kind: String(v.id || '').startsWith('8:') ? 'direct'
                : (String(v.id || '').startsWith('19:') ? 'thread' : 'other'),
          type: v.type,
          topic: tp.topic || tp.chatTitle || '',
          threadType: tp.threadType || '',
          memberCount: Array.isArray(mp) ? mp.length : (tp.memberCount || null),
          members: Array.isArray(mp) ? mp.slice(0, 4).map(m => String(m.mri || m.id || '')
            .replace(/^8:orgid:/, '').slice(0, 40)) : [],
          lastFrom: String(lm.from || '').split('/').pop(),
          lastText: String(lm.content || '').replace(/<[^>]+>/g, '').slice(0, 120),
          lastAt: lm.composetime || lm.originalarrivaltime || v.lastMessageTimeUtc || null,
          msgCount: Array.isArray(v.messages) ? v.messages.length : null,
          propKeys: Object.keys(tp).slice(0, 12)
        };
      };
      const all = rows.map(pick);
      const direct = all.filter(c => c.kind === 'direct');
      const threads = all.filter(c => c.kind !== 'direct');
      return {total: all.length, direct: direct.length, threads: threads.length,
              sampleDirect: direct.slice(0, 3), sampleThreads: threads.slice(0, 3),
              first: all[0] || null};
    }""", {"limit": max(10, limit)})

async def api_idb_enumerate(page=None) -> dict[str, Any]:
    """列出关键库的每个仓库的行数与首条记录字段（只读）。"""
    from .teams import teams_open  # 惰性 import：避免与 teams.py 循环
    page = page or await teams_open()
    return await page.evaluate(r"""async () => {
      const openDb = (name) => new Promise((res, rej) => {
        const req = indexedDB.open(name);
        req.onsuccess = () => res(req.result);
        req.onerror = () => rej(req.error);
      });
      const count = (db, store) => new Promise((res) => {
        try {
          const tx = db.transaction(store, 'readonly');
          const r = tx.objectStore(store).count();
          r.onsuccess = () => res(r.result);
          r.onerror = () => res(-1);
        } catch (e) { res(-1); }
      });
      const first = (db, store) => new Promise((res) => {
        try {
          const tx = db.transaction(store, 'readonly');
          const r = tx.objectStore(store).openCursor();
          r.onsuccess = () => {
            const cur = r.result;
            if (!cur) return res(null);
            let head = '';
            try { head = JSON.stringify(cur.value).slice(0, 400); } catch (e) { }
            const v = cur.value;
            res({key: String(cur.key).slice(0, 80),
                 fields: (v && typeof v === 'object') ? Object.keys(v).slice(0, 30) : [],
                 head});
          };
          r.onerror = () => res(null);
        } catch (e) { res(null); }
      });
      let names = [];
      try { names = (await indexedDB.databases()).map(d => d.name || ''); } catch (e) { return {error: String(e)}; }
      const targets = names.filter(n => /conversation-manager|messaging-slice|skypexspaces|chat-pane|message/i.test(n));
      const out = [];
      for (const name of targets) {
        const rec = {db: name.slice(0, 90), stores: []};
        try {
          const db = await openDb(name);
          for (const st of [...db.objectStoreNames]) {
            const n = await count(db, st);
            const f = n > 0 ? await first(db, st) : null;
            rec.stores.push({store: st, rows: n, fields: f ? f.fields : [],
                             key: f ? f.key : '', head: f ? f.head : ''});
          }
          db.close();
        } catch (e) { rec.error = String(e); }
        out.push(rec);
      }
      return {count: out.length, dbs: out};
    }""")

async def api_idb_probe(page=None, want: str = "conversation") -> dict[str, Any]:
    """列出 Teams 相关 IndexedDB 库，并把目标库的第一条记录结构/样本 dump 出来（只读）。"""
    from .teams import teams_open  # 惰性 import：避免与 teams.py 循环
    page = page or await teams_open()
    return await page.evaluate(r"""async ({want}) => {
      const out = {dbs: [], sample: null, error: ''};
      const openDb = (name) => new Promise((res, rej) => {
        const req = indexedDB.open(name);
        req.onsuccess = () => res(req.result);
        req.onerror = () => rej(req.error);
      });
      const readAll = (db, store, limit) => new Promise((res) => {
        try {
          const tx = db.transaction(store, 'readonly');
          const os = tx.objectStore(store);
          const items = [];
          const req = os.openCursor();
          req.onsuccess = () => {
            const cur = req.result;
            if (!cur || items.length >= limit) return res(items);
            items.push({key: cur.key, value: cur.value});
            cur.continue();
          };
          req.onerror = () => res(items);
        } catch (e) { res([]); }
      });
      let names = [];
      try {
        names = (await indexedDB.databases()).map(d => d.name || '');
      } catch (e) { out.error = String(e); return out; }
      const interesting = names.filter(n => /conversation|messag|chat|skypexspaces/i.test(n));
      out.dbs = interesting;
      const pick = interesting.find(n => new RegExp(want, 'i').test(n)) || interesting[0];
      if (!pick) { out.error = '没有找到相关库'; return out; }
      out.picked = pick;
      const db = await openDb(pick);
      out.stores = [...db.objectStoreNames];
      const store = out.stores[0];
      const rows = await readAll(db, store, 1);
      db.close();
      if (rows.length) {
        const v = rows[0].value;
        const shape = (o, depth) => {
          if (o === null || typeof o !== 'object') return typeof o;
          if (depth <= 0) return Array.isArray(o) ? 'array' : 'object';
          const outo = {};
          const keys = Array.isArray(o) ? o.slice(0, 2) : Object.keys(o).slice(0, 25);
          if (Array.isArray(o)) return 'array(' + o.length + ')';
          for (const k of keys) outo[k] = shape(o[k], depth - 1);
          return outo;
        };
        out.sample = {key: String(rows[0].key).slice(0, 80), shape: shape(v, 2)};
        try { out.sample.head = JSON.stringify(v).slice(0, 600); } catch (e) { }
      }
      return out;
    }""", {"want": want})

