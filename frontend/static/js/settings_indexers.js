const indexers = {};
let indexerPriorities = {};

function indexerKey(protocol, id) {
	return `${protocol}:${id}`;
};

function endpointFor(protocol) {
	return protocol === 'torznab' ? '/torznab-indexers' : '/indexers';
};

function priorityFor(protocol, id) {
	return indexerPriorities[indexerKey(protocol, id)] || 50;
};

function savePriorityMap(api_key) {
	return sendAPI(
		'PUT', '/settings/acquisition', api_key, {},
		{indexer_priorities: indexerPriorities}
	);
};

function decorateIndexer(indexer, protocol) {
	return {
		...indexer,
		protocol: protocol,
		endpoint: endpointFor(protocol),
		priority: priorityFor(protocol, indexer.id)
	};
};

function toggleIndexer(api_key, key, button) {
	const indexer = indexers[key],
		data = {
			title: indexer.title,
			base_url: indexer.base_url,
			api_key: indexer.api_key,
			enabled: !indexer.enabled
		};

	if (indexer.protocol === 'torznab')
		data.categories = indexer.categories;

	button.disabled = true;
	sendAPI('PUT', `${indexer.endpoint}/${indexer.id}`, api_key, {}, data)
	.then(response => response.json())
	.then(json => {
		indexers[key] = decorateIndexer(json.result, indexer.protocol);
		loadIndexers(api_key);
	})
	.catch(async response => {
		button.disabled = false;
		let message = 'Failed to change indexer status';
		try {
			const json = await response.json();
			message = json.error || message;
		} catch (e) {};
		button.title = message;
	});
};

function loadIndexers(api_key) {
	Promise.all([
		fetchAPI('/indexers', api_key),
		fetchAPI('/torznab-indexers', api_key),
		fetchAPI('/settings/acquisition', api_key)
	])
	.then(([newznab, torznab, acquisition]) => {
		const table = document.querySelector('#indexer-list');
		document.querySelectorAll('#indexer-list > :not(:first-child)')
			.forEach(el => el.remove());

		Object.keys(indexers).forEach(key => delete indexers[key]);
		indexerPriorities = acquisition.result.indexer_priorities || {};
		const all = [
			...newznab.result.map(i => decorateIndexer(i, 'newznab')),
			...torznab.result.map(i => decorateIndexer(i, 'torznab'))
		];
		all.sort((a, b) =>
			a.priority - b.priority || a.title.localeCompare(b.title)
		);

		all.forEach(indexer => {
			const key = indexerKey(indexer.protocol, indexer.id);
			indexers[key] = indexer;

			const entry = document.createElement('div');
			entry.classList.add('indexer-entry');
			entry.dataset.enabled = indexer.enabled;

			const edit_button = document.createElement('button');
			edit_button.classList.add('indexer-edit');
			edit_button.onclick = e => loadEditIndexer(api_key, key);
			const protocol = indexer.protocol === 'torznab' ? 'Torznab' : 'Newznab';
			edit_button.innerText =
				`${indexer.title} · ${protocol} · P${indexer.priority}`;
			edit_button.title = `Edit ${indexer.title}`;

			const toggle_button = document.createElement('button');
			toggle_button.classList.add('indexer-toggle');
			toggle_button.innerText = indexer.enabled ? 'Enabled' : 'Disabled';
			toggle_button.title = indexer.enabled
				? `Disable ${indexer.title}`
				: `Enable ${indexer.title}`;
			toggle_button.setAttribute('aria-pressed', indexer.enabled);
			toggle_button.onclick =
				e => toggleIndexer(api_key, key, toggle_button);

			entry.append(edit_button, toggle_button);
			table.appendChild(entry);
		});
	});
};

function toggleTorznabFields(prefix, protocol) {
	document.querySelectorAll(`.${prefix}-torznab-option`).forEach(row => {
		row.classList.toggle('hidden', protocol !== 'torznab');
	});
};

function showAddIndexer() {
	hide([document.querySelector('#add-error')]);
	document.querySelector('#test-indexer-add').classList.remove(
		'show-success', 'show-fail'
	);
	document.querySelector('#add-protocol-input').value = 'newznab';
	document.querySelector('#add-title-input').value = '';
	document.querySelector('#add-base-url-input').value = '';
	document.querySelector('#add-api-key-input').value = '';
	document.querySelector('#add-categories-input').value = '7030';
	document.querySelector('#add-priority-input').value = 50;
	document.querySelector('#add-enabled-input').checked = true;
	toggleTorznabFields('add', 'newznab');

	showWindow('add-indexer-window');
};

async function testAddIndexer(api_key) {
	const error = document.querySelector('#add-error');
	hide([error]);
	const test_button = document.querySelector('#test-indexer-add');
	test_button.classList.remove('show-success', 'show-fail');
	const protocol = document.querySelector('#add-protocol-input').value;

	const data = {
		base_url: document.querySelector('#add-base-url-input').value,
		api_key: document.querySelector('#add-api-key-input').value
	};
	return await sendAPI(
		'POST', `${endpointFor(protocol)}/test`, api_key, {}, data
	)
	.then(response => response.json())
	.then(json => {
		if (json.result.success)
			test_button.classList.add('show-success');
		else {
			test_button.classList.add('show-fail');
			error.innerText = 'Could not reach the given indexer';
			hide([], [error]);
		};
		return json.result.success;
	});
};

function saveAddIndexer() {
	usingApiKey().then(api_key => {
		const protocol = document.querySelector('#add-protocol-input').value;
		const priority = parseInt(document.querySelector('#add-priority-input').value);
		const data = {
			title: document.querySelector('#add-title-input').value,
			base_url: document.querySelector('#add-base-url-input').value,
			api_key: document.querySelector('#add-api-key-input').value,
			enabled: document.querySelector('#add-enabled-input').checked
		};
		if (protocol === 'torznab')
			data.categories = document.querySelector('#add-categories-input').value;

		sendAPI('POST', endpointFor(protocol), api_key, {}, data)
		.then(response => response.json())
		.then(json => {
			indexerPriorities[indexerKey(protocol, json.result.id)] = priority;
			return savePriorityMap(api_key);
		})
		.then(() => {
			loadIndexers(api_key);
			closeWindow();
		})
		.catch(e => {
			e.json().then(json => {
				const error = document.querySelector('#add-error');
				error.innerText = '*' + (json.error || 'Failed to add indexer');
				hide([], [error]);
			});
		});
	});
};

function loadEditIndexer(api_key, key) {
	hide([document.querySelector('#edit-error')]);
	document.querySelector('#test-indexer-edit').classList.remove(
		'show-success', 'show-fail'
	);
	const cached = indexers[key];

	fetchAPI(`${cached.endpoint}/${cached.id}`, api_key)
	.then(json => {
		const data = decorateIndexer(json.result, cached.protocol);
		indexers[key] = data;

		const window = document.querySelector('#edit-indexer-window');
		window.dataset.id = data.id;
		window.dataset.protocol = data.protocol;
		document.querySelector('#edit-protocol-input').value = data.protocol;
		document.querySelector('#edit-title-input').value = data.title;
		document.querySelector('#edit-base-url-input').value = data.base_url;
		document.querySelector('#edit-api-key-input').value = data.api_key;
		document.querySelector('#edit-categories-input').value = data.categories || '7030';
		document.querySelector('#edit-priority-input').value = data.priority;
		document.querySelector('#edit-enabled-input').checked = data.enabled;
		toggleTorznabFields('edit', data.protocol);

		showWindow('edit-indexer-window');
	});
};

async function testEditIndexer(api_key) {
	const error = document.querySelector('#edit-error');
	hide([error]);
	const test_button = document.querySelector('#test-indexer-edit');
	test_button.classList.remove('show-success', 'show-fail');
	const protocol = document.querySelector('#edit-indexer-window').dataset.protocol;

	const data = {
		base_url: document.querySelector('#edit-base-url-input').value,
		api_key: document.querySelector('#edit-api-key-input').value
	};
	return await sendAPI(
		'POST', `${endpointFor(protocol)}/test`, api_key, {}, data
	)
	.then(response => response.json())
	.then(json => {
		if (json.result.success)
			test_button.classList.add('show-success');
		else {
			test_button.classList.add('show-fail');
			error.innerText = 'Could not reach the given indexer';
			hide([], [error]);
		};
		return json.result.success;
	});
};

function saveEditIndexer() {
	usingApiKey().then(api_key => {
		const window = document.querySelector('#edit-indexer-window');
		const id = window.dataset.id;
		const protocol = window.dataset.protocol;
		const priority = parseInt(document.querySelector('#edit-priority-input').value);
		const data = {
			title: document.querySelector('#edit-title-input').value,
			base_url: document.querySelector('#edit-base-url-input').value,
			api_key: document.querySelector('#edit-api-key-input').value,
			enabled: document.querySelector('#edit-enabled-input').checked
		};
		if (protocol === 'torznab')
			data.categories = document.querySelector('#edit-categories-input').value;

		sendAPI('PUT', `${endpointFor(protocol)}/${id}`, api_key, {}, data)
		.then(() => {
			indexerPriorities[indexerKey(protocol, id)] = priority;
			return savePriorityMap(api_key);
		})
		.then(() => {
			loadIndexers(api_key);
			closeWindow();
		})
		.catch(e => {
			e.json().then(json => {
				const error = document.querySelector('#edit-error');
				error.innerText = '*' + (json.error || 'Failed to save indexer');
				hide([], [error]);
			});
		});
	});
};

function deleteIndexer(api_key) {
	const window = document.querySelector('#edit-indexer-window');
	const id = window.dataset.id;
	const protocol = window.dataset.protocol;
	sendAPI('DELETE', `${endpointFor(protocol)}/${id}`, api_key)
	.then(() => {
		delete indexers[indexerKey(protocol, id)];
		delete indexerPriorities[indexerKey(protocol, id)];
		return savePriorityMap(api_key);
	})
	.then(() => {
		loadIndexers(api_key);
		closeWindow();
	});
};

usingApiKey()
.then(api_key => {
	loadIndexers(api_key);
	document.querySelector('#add-indexer').onclick = e => showAddIndexer();
	document.querySelector('#test-indexer-add').onclick = e => testAddIndexer(api_key);
	document.querySelector('#test-indexer-edit').onclick = e => testEditIndexer(api_key);
	document.querySelector('#delete-indexer-edit').onclick = e => deleteIndexer(api_key);
	document.querySelector('#add-protocol-input').onchange = e =>
		toggleTorznabFields('add', e.target.value);
});

document.querySelector('#add-indexer-form').action = 'javascript:saveAddIndexer()';
document.querySelector('#edit-indexer-form').action = 'javascript:saveEditIndexer()';