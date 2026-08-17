const indexers = {};

function loadIndexers(api_key) {
	fetchAPI('/indexers', api_key)
	.then(json => {
		const table = document.querySelector('#indexer-list');

		document.querySelectorAll('#indexer-list > :not(:first-child)')
			.forEach(el => el.remove());

		json.result.forEach(indexer => {
			indexers[indexer.id] = indexer;

			const entry = document.createElement('button');
			entry.onclick = e => loadEditIndexer(api_key, indexer.id);
			entry.innerText = indexer.title + (indexer.enabled ? '' : ' (disabled)');
			table.appendChild(entry);
		});
	});
};

function showAddIndexer() {
	hide([document.querySelector('#add-error')]);
	document.querySelector('#test-indexer-add').classList.remove(
		'show-success', 'show-fail'
	);
	document.querySelector('#add-title-input').value = '';
	document.querySelector('#add-base-url-input').value = '';
	document.querySelector('#add-api-key-input').value = '';
	document.querySelector('#add-enabled-input').checked = true;

	showWindow('add-indexer-window');
};

async function testAddIndexer(api_key) {
	const error = document.querySelector('#add-error');
	hide([error]);
	const test_button = document.querySelector('#test-indexer-add');
	test_button.classList.remove('show-success', 'show-fail');

	const data = {
		base_url: document.querySelector('#add-base-url-input').value,
		api_key: document.querySelector('#add-api-key-input').value
	};
	return await sendAPI('POST', '/indexers/test', api_key, {}, data)
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
		const data = {
			title: document.querySelector('#add-title-input').value,
			base_url: document.querySelector('#add-base-url-input').value,
			api_key: document.querySelector('#add-api-key-input').value,
			enabled: document.querySelector('#add-enabled-input').checked
		};
		sendAPI('POST', '/indexers', api_key, {}, data)
		.then(response => {
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

function loadEditIndexer(api_key, id) {
	hide([document.querySelector('#edit-error')]);
	document.querySelector('#test-indexer-edit').classList.remove(
		'show-success', 'show-fail'
	);

	fetchAPI(`/indexers/${id}`, api_key)
	.then(json => {
		const data = json.result;
		indexers[id] = data;

		document.querySelector('#edit-indexer-window').dataset.id = id;
		document.querySelector('#edit-title-input').value = data.title;
		document.querySelector('#edit-base-url-input').value = data.base_url;
		document.querySelector('#edit-api-key-input').value = data.api_key;
		document.querySelector('#edit-enabled-input').checked = data.enabled;

		showWindow('edit-indexer-window');
	});
};

async function testEditIndexer(api_key) {
	const error = document.querySelector('#edit-error');
	hide([error]);
	const test_button = document.querySelector('#test-indexer-edit');
	test_button.classList.remove('show-success', 'show-fail');

	const data = {
		base_url: document.querySelector('#edit-base-url-input').value,
		api_key: document.querySelector('#edit-api-key-input').value
	};
	return await sendAPI('POST', '/indexers/test', api_key, {}, data)
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
		const id = document.querySelector('#edit-indexer-window').dataset.id;
		const data = {
			title: document.querySelector('#edit-title-input').value,
			base_url: document.querySelector('#edit-base-url-input').value,
			api_key: document.querySelector('#edit-api-key-input').value,
			enabled: document.querySelector('#edit-enabled-input').checked
		};
		sendAPI('PUT', `/indexers/${id}`, api_key, {}, data)
		.then(response => {
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
	const id = document.querySelector('#edit-indexer-window').dataset.id;
	sendAPI('DELETE', `/indexers/${id}`, api_key)
	.then(response => {
		delete indexers[id];
		loadIndexers(api_key);
		closeWindow();
	});
};

// code run on load

usingApiKey()
.then(api_key => {
	loadIndexers(api_key);
	document.querySelector('#add-indexer').onclick = e => showAddIndexer();
	document.querySelector('#test-indexer-add').onclick = e => testAddIndexer(api_key);
	document.querySelector('#test-indexer-edit').onclick = e => testEditIndexer(api_key);
	document.querySelector('#delete-indexer-edit').onclick = e => deleteIndexer(api_key);

});

document.querySelector('#add-indexer-form').action = 'javascript:saveAddIndexer()';
document.querySelector('#edit-indexer-form').action = 'javascript:saveEditIndexer()';
