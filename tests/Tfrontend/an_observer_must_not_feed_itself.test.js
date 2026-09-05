// The freeze was a MutationObserver writing into the subtree it observes.
//
//   Self time  Total time   Activity
//   4,896 ms   7,074 ms     chrome-extension://…/content.js       60.2%
//   2,913 ms   2,915 ms     (anonymous) library_import_review_ui.js:676:5  35.8%
//
// Line 676 is `relabelGroupSelect`, the callback of an observer watching
// `.search-results` with childList + subtree. It wrote
// `button.innerText = 'Select'`, and the innerText setter does not compare
// anything -- it replaces the element's children with a new text node every
// time, even when the text is already that. Each write was therefore a
// mutation the observer was watching for, so it called itself back, forever.
//
// It only bit once a search returned results: until then `.search-results`
// holds no buttons, so there is nothing to relabel and nothing to mutate.
// Which is why it looked like the search's fault. The ad blocker at 60% was
// a passenger -- its own observer was being woken by the runaway mutations.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const script = fs.readFileSync(
	path.join(root, 'frontend/static/js/library_import_review_ui.js'),
	'utf8'
);

function setText() {
	const start = script.indexOf('function setText');
	const body = script.slice(start, script.indexOf('\n\t};', start) + 4);
	return new Function(body + '\nreturn setText;')();
}

// A button whose text setter behaves like the real innerText setter: it
// always replaces the text node, so it always counts as a mutation.
function makeButton(text) {
	let mutations = 0;
	return {
		_t: text,
		get textContent() { return this._t; },
		set textContent(v) { mutations += 1; this._t = v; },
		get mutations() { return mutations; }
	};
}

test('an unconditional write into an observed subtree never settles', () => {
	// What the old code did, modelled: write, observe the mutation, write.
	const button = makeButton('');
	let calls = 0;
	const relabel = () => {
		calls += 1;
		if (calls > 50) return;          // stand-in for "forever"
		const before = button.mutations;
		button.textContent = 'Select';   // unconditional
		if (button.mutations !== before) relabel();
	};
	relabel();

	assert.ok(calls > 50, 'the unconditional write feeds itself');
});

test('setText settles after one pass', () => {
	const write = setText();
	const button = makeButton('');
	let calls = 0;
	const relabel = () => {
		calls += 1;
		if (calls > 50) throw new Error('setText did not break the loop');
		const before = button.mutations;
		write(button, 'Select');
		if (button.mutations !== before) relabel();
	};
	relabel();

	assert.equal(calls, 2, 'one write, one no-op, done');
	assert.equal(button.mutations, 1);
	assert.equal(button.textContent, 'Select');
});

test('a button already labelled is not touched at all', () => {
	const write = setText();
	const button = makeButton('Select');
	write(button, 'Select');
	assert.equal(button.mutations, 0);
});

test('the observer callback writes only through setText', () => {
	const start = script.indexOf('const relabelGroupSelect');
	assert.notEqual(start, -1);
	const body = script.slice(start, script.indexOf('};', start) + 2);

	assert.match(body, /setText\(button, 'Select'\)/);
	assert.doesNotMatch(
		body, /\.innerText\s*=/,
		'an unconditional write here reopens the loop'
	);
});

test('it is still watching the subtree it needs to watch', () => {
	// The fix is the conditional write, not narrowing the observer.
	const start = script.indexOf('new MutationObserver(relabelGroupSelect)');
	const body = script.slice(start, start + 200);
	assert.match(body, /childList: true, subtree: true/);
});
