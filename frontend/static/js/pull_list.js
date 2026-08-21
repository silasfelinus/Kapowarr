const PullListEls = {
	table: document.querySelector('#pull-list'),
	empty_message: document.querySelector('#pull-list-empty-message'),
	check_status: document.querySelector('#pull-list-check-status'),
	week_label: document.querySelector('#week-label'),
	page_label: document.querySelector('#page-label'),
	search: document.querySelector('#release-search'),
	publisher_filter: document.querySelector('#publisher-filter'),
	library_filter: document.querySelector('#library-filter'),
	rules: document.querySelector('#publisher-rules'),
	rule_list: document.querySelector('#publisher-rule-list'),
	root_folder: document.querySelector('#publisher-root-folder'),
	buttons: {
		refresh: document.querySelector('#refresh-button'),
		check: document.querySelector('#check-button'),
		reading_lists: document.querySelector('#reading-lists-button'),
		previous_week: document.querySelector('#previous-week'),
		current_week: document.querySelector('#current-week'),
		next_week: document.querySelector('#next-week'),
		rules: document.querySelector('#publisher-rules-toggle'),
		previous_page: document.querySelector('#previous-page'),
		next_page: document.querySelector('#next-page')
	},
	entry: document.querySelector('.pre-build-els .pull-list-entry'),
	rule: document.querySelector('.pre-build-els .publisher-rule')
};

const pullListState = {
	entries: [],
	publishers: [],
	stored_weeks: [],
	week: startOfWeek(new Date()),
	page: 0,
	page_size: 50
};

function startOfWeek(value) {
	const result = new Date(value);
	result.setHours(12, 0, 0, 0);
	const day = result.getDay();
	result.setDate(result.getDate() - (day === 0 ? 6 : day - 1));
	return result;
};

function dateFromIso(value) {
	return new Date(`${value}T12:00:00`);
};

function isoDate(value) {
	const year = value.getFullYear();
	const month = String(value.getMonth() + 1).padStart(2, '0');
	const day = String(value.getDate()).padStart(2, '0');
	return `${year}-${month}-${day}`;
};

function formatWeek() {
	const end = new Date(pullListState.week);
	end.setDate(end.getDate() + 6);
	PullListEls.week_label.innerText =
		`${pullListState.week.toLocaleDateString()} – ${end.toLocaleDateString()}`;
};

function filteredEntries() {
	const query = PullListEls.search.value.trim().toLowerCase();
	const publisher = PullListEls.publisher_filter.value;
	const library = PullListEls.library_filter.value;
	return pullListState.entries.filter(entry => {
		if (query && !entry.release_title.toLowerCase().includes(query)) return false;
		if (publisher && entry.publisher !== publisher) return false;
		if (library === 'library' && entry.volume_id === null) return false;
		if (library === 'missing' && entry.volume_id !== null) return false;
		return true;
	});
};

function actionButton(label, action, entry, api_key) {
	const button = document.createElement('button');
	button.type = 'button';
	button.innerText = label;
	button.onclick = () => actOnEntry(action, entry, button, api_key);
	if (entry.volume_id === null && entry.comicvine_volume_id === null) {
		button.disabled = true;
		button.title = 'This release has no ComicVine series ID';
	};
	return button;
};

function updateEmptyMessage(filtered_count) {
	if (filtered_count > 0)
		return;

	const selected = isoDate(pullListState.week);
	if (!pullListState.stored_weeks.length) {
		PullListEls.empty_message.innerText =
			'No release catalogue is stored yet. Run “Check Now” to fetch it.';
		return;
	};

	const newest = pullListState.stored_weeks[0].week_start;
	const oldest = pullListState.stored_weeks[pullListState.stored_weeks.length - 1].week_start;
	PullListEls.empty_message.innerText =
		`No releases are stored for ${selected}. Stored release data runs from `
		+ `${oldest} through ${newest}. Run “Check Now” to refresh it.`;
};

function renderList(api_key) {
	PullListEls.table.innerHTML = '';
	const filtered = filteredEntries();
	const page_count = Math.max(1, Math.ceil(filtered.length / pullListState.page_size));
	pullListState.page = Math.min(pullListState.page, page_count - 1);
	const first = pullListState.page * pullListState.page_size;
	const rows = filtered.slice(first, first + pullListState.page_size);

	PullListEls.empty_message.classList.toggle('hidden', filtered.length > 0);
	updateEmptyMessage(filtered.length);
	PullListEls.page_label.innerText = `Page ${pullListState.page + 1} of ${page_count}`;
	PullListEls.buttons.previous_page.disabled = pullListState.page === 0;
	PullListEls.buttons.next_page.disabled = pullListState.page >= page_count - 1;

	rows.forEach(obj => {
		const entry = PullListEls.entry.cloneNode(true);
		entry.querySelector('.date-column').innerText = obj.release_date || obj.week_start;
		entry.querySelector('.publisher-column').innerText = obj.publisher || 'Unknown';

		const title_link = entry.querySelector('.title-column a');
		title_link.innerText = obj.release_title;
		if (obj.link) {
			title_link.href = obj.link;
			title_link.target = '_blank';
		} else {
			title_link.removeAttribute('href');
		};
		entry.querySelector('.issue-column').innerText =
			obj.issue_number !== null ? `#${obj.issue_number}` : '';

		const library = entry.querySelector('.library-column');
		if (obj.volume_id !== null) {
			const link = document.createElement('a');
			link.href = `${url_base}/volumes/${obj.volume_id}`;
			link.innerText = obj.volume_monitored ? 'Monitored' : 'In library';
			library.appendChild(link);
		} else {
			library.innerText = 'Not added';
		};

		const availability = entry.querySelector('.availability-column');
		entry.querySelector('.source-column').innerText = obj.source;
		if (obj.availability_link) {
			const link = document.createElement('a');
			link.href = obj.availability_link;
			link.target = '_blank';
			link.innerText = obj.availability_source;
			availability.appendChild(link);
		} else {
			availability.innerText = 'Not found';
		};

		const actions = entry.querySelector('.action-column');
		if (obj.automation_success === 0) {
			const warning = document.createElement('span');
			warning.classList.add('automation-warning');
			warning.innerText = 'Retry pending';
			warning.title = obj.automation_message || 'Automation will retry';
			actions.appendChild(warning);
		};
		actions.appendChild(actionButton(
			obj.volume_id === null ? 'Add + want' : 'Want',
			'monitor', obj, api_key
		));
		actions.appendChild(actionButton('Grab', 'grab', obj, api_key));
		PullListEls.table.appendChild(entry);
	});
};

function publisherWeekCount(publisher) {
	const counts = publisher.release_counts || {};
	return counts[isoDate(pullListState.week)] || 0;
};

function updatePublisherFilter() {
	const selected = PullListEls.publisher_filter.value;
	PullListEls.publisher_filter.innerHTML = '<option value="">All publishers</option>';
	pullListState.publishers.forEach(obj => {
		const release_count = publisherWeekCount(obj);
		if (!release_count)
			return;
		const option = document.createElement('option');
		option.value = obj.publisher;
		option.innerText = `${obj.publisher} (${release_count})`;
		option.selected = obj.publisher === selected;
		PullListEls.publisher_filter.appendChild(option);
	});
};

function renderPublisherRules(api_key) {
	PullListEls.rule_list.innerHTML = '';
	pullListState.publishers.forEach(obj => {
		const rule = PullListEls.rule.cloneNode(true);
		rule.querySelector('.publisher-rule-name').innerText = obj.publisher;
		rule.querySelector('.publisher-rule-count').innerText =
			`${obj.release_count} stored releases`;
		const mode = rule.querySelector('.publisher-rule-mode');
		mode.value = obj.root_folder_id === null
			? 'off'
			: (obj.auto_search ? 'grab' : 'add');
		mode.onchange = () => savePublisherRule(obj.publisher, mode.value, api_key);
		PullListEls.rule_list.appendChild(rule);
	});
};

function loadPublishers(api_key) {
	return fetchAPI('/pulllist/publishers', api_key).then(json => {
		pullListState.publishers = json.result;
		updatePublisherFilter();
		renderPublisherRules(api_key);
	});
};

function loadStoredWeeks(api_key) {
	return fetchAPI('/pulllist/weeks', api_key).then(json => {
		pullListState.stored_weeks = json.result;
	});
};

function loadList(api_key, fallback_to_stored=false) {
	formatWeek();
	const requested_week = isoDate(pullListState.week);
	return Promise.all([
		fetchAPI('/pulllist', api_key, {week_start: requested_week}),
		loadPublishers(api_key),
		loadStoredWeeks(api_key)
	]).then(results => {
		pullListState.entries = results[0].result;
		if (
			fallback_to_stored
			&& pullListState.entries.length === 0
			&& pullListState.stored_weeks.length > 0
		) {
			const newest_week = pullListState.stored_weeks[0].week_start;
			if (newest_week !== requested_week) {
				pullListState.week = dateFromIso(newest_week);
				setCheckStatus(
					`No releases were stored for ${requested_week}; `
					+ `showing the newest stored week, ${newest_week}.`
				);
				return loadList(api_key, false);
			};
		};
		pullListState.page = 0;
		renderList(api_key);
	});
};

function loadRootFolders(api_key) {
	return fetchAPI('/rootfolder', api_key).then(json => {
		PullListEls.root_folder.innerHTML = '';
		json.result.forEach(root => {
			const option = document.createElement('option');
			option.value = root.id;
			option.innerText = root.folder;
			PullListEls.root_folder.appendChild(option);
		});
	});
};

function savePublisherRule(publisher, mode, api_key) {
	if (mode === 'off') {
		return sendAPI('DELETE', '/pulllist/publishers', api_key, {}, {publisher})
			.then(() => loadPublishers(api_key));
	};
	const root_folder_id = parseInt(PullListEls.root_folder.value);
	if (Number.isNaN(root_folder_id)) {
		alert('Add a root folder before enabling publisher automation.');
		return Promise.resolve();
	};
	return sendAPI('POST', '/pulllist/publishers', api_key, {}, {
		publisher,
		root_folder_id,
		auto_search: mode === 'grab'
	}).then(() => loadPublishers(api_key));
};

function actOnEntry(action, entry, button, api_key) {
	button.disabled = true;
	const root_folder_id = PullListEls.root_folder.value
		? parseInt(PullListEls.root_folder.value)
		: null;
	sendAPI('POST', `/pulllist/${entry.id}/action`, api_key, {}, {
		action,
		root_folder_id
	})
	.then(() => loadList(api_key))
	.catch(() => {
		button.disabled = false;
		alert('The release action failed. Check System > Tasks or logs for details.');
	});
};

function setCheckStatus(message, failed=false) {
	PullListEls.check_status.innerText = message || '';
	PullListEls.check_status.classList.toggle('hidden', !message);
	PullListEls.check_status.classList.toggle('error', failed);
};

function stopCheckSpinner() {
	PullListEls.buttons.check.disabled = false;
	PullListEls.buttons.check.querySelector('img').classList.remove('spinning');
};

function pollUntilCheckFinished(api_key, check_id) {
	fetchAPI(`/pulllist/check/${check_id}`, api_key)
	.then(json => {
		const check = json.result;
		if (check.status === 'queued' || check.status === 'running') {
			setCheckStatus(check.message || 'Refreshing the release calendar...');
			setTimeout(() => pollUntilCheckFinished(api_key, check_id), 1500);
			return;
		};

		if (check.status === 'failed') {
			setCheckStatus(check.message || 'Release calendar check failed.', true);
			stopCheckSpinner();
			return;
		};

		pullListState.week = startOfWeek(new Date());
		loadList(api_key, false)
			.then(() => setCheckStatus(
				`Release calendar updated (${check.release_count} releases).`
			))
			.catch(error => setCheckStatus(
				`Calendar refreshed but reload failed: ${error.message || 'request failed'}`,
				true
			))
			.finally(stopCheckSpinner);
	})
	.catch(error => {
		setCheckStatus(
			`Check status failed: ${error.message || 'unable to poll check'}`,
			true
		);
		stopCheckSpinner();
	});
};

function checkNow(api_key) {
	PullListEls.buttons.check.disabled = true;
	PullListEls.buttons.check.querySelector('img').classList.add('spinning');
	setCheckStatus('Starting release calendar check...');
	sendAPI('POST', '/pulllist/check', api_key)
		.then(response => response.json())
		.then(json => pollUntilCheckFinished(api_key, json.result.id))
		.catch(error => {
			setCheckStatus(
				`Check failed to start: ${error.message || 'request failed'}`,
				true
			);
			stopCheckSpinner();
		});
};

function moveWeek(amount, api_key) {
	pullListState.week.setDate(pullListState.week.getDate() + amount * 7);
	loadList(api_key);
};

usingApiKey().then(api_key => {
	loadRootFolders(api_key).then(() => loadList(api_key, true));
	PullListEls.buttons.refresh.onclick = () => loadList(api_key);
	PullListEls.buttons.check.onclick = () => checkNow(api_key);
	PullListEls.buttons.reading_lists.onclick = () => {
		window.location.href = `${url_base}/activity/reading-lists`;
	};
	PullListEls.buttons.previous_week.onclick = () => moveWeek(-1, api_key);
	PullListEls.buttons.next_week.onclick = () => moveWeek(1, api_key);
	PullListEls.buttons.current_week.onclick = () => {
		pullListState.week = startOfWeek(new Date());
		loadList(api_key);
	};
	PullListEls.buttons.rules.onclick = () => {
		PullListEls.rules.classList.toggle('hidden');
	};
	PullListEls.buttons.previous_page.onclick = () => {
		pullListState.page -= 1;
		renderList(api_key);
	};
	PullListEls.buttons.next_page.onclick = () => {
		pullListState.page += 1;
		renderList(api_key);
	};
	[PullListEls.search, PullListEls.publisher_filter, PullListEls.library_filter]
		.forEach(input => input.oninput = () => {
			pullListState.page = 0;
			renderList(api_key);
		});
});
