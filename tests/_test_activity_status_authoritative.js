/* The activity chip must trust the timestamp, not the newest history row.
 *
 * A checkin/daily_chat success writes lastCheckin/lastDailyChat and then appends
 * the history row. If the container is recreated between those two steps the
 * timestamp is today while the newest row is still this morning's failure - the
 * account HAS its reward but the chip said 失败, which is what made two accounts
 * look broken for a whole day. Same shape for the intl daily chat.
 *
 * So the rule under test: today's timestamp wins; the history row only decides
 * when there is no timestamp for today.
 *
 * Requires node (no other dependency).
 *
 *   node _test_activity_status_authoritative.js
 */
const assert = require('assert');
const {dashboardScript} = require('./_dashboard_source.js');

const code = dashboardScript();

const dom = require('./_dom_stub.js');
const reply = () => Promise.resolve({
  ok: true, status: 200,
  json: () => Promise.resolve({}), text: () => Promise.resolve(''),
});
dom.installDom({fetch: reply});

const api = new Function(code + `
  return {
    activityStatusOf: activityStatusOf,
    setToday: function(v){ ACTIVITY_TODAY = v; },
  };`)();

const pad = n => String(n).padStart(2, '0');
const stamp = d => d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
const TODAY = stamp(new Date());
const YESTERDAY = stamp(new Date(Date.now() - 86400000));

let pass = 0, fail = 0;
const check = (label, cond, extra) => {
  if (cond) { pass++; console.log('  [PASS] ' + label); }
  else { fail++; console.log('  [FAIL] ' + label + (extra ? '  ' + extra : '')); }
};

// One row per uid, newest first - the shape the panel builds.
const byUid = (rows) => {
  const out = {};
  for (const row of rows) {
    if (!out[row.uid]) out[row.uid] = row;
  }
  return out;
};

const failedCheckin = (uid) => ({
  ts: TODAY + 'T09:00:23+08:00', uid: uid, nickname: 'x', realm: 'cn',
  task: 'checkin', trigger: 'scheduler', ok: false, message: '签到失败，请稍后重试',
});
const failedChat = (uid) => ({
  ts: TODAY + 'T07:33:24+08:00', uid: uid, nickname: 'x', realm: 'intl',
  task: 'daily_chat', trigger: 'scheduler', ok: false,
  message: 'HTTP 401 code 10034',
});

api.setToday({byUid: {}, error: '', loaded: true});

console.log('[1] CN: today lastCheckin beats an older failed row');
let a = {uid: 'cn-a', realm: 'cn', lastCheckin: TODAY + ' 14:15:07'};
api.setToday({byUid: byUid([failedCheckin('cn-a')]), error: '', loaded: true});
let s = api.activityStatusOf(a);
check('state is 已领', s.gas.state === '已领', JSON.stringify(s.gas));
check('kind is done', s.gas.kind === 'done', JSON.stringify(s.gas));
check('the timestamp is shown', s.gas.at === TODAY + ' 14:15:07', JSON.stringify(s.gas));

console.log('[2] CN: a failed row still reads as failed when nothing was claimed today');
a = {uid: 'cn-b', realm: 'cn', lastCheckin: null};
api.setToday({byUid: byUid([failedCheckin('cn-b')]), error: '', loaded: true});
s = api.activityStatusOf(a);
check('state is 失败', s.gas.state === '失败', JSON.stringify(s.gas));
check('kind is failed', s.gas.kind === 'failed', JSON.stringify(s.gas));

console.log('[3] CN: yesterday lastCheckin does not cover today');
a = {uid: 'cn-c', realm: 'cn', lastCheckin: YESTERDAY + ' 09:00:00'};
api.setToday({byUid: byUid([failedCheckin('cn-c')]), error: '', loaded: true});
s = api.activityStatusOf(a);
check('state is 失败', s.gas.state === '失败', JSON.stringify(s.gas));

console.log('[4] CN: today lastCheckin with no row at all is still 已领');
a = {uid: 'cn-d', realm: 'cn', lastCheckin: TODAY + ' 09:00:36'};
api.setToday({byUid: {}, error: '', loaded: true});
s = api.activityStatusOf(a);
check('state is 已领', s.gas.state === '已领', JSON.stringify(s.gas));

console.log('[5] CN: nothing anywhere stays 无本地记录');
a = {uid: 'cn-e', realm: 'cn', lastCheckin: null};
api.setToday({byUid: {}, error: '', loaded: true});
s = api.activityStatusOf(a);
check('state is 无本地记录', s.gas.state === '无本地记录', JSON.stringify(s.gas));

console.log('[6] INTL: today lastDailyChat beats an older failed row');
a = {uid: 'intl-a', realm: 'intl', lastDailyChat: TODAY + ' 13:22:59'};
api.setToday({byUid: byUid([failedChat('intl-a')]), error: '', loaded: true});
s = api.activityStatusOf(a);
check('state is 已完成', s.state === '已完成', JSON.stringify(s));
check('kind is done', s.kind === 'done', JSON.stringify(s));

console.log('[7] INTL: a failed row still reads as failed when nothing completed today');
a = {uid: 'intl-b', realm: 'intl', lastDailyChat: null};
api.setToday({byUid: byUid([failedChat('intl-b')]), error: '', loaded: true});
s = api.activityStatusOf(a);
check('state is 失败', s.state === '失败', JSON.stringify(s));
check('kind is failed', s.kind === 'failed', JSON.stringify(s));

console.log('[8] INTL: yesterday lastDailyChat does not cover today');
a = {uid: 'intl-c', realm: 'intl', lastDailyChat: YESTERDAY + ' 20:00:00'};
api.setToday({byUid: byUid([failedChat('intl-c')]), error: '', loaded: true});
s = api.activityStatusOf(a);
check('state is 失败', s.state === '失败', JSON.stringify(s));

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
