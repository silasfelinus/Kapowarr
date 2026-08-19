const EventsEls = {
	rows: document.querySelector('#event-rows'),
	empty: document.querySelector('#events-empty'),
	status: document.querySelector('#events-status'),
	kind: document.querySelector('#event-kind'),
	level: document.querySelector('#event-level'),
	search: document.querySelector('#event-search'),
	auto_refresh: document.querySelector('#event-auto-refresh'),
	refresh: document.querySelector('#refresh-events'),
	comicvine_summary: document.querySelector('#comicvine-summary'),
	comicvine_operations: document.querySelector('#comicvine-operations')
};

const EVENT_LEVELS = {
	DEBUG: 10,
	INFO: 20,
	WARNING: 30,
	ERROR: 40,
	CRITICAL: 50
};

let eventApiKey = null;
let eventEntries = [];
let eventRefreshTimer = null;

function formatEventTime(timestamp) {
	if (!timestamp) return '';
	return new Date(timestamp * 1000).toLocaleString();
}

function eventMatchesFilters(entry) {
	if (EventsEls.kind.value !== 'all' && entry.kind !== EventsEls.kind.value)
		return false;

	const minimumLevel = EventsEls.level.value === 'warning' ? 30 :
		EventsEls.level.value === 'error' ? 40 : 0;
	if ((EVENT_LEVELS[entry.level] || 0) < minimumLevel)
		return false;

	const query = EventsEls.search.value.trim().toLowerCase();
	if (!query) return true;

	return [
		entry.kind,
		entry.level,
		entry.source,
		entry.title,
		entry.message
	].join(' ').toLowerCase().includes(query);
}

function buildEventRow(entry) {
	const row = document.createElement('tr');
	row.className = `event-${entry.level.toLowerCase()}`;

	const time = document.createElement('td');
	time.className = 'event-time';
	time.textContent = formatEventTime(entry.timestamp);
	row.appendChild(time);

	const kind = document.createElement('td');
	kind.className = 'event-kind';
	kind.textContent = entry.kind.charAt(0).toUpperCase() + entry.kind.slice(1);
	row.appendChild(kind);

	const level = document.createElement('td');
	level.className = 'event-level';
	level.textContent = entry.level;
	row.appendChild(level);

	const source = document.createElement('td');
	source.className = 'event-source';
	source.textContent = entry.source || '';
	row.appendChild(source);

	const event = document.createElement('td');
	const title = document.createElement(
		entry.volume_id !== null || entry.link ? 'a' : 'span'
	);
	title.className = 'event-title';
	title.textContent = entry.title || 'Event';
	if (title.tagName === 'A') {
		if (entry.volume_id !== null)
			title.href = `${url_base}/volumes/${entry.volume_id}`;
		else {
			title.href = entry.link;
			title.target = '_blank';
			title.rel = 'noopener noreferrer';
		}
	}
	event.appendChild(title);

	if (entry.message && entry.message !== entry.title) {
		const message = document.createElement('div');
		message.className = 'event-message';
		message.textContent = entry.message;
		event.appendChild(message);
	}
	row.appendChild(event);

	return row;
}

function renderEvents() {
	EventsEls.rows.replaceChildren();
	const visible = eventEntries.filter(eventMatchesFilters);
	visible.forEach(entry => EventsEls.rows.appendChild(buildEventRow(entry)));
	EventsEls.empty.classList.toggle('hidden', visible.length !== 0);
	EventsEls.status.textContent = `${visible.length} of ${eventEntries.length} recent events shown`;
}

function operationLabel(name) {
	return name.split('_').map(part =>
		part.charAt(0).toUpperCase() + part.slice(1)
	).join(' ');
}

function renderComicVineActivity(stats) {
	EventsEls.comicvine_operations.replaceChildren();
	const started = formatEventTime(stats.started_at);
	EventsEls.comicvine_summary.textContent =
		`${stats.total_operations} operations${started ? ` since ${started}` : ''}`;

	if (!stats.operations.length) {
		const card = document.createElement('div');
		card.className = 'operation-card';
		const title = document.createElement('h3');
		title.textContent = 'No ComicVine activity yet';
		const detail = document.createElement('p');
		detail.textContent = 'Counters begin when this Kapowarr process starts.';
		card.append(title, detail);
		EventsEls.comicvine_operations.appendChild(card);
		return;
	}

	stats.operations.forEach(operation => {
		const card = document.createElement('div');
		const hardErrors = operation.rate_limit + operation.invalid_key + operation.other_error;
		card.className = `operation-card${hardErrors ? ' has-error' : ''}`;

		const title = document.createElement('h3');
		title.textContent = operationLabel(operation.operation);
		card.appendChild(title);

		const counts = document.createElement('p');
		counts.textContent = `${operation.operations} operations · ${operation.success} successful`;
		card.appendChild(counts);

		if (operation.rate_limit) {
			const throttle = document.createElement('p');
			throttle.className = 'operation-outcome';
			throttle.textContent = `${operation.rate_limit} rate-limit outcome${operation.rate_limit === 1 ? '' : 's'}`;
			card.appendChild(throttle);
		}

		if (operation.invalid_key || operation.other_error) {
			const errors = document.createElement('p');
			errors.className = 'operation-outcome';
			errors.textContent = `${operation.invalid_key + operation.other_error} other error outcome${operation.invalid_key + operation.other_error === 1 ? '' : 's'}`;
			card.appendChild(errors);
		}

		if (operation.not_found) {
			const missing = document.createElement('p');
			missing.textContent = `${operation.not_found} not found`;
			card.appendChild(missing);
		}

		if (operation.last_outcome_at) {
			const last = document.createElement('p');
			last.textContent = `Last: ${operation.last_outcome.replaceAll('_', ' ')} · ${formatEventTime(operation.last_outcome_at)}`;
			card.appendChild(last);
		}

		EventsEls.comicvine_operations.appendChild(card);
	});
}

function refreshEvents() {
	if (!eventApiKey) return Promise.resolve();
	EventsEls.status.textContent = 'Refreshing…';
	return fetchAPI('/system/events', eventApiKey, {limit: 200})
	.then(json => {
		eventEntries = json.result.events || [];
		renderComicVineActivity(json.result.comicvine);
		renderEvents();
	})
	.catch(error => {
		EventsEls.status.textContent = `Unable to load events: ${error}`;
	});
}

function configureAutoRefresh() {
	if (eventRefreshTimer !== null) {
		clearInterval(eventRefreshTimer);
		eventRefreshTimer = null;
	}
	if (EventsEls.auto_refresh.checked)
		eventRefreshTimer = setInterval(refreshEvents, 10000);
}

usingApiKey()
.then(api_key => {
	eventApiKey = api_key;
	refreshEvents();
	EventsEls.refresh.onclick = refreshEvents;
	EventsEls.kind.onchange = renderEvents;
	EventsEls.level.onchange = renderEvents;
	EventsEls.search.oninput = renderEvents;
	EventsEls.auto_refresh.onchange = configureAutoRefresh;
});
