const WantedEls = {
	table: document.querySelector('#wanted-list'),
	empty_message: document.querySelector('#wanted-empty-message'),
	selected_count: document.querySelector('#wanted-selected-count'),
	selectall: document.querySelector('#wanted-selectall-input'),
	search: {
		form: document.querySelector('#wanted-search-form'),
		input: document.querySelector('#wanted-search-input')
	},
	buttons: {
		refresh: document.querySelector('#refresh-button'),
		bulk_search: document.querySelector('#bulk-search-button'),
		manual_import: document.querySelector('#manual-import-button')
	},
	page_turner_container: document.querySelector('#wanted-page-turner'),
	page_turner: {
		previous: document.querySelector('#previous-page'),
		next: document.querySelector('#next-page'),
		number: document.querySelector('#page-number')
	},
	entry: document.querySelector('.pre-build-els .wanted-entry'),
	manual_import: {
		form: document.querySelector('#manual-import-form'),
		volume_input: document.querySelector('#mi-volume-input'),
		issue_input: document.querySelector('#mi-issue-input'),
		filepaths_input: document.querySelector('#mi-filepaths-input'),
		error: document.querySelector('#manual-import-error'),
		results: document.querySelector('#manual-import-results'),
		imported_list: document.querySelector('#manual-import-imported'),
		skipped_list: document.querySelector('#manual-import-skipped'),
		submit: document.querySelector('#manual-import-submit')
	}
};

const WANTED_LIMIT = 100;
var offset = 0;
var total = 0;
var search_timeout = null;

function updatePageTurner() {
	const page = Math.floor(offset / WANTED_LIMIT) + 1;
	const max_page = Math.max(Math.ceil(total / WANTED_LIMIT), 1);
	WantedEls.page_turner.number.innerText = `Page ${page} / ${max_page}`;
	WantedEls.page_turner.previous.disabled = offset <= 0;
	WantedEls.page_turner.next.disabled = offset + WANTED_LIMIT >= total;
};

function updateSelectedState() {
	const checked = [...WantedEls.table.querySelectorAll(
		'input[type="checkbox"]:checked'
	)];
	WantedEls.selected_count.innerText = checked.length
		? `${checked.length} selected`
		: '';
	WantedEls.buttons.bulk_search.disabled = checked.length === 0;

	const all_boxes = [...WantedEls.table.querySelectorAll(
		'input[type="checkbox"]'
	)];
	WantedEls.selectall.checked =
		all_boxes.length > 0 && checked.length === all_boxes.length;
};

function fillList(api_key) {
	const params = {offset: offset, limit: WANTED_LIMIT};
	const query = WantedEls.search.input.value;
	if (query !== '')
		params.query = query;

	return fetchAPI('/wanted', api_key, params)
	.then(json => {
		total = json.result.total;
		updatePageTurner();

		WantedEls.table.innerHTML = '';
		WantedEls.empty_message.classList.toggle(
			'hidden', json.result.items.length > 0
		);

		json.result.items.forEach(obj => {
			const entry = WantedEls.entry.cloneNode(true);
			entry.dataset.issue_id = obj.issue_id;
			entry.dataset.volume_id = obj.volume_id;

			const volume_link = entry.querySelector('.volume-column a');
			volume_link.innerText = obj.volume_year !== null
				? `${obj.volume_title} (${obj.volume_year})`
				: obj.volume_title;
			volume_link.href = `${url_base}/volumes/${obj.volume_id}`;

			entry.querySelector('.issue-column').innerText =
				`#${obj.issue_number}`;
			entry.querySelector('.title-column').innerText =
				obj.issue_title || '';
			entry.querySelector('.publisher-column').innerText =
				obj.publisher || '';
			entry.querySelector('.date-column').innerText =
				obj.issue_date || '';

			entry.querySelector('.search-entry-button').onclick =
				e => searchIssue(api_key, obj.volume_id, obj.issue_id);
			entry.querySelector('.import-entry-button').onclick =
				e => openManualImport(obj.volume_id, obj.issue_id);
			entry.querySelector('input[type="checkbox"]').onchange =
				e => updateSelectedState();

			WantedEls.table.appendChild(entry);
		});

		updateSelectedState();
	});
};

function searchIssue(api_key, target_volume_id, issue_id) {
	// Reuses the exact same task the volume page's per-issue "Auto Search"
	// button triggers -- the hardened acquisition path, just queued from
	// the global Wanted list instead of a single volume's page.
	return sendAPI('POST', '/system/tasks', api_key, {}, {
		cmd: 'auto_search_issue',
		volume_id: target_volume_id,
		issue_id: issue_id
	});
};

function searchSelected(api_key) {
	const rows = [...WantedEls.table.querySelectorAll(
		'.wanted-entry:has(input[type="checkbox"]:checked)'
	)];
	if (!rows.length) return;

	Promise.all(rows.map(
		row => searchIssue(
			api_key,
			parseInt(row.dataset.volume_id),
			parseInt(row.dataset.issue_id)
		)
	));
};

function openManualImport(target_volume_id=null, issue_id=null) {
	WantedEls.manual_import.form.reset();
	hide([WantedEls.manual_import.error, WantedEls.manual_import.results]);
	WantedEls.manual_import.volume_input.value = target_volume_id || '';
	WantedEls.manual_import.issue_input.value = issue_id || '';
	showWindow('manual-import-window');
};

function submitManualImport(api_key) {
	const volume_id = parseInt(WantedEls.manual_import.volume_input.value);
	const raw_issue_id = WantedEls.manual_import.issue_input.value;
	const filepaths = WantedEls.manual_import.filepaths_input.value
		.split('\n')
		.map(f => f.trim())
		.filter(f => f.length > 0);

	hide([WantedEls.manual_import.error]);

	if (!volume_id || !filepaths.length) {
		WantedEls.manual_import.error.innerText =
			'*A volume ID and at least one filepath are required';
		hide([], [WantedEls.manual_import.error]);
		return;
	};

	const body = {volume_id: volume_id, filepaths: filepaths};
	if (raw_issue_id) body.issue_id = parseInt(raw_issue_id);

	WantedEls.manual_import.submit.disabled = true;

	sendAPI('POST', '/manualimport', api_key, {}, body)
	.then(response => response.json())
	.then(json => {
		WantedEls.manual_import.imported_list.innerHTML = '';
		WantedEls.manual_import.skipped_list.innerHTML = '';

		json.result.imported.forEach(f => {
			const li = document.createElement('li');
			li.innerText = f.moved_to
				? `${f.filepath} -> ${f.moved_to}`
				: f.filepath;
			WantedEls.manual_import.imported_list.appendChild(li);
		});
		json.result.skipped.forEach(f => {
			const li = document.createElement('li');
			li.innerText = `${f.filepath} - ${f.reason}`;
			WantedEls.manual_import.skipped_list.appendChild(li);
		});

		hide([], [WantedEls.manual_import.results]);
		fillList(api_key);
	})
	.catch(e => {
		e.json().then(json => {
			WantedEls.manual_import.error.innerText =
				'*' + (json.error || 'Failed to manually import the file(s)');
			hide([], [WantedEls.manual_import.error]);
		});
	})
	.finally(() => {
		WantedEls.manual_import.submit.disabled = false;
	});
};

function goToPreviousPage(api_key) {
	if (offset <= 0) return;
	offset = Math.max(offset - WANTED_LIMIT, 0);
	fillList(api_key);
};

function goToNextPage(api_key) {
	if (offset + WANTED_LIMIT >= total) return;
	offset += WANTED_LIMIT;
	fillList(api_key);
};

function search(api_key) {
	offset = 0;
	fillList(api_key);
};

// code run on load
usingApiKey()
.then(api_key => {
	fillList(api_key);

	WantedEls.buttons.refresh.onclick = e => fillList(api_key);
	WantedEls.buttons.bulk_search.onclick = e => searchSelected(api_key);
	WantedEls.buttons.manual_import.onclick = e => openManualImport();

	WantedEls.selectall.onchange = e => {
		WantedEls.table.querySelectorAll('input[type="checkbox"]').forEach(
			c => c.checked = WantedEls.selectall.checked
		);
		updateSelectedState();
	};

	WantedEls.page_turner.previous.onclick = e => goToPreviousPage(api_key);
	WantedEls.page_turner.next.onclick = e => goToNextPage(api_key);

	WantedEls.search.form.action = 'javascript:search(api_key);';
	WantedEls.search.input.oninput = e => {
		clearTimeout(search_timeout);
		search_timeout = setTimeout(() => search(api_key), 400);
	};

	WantedEls.manual_import.form.action = 'javascript:submitManualImport(api_key);';

	if (typeof socket !== 'undefined') {
		socket.on('downloaded_status', data => fillList(api_key));
	};
});
