const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
const source = fs.readFileSync(
	path.join(root, 'frontend/static/js/volumes.js'), 'utf8'
);

// Pull the windowing out of the page script and run it against a fake
// layout. The arithmetic is the part that can be wrong in a way nobody
// notices until the library stops in the A's, and it does not need a
// browser to be wrong.
function extract(name) {
	const start = source.indexOf(`function ${name}(`);
	assert.notEqual(start, -1, `${name} not found`);
	let depth = 0;
	for (let i = source.indexOf('{', start); i < source.length; i++) {
		if (source[i] === '{') depth++;
		else if (source[i] === '}' && --depth === 0)
			return source.slice(start, i + 1);
	};
	throw new Error(`unterminated ${name}`);
};

const OVERSCAN = Number(source.match(/LIBRARY_OVERSCAN_PX = (\d+)/)[1]);
const CORRECTIONS = Number(
	source.match(/MAX_GEOMETRY_CORRECTIONS = (\d+)/)[1]
);

// A list of `total` items, `per_row` across, each row `row_height` tall,
// scrolled inside a `viewport`-tall window.
function harness({total, per_row = 1, row_height = 40, viewport = 800}) {
	const state = {
		scroll: 0, before: 0, after: 0, range: null, renders: 0,
		listeners: {}
	};

	const context = {
		LIBRARY_OVERSCAN_PX: OVERSCAN,
		MAX_GEOMETRY_CORRECTIONS: CORRECTIONS,
		LIBRARY_WINDOW_SAMPLE: 40,
		console,
		setTimeout, clearTimeout,
		window: {
			innerHeight: viewport,
			addEventListener() {}, removeEventListener() {}
		},
		document: {
			documentElement: {clientHeight: viewport},
			addEventListener(type, fn) { state.listeners[type] = fn; },
			removeEventListener() {}
		},
		// Run paints straight through, so a test is a sequence of states
		// rather than a race.
		scheduleLibraryPaint(fn) { fn(); return 1; }
	};
	vm.createContext(context);
	vm.runInContext(extract('virtualiseLibraryView'), context);

	const anchor = {
		// Item 0 sits at the top of the content; scrolling moves it up.
		getBoundingClientRect: () => ({top: -state.scroll + state.before * 0})
	};

	const window_ = context.virtualiseLibraryView({
		anchor,
		total,
		itemsPerRow: () => ({per_row, row_height}),
		setSpacers: (before, after) => {
			state.before = before;
			state.after = after;
		},
		renderRange: (start, end) => {
			state.range = [start, end];
			state.renders += 1;
		}
	});

	return {
		state,
		start: () => window_.start(40),
		// Clamped the way a real scroller clamps: you cannot scroll past the
		// last viewport of content.
		scrollTo(px) {
			const max = Math.max(
				0, Math.ceil(total / per_row) * row_height - viewport
			);
			state.scroll = Math.max(0, Math.min(px, max));
			state.listeners.scroll();
		},
		// What the page is claiming the whole list is worth, top to bottom.
		claimedHeight() {
			const [start, end] = state.range;
			const rows = Math.ceil((end - start) / per_row);
			return state.before + rows * row_height + state.after;
		},
		expectedHeight: Math.ceil(total / per_row) * row_height
	};
};

test('the scroll height is the whole library, from the first paint', () => {
	const h = harness({total: 5480});
	h.start();

	assert.equal(h.claimedHeight(), h.expectedHeight);
	assert.equal(h.state.range[0], 0, 'the window starts at the top');
});

test('and stays the whole library wherever you scroll to', () => {
	const h = harness({total: 5480});
	h.start();

	for (const fraction of [0.1, 0.25, 0.5, 0.75, 0.99, 1]) {
		h.scrollTo(h.expectedHeight * fraction);
		assert.equal(
			h.claimedHeight(), h.expectedHeight,
			`height moved at ${fraction * 100}% -- the scrollbar would jump`
		);
	};
});

test('the end of the alphabet is reachable', () => {
	// The bug this replaces: a runway that only grew when a scroll listener
	// fired left a 5,480-volume library stopping at "A Dark Interlude", with
	// a scrollbar that claimed there was more and no way to get to it.
	const h = harness({total: 5480});
	h.start();
	h.scrollTo(h.expectedHeight);

	assert.equal(h.state.range[1], 5480, 'the last volume must be rendered');
	assert.equal(h.state.after, 0, 'nothing is left below the last row');
});

test('jumping straight to the middle renders the middle', () => {
	// Not "renders everything up to the middle": a window, not a runway.
	const h = harness({total: 5480, row_height: 40});
	h.start();
	h.scrollTo(100000);

	const [start, end] = h.state.range;
	assert.ok(start > 2000, `expected to skip ahead, started at ${start}`);
	assert.ok(end > start, 'the window must not be empty');
	assert.ok(end - start < 200, `window of ${end - start} is not a window`);
});

test('only a viewport-and-a-bit is ever alive', () => {
	const h = harness({total: 5480, viewport: 800, row_height: 40});
	h.start();

	let widest = 0;
	for (let px = 0; px <= h.expectedHeight; px += 5000) {
		h.scrollTo(px);
		widest = Math.max(widest, h.state.range[1] - h.state.range[0]);
	};

	// viewport + overscan both ways, in rows, plus a row of slack.
	const bound = Math.ceil((800 + 2 * OVERSCAN) / 40) + 2;
	assert.ok(widest <= bound, `${widest} rows alive, expected at most ${bound}`);
});

test('a grid several cards across counts in rows, not cards', () => {
	const h = harness({total: 5480, per_row: 3, row_height: 200, viewport: 800});
	h.start();

	assert.equal(h.claimedHeight(), h.expectedHeight);
	h.scrollTo(h.expectedHeight);
	assert.equal(h.state.range[1], 5480);

	// Whole rows only, so a window's last row never stretches its cards the
	// way a genuinely short final row does.
	h.scrollTo(h.expectedHeight / 2);
	assert.equal(h.state.range[0] % 3, 0, 'window must start on a row boundary');
});

test('a library that fits on screen is simply rendered', () => {
	const h = harness({total: 12});
	h.start();

	assert.deepEqual(h.state.range, [0, 12]);
	assert.equal(h.state.before, 0);
	assert.equal(h.state.after, 0);
});

test('an empty library does not divide by anything', () => {
	const h = harness({total: 0});
	h.start();

	assert.deepEqual(h.state.range, [0, 0]);
});

test('scrolling back and forth does not rebuild what is already there', () => {
	const h = harness({total: 5480});
	h.start();
	const after_start = h.state.renders;

	h.scrollTo(4);
	h.scrollTo(8);
	assert.equal(
		h.state.renders, after_start,
		'a few pixels of scroll must not repaint the window'
	);
});
