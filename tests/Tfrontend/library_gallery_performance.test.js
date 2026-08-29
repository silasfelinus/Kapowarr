const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const volumes = fs.readFileSync(
	path.join(root, 'frontend/static/js/volumes.js'),
	'utf8'
);

// The library is a window over the whole result set: the spacers above and
// below it carry the height of everything outside, so the scrollbar always
// measures the real library and the last volume is one drag away.
//
// Two earlier shapes are both ruled out here. The first rendered 16 entries
// and waited for a scroll event before asking for 16 more, which made the
// view reachable only as far as that listener happened to fire -- and it was
// attached to an element that never scrolls, so a 5,480-volume library
// stopped in the A's with a scrollbar claiming otherwise. The second built
// every entry up front, which fixed reachability and cost 2.7 seconds of
// build and layout for the table on a desktop; on a phone that read as
// freeze, a chunk of table, freeze.
//
// `the_whole_library_is_always_reachable.test.js` exercises the arithmetic
// against a fake layout. What is checked here is that the page still wires
// it up, and that neither old shape has crept back.
test('the library renders a window, not a runway and not everything', () => {
	for (const gone of [
		'LIBRARY_RENDER_BATCH_SIZE',
		'LIBRARY_RENDER_AHEAD_PX',
		'maybeRenderLibraryMore',
		'scheduleLibraryRenderCheck',
		'scheduleNextLibraryBatch',
		'viewHasMoreToRender',
		'library_render_offsets'
	]) {
		assert.ok(
			!volumes.includes(gone),
			`${gone} grew a runway from the top; the window moves with the scroll`
		);
	};

	const build = volumes
		.split('function buildLibraryView')[1]
		.split('function ensureLibraryViewBuilt')[0];

	assert.ok(
		!build.includes('renderLibraryEntries(view, library_volumes, api_key)'),
		'building every entry up front is what made the table freeze'
	);
	assert.match(build, /virtualiseLibraryView\(\{/);
	assert.match(build, /total: library_volumes\.length/,
		'the window must know the size of the whole library');
	assert.match(build, /library_volumes\.slice\(start, end\)/);
	assert.match(build, /setSpacers/);

	// Still yielded once, so the loading state paints before the DOM work.
	assert.match(build, /scheduleLibraryPaint/);
});

test('a scroll listener is allowed, but only one that can hear the scroll', () => {
	// The original defect was not the listener; it was a listener on an
	// element that never scrolls. `general.css` gives
	// `main > *:not(.tool-bar-container)` its own overflow, and mass edit
	// moves the scrolling from `#library-container` to `#table-container`,
	// so there is no single element to attach to. Captured at the document,
	// every scroll is heard whatever is doing it.
	const virtualiser = volumes
		.split('function virtualiseLibraryView')[1]
		.split('let library_view_window')[0];

	assert.match(
		virtualiser,
		/document\.addEventListener\('scroll', on_scroll, \{\s*capture: true/,
		'a listener on one named element misses the other scroller'
	);
	assert.ok(
		!virtualiser.includes("scroller.addEventListener('scroll'"),
		'that is the attachment that left the library stopping in the A\'s'
	);
	// And the position is read from the content, not from one scroller.
	assert.match(virtualiser, /anchor\.getBoundingClientRect\(\)\.top/);
	assert.ok(
		!virtualiser.includes('scroller.scrollTop'),
		'reading one element\'s scrollTop assumes it is the one scrolling'
	);
});

test('the window is torn down when the view it belongs to is', () => {
	const clear = volumes
		.split('function clearLibraryView')[1]
		.split('// Geometry differs between the two views')[0];

	assert.match(clear, /destroyLibraryWindow\(\)/,
		'a stale window would keep painting into a container that is gone');
});

test('filtering or sorting bulk-clears the old gallery DOM', () => {
	const clear = volumes
		.split('function clearLibraryView')[1]
		.split('// Geometry differs between the two views')[0];

	assert.match(clear, /replaceChildren\(space_taker\)/);
	assert.match(clear, /library_els\.views\.table\.replaceChildren\(\)/);
	assert.ok(
		!clear.includes("querySelectorAll('.list-entry').forEach"),
		'removing thousands of poster cards one JS call at a time freezes while applying a filter'
	);
});

test('lazy table rows keep complete mass-edit selection semantics', () => {
	const buildTable = volumes
		.split('function buildTableEntry')[1]
		.split('const view_builders')[0];
	assert.match(buildTable, /selected_volume_ids\.has\(Number\(volume\.id\)\)/);
	assert.match(buildTable, /checkbox\.onchange/);
	assert.match(buildTable, /selected_volume_ids\.add\(volume_id\)/);
	assert.match(buildTable, /selected_volume_ids\.delete\(volume_id\)/);

	const action = volumes
		.split('function runAction')[1]
		.split('function updateLibraryDownloadStatuses')[0];
	assert.match(action, /const volume_ids = \[\.\.\.selected_volume_ids\];/);
	assert.ok(
		!action.includes("querySelectorAll(\n\t\t'input[type=\"checkbox\"]:checked'"),
		'mass edit must not depend on every selected volume having a rendered row'
	);
});

test('queue progress events update queue volumes, not the whole library', () => {
	const update = volumes
		.split('function updateLibraryDownloadStatuses')[1]
		.split('// code run on load')[0];

	assert.match(update, /changed_volume_ids\.forEach/);
	assert.ok(
		!update.includes('library_entries.forEach'),
		'a queue tick must not walk thousands of library entries'
	);

	const listener = volumes
		.split("'kapowarr:download-queue-changed'")[1]
		.split("window.addEventListener")[0];
	assert.match(listener, /updateLibraryDownloadStatuses\(e\.detail \|\| \[\]\)/);
});
