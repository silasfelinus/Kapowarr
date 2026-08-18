const ReadingListEls = {
	picker: document.querySelector('#reading-list-buttons'),
	empty: document.querySelector('#reading-list-empty'),
	placeholder: document.querySelector('#reading-list-placeholder'),
	content: document.querySelector('#reading-list-content'),
	title: document.querySelector('#reading-list-title'),
	summary: document.querySelector('#reading-list-summary'),
	message: document.querySelector('#reading-list-action-message'),
	entries: document.querySelector('#reading-list-entries'),
	entry: document.querySelector('.pre-build-els .reading-entry'),
	file: document.querySelector('#cbl-file-input'),
	buttons: {
		import: document.querySelector('#import-cbl-button'),
		search: document.querySelector('#search-missing-button'),
		export: document.querySelector('#export-cbl-button'),
		delete: document.querySelector('#delete-list-button')
	}
};

let selectedReadingList = null;

function setReadingListButtonsEnabled(enabled) {
	ReadingListEls.buttons.search.disabled = !enabled;
	ReadingListEls.buttons.export.disabled = !enabled;
	ReadingListEls.buttons.delete.disabled = !enabled;
};

function showReadingListMessage(message) {
	ReadingListEls.message.innerText = message;
	ReadingListEls.message.classList.toggle('hidden', !message);
};

function renderReadingList(list) {
	selectedReadingList = list.id;
	ReadingListEls.placeholder.classList.add('hidden');
	ReadingListEls.content.classList.remove('hidden');
	ReadingListEls.title.innerText = list.title;
	ReadingListEls.summary.innerText =
		`${list.entry_count} entries · ${list.owned_count} owned · `
		+ `${list.missing_count} missing · ${list.unresolved_count} unresolved`;
	showReadingListMessage('');

	ReadingListEls.entries.innerHTML = '';
	list.entries.forEach(obj => {
		const entry = ReadingListEls.entry.cloneNode(true);
		entry.querySelector('.position-column').innerText = obj.position;

		const comicLink = entry.querySelector('.comic-column a');
		comicLink.innerText = `${obj.series} #${obj.issue_number}`
			+ (obj.volume_year ? ` (${obj.volume_year})` : '');
		if (obj.volume_id !== null) {
			comicLink.href = `${url_base}/volumes/${obj.volume_id}`;
		} else {
			comicLink.removeAttribute('href');
		};

		const status = entry.querySelector('.status-column');
		status.innerText = obj.status;
		status.classList.add(`status-${obj.status}`);
		ReadingListEls.entries.appendChild(entry);
	});

	ReadingListEls.picker.querySelectorAll('.reading-list-select').forEach(button => {
		button.classList.toggle('selected', Number(button.dataset.id) === list.id);
	});
	setReadingListButtonsEnabled(true);
};

function loadReadingList(api_key, id) {
	return fetchAPI(`/readinglists/${id}`, api_key)
	.then(json => renderReadingList(json.result));
};

function loadReadingLists(api_key, preferredId=null) {
	return fetchAPI('/readinglists', api_key)
	.then(json => {
		ReadingListEls.picker.innerHTML = '';
		const lists = json.result;
		ReadingListEls.empty.classList.toggle('hidden', lists.length > 0);

		lists.forEach(list => {
			const button = document.createElement('button');
			button.type = 'button';
			button.className = 'reading-list-select';
			button.dataset.id = list.id;
			const title = document.createElement('strong');
			title.innerText = list.title;
			const counts = document.createElement('small');
			counts.innerText =
				`${list.owned_count}/${list.entry_count} owned`
				+ (list.unresolved_count ? ` · ${list.unresolved_count} unresolved` : '');
			button.append(title, counts);
			button.onclick = () => loadReadingList(api_key, list.id);
			ReadingListEls.picker.appendChild(button);
		});

		if (!lists.length) {
			selectedReadingList = null;
			ReadingListEls.content.classList.add('hidden');
			ReadingListEls.placeholder.classList.remove('hidden');
			setReadingListButtonsEnabled(false);
			return;
		};

		const selected = lists.find(list => list.id === preferredId)
			|| lists.find(list => list.id === selectedReadingList)
			|| lists[0];
		return loadReadingList(api_key, selected.id);
	});
};

function importCBL(api_key, file) {
	const form = new FormData();
	form.append('file', file);
	ReadingListEls.buttons.import.disabled = true;

	return fetch(
		`${url_base}/api/readinglists/import?api_key=${encodeURIComponent(api_key)}`,
		{method: 'POST', body: form}
	)
	.then(async response => {
		if (!response.ok) {
			const json = await response.json().catch(() => ({}));
			throw new Error(json.error || 'CBL import failed');
		};
		return response.json();
	})
	.then(json => loadReadingLists(api_key, json.result.id))
	.catch(error => showReadingListMessage(error.message))
	.finally(() => {
		ReadingListEls.buttons.import.disabled = false;
		ReadingListEls.file.value = '';
	});
};

function searchMissing(api_key) {
	if (selectedReadingList === null) return;
	ReadingListEls.buttons.search.disabled = true;
	showReadingListMessage('Queueing searches for missing issues…');

	sendAPI(
		'POST',
		`/readinglists/${selectedReadingList}/search-missing`,
		api_key
	)
	.then(response => response.json())
	.then(json => {
		showReadingListMessage(
			json.result.queued
				? `Queued ${json.result.queued} missing issue search(es).`
				: 'No new missing-issue searches needed.'
		);
	})
	.catch(() => showReadingListMessage('Could not queue missing-issue searches.'))
	.finally(() => ReadingListEls.buttons.search.disabled = false);
};

function exportCBL(api_key) {
	if (selectedReadingList === null) return;
	window.location.href =
		`${url_base}/api/readinglists/${selectedReadingList}/export`
		+ `?api_key=${encodeURIComponent(api_key)}`;
};

function deleteReadingList(api_key) {
	if (selectedReadingList === null) return;
	if (!confirm('Delete this reading list? Your comic files will not be touched.')) return;

	sendAPI('DELETE', `/readinglists/${selectedReadingList}`, api_key)
	.then(() => {
		selectedReadingList = null;
		return loadReadingLists(api_key);
	});
};

usingApiKey()
.then(api_key => {
	setReadingListButtonsEnabled(false);
	loadReadingLists(api_key);

	ReadingListEls.buttons.import.onclick = () => ReadingListEls.file.click();
	ReadingListEls.file.onchange = () => {
		if (ReadingListEls.file.files.length) {
			importCBL(api_key, ReadingListEls.file.files[0]);
		};
	};
	ReadingListEls.buttons.search.onclick = () => searchMissing(api_key);
	ReadingListEls.buttons.export.onclick = () => exportCBL(api_key);
	ReadingListEls.buttons.delete.onclick = () => deleteReadingList(api_key);
});
