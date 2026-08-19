const LogEls = {
	capture_level: document.querySelector('#log-capture-level'),
	view_level: document.querySelector('#log-view-level'),
	search: document.querySelector('#log-search'),
	auto_refresh: document.querySelector('#log-auto-refresh'),
	refresh: document.querySelector('#refresh-logs'),
	download: document.querySelector('#download-logs'),
	rows: document.querySelector('#log-rows'),
	empty: document.querySelector('#logs-empty'),
	status: document.querySelector('#logs-status')
};

let log_api_key = null;
let log_entries = [];
let auto_refresh_timer = null;
let last_capture_level = null;

function formatLogTime(timestamp) {
	return timestamp.replace('T', ' ');
};

function renderLogs() {
	const query = LogEls.search.value.trim().toLowerCase();
	const visible = query
		? log_entries.filter(entry => [
			entry.timestamp,
			entry.level,
			entry.source,
			entry.process,
			entry.thread,
			entry.message
		].join(' ').toLowerCase().includes(query))
		: log_entries;

	LogEls.rows.innerHTML = '';
	const fragment = document.createDocumentFragment();

	visible.forEach(entry => {
		const row = document.createElement('tr');
		row.classList.add(`log-${entry.level.toLowerCase()}`);

		const time = document.createElement('td');
		time.classList.add('log-time');
		time.textContent = formatLogTime(entry.timestamp);

		const level = document.createElement('td');
		level.classList.add('log-level');
		level.textContent = entry.level;

		const source = document.createElement('td');
		source.classList.add('log-source');
		source.textContent = entry.source;
		source.title = `${entry.process} · ${entry.thread}`;

		const message = document.createElement('td');
		message.classList.add('log-message');
		const pre = document.createElement('pre');
		pre.textContent = entry.message;
		message.appendChild(pre);

		row.append(time, level, source, message);
		fragment.appendChild(row);
	});

	LogEls.rows.appendChild(fragment);
	LogEls.empty.classList.toggle('hidden', visible.length !== 0);
	LogEls.status.textContent = `${visible.length} shown · ${log_entries.length} loaded`;
};

async function refreshLogs() {
	if (!log_api_key)
		return;

	LogEls.refresh.disabled = true;
	LogEls.status.textContent = 'Refreshing…';
	try {
		const json = await fetchAPI('/system/logs/view', log_api_key, {
			level: LogEls.view_level.value,
			limit: 1000
		});
		log_entries = json.result.entries;
		last_capture_level = String(json.result.capture_level);
		LogEls.capture_level.value = last_capture_level;
		renderLogs();
	} catch (error) {
		console.error(error);
		LogEls.status.textContent = 'Could not load logs.';
	} finally {
		LogEls.refresh.disabled = false;
	};
};

async function changeCaptureLevel() {
	if (!log_api_key)
		return;

	const next_level = Number(LogEls.capture_level.value),
		previous_level = last_capture_level;
	LogEls.capture_level.disabled = true;
	LogEls.status.textContent = next_level === 10
		? 'Enabling debug capture…'
		: 'Returning capture to info…';

	try {
		await sendAPI('PUT', '/settings', log_api_key, {}, {
			log_level: next_level
		});
		last_capture_level = String(next_level);
		LogEls.status.textContent = next_level === 10
			? 'Debug capture enabled.'
			: 'Info capture enabled.';
	} catch (error) {
		console.error(error);
		if (previous_level !== null)
			LogEls.capture_level.value = previous_level;
		LogEls.status.textContent = 'Could not change capture level.';
	} finally {
		LogEls.capture_level.disabled = false;
	};
};

function updateAutoRefresh() {
	if (auto_refresh_timer !== null) {
		clearInterval(auto_refresh_timer);
		auto_refresh_timer = null;
	};

	if (LogEls.auto_refresh.checked)
		auto_refresh_timer = setInterval(refreshLogs, 5000);
};

LogEls.refresh.onclick = refreshLogs;
LogEls.view_level.onchange = refreshLogs;
LogEls.search.oninput = renderLogs;
LogEls.capture_level.onchange = changeCaptureLevel;
LogEls.auto_refresh.onchange = updateAutoRefresh;

document.addEventListener('visibilitychange', () => {
	if (!document.hidden && LogEls.auto_refresh.checked)
		refreshLogs();
});

usingApiKey()
.then(api_key => {
	log_api_key = api_key;
	LogEls.download.href = `${url_base}/api/system/logs?api_key=${encodeURIComponent(api_key)}`;
	refreshLogs();
});
