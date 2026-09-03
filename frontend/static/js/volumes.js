const library_els = {
	pages: {
		loading: document.querySelector('#loading-library'),
		empty: document.querySelector('#empty-library'),
		view: document.querySelector('#library-container'),
	},
	views: {
		list: document.querySelector('#list-library'),
		table: document.querySelector('#table-library'),
	},
	alphabet: {
		rail: document.querySelector('#alphabet-rail'),
		letters: document.querySelector('#alphabet-letters'),
		bubble: document.querySelector('#alphabet-bubble')
	},
	view_options: {
		sort: document.querySelector('#sort-button'),
		view: document.querySelector('#view-button'),
		filter: document.querySelector('#filter-button')
	},
	task_buttons: {
		update_all: document.querySelector('#updateall-button'),
		search_all: document.querySelector('#searchall-button')
	},
	search: {
		clear: document.querySelector('#clear-search'),
		container: document.querySelector('#search-container'),
		input: document.querySelector('#search-input')
	},
	stats: {
		footer: document.querySelector('#lib-stats'),
		volume_count: document.querySelector('#volume-count'),
		volume_monitored_count: document.querySelector('#volume-monitored-count'),
		volume_unmonitored_count: document.querySelector('#volume-unmonitored-count'),
		issue_count: document.querySelector('#issue-count'),
		issue_download_count: document.querySelector('#issue-download-count'),
		file_count: document.querySelector('#file-count'),
		total_file_size: document.querySelector('#total-file-size')
	},
	mass_edit: {
		bar: document.querySelector('.action-bar'),
		button: document.querySelector('#massedit-button'),
		toggle: document.querySelector('#massedit-toggle'),
		select_all: document.querySelector('#selectall-input'),
		cancel: document.querySelector('#cancel-massedit'),
		progress: document.querySelector("#massedit-progress")
	}
};

const pre_build_els = {
	list_entry: document.querySelector('.pre-build-els .list-entry'),
	table_entry: document.querySelector('.pre-build-els .table-entry')
};

// Keep each individual main-thread job small enough for mobile browsers. More
// importantly, do not drain the entire library once the first batch is visible:
// render only a viewport-sized runway and extend it as the user approaches the
// footer. A 2,000-volume library should not become a 2,000-card DOM just because
// the page was opened.
let library_render_generation = 0;
let library_fetching = false;

// Volume ID -> LibraryEntry, covering every volume in the current listing.
// The registry is deliberately complete even when most rows/cards have not been
// rendered yet, so socket progress updates remain correct for off-screen volumes.
const library_entries = new Map();

// The most recent `/volumes` payload, kept so that switching views can build the
// other view without going back to the server.
let library_volumes = [];

// Only the visible view is built.
// view currently exists in the DOM; it advances only while the footer is near the
// viewport rather than racing to library_volumes.length in the background.
const library_built_views = {list: false, table: false};
const library_render_pending = {list: false, table: false};
const selected_volume_ids = new Set();

// Queue change events carry the complete current queue. Remember which volumes
// were represented last time so an ended download can clear its badge without
// walking every volume in the library on every progress tick.
let library_download_volume_ids = new Set();

function showLibraryPage(el) {
	hide(Object.values(library_els.pages), [el]);
};

function scheduleLibraryRender(callback) {
	if (typeof window.requestIdleCallback === 'function') {
		window.requestIdleCallback(callback, {timeout: 100});
	} else {
		setTimeout(callback, 0);
	};
};

function scheduleLibraryPaint(callback) {
	if (typeof window.requestAnimationFrame === 'function')
		window.requestAnimationFrame(callback);
	else
		setTimeout(callback, 0);
};

function inMassEdit() {
	return library_els.mass_edit.toggle.hasAttribute('checked');
};

// Which view the user is actually looking at. Mass edit always shows the table
// regardless of the view selector -- see the `#massedit-toggle` rules in
// volumes.css -- so it is not simply the value of the dropdown.
function activeLibraryView() {
	if (inMassEdit())
		return 'table';

	return library_els.view_options.view.value === 'table' ? 'table' : 'list';
};

class LibraryEntry {
	constructor(id, api_key) {
		this.id = id;
		this.api_key = api_key;
		// Either may be null: a view is only built once it is looked at.
		this.list_entry = null;
		this.table_entry = null;

		this.monitored = false;
		this.downloaded_count = 0;
		this.total_count = 0;
		this.download_status = null;
	};

	setMonitored(monitored) {
		sendAPI('PUT', `/volumes/${this.id}`, this.api_key, {}, {
			monitored: monitored
		})
		.then(response => {
			this.monitored = monitored;
			this.renderMonitored();
			// The bar is coloured by monitored state, so it follows along.
			this.renderProgressBar();
		});
	};

	renderMonitored() {
		if (this.list_entry !== null) {
			if (this.monitored)
				this.list_entry.setAttribute('monitored', '');
			else
				this.list_entry.removeAttribute('monitored');
		};

		if (this.table_entry !== null) {
			const monitored_button =
				this.table_entry.querySelector('.table-monitored');
			monitored_button.onclick = e => this.setMonitored(!this.monitored);

			// An attribute, not `setIcon`. `setIcon` assigns `innerHTML`, so
			// every row parsed its own copy of a bookmark SVG and kept the
			// resulting element tree: on a 5,000-volume library that is 5,000
			// HTML-fragment parses during the build and ~20,000 live SVG nodes
			// afterwards, each with its own layout and paint objects. It is by
			// far the most expensive thing in a row that is otherwise text,
			// and it is what put mobile Chrome over the edge -- the table
			// scrolled a little way and then died.
			//
			// `.table-monitored` in volumes.css draws the same two shapes as a
			// CSS mask keyed off this attribute. No SVG nodes, no per-row
			// parse, and the toggle is one attribute write.
			const label = this.monitored ? 'Monitored' : 'Unmonitored';
			monitored_button.dataset.monitored =
				this.monitored ? 'true' : 'false';
			monitored_button.title = label;
			monitored_button.ariaLabel = label;
		};
	};

	getProgress() {
		return [this.downloaded_count, this.total_count];
	};

	setProgressBar(
		downloaded_count,
		total_count
	) {
		this.downloaded_count = Math.min(downloaded_count, total_count);
		this.total_count = total_count;
		this.renderProgressBar();
		return;
	};

	renderProgressBar() {
		const progress = this.total_count > 0
			? this.downloaded_count / this.total_count * 100
			: 0;

		let color;
		if (progress === 100)
			color = 'var(--success-color)';
		else if (this.monitored)
			color = 'var(--accent-color)';
		else
			color = 'var(--error-color)';

		const text = `${this.downloaded_count}/${this.total_count}`;

		if (this.list_entry !== null) {
			const bar = this.list_entry.querySelector('.list-prog-bar');
			this.list_entry.querySelector('.list-prog-num').innerText = text;
			bar.style.width = `${progress}%`;
			bar.style.backgroundColor = color;
		};

		if (this.table_entry !== null) {
			const bar = this.table_entry.querySelector('.table-prog-bar');
			this.table_entry.querySelector('.table-prog-num').innerText = text;
			bar.style.width = `${progress}%`;
			bar.style.backgroundColor = color;
		};

		return;
	};

	setDownloadStatus(download_status) {
		this.download_status = download_status;
		this.renderDownloadStatus();
		return;
	};

	renderDownloadStatus() {
		const elements = [];
		if (this.list_entry !== null)
			elements.push(this.list_entry.querySelector('.list-download-status'));
		if (this.table_entry !== null)
			elements.push(this.table_entry.querySelector('.table-download-status'));

		elements.forEach(element => {
			if (this.download_status === null) {
				element.classList.add('hidden');
				element.innerText = '';
				delete element.dataset.status;
				return;
			};

			element.innerText = this.download_status.text;
			element.title = `${this.download_status.text}. View download queue.`;
			element.dataset.status = this.download_status.status;
			element.classList.remove('hidden');
		});
	};
};

function buildListEntry(entry, volume, api_key, fragment) {
	const list_entry = pre_build_els.list_entry.cloneNode(true);

	list_entry.ariaLabel =
		`View the volume ${volume.title} (${volume.year}) Volume ${volume.volume_number}`;
	list_entry.href = `${url_base}/volumes/${volume.id}`;
	list_entry.querySelector('.list-img').src =
		`${url_base}/api/volumes/${volume.id}/cover?api_key=${api_key}`;

	const list_title = list_entry.querySelector('.list-title');
	list_title.innerText =
	list_title.title =
		`${volume.title} (${volume.year})`;
	list_entry.querySelector('.list-volume').innerText =
		`Volume ${volume.volume_number}`;

	entry.list_entry = list_entry;
	fragment.appendChild(list_entry);
};

function buildTableEntry(entry, volume, api_key, fragment) {
	const table_entry = pre_build_els.table_entry.cloneNode(true);

	table_entry.ariaLabel =
		`View the volume ${volume.title} (${volume.year}) Volume ${volume.volume_number}`;
	table_entry.dataset.id = volume.id;
	// Selection lives in data, not in the presence of a DOM row. A lazy row
	// therefore appears with the same state it would have had if every table row
	// had been built eagerly.
	const checkbox = table_entry.querySelector('input[type="checkbox"]');
	checkbox.checked = selected_volume_ids.has(Number(volume.id));
	checkbox.onchange = () => {
		const volume_id = Number(volume.id);
		if (checkbox.checked)
			selected_volume_ids.add(volume_id);
		else
			selected_volume_ids.delete(volume_id);

		library_els.mass_edit.select_all.checked =
			selected_volume_ids.size === library_volumes.length;
	};

	const link = table_entry.querySelector('.table-link');
	link.href = `${url_base}/volumes/${volume.id}`;
	link.innerText = volume.title;

	table_entry.querySelector('.table-year').innerText = volume.year;
	// The number alone. "Volume 1" repeated down a column headed "Vol."
	// spends the width of the whole word to say nothing, and on a phone
	// that width comes straight out of the title.
	table_entry.querySelector('.table-volume').innerText =
		volume.volume_number;

	entry.table_entry = table_entry;
	fragment.appendChild(table_entry);
};

const view_builders = {
	list: buildListEntry,
	table: buildTableEntry
};

function createLibraryEntry(volume, api_key) {
	const entry = new LibraryEntry(volume.id, api_key);
	entry.monitored = Boolean(volume.monitored);
	entry.downloaded_count = Math.min(
		volume.issues_downloaded_monitored,
		volume.issue_count_monitored
	);
	entry.total_count = volume.issue_count_monitored;
	library_entries.set(volume.id, entry);
	return entry;
};

// Build `view` for `volumes` and paint the results in one insert.
function renderLibraryEntries(view, volumes, api_key) {
	const fragment = document.createDocumentFragment();

	for (const volume of volumes) {
		let entry = library_entries.get(volume.id);
		if (entry === undefined)
			entry = createLibraryEntry(volume, api_key);

		entry.download_status = getVolumeDownloadStatus(volume.id);
		view_builders[view](entry, volume, api_key, fragment);

		// Applied after the element is attached to the entry so that the freshly
		// built view picks up the state the entry is already carrying.
		entry.renderMonitored();
		entry.renderProgressBar();
		entry.renderDownloadStatus();
	};

	// The caller places the fragment: a windowed view inserts it between its
	// spacers, and the poster grid before its space-taker.
	return fragment;
};

// How much to render beyond the visible band, in pixels of scroll.
// Enough that an ordinary fling lands on rows that already exist.
const LIBRARY_OVERSCAN_PX = 1500;

// How many entries to build before measuring one. Enough to fill any phone.
const LIBRARY_WINDOW_SAMPLE = 40;

// How many times one paint may re-measure and try again before settling for
// what it has.
const MAX_GEOMETRY_CORRECTIONS = 3;

// How many frames to keep trying for a first measurement before settling
// for what is rendered and waiting for a scroll to try again.
const MEASURE_ATTEMPTS = 5;

// How many times a jump may estimate, repaint and re-measure. Two is
// usually enough -- the first pass gets the target rendered, the second
// aligns it exactly -- and the third covers a row whose height changed
// under the first correction.
const SCROLL_SETTLE_PASSES = 4;

// Kept with the spacer rows that have to span the table.
const TABLE_COLUMN_COUNT = 6;

// Render a window of a long list instead of all of it, while keeping the
// scroll height the whole list would have.
//
// This is not the batching that used to be here. That grew a runway from the
// top and only extended it when a scroll event fired, so the library was
// reachable exactly as far as the listener happened to run -- and the
// listener was attached to an element that never scrolls, which is why a
// 5,480-volume library stopped in the A's. The scrollbar was a lie about a
// list that did not exist yet.
//
// Here the whole list always exists as scroll height: two spacers stand in
// for everything outside the window, so the scrollbar is honest, the end of
// the alphabet is one drag away, and jumping to it renders what is there.
// What changes is only how many elements are alive at once. Building all
// 5,480 rows up front cost 2.7 seconds of layout on a desktop, and on a
// phone it read as freeze, a chunk of table, freeze.
//
// Entry state does not live in the DOM -- `library_entries` holds every
// volume whether or not it is rendered -- so a row that scrolls into view
// arrives with the monitored state, progress and download status it would
// have had all along.
function virtualiseLibraryView(config) {
	const {anchor, total, setSpacers, renderRange, itemsPerRow, elementAt}
		= config;
	let row_height = 0;
	let per_row = 1;
	let resize_timer = null;
	// Bounded so a geometry that will not settle cannot loop forever.
	let corrections = 0;
	let rendered = null;
	let frame = null;
	// Run once the window has actually been repainted. A correction queued
	// with a bare rAF runs *before* the repaint it is meant to follow --
	// which is how a jump ended up 95px above where it had just aligned.
	let after_paint = [];
	// A drag down the rail asks for a dozen letters in a second, each
	// leaving corrections queued behind it. Only the last one asked for is
	// still wanted; an earlier letter's correction firing afterwards would
	// pull the library back to where the finger used to be.
	let jump = 0;

	// Measured against the viewport rather than against any one scroller's
	// `scrollTop`. Which element actually scrolls is not fixed here: outside
	// mass edit it is `#library-container`, and mass edit hands the job to
	// `#table-container` by giving it a height while hiding the outer
	// overflow. Reading the anchor's own position on screen is true under
	// both, and under a page that scrolls for some third reason.
	function visibleRange() {
		const anchor_top = anchor.getBoundingClientRect().top;
		const viewport = window.innerHeight
			|| document.documentElement.clientHeight;

		const top = -anchor_top - LIBRARY_OVERSCAN_PX;
		const bottom = -anchor_top + viewport + LIBRARY_OVERSCAN_PX;

		const first_row = Math.max(0, Math.floor(top / row_height));
		const last_row = Math.max(first_row + 1, Math.ceil(bottom / row_height));

		return [
			Math.min(total, first_row * per_row),
			Math.min(total, last_row * per_row)
		];
	}

	// The row to keep still across a repaint: whichever rendered one is at
	// the top of the scrolling area, named by its place in the library so it
	// can be found again once the window has been rebuilt.
	function topmostRendered(el) {
		if (!elementAt)
			return null;

		const top = viewportTop(el);
		let best = null;
		for (const node of anchor.querySelectorAll('[data-library-index]')) {
			const offset = node.getBoundingClientRect().top - top;
			if (offset >= -1 && (best === null || offset < best.offset))
				best = {index: Number(node.dataset.libraryIndex), offset: offset};
		};
		return best;
	}

	function paint(start, end) {
		if (rendered && rendered[0] === start && rendered[1] === end)
			return;

		// `row_height` is one number for rows that are not all the same
		// height -- a card carrying a progress bar or a download status is
		// taller than one that is not -- so the spacers are an estimate,
		// and every repaint that changes them slides the library under
		// whoever is reading it. Ordinary scrolling shows this as drift;
		// a jump shows it as landing on the wrong letter.
		//
		// So hold one row still: note where it is, repaint, and give back
		// however far it moved.
		const el = scroller();
		const keep = topmostRendered(el);

		const rows_before = Math.floor(start / per_row);
		const rows_after = Math.max(
			0, Math.ceil(total / per_row) - Math.ceil(end / per_row)
		);
		setSpacers(rows_before * row_height, rows_after * row_height);
		renderRange(start, end);
		rendered = [start, end];

		if (keep === null)
			return;

		const again = elementAt(keep.index);
		if (again === null)
			return;

		const moved = again.getBoundingClientRect().top - viewportTop(el)
			- keep.offset;
		if (Math.abs(moved) >= 1)
			el.scrollBy({top: moved, behavior: 'instant'});
	}

	function update() {
		frame = null;
		const settle = after_paint;
		after_paint = [];

		if (row_height <= 0) {
			// Never measured, or measured while the view was hidden. A
			// scroll is as good a moment to try again as any, and until it
			// succeeds the sample is what is on screen.
			adopt(itemsPerRow());
			if (row_height <= 0)
				return;
		};

		const was = [per_row, row_height];
		const [start, end] = visibleRange();
		paint(start, end);

		// Re-measure from what is actually on screen. A resize, a late web
		// font or a zoom changes both numbers, and the spacers are only
		// honest while they agree with the rows between them.
		adopt(itemsPerRow());
		if (
			corrections < MAX_GEOMETRY_CORRECTIONS
			&& (per_row !== was[0] || Math.abs(row_height - was[1]) > 0.5)
		) {
			corrections += 1;
			rendered = null;
			schedule();
		} else {
			corrections = 0;
		};

		for (const fn of settle)
			fn();
	}

	function schedule() {
		if (frame !== null)
			return;

		// Claimed before scheduling, not after. `scheduleLibraryPaint` falls
		// back to `setTimeout` where there is no rAF, and a scheduler that
		// ran the callback synchronously would have `update` clear the flag
		// and this line set it again straight afterwards -- leaving it
		// permanently set and every later scroll ignored.
		frame = true;
		scheduleLibraryPaint(update);
	}

	function adopt(geometry) {
		if (geometry.row_height > 0)
			row_height = geometry.row_height;
		// A measurement that cannot tell a full row from a partial one says
		// so with zero rather than guessing; the last good count stands.
		if (geometry.per_row > 0)
			per_row = geometry.per_row;
	}

	// Bootstrap: render enough to measure with, then take over from the
	// measurement. Nothing can be computed before something exists.
	function measureFrom(sample_end) {
		setSpacers(0, 0);
		renderRange(0, sample_end);
		rendered = [0, sample_end];

		adopt(itemsPerRow());
		return row_height > 0;
	}

	const on_scroll = () => schedule();
	const debounced_resize = () => {
		if (resize_timer !== null)
			clearTimeout(resize_timer);
		resize_timer = setTimeout(() => {
			resize_timer = null;
			on_resize();
		}, 150);
	};
	// A width change moves both numbers -- a narrower grid fits fewer cards
	// across, so the same library needs more rows. Dropping the cached range
	// forces a repaint, and `update` re-measures from it.
	const on_resize = () => {
		rendered = null;
		schedule();
	};

	// Which element actually scrolls is not fixed -- see `visibleRange`. Ask
	// the DOM rather than assume: the first ancestor that can scroll, or the
	// document if none of them do.
	function scroller() {
		let el = anchor.parentElement;
		while (el) {
			const overflow = getComputedStyle(el).overflowY;
			if (
				(overflow === 'auto' || overflow === 'scroll')
				&& el.scrollHeight > el.clientHeight
			)
				return el;
			el = el.parentElement;
		};
		return document.scrollingElement || document.documentElement;
	}

	// Where the top of the scrolling area is on screen, which is where a
	// row being jumped to should end up. Not zero: outside mass edit the
	// scroller is `#library-container`, which starts partway down the page.
	function viewportTop(el) {
		return el === document.scrollingElement || el === document.documentElement
			? 0
			: el.getBoundingClientRect().top + el.clientTop;
	}

	// Move `index` towards the top of the scrolling area, and report how far
	// out it still was. Measured off the element when the window happens to
	// hold it, estimated from the average row otherwise.
	function nudgeToward(el, index) {
		const top = viewportTop(el);
		const found = elementAt ? elementAt(index) : null;
		const delta = found
			? found.getBoundingClientRect().top - top
			: Math.floor(index / per_row) * row_height
				+ anchor.getBoundingClientRect().top - top;

		// A sub-pixel remainder is the browser's own rounding, not
		// something another pass can improve.
		if (Math.abs(delta) >= 1)
			el.scrollBy({top: delta, behavior: 'instant'});

		return delta;
	}

	// Keep nudging after each repaint until the row stays where it was put.
	function settleOnto(el, index, tries, token) {
		if (tries <= 0)
			return;

		after_paint.push(() => {
			if (token !== jump)
				return;
			if (Math.abs(nudgeToward(el, index)) >= 1)
				settleOnto(el, index, tries - 1, token);
		});
		schedule();
	}

	return {
		// Put the row holding `index` at the top of the scrolling area.
		//
		// Arithmetic gets close and then the DOM settles it. The estimate
		// has to exist at all because the element for that index almost
		// certainly does not -- that is what windowing means, and there is
		// nothing to call `scrollIntoView` on. But `row_height` is one
		// number for rows that are not all the same height: a wrapped title
		// makes a taller card, and the same library measured 240 at rest
		// and 256 two seconds later. Every row of difference is an
		// accumulating error, so a jump computed from it alone lands short,
		// and lands shorter the further it goes -- which is how the rail
		// arrived at the K's when it said M, and at the C's when it said O.
		//
		// So: estimate, paint, and look. Once the target row has been
		// rendered its true position is a fact rather than a product, and
		// the last step is measured. `update` is called rather than
		// scheduled because each pass needs the paint the previous one
		// asked for; a rAF here would return before anything moved.
		scrollToIndex(index) {
			if (row_height <= 0)
				return false;

			const el = scroller();
			const token = ++jump;
			for (let pass = 0; pass < SCROLL_SETTLE_PASSES; pass++) {
				if (Math.abs(nudgeToward(el, index)) < 1)
					break;
				// The window has to be rebuilt where we now are, or the
				// next pass reads the rows belonging to where we were.
				rendered = null;
				update();
			};

			// And then keep correcting after each repaint until it stops
			// moving. The spacers are sized from that same average row
			// height, so every repaint shifts the content under us by
			// whatever the average is wrong by -- including the repaints
			// the scroll itself provokes, which would otherwise quietly
			// undo the alignment just achieved.
			settleOnto(el, index, SCROLL_SETTLE_PASSES, token);
			return true;
		},

		start(sample_end) {
			// Captured at the document, so whichever element is scrolling
			// is heard from without having to know which one it is.
			document.addEventListener('scroll', on_scroll, {
				capture: true, passive: true
			});
			// A phone rotating, or a browser bar sliding away, should not
			// repaint once per pixel.
			window.addEventListener('resize', debounced_resize);

			// Measuring can fail for a frame -- a container not laid out
			// yet, a font still loading. Try again on the next frame rather
			// than assuming, and never fall back to rendering the whole
			// library: that fallback was worse than the problem, because it
			// silently restored exactly what windowing replaced. A view
			// that has not measured yet shows its sample and keeps trying.
			const attempt = tries => {
				if (measureFrom(Math.min(total, sample_end)) || tries <= 0) {
					schedule();
					return;
				};
				scheduleLibraryPaint(() => attempt(tries - 1));
			};

			attempt(MEASURE_ATTEMPTS);
		},
		destroy() {
			document.removeEventListener('scroll', on_scroll, {capture: true});
			window.removeEventListener('resize', debounced_resize);
			if (resize_timer !== null)
				clearTimeout(resize_timer);
			rendered = null;
		}
	};
};

// Which field each sort actually orders by, where that field is text. The
// rail is only honest for these: under "Year" or "Recently Added" a letter
// says nothing about where anything is.
const ALPHABETICAL_SORTS = {
	title: volume => volume.title,
	publisher: volume => volume.publisher
};

// Below this there is nothing to jump past.
const ALPHABET_RAIL_MINIMUM = 30;

// Where each letter starts in the library, in the order the library came in.
//
// Read off the data rather than worked out from the alphabet, because the
// server does the sorting and the rail has to agree with it -- collation,
// leading articles, punctuation and all. A letter with nothing under it is
// not offered, which is why the rail on a small library is short rather than
// twenty-six dead targets.
function buildAlphabetIndex(volumes, keyOf) {
	const letters = [];
	const seen = new Set();

	volumes.forEach((volume, index) => {
		const value = (keyOf(volume) || '').trim();
		const first = value.charAt(0).toUpperCase();
		// Anything that is not a letter shares one bucket, which is where
		// the numbers and the punctuation live.
		const letter = first >= 'A' && first <= 'Z' ? first : '#';

		if (!seen.has(letter)) {
			seen.add(letter);
			letters.push({letter: letter, index: index});
		};
	});

	return letters;
}

let alphabet_index = [];

// Draw the rail for the library as it currently stands.
function renderAlphabetRail() {
	const els = library_els.alphabet;
	if (!els.rail)
		return;

	const keyOf = ALPHABETICAL_SORTS[library_els.view_options.sort.value];
	alphabet_index = keyOf && library_volumes.length >= ALPHABET_RAIL_MINIMUM
		? buildAlphabetIndex(library_volumes, keyOf)
		: [];

	// One letter is not a rail, it is a label.
	if (alphabet_index.length < 2) {
		els.rail.classList.add('hidden');
		els.letters.replaceChildren();
		return;
	};

	els.letters.replaceChildren(...alphabet_index.map(entry => {
		const button = document.createElement('button');
		button.type = 'button';
		button.className = 'alphabet-letter';
		button.textContent = entry.letter;
		button.dataset.index = entry.index;
		button.setAttribute(
			'aria-label', `Jump to ${entry.letter === '#'
				? 'numbers and symbols' : entry.letter}`);
		return button;
	}));
	els.rail.classList.remove('hidden');
};

function jumpToAlphabetIndex(index, letter) {
	if (library_view_window === null)
		return;

	library_view_window.scrollToIndex(index);
	const bubble = library_els.alphabet.bubble;
	if (bubble) {
		bubble.textContent = letter;
		bubble.classList.remove('hidden');
	};
};

function hideAlphabetBubble() {
	const bubble = library_els.alphabet.bubble;
	if (bubble)
		bubble.classList.add('hidden');
};

// Which letter a pointer at `client_y` is over.
//
// By proportion along the letters rather than by hit-testing an element, so
// a finger that slides off the side of a rail two characters wide -- which
// on a phone it does constantly -- goes on scrubbing instead of stopping
// dead.
//
// Measured from the first letter's top to the last one's bottom, not from
// the container's box. The container is padded, and spreading the alphabet
// across padding it does not occupy shifts every letter by a fraction of
// one: a finger on the visible "#" reported A, and a finger on "Z"
// reported Y.
function letterAtPointer(client_y) {
	if (!alphabet_index.length)
		return null;

	const letters = library_els.alphabet.letters.children;
	if (!letters.length)
		return null;

	const first = letters[0].getBoundingClientRect();
	const last = letters[letters.length - 1].getBoundingClientRect();
	const span = last.bottom - first.top;
	if (span <= 0)
		return null;

	const proportion = (client_y - first.top) / span;
	const position = Math.floor(proportion * alphabet_index.length);
	return alphabet_index[
		Math.min(alphabet_index.length - 1, Math.max(0, position))
	];
};

function setupAlphabetRail() {
	const els = library_els.alphabet;
	if (!els.rail)
		return;

	let scrubbing = false;
	let last_letter = null;

	const scrubTo = client_y => {
		const entry = letterAtPointer(client_y);
		if (entry === null || entry.letter === last_letter)
			return;
		last_letter = entry.letter;
		jumpToAlphabetIndex(entry.index, entry.letter);
	};

	els.rail.addEventListener('pointerdown', event => {
		scrubbing = true;
		last_letter = null;
		// So a finger that leaves the rail keeps sending moves here.
		if (els.rail.setPointerCapture)
			els.rail.setPointerCapture(event.pointerId);
		scrubTo(event.clientY);
		event.preventDefault();
	});

	els.rail.addEventListener('pointermove', event => {
		if (scrubbing)
			scrubTo(event.clientY);
	});

	const release = () => {
		if (!scrubbing)
			return;
		scrubbing = false;
		last_letter = null;
		hideAlphabetBubble();
	};
	els.rail.addEventListener('pointerup', release);
	els.rail.addEventListener('pointercancel', release);

	// Keyboard and assistive technology never see the drag, so the letters
	// stay ordinary buttons underneath it.
	//
	// The letters take no pointer events -- that is what keeps a drag from
	// snagging on them -- so a click arrives with the container as its
	// target and `closest` finds nothing. Asking which letter is at the
	// click's position works for both, and for a keyboard `Enter`, whose
	// event carries the button itself.
	els.letters.addEventListener('click', event => {
		const entry = event.target.closest('.alphabet-letter')
			? {
				index: Number(event.target.closest('.alphabet-letter')
					.dataset.index),
				letter: event.target.closest('.alphabet-letter').textContent
			}
			: letterAtPointer(event.clientY);
		if (!entry)
			return;
		jumpToAlphabetIndex(entry.index, entry.letter);
		setTimeout(hideAlphabetBubble, 600);
	});
};

// The live window for whichever view is built, so switching views or
// refetching tears the old one down.
let library_view_window = null;

function destroyLibraryWindow() {
	if (library_view_window !== null) {
		library_view_window.destroy();
		library_view_window = null;
	};
};

function clearLibraryView(view) {
	destroyLibraryWindow();

	if (view === 'list') {
		const space_taker = library_els.views.list.querySelector('.space-taker');
		library_els.views.list.replaceChildren(space_taker);
	} else {
		library_els.views.table.replaceChildren();
	};

	library_built_views[view] = false;
	library_render_pending[view] = false;
	library_entries.forEach(entry => {
		if (view === 'list')
			entry.list_entry = null;
		else
			entry.table_entry = null;
	});
};

// Geometry differs between the two views; the windowing does not. A table
// row spans the table and there is one per row; a poster card is one of
// however many fit across the grid at the current width.
const library_view_geometry = {
	table: {
		container: () => library_els.views.table,
		entry_selector: '.table-entry',
		makeSpacer: () => {
			const spacer = document.createElement('tr');
			spacer.className = 'library-spacer';
			spacer.setAttribute('aria-hidden', 'true');
			const cell = document.createElement('td');
			cell.colSpan = TABLE_COLUMN_COUNT;
			spacer.appendChild(cell);
			return spacer;
		},
		setSpacerHeight: (spacer, px) => spacer.firstChild.style.height = `${px}px`,
		measure: entries => ({
			per_row: 1,
			row_height: entries.length
				? entries[0].getBoundingClientRect().height
				: 0
		}),
	},
	list: {
		container: () => library_els.views.list,
		entry_selector: '.list-entry',
		makeSpacer: () => {
			const spacer = document.createElement('div');
			spacer.className = 'library-spacer';
			spacer.setAttribute('aria-hidden', 'true');
			return spacer;
		},
		setSpacerHeight: (spacer, px) => spacer.style.height = `${px}px`,
		measure: entries => {
			if (!entries.length)
				return {per_row: 0, row_height: 0};

			// Cards wrap, so how many fit across is a fact about the rendered
			// grid rather than something to compute from the stylesheet.
			// Count the ones sharing the first card's top edge.
			const first_top = entries[0].offsetTop;
			let per_row = 0;
			for (const entry of entries) {
				if (entry.offsetTop !== first_top)
					break;
				per_row += 1;
			};

			const next = entries[per_row];
			if (next === undefined) {
				// Only one row is rendered, so there is no way to tell a full
				// row from a partial one. Measuring here would be actively
				// wrong at the end of the library, where the last row holds
				// whatever is left: reading 12 cards as the width of the grid
				// would inflate every row count that follows and the scroll
				// height with them. Report the height and decline the count.
				return {
					per_row: 0,
					row_height: entries[0].getBoundingClientRect().height
				};
			};

			return {
				per_row: per_row,
				// Card plus the grid gap, taken from the row below.
				row_height: next.offsetTop - first_top
			};
		}
	}
};

// Build a view as a window over the whole library.
//
// The list is complete as scroll height from the first paint: two spacers
// stand in for everything outside the window, so the scrollbar measures the
// real library and the end of the alphabet is one drag away. Only what is
// near the viewport is an element.
//
// Building all of them was the previous attempt at "the skeleton is the
// whole set". It was right about the scrollbar and wrong about the cost:
// 5,480 table rows measured at 2.7 seconds of build and layout on a
// desktop, which on a phone reads as freeze, a chunk of table, freeze.
function buildLibraryView(view, api_key, generation, on_first_batch=null) {
	clearLibraryView(view);
	library_built_views[view] = true;
	library_render_pending[view] = true;

	const geometry = library_view_geometry[view];
	const container = geometry.container();
	const spacer_before = geometry.makeSpacer();
	const spacer_after = geometry.makeSpacer();

	// Yield once so the loading state gets a paint before DOM construction:
	// JSON parsing and a network callback would otherwise run straight into it.
	scheduleLibraryPaint(() => {
		if (generation !== library_render_generation)
			return;
		library_render_pending[view] = false;

		// The poster grid keeps its trailing space-taker, which stops a short
		// final row from stretching across the whole width.
		const space_taker = container.querySelector('.space-taker');
		container.replaceChildren(
			...[spacer_before, spacer_after, space_taker].filter(Boolean)
		);

		library_view_window = virtualiseLibraryView({
			anchor: container,
			total: library_volumes.length,
			itemsPerRow: () => geometry.measure(
				[...container.querySelectorAll(geometry.entry_selector)]
			),
			setSpacers: (before, after) => {
				geometry.setSpacerHeight(spacer_before, before);
				geometry.setSpacerHeight(spacer_after, after);
			},
			renderRange: (start, end) => {
				while (spacer_before.nextSibling !== spacer_after)
					spacer_before.nextSibling.remove();

				container.insertBefore(
					renderLibraryEntries(
						view, library_volumes.slice(start, end), api_key
					),
					spacer_after
				);

				// Stamped so `scrollToIndex` can find out where it really
				// landed. Row heights are not uniform -- a wrapped title
				// makes a taller card -- so arithmetic alone only ever
				// gets close.
				let at = start;
				for (
					let el = spacer_before.nextSibling;
					el !== spacer_after;
					el = el.nextSibling
				)
					el.dataset.libraryIndex = at++;
			},
			elementAt: index => container.querySelector(
				`[data-library-index="${index}"]`
			)
		});

		// Shown *before* the window measures itself. `showLibraryPage` is
		// what removes `hidden` from the container, and every
		// `getBoundingClientRect` inside a `display: none` subtree is zero:
		// measuring first meant measuring nothing, every time, on every
		// device. The window then could not compute a row height, and fell
		// back to rendering the whole library -- which is the behaviour
		// windowing exists to replace. The Chromium bench never caught it
		// because its container was visible from the first frame.
		if (on_first_batch !== null)
			on_first_batch();

		library_view_window.start(LIBRARY_WINDOW_SAMPLE);
		library_els.mass_edit.button.disabled = false;
	});
};

// Make sure the view the user is about to look at exists, building it on the
// spot if this is the first time it has been asked for.
function ensureLibraryViewBuilt(api_key) {
	if (library_fetching || library_volumes.length === 0)
		return;

	const view = activeLibraryView();
	if (!library_built_views[view])
		buildLibraryView(view, api_key, library_render_generation);
};

function populateLibrary(volumes, api_key, generation, on_first_batch) {
	clearLibraryView('list');
	clearLibraryView('table');
	library_entries.clear();
	library_volumes = volumes;
	selected_volume_ids.clear();
	library_els.mass_edit.select_all.checked = false;

	// Seed only lightweight state for every volume. DOM and queue summaries are
	// created on demand, but socket progress remains correct before that happens.
	volumes.forEach(volume => createLibraryEntry(volume, api_key));

	buildLibraryView(
		activeLibraryView(),
		api_key,
		generation,
		on_first_batch
	);
	renderAlphabetRail();
};

function fetchLibrary(api_key) {
	library_els.mass_edit.progress.innerText = '';
	library_els.mass_edit.button.disabled = true;
	showLibraryPage(library_els.pages.loading);
	library_fetching = true;
	const generation = ++library_render_generation;

	const params = {
		sort: library_els.view_options.sort.value,
		filter: library_els.view_options.filter.value
	};
	const query = library_els.search.input.value;
	if (query !== '')
		params.query = query;

	fetchAPI('/volumes', api_key, params)
	.then(json => {
		if (generation !== library_render_generation)
			return;

		library_fetching = false;
		if (json.result.length === 0) {
			clearLibraryView('list');
			clearLibraryView('table');
			library_volumes = [];
			library_entries.clear();
			renderAlphabetRail();
			library_els.mass_edit.button.disabled = false;
			showLibraryPage(library_els.pages.empty);
		} else {
			populateLibrary(
				json.result,
				api_key,
				generation,
				() => showLibraryPage(library_els.pages.view)
			);
		};
	});
};

function searchLibrary() {
	usingApiKey().then(api_key => fetchLibrary(api_key));
};

function clearSearch(api_key) {
	library_els.search.input.value = '';
	fetchLibrary(api_key);
};

function fetchStats(api_key) {
	fetchAPI('/volumes/stats', api_key)
	.then(json => {
		library_els.stats.volume_count.innerText = json.result.volumes;
		library_els.stats.volume_monitored_count.innerText = json.result.monitored;
		library_els.stats.volume_unmonitored_count.innerText = json.result.unmonitored;
		library_els.stats.issue_count.innerText = json.result.issues;
		library_els.stats.issue_download_count.innerText = json.result.downloaded_issues;
		library_els.stats.file_count.innerText = json.result.files;
		library_els.stats.total_file_size.innerText =
			json.result.total_file_size > 0
			? convertSize(json.result.total_file_size)
			: '0 MB';
	});
};

//
// Mass Edit
//
function runAction(api_key, action, args={}) {
	showLibraryPage(library_els.pages.loading);

	const volume_ids = [...selected_volume_ids];

	sendAPI('POST', '/masseditor', api_key, {}, {
		'volume_ids': volume_ids,
		'action': action,
		'args': args
	})
	.then(response => {
		selected_volume_ids.clear();
		library_els.mass_edit.select_all.checked = false;
		fetchLibrary(api_key);
	});
};

function updateLibraryDownloadStatuses(downloads) {
	const next_volume_ids = new Set(
		downloads
			.map(download => Number(download.volume_id))
			.filter(Number.isFinite)
	);
	const changed_volume_ids = new Set([
		...library_download_volume_ids,
		...next_volume_ids
	]);

	changed_volume_ids.forEach(volume_id => {
		const entry = library_entries.get(volume_id);
		if (entry === undefined)
			return;

		// Do not spend DOM work on a volume whose two views are both still lazy.
		// Its status is recomputed from the shared queue when it materialises.
		if (entry.list_entry === null && entry.table_entry === null)
			return;

		entry.setDownloadStatus(getVolumeDownloadStatus(volume_id));
	});
	library_download_volume_ids = next_volume_ids;
};

// code run on load

const lib_options = getLocalStorage('lib_sorting', 'lib_view', 'lib_filter');
library_els.view_options.sort.value = lib_options.lib_sorting;
library_els.view_options.view.value = lib_options.lib_view;
library_els.view_options.filter.value = lib_options.lib_filter;

usingApiKey()
.then(api_key => {
	fetchLibrary(api_key);
	fetchStats(api_key);

	library_els.search.clear.onclick =
		e => clearSearch(api_key);

	library_els.task_buttons.update_all.onclick =
		e => sendAPI('POST', '/system/tasks', api_key, {}, {
			'cmd': 'update_all',
			'allow_skipping': false
		});
	library_els.task_buttons.search_all.onclick =
		e => sendAPI('POST', '/system/tasks', api_key, {}, {'cmd': 'search_all'});

	setupAlphabetRail();

	library_els.view_options.sort.onchange = e => {
		setLocalStorage({'lib_sorting': library_els.view_options.sort.value});
		fetchLibrary(api_key);
	};
	library_els.view_options.view.onchange = e => {
		setLocalStorage({'lib_view': library_els.view_options.view.value});
		// Switching views needs no round trip -- the payload is already here,
		// only the DOM for the newly selected view is missing.
		ensureLibraryViewBuilt(api_key);
	};
	library_els.view_options.filter.onchange = e => {
		setLocalStorage({'lib_filter': library_els.view_options.filter.value});
		fetchLibrary(api_key);
	};

	library_els.mass_edit.button.onclick =
	library_els.mass_edit.cancel.onclick =
		e => {
			const toggle = library_els.mass_edit.toggle;
			if (toggle.hasAttribute('checked')) {
				toggle.removeAttribute('checked');
				// Back to whatever the view selector says, which may never have
				// been built if the session started in mass edit.
				ensureLibraryViewBuilt(api_key);
			} else {
				const select = document.querySelector('select[name="root_folder_id"]');
				if (select.querySelector('option') === null) {
					fetchAPI('/rootfolder', api_key)
					.then(json => {
						json.result.forEach(rf => {
							const entry = document.createElement('option');
							entry.value = rf.id;
							entry.innerText = rf.folder;
							select.appendChild(entry);
						});
						toggle.setAttribute('checked', '');
						// Mass edit is always the table view, whatever the view
						// selector is set to.
						ensureLibraryViewBuilt(api_key);
					});
				} else {
					toggle.setAttribute('checked', '');
					ensureLibraryViewBuilt(api_key);
				};
			}
		};
	library_els.mass_edit.bar.querySelectorAll('.action-divider > button[data-action]').forEach(
		b => b.onclick = e => runAction(api_key, e.target.dataset.action)
	);
	library_els.mass_edit.bar.querySelector('button[data-action="delete"]').onclick =
		e => runAction(
			api_key,
			e.target.dataset.action,
			{
				'delete_folder': document.querySelector(
					'select[name="delete_folder"]'
				).value === "true"
			}
		);
	library_els.mass_edit.bar.querySelector('button[data-action="root_folder"]').onclick =
		e => runAction(
			api_key,
			e.target.dataset.action,
			{
				'root_folder_id': parseInt(document.querySelector(
					'select[name="root_folder_id"]'
				).value)
			}
		);
	library_els.mass_edit.bar.querySelector('button[data-action="monitoring_scheme"]').onclick =
		e => runAction(
			api_key,
			e.target.dataset.action,
			{
				'monitoring_scheme': document.querySelector(
					'select[name="monitoring_scheme"]'
				).value
			}
		);

	document.addEventListener(
		'kapowarr:download-queue-changed',
		e => updateLibraryDownloadStatuses(e.detail || [])
	);

	socket.on(
		'downloaded_status',
		data => {
			const entry = library_entries.get(data.volume_id);
			if (entry === undefined)
				return;
			const new_progress = entry.getProgress();
			new_progress[0] += data.downloaded_issues.length
							- data.not_downloaded_issues.length;
			entry.setProgressBar(new_progress[0], new_progress[1])
		}
	);
	// Socket is init after API key so wait for that like this
	socket.on(
		'mass_editor_status',
		data => library_els.mass_edit.progress.innerText = `${data.current_item}/${data.total_items}`
	);
});
library_els.search.container.action = 'javascript:searchLibrary();';
library_els.mass_edit.select_all.onchange = e => {
	selected_volume_ids.clear();
	if (library_els.mass_edit.select_all.checked)
		library_volumes.forEach(
			volume => selected_volume_ids.add(Number(volume.id))
		);

	library_els.views.table.querySelectorAll('input[type="checkbox"]')
		.forEach(c => c.checked = library_els.mass_edit.select_all.checked);
};
