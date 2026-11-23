// Exercises the homework request sequencer and per-flow resource state.
import { pathToFileURL } from 'node:url';
import path from 'node:path';

// the state module publishes itself on window for debugging
globalThis.window = globalThis.window || {};

const root = process.argv[2];
const load = (f) => import(pathToFileURL(path.join(root, 'static/js/homework', f)).href);

const { createSequencer } = await load('homework.utils.js');
const { homeworkState } = await load('homework.state.js');

// sequencer
const seq = createSequencer();
const first = seq.next();
const second = seq.next();
const seqOut = {
  firstStale: !seq.isCurrent(first),
  secondCurrent: seq.isCurrent(second),
  currentValue: seq.current() === second,
  currentDoesNotAdvance: (seq.current(), seq.current() === second),
};

// per-flow resources
homeworkState.reset();
homeworkState.addResource('create', { filename: 'draft.pdf', type: 'pdf', url: 'https://x/a' });
homeworkState.addResource('solution', { filename: 'sol.png', type: 'image', url: 'https://x/b' });
const afterAdd = {
  create: homeworkState.getResources('create').map((r) => r.filename),
  solution: homeworkState.getResources('solution').map((r) => r.filename),
};
homeworkState.clearResources('solution');
const afterClearSolution = {
  create: homeworkState.getResources('create').length,
  solution: homeworkState.getResources('solution').length,
};
homeworkState.removeResource('create', 0);
const afterRemove = homeworkState.getResources('create').length;

let unknownFlowThrows = false;
try { homeworkState.addResource('bogus', {}); } catch { unknownFlowThrows = true; }

homeworkState.addResource('create', { filename: 'again.pdf', type: 'pdf', url: 'https://x/c' });
homeworkState.addResource('solution', { filename: 'again.png', type: 'image', url: 'https://x/d' });
homeworkState.reset();
const afterReset = [homeworkState.getResources('create').length, homeworkState.getResources('solution').length];

const { beginFlight, endFlight } = await load('homework.utils.js');
const flight = [beginFlight('k'), beginFlight('k'), (endFlight('k'), beginFlight('k'))];

console.log(JSON.stringify({ flight, seqOut, afterAdd, afterClearSolution, afterRemove, unknownFlowThrows, afterReset }));
