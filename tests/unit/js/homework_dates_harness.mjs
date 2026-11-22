// Prints JSON describing the homework date helpers' behaviour under the
// timezone set via the TZ env var (set by the pytest wrapper).
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const root = process.argv[2];
const utils = await import(pathToFileURL(path.join(root, 'static/js/homework/homework.utils.js')).href);

console.log(JSON.stringify({
  toUtc: utils.localInputToUtcIso('2030-06-01T10:00'),
  toUtcEmpty: utils.localInputToUtcIso(''),
  toUtcBad: utils.localInputToUtcIso('garbage'),
  fromNaive: utils.serverUtcToLocalInput('2030-06-01T08:00:00'),
  fromZ: utils.serverUtcToLocalInput('2030-06-01T08:00:00Z'),
  fromOffset: utils.serverUtcToLocalInput('2030-06-01T10:00:00+02:00'),
  fromBad: utils.serverUtcToLocalInput('nope'),
}));
