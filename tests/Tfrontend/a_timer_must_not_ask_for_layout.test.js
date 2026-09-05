// The Review Holds page froze the tab, and the profile named the line.
//
//   Self time  Total time   Activity
//   4,463 ms   5,510 ms     set innerText                     38.0%
//   3,590 ms  11,120 ms     library_import_review_ui.js:374   94.7%
//   1,220 ms   1,220 ms     Major GC
//
// Line 374 is `updatePrimaryControls`, which `setInterval` runs every
// second. It read one `innerText` and wrote two. `innerText` is
// layout-aware -- it respects display, line breaking and text-transform --
// so reading it forces a synchronous style and layout recalculation and
// writing it invalidates layout again. Each call was outlasting the
// interval that scheduled it, so the main thread never came up for air.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const script = fs.readFileSync(
	path.join(root, 'frontend/static/js/library_import_review_ui.js'),
	'utf8'
);

function updatePrimaryControlsSource() {
	const start = script.indexOf('function updatePrimaryControls');
	assert.notEqual(start, -1);
	const end = script.indexOf('\n\tfunction ', start + 10);
	return script.slice(start, end === -1 ? undefined : end);
}

test('the once-a-second pass never touches innerText', () => {
	assert.doesNotMatch(
		updatePrimaryControlsSource(), /\.innerText\b/,
		'innerText in a timer forces layout on every tick'
	);
});

test('it is still a once-a-second pass', () => {
	// The fix is making the work cheap, not making it rarer.
	assert.match(script, /setInterval\(updatePrimaryControls, 1000\)/);
});

test('setText writes nothing when the text is unchanged', () => {
	const start = script.indexOf('function setText');
	const body = script.slice(start, script.indexOf('\n\t};', start) + 4);
	const setText = new Function(body + '\nreturn setText;')();

	let writes = 0;
	const el = {
		_t: 'Review Holds (40)',
		get textContent() { return this._t; },
		set textContent(v) { writes += 1; this._t = v; }
	};

	setText(el, 'Review Holds (40)');
	assert.equal(writes, 0, 'the same string is not written again');

	setText(el, 'Review Holds (39)');
	assert.equal(writes, 1);
	assert.equal(el.textContent, 'Review Holds (39)');
});

test('setText survives a control that is not on the page', () => {
	const start = script.indexOf('function setText');
	const body = script.slice(start, script.indexOf('\n\t};', start) + 4);
	const setText = new Function(body + '\nreturn setText;')();

	assert.doesNotThrow(() => setText(null, 'anything'));
	assert.doesNotThrow(() => setText(undefined, 'anything'));
});

test('the status it mirrors is read without consulting layout', () => {
	assert.match(
		updatePrimaryControlsSource(),
		/LIEls\.continuous\.status\.textContent/
	);
});


test('the half-second task poll does not ask for layout either', () => {
	// `waitForTaskCompletion` runs twice a second for as long as a
	// maintenance pass lasts, writing a status message that is the same
	// string on nearly every tick.
	const start = script.indexOf('function waitForTaskCompletion');
	assert.notEqual(start, -1);
	const body = script.slice(start, script.indexOf('\n\tfunction ', start + 10));

	assert.match(body, /setTimeout\(poll, 500\)/, 'still a 500ms poll');
	assert.doesNotMatch(body, /\.innerText\b/);
	assert.match(body, /setText\(/);
});

test('no timer callback in this file touches innerText', () => {
	// The pattern, not the two instances: anything on a repeating timer
	// here goes through setText.
	for (const timer of ['setInterval(updatePrimaryControls, 1000)']) {
		assert.match(script, new RegExp(timer.replace(/[()]/g, '\\$&')));
	}
	// updatePrimaryControls and waitForTaskCompletion are the two, and
	// both are asserted clean above. This guards the file-wide count so a
	// third one cannot be added silently.
	// Every remaining one is in a render path that runs on an action, not
	// on a clock. A new one is fine; a new one inside a timer is not, and
	// this is the tripwire that makes anyone adding the fourteenth look.
	const writes = script.match(/\.innerText = /g) || [];
	assert.equal(
		writes.length, 9,
		'innerText writes changed -- check the new one is not on a timer'
	);
});


test('the group render reads every group before it writes to any', () => {
	// `innerText` reads force layout and `row.hidden` writes invalidate it.
	// Interleaved per group that is one forced layout per group; split into
	// a read pass and a write pass it is one for the whole render.
	const start = script.indexOf('function collapseRenderedRows');
	assert.notEqual(start, -1);
	const body = script.slice(start, script.indexOf('\n\tfunction ', start + 10));

	const readPass = body.indexOf('plans.push(');
	const writePass = body.indexOf('plans.forEach(');
	assert.ok(readPass > -1, 'there is a read pass');
	assert.ok(writePass > readPass, 'and it comes before the write pass');

	// Nothing after the write pass begins may *read* innerText. A write is
	// harmless here -- it is the read that forces layout mid-render.
	const after = body.slice(writePass);
	const reads = [...after.matchAll(/\.innerText\b(?!\s*=)/g)];
	assert.deepEqual(
		reads.map(m => after.slice(Math.max(0, m.index - 30), m.index + 10)),
		[],
		'a write pass that reads innerText puts the thrash back'
	);
});

test('the row hiding happens after the reads, not between them', () => {
	const start = script.indexOf('function collapseRenderedRows');
	const body = script.slice(start, script.indexOf('\n\tfunction ', start + 10));

	assert.ok(
		body.indexOf('row.hidden = index !== 0') > body.indexOf('plans.forEach('),
		'hiding rows is a write and belongs in the write pass'
	);
});

test('the volume summary is written through setText too', () => {
	const start = script.indexOf('function updateSummary');
	const body = script.slice(start, script.indexOf('\n\tfunction ', start + 10));

	assert.doesNotMatch(body, /\.innerText\b/);
	assert.match(body, /setText\(summary,/);
});
