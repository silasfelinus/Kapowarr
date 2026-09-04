// The tasks page rounded every duration to whole hours, so the two tasks
// that run every fifteen minutes -- Watched Folder Import and Feed Sync --
// read "0 hours", last run "0 hours ago", next run "in 0 hours". Zero is
// the number a broken schedule shows, and it was the number a perfectly
// healthy quarter-hourly task showed on all three columns at once.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const script = fs.readFileSync(
	path.join(root, 'frontend/static/js/tasks.js'),
	'utf8'
);

// The file talks to the DOM at load, so lift out just the two functions
// under test and run them in this realm.
function load() {
	const wanted = ['describeDuration', 'convertInterval', 'convertTime'];
	const bodies = wanted.map(name => {
		const start = script.indexOf(`function ${name}(`);
		assert.notEqual(start, -1, `${name} is defined in tasks.js`);
		const end = script.indexOf('\n};', start);
		assert.notEqual(end, -1, `${name} is a complete function`);
		return script.slice(start, end + 3);
	});
	return new Function(
		`${bodies.join('\n')}\nreturn {${wanted.join(', ')}};`
	)();
}

test('a quarter-hourly task says fifteen minutes, not zero hours', () => {
	const { convertInterval } = load();
	assert.equal(convertInterval(900), '15 minutes');
	assert.equal(convertInterval(3600), '1 hour');
	assert.equal(convertInterval(86400), '1 day');
	assert.equal(convertInterval(604800), '7 days');
});

test('every seeded interval reads as a real amount of time', () => {
	const { convertInterval } = load();
	// The seeded set from `task_intervals`.
	for (const seconds of [900, 3600, 86400, 604800]) {
		const said = convertInterval(seconds);
		assert.doesNotMatch(
			said, /^0 /, `${seconds}s should not round away to nothing`
		);
	}
});

test('units change over at the next unit, not by rounding', () => {
	const { describeDuration } = load();
	assert.equal(describeDuration(59), '59 seconds');
	assert.equal(describeDuration(60), '1 minute');
	assert.equal(describeDuration(3599), '60 minutes');
	assert.equal(describeDuration(3600), '1 hour');
	assert.equal(describeDuration(86399), '24 hours');
	assert.equal(describeDuration(86400), '1 day');
});

test('singulars are singular', () => {
	const { describeDuration } = load();
	assert.equal(describeDuration(1), '1 second');
	assert.equal(describeDuration(60), '1 minute');
	assert.equal(describeDuration(3600), '1 hour');
	assert.equal(describeDuration(86400), '1 day');
	assert.equal(describeDuration(172800), '2 days');
});

test('a run that just happened says so rather than naming zero', () => {
	const { convertTime } = load();
	const now = Date.now() / 1000;
	assert.equal(convertTime(now - 5, false), 'just now');
	assert.equal(convertTime(now + 5, true), 'any moment');
	assert.equal(convertTime(now - 900, false), '15 minutes ago');
	assert.equal(convertTime(now + 900, true), 'in 15 minutes');
});

test('a task that has never run still says Never', () => {
	const { convertTime } = load();
	assert.equal(convertTime(null, false), 'Never');
	assert.equal(convertTime(null, true), 'Never');
});

test('nothing on the page leaks an implicit global', () => {
	// `result = ...` with no declaration was assigning to the window.
	assert.doesNotMatch(script, /^\tresult = /m);
});
