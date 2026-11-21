// Renders the homework (and homework-activity) templates with hostile payloads
// and prints every rendered HTML string as JSON so pytest can assert that no
// executable markup survives. Run by tests/unit/test_homework_frontend_escaping.py.
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { register } from 'node:module';

register('./stub_https_loader.mjs', import.meta.url);

const root = path.resolve(process.argv[2]);
const imp = (rel) => import(pathToFileURL(path.join(root, rel)).href);

const fakeEl = () => new Proxy({ innerHTML: '', dataset: {}, style: {}, children: [], value: '' }, {
  get(t, k) {
    if (k in t) return t[k];
    if (k === 'classList') return { add() {}, remove() {}, toggle() {}, contains() { return false; } };
    if (k === 'querySelector') return () => fakeEl();
    if (k === 'querySelectorAll') return () => [];
    if (typeof k === 'string') return () => fakeEl();
  },
  set(t, k, v) { t[k] = v; return true; },
});
const shared = fakeEl();
globalThis.window = globalThis;
globalThis.document = {
  getElementById: () => shared,
  querySelector: () => fakeEl(),
  querySelectorAll: () => [],
  createElement: () => fakeEl(),
  addEventListener() {},
  body: fakeEl(),
  readyState: 'complete',
};
globalThis.localStorage = { getItem() { return null; }, setItem() {} };

const X = '<img src=x onerror=alert(1)>';
const Q = '"><svg onload=alert(2)>';
const J = 'javascript:alert(3)';
const evilResource = { url: J, type: 'document', filename: X };
const evilUser = { name: Q, username: X, avatar: J, department: X, active_details: { last_active: X } };
const out = {};

const t = await imp('static/js/homework/homework.templates.js');
const baseHw = {
  id: 1, title: X, subject: X, description: X, difficulty: 'hard', status: 'not_started',
  hours_until_due: 5, due_date: '2026-12-01T10:00:00', resources: [evilResource, evilResource],
  student: evilUser, helper: evilUser, help_count: 1, help_requests_count: 1, is_shared: true,
  already_helping: true, my_help_status: 'pending', my_submission_id: 9,
};
out.myCard = t.renderMyHomeworkCard(baseHw);
out.connCard = t.renderConnectionsHomeworkCard(baseHw);
out.helpRequests = t.renderMyHelpRequestsList([baseHw]);
out.helping = t.renderHelpingWithList([baseHw]);
out.resourceItem = t.renderResourceItem(evilResource, 0, false);

const s = await imp('static/js/homework/homework.submission.js');
const sub = {
  id: 5, title: X, subject: X, description: X, difficulty: X, status: 'submitted',
  created_at: '2026-11-01T00:00:00', submitted_at: '2026-11-02T00:00:00',
  requester: evilUser, helper: evilUser,
  assignment: { due_date: '2026-12-01T00:00:00', estimated_hours: 2, status: 'not_started' },
  solution: { text: X, resources: [evilResource] }, feedback: { text: X, resources: [evilResource] },
};
out.subHelper = s.renderSubmissionDetailHelper(sub);
out.subRequester = s.renderSubmissionDetailRequester(sub);

const a = await imp('static/js/activity/activity.feed.js');
shared.innerHTML = '';
a.renderActivityFeed([
  { id: 1, type: 'started_homework', user: evilUser, user_id: 1, name: Q, time_ago: '1m',
    data: { assignment_id: 3, assignment_title: X, subject: X } },
  { id: 2, type: 'completed_homework', user: evilUser, name: Q, time_ago: '1m', data: { assignment_title: X, subject: X, helper_count: 2 } },
  { id: 3, type: 'offered_help', user: evilUser, name: Q, time_ago: '1m', data: { requester_name: X, subject: X } },
  { id: 4, type: 'submitted_solution', user: evilUser, name: Q, time_ago: '1m', data: { requester_name: X, subject: X } },
]);
out.activity = shared.innerHTML;

console.log(JSON.stringify(out));
