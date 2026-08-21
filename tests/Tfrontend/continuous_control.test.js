const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
const source = fs.readFileSync(
	path.join(root, 'frontend/static/js/library_import.js'),
	'utf8'
);

// Every other test in this suite matches patterns against the source, which
// cannot tell what a control ends up saying. That is how a build shipped whose
// button read "Stop Import" and was disabled on a pass the panel had just
// described as paused: the source contained everything the patterns looked
// for. This one runs the real function and reads the button afterwards.
function control(job, live, opts) {
	// `showContinuousTask` is the next declaration in the file; slicing to it
	// keeps this pinned to the real function rather than a copy that could
	// drift away from what ships.
	const body = source.slice(
		source.indexOf('function renderContinuousControl'),
		source.indexOf('function showContinuousTask')
	);
	const button = {
		innerText: '', disabled: false, dataset: {},
		classes: new Set(),
		classList: {
			add(n) { button.classes.add(n); },
			remove(n) { button.classes.delete(n); }
		}
	};
	const context = vm.createContext({
		LIEls: {buttons: {continuous_stop: button}},
		continuousStopRequested: (opts || {}).stopRequested || false,
		job, live
	});
	vm.runInContext(`${body}\nrenderContinuousControl(job, live);`, context);
	return button;
}

// The state in the reported screenshot: a fresh snapshot staged by Reset &
// Re-evaluate, paused at zero with every folder still to check.
const PAUSED_FRESH = {
	status: 'paused',
	checked_folders: 0,
	total_folders: 3720,
	remaining_folders: 3720,
	imported_volumes: 0,
	is_live: false,
	is_stalled: false
};

test('a paused pass with folders left offers Resume, enabled', () => {
	const button = control(PAUSED_FRESH, false);

	assert.equal(button.innerText, 'Resume Import');
	assert.equal(button.disabled, false);
	assert.equal(button.dataset.action, 'resume');
	assert.ok(!button.classes.has('hidden'));
});

test('a running pass offers Stop', () => {
	const button = control(
		{...PAUSED_FRESH, status: 'running', is_live: true}, true
	);

	assert.equal(button.innerText, 'Stop Import');
	assert.equal(button.dataset.action, 'stop');
	assert.equal(button.disabled, false);
});

test('a job marked running with no worker is stoppable, which pauses it', () => {
	const button = control(
		{...PAUSED_FRESH, status: 'running', is_stalled: true}, false
	);

	assert.equal(button.innerText, 'Stop Import');
	assert.equal(button.dataset.action, 'stop');
	assert.equal(button.disabled, false);
});

test('a finished pass with nothing left hides the control', () => {
	const button = control({
		...PAUSED_FRESH,
		status: 'complete',
		checked_folders: 3720,
		remaining_folders: 0
	}, false);

	assert.ok(button.classes.has('hidden'));
});

test('a live pass already asked to stop shows Stop disabled, not Resume', () => {
	const button = control(
		{...PAUSED_FRESH, status: 'running', is_live: true},
		true,
		{stopRequested: true}
	);

	assert.equal(button.innerText, 'Stop Import');
	assert.equal(button.disabled, true);
});

test('no reachable state leaves a disabled Stop on a stopped pass', () => {
	// The exact shape of the bug: the panel says stopped, and the only control
	// is a Stop button that cannot be clicked.
	for (const status of ['paused', 'running', 'complete']) {
		for (const remaining of [0, 3720]) {
			const button = control({
				...PAUSED_FRESH,
				status,
				remaining_folders: remaining,
				is_stalled: status === 'running'
			}, false);

			const dead = button.disabled && !button.classes.has('hidden');
			assert.ok(
				!dead,
				`status=${status} remaining=${remaining} left a dead control `
				+ `reading "${button.innerText}"`
			);
		}
	}
});

test('a snapshot that never ran is not described as interrupted', () => {
	// Reset & Re-evaluate stages a fresh pass already paused, so "paused after
	// 0/3720 folders" reports an interruption that never happened and buries
	// the fact that it is simply waiting to be started.
	const body = source.slice(
		source.indexOf('function describeFinishedJob'),
		source.indexOf('function paintContinuousStatus')
	);
	const context = vm.createContext({
		describeReviewReasons: () => '',
		job: {
			status: 'paused',
			checked_folders: 0,
			total_folders: 3720,
			remaining_folders: 3720,
			imported_volumes: 0
		}
	});
	const text = vm.runInContext(`${body}\ndescribeFinishedJob(job);`, context);

	assert.match(text, /ready to check 3720 folders/i);
	assert.doesNotMatch(text, /paused after 0/);
});

test('a pass interrupted partway still says where it stopped', () => {
	const body = source.slice(
		source.indexOf('function describeFinishedJob'),
		source.indexOf('function paintContinuousStatus')
	);
	const context = vm.createContext({
		describeReviewReasons: () => '',
		job: {
			status: 'paused',
			checked_folders: 412,
			total_folders: 3720,
			remaining_folders: 3308,
			imported_volumes: 96
		}
	});
	const text = vm.runInContext(`${body}\ndescribeFinishedJob(job);`, context);

	assert.match(text, /paused after 412\/3720/);
});
