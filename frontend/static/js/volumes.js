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

const LIBRARY_RENDER_BATCH_SIZE = 50;
let library_render_generation = 0;

// Volume ID -> LibraryEntry, covering every volume in the current listing.
// A keyed registry rather than a `.vol-<id>` querySelector per update: progress
// and download-status events arrive one volume at a time, and looking each one
// up by scanning the whole library made a busy download queue cost O(library)
// per event.
const library_entries = new Map();

// The most recent `/volumes` payload, kept so that switching views can build the
// other view without going back to the server.
let library_volumes = [];

// Only the visible view is built. A library of any size is otherwise paying
// twice: the poster grid allocates an <img> per volume, and every table row
// parses a full inline SVG through `setIcon`, so building the hidden view was
// as expensive as building the one being looked at.
const library_built_views = {list: false, table: false};

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
			if (this.monitored)
				setIcon(monitored_button, icons.monitored, 'Monitored');
			else
				setIcon(monitored_button, icons.unmonitored, 'Unmonitored');
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
	list_entry.classList.add(`vol-${volume.id}`);
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
	table_entry.classList.add(`vol-${volume.id}`);
	table_entry.dataset.id = volume.id;

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

// Build `view` for `volumes[offset:end]` and paint the results in one insert.
function renderLibraryBatch(view, volumes, api_key, offset, end) {
	const fragment = document.createDocumentFragment();

	for (let i = offset; i < end; i++) {
		const volume = volumes[i];

		let entry = library_entries.get(volume.id);
		if (entry === undefined) {
			entry = new LibraryEntry(volume.id, api_key);
			entry.monitored = Boolean(volume.monitored);
			entry.setProgressBar(
				volume.issues_downloaded_monitored,
				volume.issue_count_monitored
			);
			entry.download_status = getVolumeDownloadStatus(volume.id);
			library_entries.set(volume.id, entry);
		};

		view_builders[view](entry, volume, api_key, fragment);

		// Applied after the element is attached to the entry so that the freshly
		// built view picks up the state the entry is already carrying.
		entry.renderMonitored();
		entry.renderProgressBar();
		entry.renderDownloadStatus();
	};

	if (view === 'list')
		library_els.views.list.insertBefore(
			fragment,
			library_els.views.list.querySelector('.space-taker')
		);
	else
		library_els.views.table.appendChild(fragment);
};

function clearLibraryView(view) {
	if (view === 'list')
		library_els.views.list.querySelectorAll('.list-entry').forEach(
			e => e.remove()
		);
	else
		library_els.views.table.innerHTML = '';

	library_built_views[view] = false;
	library_entries.forEach(entry => {
		if (view === 'list')
			entry.list_entry = null;
		else
			entry.table_entry = null;
	});
};

// Build `view` in idle-time batches. `on_first_batch` fires once the first batch
// is on screen, so the loading screen can be swapped out before the rest of a
// large library has finished rendering.
function buildLibraryView(view, api_key, generation, on_first_batch=null) {
	clearLibraryView(view);
	library_built_views[view] = true;

	const volumes = library_volumes;
	let offset = 0;
	let first_batch = true;

	function renderBatch() {
		if (generation !== library_render_generation)
			return;

		const end = Math.min(offset + LIBRARY_RENDER_BATCH_SIZE, volumes.length);
		renderLibraryBatch(view, volumes, api_key, offset, end);
		offset = end;

		if (first_batch) {
			first_batch = false;
			if (on_first_batch !== null)
				on_first_batch();
		};

		if (offset < volumes.length) {
			scheduleLibraryRender(renderBatch);
		} else {
			library_els.mass_edit.button.disabled = false;
		};
	};

	renderBatch();
};

// Make sure the view the user is about to look at exists, building it on the
// spot if this is the first time it has been asked for.
function ensureLibraryViewBuilt(api_key) {
	const view = activeLibraryView();
	if (library_built_views[view] || library_volumes.length === 0)
		return;

	buildLibraryView(view, api_key, library_render_generation);
};

function populateLibrary(volumes, api_key, generation, on_first_batch) {
	library_volumes = volumes;
	library_entries.clear();
	clearLibraryView('list');
	clearLibraryView('table');

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

		if (json.result.length === 0) {
			library_volumes = [];
			library_entries.clear();
			clearLibraryView('list');
			clearLibraryView('table');
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

	const volume_ids = [...library_els.views.table.querySelectorAll(
		'input[type="checkbox"]:checked'
	)].map(v => parseInt(v.parentNode.parentNode.dataset.id))

	sendAPI('POST', '/masseditor', api_key, {}, {
		'volume_ids': volume_ids,
		'action': action,
		'args': args
	})
	.then(response => {
		library_els.mass_edit.select_all.checked = false;
		fetchLibrary(api_key);
	});
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
		() => library_entries.forEach(
			entry => entry.setDownloadStatus(getVolumeDownloadStatus(entry.id))
		)
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
library_els.mass_edit.select_all.onchange =
	e => library_els.views.table.querySelectorAll('input[type="checkbox"]')
			.forEach(c => c.checked = library_els.mass_edit.select_all.checked);
