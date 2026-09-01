const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
const source = fs.readFileSync(
	path.join(root, 'frontend/static/js/library_import.js'), 'utf8'
);
const reviewUi = fs.readFileSync(
	path.join(root, 'frontend/static/js/library_import_review_ui.js'), 'utf8'
);

// `setInterval(refresh, 1000)` fired whether or not the previous snapshot
// had come back. That is fine while the server answers in milliseconds and
// catastrophic when it does not: a library import holding the SQLite writer
// makes the endpoint take seconds, the timer keeps adding requests, and the
// browser's connection pool for the host fills with stale polls. Every other
// request the page makes queues behind them -- which is why the search
// window froze the page "even before I type anything". Kapowarr's log shows
// the far end of the same pileup: waitress refusing new connections
// twenty-one times in twenty-seven seconds.

// Run the real loop against a clock and a server we control.
function harness({responseMs}) {
	const state = {inFlight: 0, peakInFlight: 0, started: 0, settled: 0};
	let now = 0;
	const timers = [];

	const context = {
		console,
		CONTINUOUS_POLL_INTERVAL_MS: 1000,
		continuousPoll: null,
		stopContinuousPollFn: null,
		continuousWasRunning: false,
		continuousTaskId: null,
		setTimeout: (fn, ms) => {
			const id = timers.length + 1;
			timers.push({id, at: now + ms, fn});
			return id;
		},
		clearTimeout: id => {
			const i = timers.findIndex(t => t.id === id);
			if (i !== -1) timers.splice(i, 1);
		},
		// The server: every call takes `responseMs` to come back.
		fetchContinuousSnapshot() {
			state.started += 1;
			state.inFlight += 1;
			state.peakInFlight = Math.max(state.peakInFlight, state.inFlight);
			return new Promise(resolve => {
				timers.push({
					id: -1, at: now + responseMs,
					fn: () => {
						state.inFlight -= 1;
						state.settled += 1;
						resolve({task: {id: 1}, job: {}});
					}
				});
			});
		},
		showContinuousTask() {},
		paintContinuousStatus() {}
	};
	vm.createContext(context);

	for (const name of [
		'CONTINUOUS_POLL_INTERVAL_MS', 'stopContinuousPollFn',
		'stopContinuousPoll', 'pollContinuousTask'
	]) {
		const start = source.indexOf(
			name === 'CONTINUOUS_POLL_INTERVAL_MS'
				? 'const CONTINUOUS_POLL_INTERVAL_MS'
				: name === 'stopContinuousPollFn'
					? 'let stopContinuousPollFn'
					: `function ${name}(`
		);
		assert.notEqual(start, -1, `${name} not found`);
		let end;
		if (source[start] === 'c' || source[start] === 'l') {
			end = source.indexOf('\n', start) + 1;
		} else {
			let depth = 0;
			for (let i = source.indexOf('{', start); i < source.length; i++) {
				if (source[i] === '{') depth++;
				else if (source[i] === '}' && --depth === 0) { end = i + 1; break; }
			}
		}
		vm.runInContext(source.slice(start, end), context);
	}

	return {
		state,
		start: () => context.pollContinuousTask('key'),
		stop: () => context.stopContinuousPoll(),
		// Advance the clock, firing whatever is due, draining microtasks
		// between steps so promise callbacks land.
		async advance(ms, step = 100) {
			for (let elapsed = 0; elapsed < ms; elapsed += step) {
				now += step;
				const due = timers.filter(t => t.at <= now);
				for (const t of due) timers.splice(timers.indexOf(t), 1);
				for (const t of due) t.fn();
				await Promise.resolve();
				await Promise.resolve();
			}
		}
	};
};

test('a fast server is polled about once a second', async () => {
	const h = harness({responseMs: 50});
	h.start();
	await h.advance(10000);
	h.stop();

	assert.ok(
		h.state.started >= 8 && h.state.started <= 11,
		`expected ~10 polls in 10s, got ${h.state.started}`
	);
	assert.equal(h.state.peakInFlight, 1);
});

test('a slow server is not hammered while it is slow', async () => {
	// Five seconds per reply. The interval version issued one request per
	// second regardless and had five outstanding within five seconds, then
	// ten, then fifteen.
	const h = harness({responseMs: 5000});
	h.start();
	await h.advance(30000);
	h.stop();

	assert.equal(
		h.state.peakInFlight, 1,
		'a second request must not start while the first is outstanding'
	);
	assert.ok(
		h.state.started <= 6,
		`a 5s server should be asked at most ~5 times in 30s, got ${h.state.started}`
	);
});

test('a server that never answers is asked exactly once', async () => {
	const h = harness({responseMs: 10 ** 9});
	h.start();
	await h.advance(60000);
	h.stop();

	assert.equal(h.state.started, 1);
});

test('stopping it stops it, including the request already in flight', async () => {
	const h = harness({responseMs: 2000});
	h.start();
	await h.advance(500);
	h.stop();
	const started = h.state.started;

	await h.advance(20000);

	assert.equal(
		h.state.started, started,
		'the in-flight reply must not arm another round after the stop'
	);
});

test('restarting the poll does not leave the old one running', async () => {
	const h = harness({responseMs: 100});
	h.start();
	await h.advance(3000);
	h.start();
	await h.advance(3000);
	h.stop();

	assert.equal(
		h.state.peakInFlight, 1,
		'two loops would double the request rate'
	);
});

test('the review UI stops the poll through the poll, not the handle', () => {
	// Clearing the timer alone leaves an in-flight request that arms the
	// next round when it lands.
	assert.match(reviewUi, /stopContinuousPoll\(\)/);
	assert.ok(
		!reviewUi.includes('clearInterval(continuousPoll)'),
		'there is no interval to clear any more'
	);
});

test('nothing in the page polls on a fixed interval', () => {
	// The call, not the paragraph explaining why there is not one.
	const code = source
		.split('\n')
		.filter(line => !line.trim().startsWith('//'))
		.join('\n');

	assert.ok(
		!code.includes('setInterval('),
		'a fixed interval cannot know whether the last request came back'
	);
});
