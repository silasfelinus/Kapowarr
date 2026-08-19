const ImportListEls = {
	form: document.querySelector('#import-list-form'),
	id: document.querySelector('#import-list-id'),
	name: document.querySelector('#import-list-name'),
	provider: document.querySelector('#import-list-provider'),
	url: document.querySelector('#import-list-url'),
	root: document.querySelector('#import-list-root'),
	enabled: document.querySelector('#import-list-enabled'),
	auto: document.querySelector('#import-list-auto'),
	monitored: document.querySelector('#import-list-monitored'),
	monitor_new: document.querySelector('#import-list-monitor-new'),
	search: document.querySelector('#import-list-search'),
	save: document.querySelector('#save-import-list'),
	cancel: document.querySelector('#cancel-import-list-edit'),
	form_status: document.querySelector('#import-list-form-status'),
	rows: document.querySelector('#import-list-rows'),
	empty: document.querySelector('#no-import-lists'),
	sync_all: document.querySelector('#sync-all-lists'),
	exclusion_form: document.querySelector('#exclusion-form'),
	exclusion_id: document.querySelector('#exclusion-id'),
	exclusion_note: document.querySelector('#exclusion-note'),
	exclusion_list: document.querySelector('#exclusion-list'),
	exclusion_status: document.querySelector('#exclusion-status')
};

let importListApiKey = null;
let importLists = [];

async function sendImportListAPI(method, path, data = {}) {
	const response = await sendAPI(method, path, importListApiKey, {}, data);
	if (!response.ok) {
		let detail = `Request failed (${response.status})`;
		try {
			const json = await response.json();
			detail = json.error || detail;
		} catch (_) {}
		throw new Error(detail);
	}
	return response.status === 204 ? {} : response.json();
}

function formatSyncTime(timestamp) {
	return timestamp ? new Date(timestamp * 1000).toLocaleString() : 'Never';
}

function resetImportListForm() {
	ImportListEls.form.reset();
	ImportListEls.id.value = '';
	ImportListEls.provider.value = 'remote_cbl';
	ImportListEls.enabled.checked = true;
	ImportListEls.auto.checked = false;
	ImportListEls.monitored.checked = true;
	ImportListEls.monitor_new.checked = true;
	ImportListEls.search.checked = false;
	ImportListEls.save.textContent = 'Add Import List';
	ImportListEls.cancel.classList.add('hidden');
	ImportListEls.form_status.textContent = '';
}

function formPayload() {
	return {
		name: ImportListEls.name.value.trim(),
		provider: 'remote_cbl',
		source_url: ImportListEls.url.value.trim(),
		root_folder_id: Number.parseInt(ImportListEls.root.value, 10),
		enabled: ImportListEls.enabled.checked,
		enable_auto: ImportListEls.auto.checked,
		monitored: ImportListEls.monitored.checked,
		monitor_new_issues: ImportListEls.monitor_new.checked,
		search_on_add: ImportListEls.search.checked
	};
}

function editImportList(definition) {
	ImportListEls.id.value = definition.id;
	ImportListEls.name.value = definition.name;
	ImportListEls.provider.value = definition.provider;
	ImportListEls.url.value = definition.source_url;
	ImportListEls.root.value = definition.root_folder_id;
	ImportListEls.enabled.checked = Boolean(definition.enabled);
	ImportListEls.auto.checked = Boolean(definition.enable_auto);
	ImportListEls.monitored.checked = Boolean(definition.monitored);
	ImportListEls.monitor_new.checked = Boolean(definition.monitor_new_issues);
	ImportListEls.search.checked = Boolean(definition.search_on_add);
	ImportListEls.save.textContent = 'Save Import List';
	ImportListEls.cancel.classList.remove('hidden');
	ImportListEls.form_status.textContent = `Editing ${definition.name}`;
	ImportListEls.form.scrollIntoView({behavior: 'smooth', block: 'start'});
}

function makeActionButton(text, handler, secondary = false) {
	const button = document.createElement('button');
	button.type = 'button';
	button.textContent = text;
	if (secondary) button.classList.add('secondary');
	button.onclick = handler;
	return button;
}

function renderImportLists() {
	ImportListEls.rows.replaceChildren();
	ImportListEls.empty.classList.toggle('hidden', importLists.length !== 0);

	importLists.forEach(definition => {
		const row = document.createElement('tr');

		const nameCell = document.createElement('td');
		const name = document.createElement('div');
		name.className = 'import-list-name';
		name.textContent = definition.name;
		const url = document.createElement('div');
		url.className = 'import-list-url';
		url.textContent = definition.source_url;
		url.title = definition.source_url;
		nameCell.append(name, url);
		row.appendChild(nameCell);

		const status = document.createElement('td');
		const enabled = definition.enabled ? 'Enabled' : 'Disabled';
		const auto = definition.enable_auto ? 'Automatic Add' : 'Preview only';
		status.textContent = `${enabled} · ${auto}`;
		row.appendChild(status);

		const lastSync = document.createElement('td');
		lastSync.textContent = formatSyncTime(definition.last_sync);
		row.appendChild(lastSync);

		const result = document.createElement('td');
		const counts = document.createElement('div');
		counts.textContent = (
			`${definition.last_exact_volume_count || 0} exact · ` +
			`${definition.last_added_count || 0} added · ` +
			`${definition.last_unresolved_count || 0} unresolved`
		);
		result.appendChild(counts);
		if (definition.last_error) {
			const error = document.createElement('div');
			error.className = 'sync-error';
			error.textContent = definition.last_error;
			result.appendChild(error);
		}
		row.appendChild(result);

		const actions = document.createElement('td');
		actions.className = 'import-list-actions';
		actions.append(
			makeActionButton('Sync', async () => {
				try {
					await sendImportListAPI('POST', `/importlists/${definition.id}/sync`);
					ImportListEls.form_status.textContent = `Queued sync for ${definition.name}`;
				} catch (error) {
					ImportListEls.form_status.textContent = error.message;
				}
			}),
			makeActionButton('Edit', () => editImportList(definition), true),
			makeActionButton('Delete', async () => {
				if (!confirm(`Delete Import List “${definition.name}”?`)) return;
				try {
					await sendImportListAPI('DELETE', `/importlists/${definition.id}`);
					await loadImportLists();
				} catch (error) {
					ImportListEls.form_status.textContent = error.message;
				}
			}, true)
		);
		row.appendChild(actions);
		ImportListEls.rows.appendChild(row);
	});
}

async function loadRootFolders() {
	const json = await fetchAPI('/rootfolder', importListApiKey);
	ImportListEls.root.replaceChildren();
	(json.result || []).forEach(rootFolder => {
		const option = document.createElement('option');
		option.value = rootFolder.id;
		option.textContent = rootFolder.folder;
		ImportListEls.root.appendChild(option);
	});
}

async function loadImportLists() {
	const json = await fetchAPI('/importlists', importListApiKey);
	importLists = json.result || [];
	renderImportLists();
}

function renderExclusions(exclusions) {
	ImportListEls.exclusion_list.replaceChildren();
	if (!exclusions.length) {
		const empty = document.createElement('p');
		empty.textContent = 'No excluded volumes.';
		ImportListEls.exclusion_list.appendChild(empty);
		return;
	}

	exclusions.forEach(exclusion => {
		const entry = document.createElement('div');
		entry.className = 'exclusion-entry';
		const detail = document.createElement('p');
		const id = document.createElement('strong');
		id.textContent = `CV ${exclusion.comicvine_volume_id}`;
		detail.appendChild(id);
		if (exclusion.note) {
			const note = document.createElement('small');
			note.textContent = exclusion.note;
			detail.appendChild(note);
		}
		const remove = makeActionButton('Remove', async () => {
			try {
				await sendImportListAPI(
					'DELETE',
					`/importlists/exclusions/${exclusion.comicvine_volume_id}`
				);
				await loadExclusions();
			} catch (error) {
				ImportListEls.exclusion_status.textContent = error.message;
			}
		}, true);
		entry.append(detail, remove);
		ImportListEls.exclusion_list.appendChild(entry);
	});
}

async function loadExclusions() {
	const json = await fetchAPI('/importlists/exclusions', importListApiKey);
	renderExclusions(json.result || []);
}

usingApiKey().then(async apiKey => {
	importListApiKey = apiKey;
	try {
		await Promise.all([loadRootFolders(), loadImportLists(), loadExclusions()]);
	} catch (error) {
		ImportListEls.form_status.textContent = `Unable to load Import Lists: ${error}`;
	}

	ImportListEls.form.onsubmit = async event => {
		event.preventDefault();
		ImportListEls.form_status.textContent = 'Saving…';
		const editingId = ImportListEls.id.value;
		try {
			await sendImportListAPI(
				editingId ? 'PUT' : 'POST',
				editingId ? `/importlists/${editingId}` : '/importlists',
				formPayload()
			);
			resetImportListForm();
			await loadImportLists();
		} catch (error) {
			ImportListEls.form_status.textContent = error.message;
		}
	};

	ImportListEls.cancel.onclick = resetImportListForm;

	ImportListEls.sync_all.onclick = async () => {
		try {
			await sendImportListAPI('POST', '/system/tasks', {cmd: 'import_list_sync'});
			ImportListEls.form_status.textContent = 'Queued Import List Sync';
		} catch (error) {
			ImportListEls.form_status.textContent = error.message;
		}
	};

	ImportListEls.exclusion_form.onsubmit = async event => {
		event.preventDefault();
		try {
			await sendImportListAPI('POST', '/importlists/exclusions', {
				comicvine_volume_id: Number.parseInt(ImportListEls.exclusion_id.value, 10),
				note: ImportListEls.exclusion_note.value.trim()
			});
			ImportListEls.exclusion_form.reset();
			ImportListEls.exclusion_status.textContent = 'Exclusion saved';
			await loadExclusions();
		} catch (error) {
			ImportListEls.exclusion_status.textContent = error.message;
		}
	};
});
