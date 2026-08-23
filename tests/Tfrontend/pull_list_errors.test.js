const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
const source = fs.readFileSync(
	path.join(root, 'frontend/static/js/pull_list.js'),
	'utf8'
);
const runner = fs.readFileSync(
	path.join(root, 'backend/features/pull_list_parallel.py'),
	'utf8'
);

function slice(from, to) {
	return source.slice(source.indexOf(from), source.indexOf(to));
}

// `fetchAPI` rejects with the Response itself, and a Response has no
// `.message` -- so every HTTP failure collapsed to one fallback string. A 404,
// a 500 and a dead connection read identically on screen and in the log entry
// the page reports, leaving nothing to tell them apart with.
function describe(error) {
	const body = slice('function pullListErrorMessage', 'function isNotFound');
	const context = vm.createContext({error});
	return vm.runInContext(
		`${body}\npullListErrorMessage(error, 'unable to poll check');`,
		context
	);
}

test('a 404 is reported as a 404, not as a generic failure', () => {
	assert.equal(
		describe({status: 404, statusText: 'NOT FOUND'}),
		'HTTP 404 NOT FOUND'
	);
});

test('a server error keeps its status too', () => {
	assert.equal(describe({status: 500}), 'HTTP 500');
});

test('a real Error still reports its message', () => {
	assert.equal(describe(new Error('network down')), 'network down');
});

test('something with no status at all still falls back', () => {
	assert.equal(describe({}), 'unable to poll check');
});

test('a lost check is explained as a restart, not reported as an error', () => {
	// Checks live in the server process, so a restart loses the one the page
	// was following and every later poll 404s forever. Reporting that as a
	// client error buries a restart in the log as a fault.
	const poll = slice(
		'function pollUntilCheckFinished',
		'function checkNow'
	);

	assert.match(poll, /if \(isNotFound\(error\)\)/);
	assert.match(poll, /restarted while it was in progress/);
	assert.match(
		poll,
		/restarted while it was in progress[\s\S]*?stopCheckSpinner\(\);\s*\n\s*return;/,
		'the spinner has to stop -- there is nothing left to wait for'
	);
});

test('the reported message carries the server’s own error detail', () => {
	// Sending only the fallback string meant the log entry said no more than
	// the screen did, which is why a failure could look unlogged.
	assert.match(source, /async function describePullListError/);
	assert.match(source, /error\.clone\(\)\.json\(\)/);
	assert.match(source, /body\.error \|\| body\.result/);
});

test('check ids are not handed out twice across restarts', () => {
	// Ids restarting from 1 meant the next check after a restart could take
	// the id a browser was still polling for the check it lost, so that poller
	// would silently attach to an unrelated refresh.
	assert.match(runner, /self\._next_id = round\(time\(\)\) \* 1000/);
	assert.doesNotMatch(runner, /self\._next_id = 1\b/);
});
