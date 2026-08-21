const LogEls = {
	view_level: document.querySelector('#log-view-level'),
	page_size: document.querySelector('#log-page-size'),
	refresh: document.querySelector('#refresh-logs'),
	clear: document.querySelector('#clear-logs'),
	download: document.querySelector('#download-logs'),
	rows: document.querySelector('#log-rows'),
	empty: document.querySelector('#logs-empty'),
	status: document.querySelector('#logs-status'),
	page_label: document.querySelector('#log-page-label'),
	first: document.querySelector('#log-first'),
	prev: document.querySelector('#log-prev'),
	next: document.querySelector('#log-next'),
	last: document.querySelector('#log-last')
};

let log_api_key = null;
let log_page = 1;
let log_pagination = {page: 1, total_pages: 1, total_entries: 0};

function formatLogTime(timestamp) {
	return timestamp.replace('T', ' ');
};

// A log line and a stack trace are different things to read. The summary is
// what you scan a hundred of; the trace is what you read one of. Rendering
// every entry's full text inline turned a page with a handful of exceptions
// into a wall of frames with the actual sequence of events buried in it.
function splitLogMessage(message) {
	const newline = message.indexOf('\n');
	if (newline === -1)
		return {summary: message, details: ''};

	return {
		summary: message.slice(0, newline),
		details: message.slice(newline + 1).replace(/\s+$/, '')
	};
};

function buildLogRow(entry) {
	const row = document.createElement('tr');
	row.classList.add(`log-${entry.level.toLowerCase()}`);

	const time = document.createElement('td');
	time.classList.add('log-time');
	time.textContent = formatLogTime(entry.timestamp);

	const level = document.createElement('td');
	level.classList.add('log-level');
	const badge = document.createElement('span');
	badge.classList.add('log-level-badge');
	badge.textContent = entry.level;
	level.appendChild(badge);

	const message = document.createElement('td');
	message.classList.add('log-message');
	// The source and thread stay available without spending a column on them:
	// they matter once you are already reading one entry, not while scanning.
	message.title = `${entry.source} · ${entry.process} · ${entry.thread}`;

	const {summary, details} = splitLogMessage(entry.message);
	if (!details) {
		const text = document.createElement('p');
		text.classList.add('log-summary');
		text.textContent = summary;
		message.appendChild(text);
		row.append(time, level, message);
		return row;
	};

	const toggle = document.createElement('button');
	toggle.type = 'button';
	toggle.classList.add('log-summary', 'log-expand');
	toggle.setAttribute('aria-expanded', 'false');

	const chevron = document.createElement('span');
	chevron.classList.add('log-chevron');
	chevron.setAttribute('aria-hidden', 'true');
	chevron.textContent = '▸';

	const label = document.createElement('span');
	label.textContent = summary;

	const count = document.createElement('span');
	count.classList.add('log-detail-count');
	const lines = details.split('\n').length;
	count.textContent = `${lines} more line${lines === 1 ? '' : 's'}`;

	toggle.append(chevron, label, count);

	const body = document.createElement('pre');
	body.classList.add('log-details', 'hidden');
	body.textContent = details;

	toggle.onclick = () => {
		const open = body.classList.toggle('hidden') === false;
		toggle.setAttribute('aria-expanded', String(open));
		chevron.textContent = open ? '▾' : '▸';
	};

	message.append(toggle, body);
	row.append(time, level, message);
	return row;
};

function renderPagination() {
	const {page, total_pages, total_entries} = log_pagination;

	LogEls.page_label.textContent = `Page ${page} of ${total_pages}`;
	LogEls.status.textContent = total_entries === 1
		? '1 entry'
		: `${total_entries} entries`;

	LogEls.first.disabled = LogEls.prev.disabled = page <= 1;
	LogEls.next.disabled = LogEls.last.disabled = page >= total_pages;
};

function renderLogs(entries) {
	LogEls.rows.innerHTML = '';
	const fragment = document.createDocumentFragment();
	entries.forEach(entry => fragment.appendChild(buildLogRow(entry)));
	LogEls.rows.appendChild(fragment);
	LogEls.empty.classList.toggle('hidden', entries.length !== 0);
	renderPagination();
};

async function refreshLogs() {
	if (!log_api_key)
		return;

	LogEls.refresh.disabled = true;
	try {
		const json = await fetchAPI('/system/logs/view', log_api_key, {
			level: LogEls.view_level.value,
			page_size: LogEls.page_size.value,
			page: log_page
		});
		const result = json.result;
		log_pagination = result;
		// The server clamps a page past the end, so follow it back rather than
		// asking for a page that no longer exists on every later refresh.
		log_page = result.page;
		renderLogs(result.entries);
	} catch (error) {
		console.error(error);
		LogEls.status.textContent = 'Could not load logs.';
	} finally {
		LogEls.refresh.disabled = false;
	};
};

function goToPage(page) {
	log_page = page;
	refreshLogs();
};

async function clearLogs() {
	if (!log_api_key)
		return;

	if (!confirm('Clear the log file? This cannot be undone.'))
		return;

	LogEls.clear.disabled = true;
	LogEls.status.textContent = 'Clearing…';
	try {
		await sendAPI('POST', '/system/logs/clear', log_api_key);
		log_page = 1;
		await refreshLogs();
	} catch (error) {
		console.error(error);
		LogEls.status.textContent = 'Could not clear the log.';
	} finally {
		LogEls.clear.disabled = false;
	};
};

LogEls.refresh.onclick = () => refreshLogs();
LogEls.clear.onclick = clearLogs;
LogEls.first.onclick = () => goToPage(1);
LogEls.prev.onclick = () => goToPage(log_pagination.page - 1);
LogEls.next.onclick = () => goToPage(log_pagination.page + 1);
LogEls.last.onclick = () => goToPage(log_pagination.total_pages);

// Changing what is shown starts the reader over at the top of the new list.
LogEls.view_level.onchange = () => goToPage(1);
LogEls.page_size.onchange = () => goToPage(1);

usingApiKey()
.then(api_key => {
	log_api_key = api_key;
	LogEls.download.href = `${url_base}/api/system/logs?api_key=${encodeURIComponent(api_key)}`;
	refreshLogs();
});
