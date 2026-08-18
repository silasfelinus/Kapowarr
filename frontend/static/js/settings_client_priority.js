// Lightweight priority overlay for the existing external-client settings UI.
// Keep credentials/test/save behavior in settings_download_clients.js untouched.

function makeClientPriorityRow(api_key, client_id) {
	const row = document.createElement('tr');
	row.classList.add('client-priority-row');

	const header = document.createElement('th');
	const label = document.createElement('label');
	label.innerText = 'Priority';
	label.setAttribute('for', 'edit-client-priority-input');
	header.appendChild(label);
	row.appendChild(header);

	const cell = document.createElement('td');
	const input = document.createElement('input');
	input.type = 'number';
	input.id = 'edit-client-priority-input';
	input.min = '1';
	input.max = '100';
	input.value = '50';
	input.required = true;
	cell.appendChild(input);

	const description = document.createElement('p');
	description.innerText = '1 is highest. Equal-priority clients still share work by queue load. Priority saves immediately.';
	cell.appendChild(description);
	row.appendChild(cell);

	fetchAPI(`/externalclients/${client_id}`, api_key)
	.then(json => {
		input.value = json.result.priority || 50;
	});

	input.onchange = () => {
		let priority = parseInt(input.value);
		if (Number.isNaN(priority))
			priority = 50;
		priority = Math.max(1, Math.min(100, priority));
		input.value = priority;

		fetchAPI('/settings/acquisition', api_key)
		.then(json => {
			const priorities = {...(json.result.client_priorities || {})};
			priorities[String(client_id)] = priority;
			return sendAPI(
				'PUT', '/settings/acquisition', api_key, {},
				{client_priorities: priorities}
			);
		});
	};

	return row;
};

// The original function removes dynamic credential rows synchronously before
// starting its API requests. Add our row immediately after that cleanup; the
// original code can then append username/password/token rows after it.
const loadEditClientWithoutPriority = loadEditClient;
loadEditClient = function(api_key, id) {
	loadEditClientWithoutPriority(api_key, id);
	const form = document.querySelector('#edit-client-form tbody');
	form.querySelectorAll('.client-priority-row').forEach(row => row.remove());
	form.appendChild(makeClientPriorityRow(api_key, id));
};

// Give the list a small bit of context without replacing its established
// rendering code. Newly added clients intentionally start at priority 50.
const externalClientList = document.querySelector('#external-client-list');
if (externalClientList && !document.querySelector('#client-priority-help')) {
	const help = document.createElement('p');
	help.id = 'client-priority-help';
	help.classList.add('description');
	help.innerText = 'Edit a client to set its acquisition priority. New clients start at priority 50.';
	externalClientList.before(help);
};
