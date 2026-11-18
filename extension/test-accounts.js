'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const { parseAccountImport, mergeImportedAccounts } = require('./accounts');

const sites = [{ id: 'glados-cloud', domain: 'glados.cloud' }];
const cookie = 'gld:sess=example-token; gld:sess.sig=example-signature; koa:sess=value==;';
const raw = JSON.stringify([
  { email: 'one@example.com', domain: 'glados.cloud', cookie },
  { email: 'two@example.com', domain: 'glados.cloud', cookie: cookie.replace('example-token', 'second-token') }
]);
const parsed = parseAccountImport(raw, sites);
assert.equal(parsed.length, 2);
assert.equal(parsed[0].cookie, cookie);
assert.equal(parsed[0].name, 'one@example.com');
assert.equal(parsed[1].authMode, 'cookie');
assert.equal(parsed[0].exchangePlan, undefined);
assert.throws(() => parseAccountImport(JSON.stringify([{ email: 'one@example.com', cookie, domain: 'attacker.example' }]), sites), /域名/);
assert.throws(() => parseAccountImport(JSON.stringify([{ email: 'one@example.com', cookie: cookie + '\r\nInjected: header' }]), sites), /单行/);
assert.throws(() => parseAccountImport(JSON.stringify([{ email: 'one@example.com', cookie }, { email: 'ONE@example.com', cookie }]), sites), /重复/);

const merged = mergeImportedAccounts([{ id: 'existing', email: 'ONE@example.com', siteId: 'glados-cloud', cookie: 'old', last: { status: 'repeat' } }], parsed, 1000);
assert.equal(merged.length, 2);
assert.equal(merged[0].id, 'existing');
assert.equal(merged[0].cookie, cookie);
assert.equal(merged[0].last, null);

const noop = () => {};
const asyncNoop = async () => {};
let rules;
const sandbox = {
  console, setTimeout, clearTimeout, setInterval, clearInterval,
  Date, Math, JSON, Promise, Number, String, Array, Object, Map, Set, URLSearchParams,
  chrome: {
    runtime: { onInstalled: { addListener: noop }, onStartup: { addListener: noop }, onMessage: { addListener: noop }, getURL: value => value, lastError: null },
    alarms: { onAlarm: { addListener: noop }, get: asyncNoop, create: noop, clear: asyncNoop },
    storage: { local: { get: async () => ({}), set: asyncNoop }, onChanged: { addListener: noop } },
    declarativeNetRequest: { updateSessionRules: async update => { rules = update; }, getSessionRules: async () => [] },
    notifications: { create: noop },
    tabs: { create: asyncNoop, get: asyncNoop, update: asyncNoop, remove: asyncNoop },
    scripting: { executeScript: asyncNoop },
    cookies: { getAll: async () => [] }
  }
};
vm.createContext(sandbox);
const background = fs.readFileSync(path.join(__dirname, 'background.js'), 'utf8');
assert(!background.includes('/api/user/exchange'));
vm.runInContext(background, sandbox, { filename: 'background.js' });

async function run() {
  await sandbox.addTabScopedRule(123, { cookie }, { domain: 'glados.cloud', checkinPath: '/console/checkin' });
  assert.deepEqual(Array.from(rules.addRules[0].condition.tabIds), [123]);
  const matches = new RegExp(rules.addRules[0].condition.regexFilter);
  assert(matches.test('https://glados.cloud/api/user/status'));
  assert(!matches.test('https://challenges.cloudflare.com/turnstile/v0/api.js'));
  assert(!matches.test('https://glados.cloud.attacker.example/api/user/status'));

  sandbox.chrome.scripting.executeScript = async () => [{ result: { email: 'different@example.com', statusCode: 0 } }];
  const result = { domain: 'glados.cloud' };
  assert.equal(await sandbox.verifyIdentity(123, { id: 'one', email: 'one@example.com' }, result, 'session'), false);
  assert.equal(result.identityFailed, true);
  assert(result.message.includes('身份校验失败'));
  console.log('Account import, strict email binding, Cookie isolation, and no-exchange checks passed.');
}

run().catch(error => { console.error(error); process.exitCode = 1; });
