const notificationServices = {};
let notificationOptions = null;

function eventLabel(event) {
	return event
		.split('_')
		.map(w => w.charAt(0).toUpperCase() + w.slice(1))
		.join(' ');
};

function buildEventCheckboxes(container_id, checked_events = []) {
	const container = document.querySelector(`#${container_id}`);
	container.innerHTML = '';
	notificationOptions.events.forEach(event => {
		const label = document.createElement('label');
		label.classList.add('event-checkbox');
		const checkbox = document.createElement('input');
		checkbox.type = 'checkbox';
		checkbox.value = event;
		checkbox.checked = checked_events.includes(event);
		label.appendChild(checkbox);
		label.append(' ' + eventLabel(event));
		container.appendChild(label);
	});
};

function getCheckedEvents(container_id) {
	return [...document.querySelectorAll(`#${container_id} input:checked`)]
		.map(el => el.value);
};

function loadNotificationOptions(api_key) {
	return fetchAPI('/notifications/options', api_key)
	.then(json => {
		notificationOptions = json.result;

		const type_select = document.querySelector('#add-type-input');
		type_select.innerHTML = '';
		notificationOptions.service_types.forEach(t => {
			const option = document.createElement('option');
			option.value = t;
			option.innerText = t.charAt(0).toUpperCase() + t.slice(1);
			type_select.appendChild(option);
		});
	});
};

function loadNotificationServices(api_key) {
	fetchAPI('/notifications', api_key)
	.then(json => {
		const table = document.querySelector('#notification-list');

		document.querySelectorAll('#notification-list > :not(:first-child)')
			.forEach(el => el.remove());

		json.result.forEach(service => {
			notificationServices[service.id] = service;

			const entry = document.createElement('button');
			entry.onclick = e => loadEditNotification(api_key, service.id);
			entry.innerText = service.title + (service.enabled ? '' : ' (disabled)');
			table.appendChild(entry);
		});
	});
};

function showAddNotification(api_key) {
	hide([document.querySelector('#add-error')]);
	document.querySelector('#test-notification-add').classList.remove(
		'show-success', 'show-fail'
	);
	document.querySelector('#add-title-input').value = '';
	document.querySelector('#add-url-input').value = '';
	document.querySelector('#add-enabled-input').checked = true;

	loadNotificationOptions(api_key)
	.then(() => {
		buildEventCheckboxes('add-events-list');
		showWindow('add-notification-window');
	});
};

async function testAddNotification(api_key) {
	const error = document.querySelector('#add-error');
	hide([error]);
	const test_button = document.querySelector('#test-notification-add');
	test_button.classList.remove('show-success', 'show-fail');

	const data = {
		service_type: document.querySelector('#add-type-input').value,
		url: document.querySelector('#add-url-input').value
	};
	return await sendAPI('POST', '/notifications/test', api_key, {}, data)
	.then(response => response.json())
	.then(json => {
		if (json.result.success)
			test_button.classList.add('show-success');
		else {
			test_button.classList.add('show-fail');
			error.innerText = 'Could not reach the given URL';
			hide([], [error]);
		};
		return json.result.success;
	});
};

function saveAddNotification() {
	usingApiKey().then(api_key => {
		const data = {
			service_type: document.querySelector('#add-type-input').value,
			title: document.querySelector('#add-title-input').value,
			url: document.querySelector('#add-url-input').value,
			events: getCheckedEvents('add-events-list'),
			enabled: document.querySelector('#add-enabled-input').checked
		};
		sendAPI('POST', '/notifications', api_key, {}, data)
		.then(response => {
			loadNotificationServices(api_key);
			closeWindow();
		})
		.catch(e => {
			e.json().then(json => {
				const error = document.querySelector('#add-error');
				error.innerText = '*' + (json.error || 'Failed to add notification service');
				hide([], [error]);
			});
		});
	});
};

function loadEditNotification(api_key, id) {
	hide([document.querySelector('#edit-error')]);
	document.querySelector('#test-notification-edit').classList.remove(
		'show-success', 'show-fail'
	);

	loadNotificationOptions(api_key)
	.then(() => fetchAPI(`/notifications/${id}`, api_key))
	.then(service => {
		const data = service.result;
		notificationServices[id] = data;

		document.querySelector('#edit-notification-window').dataset.id = id;
		document.querySelector('#edit-title-input').value = data.title;
		document.querySelector('#edit-type-display').innerText =
			data.service_type.charAt(0).toUpperCase() + data.service_type.slice(1);
		document.querySelector('#edit-url-input').value = data.url;
		document.querySelector('#edit-enabled-input').checked = data.enabled;
		buildEventCheckboxes('edit-events-list', data.events);

		showWindow('edit-notification-window');
	});
};

async function testEditNotification(api_key) {
	const error = document.querySelector('#edit-error');
	hide([error]);
	const test_button = document.querySelector('#test-notification-edit');
	test_button.classList.remove('show-success', 'show-fail');
	const id = document.querySelector('#edit-notification-window').dataset.id;

	const data = {
		service_type: notificationServices[id].service_type,
		url: document.querySelector('#edit-url-input').value
	};
	return await sendAPI('POST', '/notifications/test', api_key, {}, data)
	.then(response => response.json())
	.then(json => {
		if (json.result.success)
			test_button.classList.add('show-success');
		else {
			test_button.classList.add('show-fail');
			error.innerText = 'Could not reach the given URL';
			hide([], [error]);
		};
		return json.result.success;
	});
};

function saveEditNotification() {
	usingApiKey().then(api_key => {
		const id = document.querySelector('#edit-notification-window').dataset.id;
		const data = {
			title: document.querySelector('#edit-title-input').value,
			url: document.querySelector('#edit-url-input').value,
			events: getCheckedEvents('edit-events-list'),
			enabled: document.querySelector('#edit-enabled-input').checked
		};
		sendAPI('PUT', `/notifications/${id}`, api_key, {}, data)
		.then(response => {
			loadNotificationServices(api_key);
			closeWindow();
		})
		.catch(e => {
			e.json().then(json => {
				const error = document.querySelector('#edit-error');
				error.innerText = '*' + (json.error || 'Failed to save notification service');
				hide([], [error]);
			});
		});
	});
};

function deleteNotification(api_key) {
	const id = document.querySelector('#edit-notification-window').dataset.id;
	sendAPI('DELETE', `/notifications/${id}`, api_key)
	.then(response => {
		delete notificationServices[id];
		loadNotificationServices(api_key);
		closeWindow();
	});
};

// code run on load

usingApiKey()
.then(api_key => {
	loadNotificationServices(api_key);
	document.querySelector('#add-notification').onclick = e => showAddNotification(api_key);
	document.querySelector('#test-notification-add').onclick = e => testAddNotification(api_key);
	document.querySelector('#test-notification-edit').onclick = e => testEditNotification(api_key);
	document.querySelector('#delete-notification-edit').onclick = e => deleteNotification(api_key);

});

document.querySelector('#add-notification-form').action = 'javascript:saveAddNotification()';
document.querySelector('#edit-notification-form').action = 'javascript:saveEditNotification()';
