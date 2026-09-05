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
		updatePrimaryControlsSource(), /innerText/,
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
