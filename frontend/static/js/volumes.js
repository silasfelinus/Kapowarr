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
	table_entry.querySelector('.table-volume').innerText =
		`Volume ${volume.volume_number}`;

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
	const {anchor, total, setSpacers, renderRange, itemsPerRow} = config;
	let row_height = 0;
	let per_row = 1;
	let resize_timer = null;
	// Bounded so a geometry that will not settle cannot loop forever.
	let corrections = 0;
	let rendered = null;
	let frame = null;

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

	function paint(start, end) {
		if (rendered && rendered[0] === start && rendered[1] === end)
			return;

		const rows_before = Math.floor(start / per_row);
		const rows_after = Math.max(
			0, Math.ceil(total / per_row) - Math.ceil(end / per_row)
		);
		setSpacers(rows_before * row_height, rows_after * row_height);
		renderRange(start, end);
		rendered = [start, end];
	}

	function update() {
		frame = null;

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

	return {
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
			}
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
