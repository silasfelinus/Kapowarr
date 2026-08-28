const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const volumesSource = fs.readFileSync(
	path.join(__dirname, '../../frontend/static/js/volumes.js'),
	'utf8'
);
const generalCss = fs.readFileSync(
	path.join(__dirname, '../../frontend/static/css/general.css'),
	'utf8'
);

// The library renders in batches and only lays down a bounded runway, so
// every batch after the first depends on a scroll event reaching
// `scheduleLibraryRenderCheck`. It was listening on the window, and the
// library does not scroll the window: an inner container does. `scroll`
// does not bubble, so nothing arrived and a 5480-volume library stopped
// at the A's.

test('the library is not what scrolls the window', () => {
	// The premise. If this ever stops being true the listener below can
	// go back to the window, and this test says so out loud.
	assert.match(
		generalCss,
		/body\s*\{[^}]*height:\s*100dvh/,
		'body is no longer pinned to the viewport'
	);
	assert.match(
		generalCss,
		/main\s*>\s*\*:not\(\.tool-bar-container\)\s*\{[^}]*overflow-y:\s*auto/,
		'the view container no longer scrolls itself'
	);
});

test('scroll is observed where scrolling actually happens', () => {
	const listener = volumesSource.slice(
		volumesSource.indexOf("\t\t'scroll',"),
		volumesSource.indexOf("\t\t'resize',")
	);
	assert.notEqual(listener.length, 0, 'no scroll listener found');

	// Capturing, because a non-bubbling event still travels the capture
	// phase and so is seen whichever element performs the scroll.
	assert.match(listener, /capture:\s*true/);
	assert.match(listener, /passive:\s*true/);
});

test('the scroll listener is on the document, not the window', () => {
	const registration = volumesSource.slice(
		volumesSource.lastIndexOf(
			'addEventListener',
			volumesSource.indexOf("\t\t'scroll',")
		) - 20,
		volumesSource.indexOf("\t\t'scroll',")
	);
	assert.match(registration, /document\.addEventListener/);
	assert.doesNotMatch(registration, /window\.addEventListener/);
});

test('the runway check still measures against the viewport', () => {
	// `getBoundingClientRect` is viewport-relative, so it stays correct
	// when an inner element is the scroller. Only the wiring was wrong;
	// if this measurement ever moves to a scroll offset it has to become
	// container-aware at the same time.
	const check = volumesSource.slice(
		volumesSource.indexOf('function maybeRenderLibraryMore'),
		volumesSource.indexOf('function scheduleLibraryRenderCheck')
	);
	assert.match(check, /getBoundingClientRect\(\)\.top/);
	assert.match(check, /window\.innerHeight/);
});
