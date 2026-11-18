'use strict';

function parseAccountImport(raw, sites) {
  let entries;
  try {
    entries = JSON.parse(raw);
  } catch (_) {
    throw new Error('账号文件必须是有效的 JSON 数组');
  }
  if (!Array.isArray(entries) || !entries.length) throw new Error('账号文件必须包含至少一个账号');
  const seen = new Set();
  return entries.map((entry, index) => {
    const label = `账号 ${index + 1}`;
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) throw new Error(`${label} 必须是账号对象`);
    const email = typeof entry.email === 'string' ? entry.email.trim() : '';
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) throw new Error(`${label} 缺少有效的绑定邮箱`);
    const cookie = typeof entry.cookie === 'string' ? entry.cookie.trim().replace(/^cookie\s*:\s*/i, '') : '';
    if (!cookie || /[^\x20-\x7e]/.test(cookie)) throw new Error(`${label} 的 Cookie 必须是完整的单行请求头`);
    const pairs = new Map(cookie.split(';').map(part => {
      const split = part.indexOf('=');
      return split > 0 ? [part.slice(0, split).trim(), part.slice(split + 1).trim()] : ['', ''];
    }));
    if (!(pairs.get('gld:sess') && pairs.get('gld:sess.sig')) && !(pairs.get('koa:sess') && pairs.get('koa:sess.sig'))) {
      throw new Error(`${label} 缺少成对的会话 Cookie 与签名`);
    }
    const domain = String(entry.domain || entry.origin || 'glados.cloud').trim().toLowerCase().replace(/^https:\/\//, '').replace(/\/$/, '');
    const site = sites.find(site => site.domain === domain);
    if (!site || !['glados.cloud', 'railgun.info', 'glados.network', 'glados.rocks', 'glados.one', 'glados.space', 'glados.vip', 'glados-facility.com'].includes(domain)) {
      throw new Error(`${label} 的域名不在支持列表中`);
    }
    const key = `${domain}|${email.toLowerCase()}`;
    if (seen.has(key)) throw new Error(`${label} 的邮箱和域名重复`);
    seen.add(key);
    const userAgent = typeof entry['user-agent'] === 'string' ? entry['user-agent'].trim() : '';
    if (/[^\x20-\x7e]/.test(userAgent) || userAgent.length > 1024) throw new Error(`${label} 的 User-Agent 格式不正确`);
    const name = typeof entry.name === 'string' ? entry.name.trim() : email;
    if (!name || name.length > 60 || /[\x00-\x1f]/.test(name)) throw new Error(`${label} 的名称格式不正确`);
    return { email, name, cookie, userAgent, siteId: site.id, authMode: 'cookie', enabled: true };
  });
}

function mergeImportedAccounts(existing, entries, now = Date.now()) {
  const accounts = existing.map(account => ({ ...account }));
  for (const entry of entries) {
    const index = accounts.findIndex(account => account.siteId === entry.siteId && String(account.email || (account.snapshot && account.snapshot.email) || '').toLowerCase() === entry.email.toLowerCase());
    if (index >= 0) {
      const previous = accounts[index];
      accounts[index] = { ...previous, ...entry, last: previous.cookie === entry.cookie ? previous.last : null };
    } else {
      accounts.push({ ...entry, id: `acc_${now.toString(36)}_${accounts.length}`, last: null, snapshot: null, createdAt: now });
    }
  }
  return accounts;
}

if (typeof module !== 'undefined' && module.exports) module.exports = { parseAccountImport, mergeImportedAccounts };
