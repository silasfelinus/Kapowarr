// "Run in Background" is the only way from the Continuous Import panel back
// to the import options, and its label was fixed in the template. So a
// finished pass -- 490/490 folders, nothing left to run -- still offered to
// run something in the background, which is not a description of anything.
//
// Silas, 2026-09-05: "run in background is a pretty opaque way to get back
// to the main screen, yes? Why do we have two different versions of that
// screen?" There is one start screen; the panel simply never said it was
// the door back to it.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const script = fs.readFileSync(
	path.join(root, 'frontend/static/js/library_import.js'),
	'utf8'
);
const template = fs.readFileSync(
	path.join(root, 'frontend/templates/library_import.html'),
	'utf8'
);

function renderControl() {
	// Lift the function and run it against a recording double.
	const start = script.indexOf('function renderContinuousControl');
	const end = script.indexOf('\n};', start) + 3;
	const labels = {back: null, stop: null};
	const els = {
		buttons: {
			continuous_back: {
				set innerText(v) { labels.back = v; },
				get innerText() { return labels.back; }
			},
			continuous_stop: {
				set innerText(v) { labels.stop = v; },
				get innerText() { return labels.stop; },
				dataset: {},
				disabled: false,
				classList: {add() {}, remove() {}}
			}
		}
	};
	const fn = new Function(
		'LIEls', 'continuousStopRequested',
		script.slice(start, end) + '\nreturn renderContinuousControl;'
	)(els, false);
	return {fn, labels};
}

test('while a pass is running, the button leaves it running', () => {
	const {fn, labels} = renderControl();
	fn({checked_folders: 12, total_folders: 490, remaining_folders: 478}, true);
	assert.equal(labels.back, 'Run in Background');
});

test('once it has finished, the button says where it goes', () => {
	const {fn, labels} = renderControl();
	fn({checked_folders: 490, total_folders: 490, remaining_folders: 0}, false);
	assert.equal(labels.back, 'Back to Import Options');
});

test('a paused pass with folders left is not running either', () => {
	const {fn, labels} = renderControl();
	fn({checked_folders: 40, total_folders: 490, remaining_folders: 450}, false);
	assert.equal(labels.back, 'Back to Import Options');
	// And the other button offers to continue it rather than start over.
	assert.equal(labels.stop, 'Resume Import');
});

test('with no job at all the panel still names the way out', () => {
	const {fn, labels} = renderControl();
	fn(null, false);
	assert.equal(labels.back, 'Back to Import Options');
});

test('the template no longer decides the label on its own', () => {
	// It still ships one, for the moment before the first render.
	assert.match(template, /id="continuous-back-button"/);
	assert.match(script, /LIEls\.buttons\.continuous_back\.innerText = live/);
});
