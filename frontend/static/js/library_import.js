const LIEls = {
	pre_build: {
		li_result: document.querySelector('.pre-build-els .li-result'),
		search_result: document.querySelector('.pre-build-els .search-result')
	},
	views: {
		start: document.querySelector('#start-window'),
		no_result: document.querySelector('#no-result-window'),
		list: document.querySelector('#list-window'),
		loading: document.querySelector('#loading-window'),
		no_cv: document.querySelector('#no-cv-window'),
		error: document.querySelector('#import-error-window'),
		continuous: document.querySelector('#continuous-window')
	},
	proposal_list: document.querySelector('.proposal-list'),
	proposal_note: document.querySelector('#continuous-review-note'),
	import_result: document.querySelector('#import-result-message'),
	select_all: document.querySelector('#selectall-input'),
	search: {
		window: document.querySelector('#cv-window'),
		input: document.querySelector('#search-input'),
		results: document.querySelector('.search-results'),
		container: document.querySelector('.search-results-container'),
		bar: document.querySelector('.search-bar')
	},
	continuous: {
		status: document.querySelector('#continuous-status'),
		progress: document.querySelector('#continuous-progress'),
		checked: document.querySelector('#continuous-checked'),
		imported: document.querySelector('#continuous-imported'),
		review: document.querySelector('#continuous-review'),
		remaining: document.querySelector('#continuous-remaining')
	},
	buttons: {
		cancel: document.querySelectorAll('.cancel-button'),
		run: document.querySelector('#run-import-button'),
		continuous: document.querySelector('#continuous-import-button'),
		continuous_back: document.querySelector('#continuous-back-button'),
		continuous_review: document.querySelector('#continuous-review-button'),
		continuous_stop: document.querySelector('#continuous-stop-button'),
		import: document.querySelector('#import-button'),
		import_rename: document.querySelector('#import-rename-button')
	}
};

const rowid_to_filepath = {};
let continuousTaskId = null;
let continuousPoll = null;
let continuousWasRunning = false;
let continuousStopRequested = false;
let continuousPanelDismissed = false;
let continuousReviewOpen = false;
let continuousReviewCache = [];
let continuousReviewFolderCount = 0;
let continuousLastSnapshotAt = 0;

function showImportError(error) {
	const message = document.querySelector('#import-error-message');
	let text = 'The import failed. Check the Kapowarr logs for details.';
	const all_work_views = [
		LIEls.views.loading,
		LIEls.views.list,
		LIEls.views.start,
		LIEls.views.continuous
	];

	if (!error || typeof error.json !== 'function') {
		message.innerText = text;
		hide(all_work_views, [LIEls.views.error]);
		return Promise.resolve();
	};

	return error.json()
	.then(json => {
		if (json.error === 'InvalidComicVineApiKey') {
			hide(
				[...all_work_views, LIEls.views.error],
				[LIEls.views.no_cv]
			);
			return;
		};

		if (json.error === 'InvalidKeyValue') {
			hide(
				[...all_work_views, LIEls.views.error],
				[LIEls.views.start, document.querySelector('#folder-filter-error')]
			);
			return;
		};

		if (json.error === 'CVRateLimitReached')
			text = 'ComicVine asked Kapowarr to slow down. Nothing was intentionally treated as a bad match. Try the paced Continuous Auto-Import mode, or retry this review batch after ComicVine has cooled down.';
		else if (json.error)
			text = `Import failed: ${json.error}`;

		message.innerText = text;
		hide(all_work_views, [LIEls.views.error]);
	})
	.catch(() => {
		message.innerText = text;
		hide(all_work_views, [LIEls.views.error]);
	});
};

function formatReviewReason(reason) {
	return {
		'tie': 'Top candidates tied',
		'weak-score': 'Best match is weak',
		'no-candidate': 'No viable candidate'
	}[reason] || '';
};

function renderProposalResults(results, from_continuous=false) {
	LIEls.proposal_list.innerHTML = '';
	LIEls.select_all.checked = true;
	Object.keys(rowid_to_filepath).forEach(key => delete rowid_to_filepath[key]);

	results.forEach((result, rowid) => {
		const entry = LIEls.pre_build.li_result.cloneNode(true);
		entry.dataset.rowid = rowid;
		entry.dataset.group_number = result.group_number;
		rowid_to_filepath[rowid] = {
			cv_id: result.cv.id || null,
			filepath: result.filepath
		};

		const title = entry.querySelector('.file-column');
		title.innerText = result.file_title;
		title.title = result.filepath;

		const CV_link = entry.querySelector('a');
		CV_link.href = result.cv.link || '';
		CV_link.innerText = result.cv.title || '';

		entry.querySelector('.issue-count').innerText =
			result.cv.issue_count === null ? '' : result.cv.issue_count;
		entry.querySelector('.review-reason').innerText =
			formatReviewReason(result.review_reason);

		entry.querySelector('button').onclick = e => openEditCVMatch(rowid);
		LIEls.proposal_list.appendChild(entry);
	});

	if (from_continuous)
		hide([], [LIEls.proposal_note]);
	else
		hide([LIEls.proposal_note]);

	if (results.length > 0)
		hide([LIEls.views.loading, LIEls.views.continuous], [LIEls.views.list]);
	else
		hide([LIEls.views.loading], [LIEls.views.no_result]);
};

function loadProposal(api_key) {
	const params = {
		limit: parseInt(document.querySelector('#limit-input').value),
		limit_parent_folder: document.querySelector('#folder-input').value,
		only_english: document.querySelector('#lang-input').value
	};
	const ffi = document.querySelector('#folder-filter-input');
	if (ffi.offsetParent !== null && (ffi.value || null) !== null)
		params.folder_filter = encodeURIComponent(ffi.value);

	continuousReviewOpen = false;
	hide(
		[
			LIEls.views.start,
			LIEls.views.error,
			document.querySelector('#folder-filter-error')
		],
		[LIEls.views.loading]
	);

	fetchAPI('/libraryimport', api_key, params)
	.then(json => renderProposalResults(json.result))
	.catch(e => showImportError(e));
};

function toggleSelectAll() {
	const checked = LIEls.select_all.checked;
	LIEls.proposal_list.querySelectorAll('input[type="checkbox"]').forEach(
		e => e.checked = checked
	);
};

function openEditCVMatch(rowid) {
	LIEls.search.window.dataset.rowid = rowid;
	LIEls.search.results.innerHTML = '';
	hide([LIEls.search.container]);
	LIEls.search.input.value = '';
	showWindow('cv-window');
	LIEls.search.input.focus();
};

function editCVMatch(
	rowid,
	comicvine_id,
	site_url,
	title,
	year,
	issue_count,
	group_number=null
) {
	let target_td;
	if (group_number === null)
		target_td = document.querySelectorAll(`tr[data-rowid="${rowid}"]`);
	else
		target_td = document.querySelectorAll(`tr[data-group_number="${group_number}"]`);

	target_td.forEach(tr => {
		rowid_to_filepath[tr.dataset.rowid].cv_id = parseInt(comicvine_id);
		const link = tr.querySelector('a');
		link.href = site_url;
		link.innerText = `${title} (${year})`;
		tr.querySelector('.issue-count').innerText = issue_count;
	});
};

function searchCV() {
	const input = LIEls.search.input;
	input.blur();
	usingApiKey()
	.then(api_key => {
		LIEls.search.results.innerHTML = '';
		fetchAPI('/volumes/search', api_key, {query: input.value})
		.then(json => {
			json.result.forEach(result => {
				const entry = LIEls.pre_build.search_result.cloneNode(true);

				const title = entry.querySelector('td:nth-child(1) a');
				title.href = result.site_url;
				title.innerText = `${result.title} (${result.year})`;

				entry.querySelector('td:nth-child(2)').innerText =
					result.issue_count;

				const select_button = entry.querySelector('td:nth-child(3) button');
				select_button.onclick = e => {
					editCVMatch(
						LIEls.search.window.dataset.rowid,
						result.comicvine_id,
						result.site_url,
						result.title,
						result.year,
						result.issue_count
					);
					closeWindow();
				};

				const select_for_all_button = entry.querySelector('td:nth-child(4) button');
				select_for_all_button.onclick = e => {
					const rowid = LIEls.search.window.dataset.rowid;
					const group_number = document.querySelector(`tr[data-rowid="${rowid}"]`)
						.dataset.group_number;
					editCVMatch(
						rowid,
						result.comicvine_id,
						result.site_url,
						result.title,
						result.year,
						result.issue_count,
						group_number
					);
					closeWindow();
				};

				LIEls.search.results.appendChild(entry);
			});
			hide([], [LIEls.search.container]);
		});
	});
};

function returnFromReviewList() {
	continuousReviewOpen = false;
	if (continuousTaskId !== null) {
		continuousPanelDismissed = false;
		hide([LIEls.views.list], [LIEls.views.continuous]);
	} else {
		hide([LIEls.views.list], [LIEls.views.start]);
	};
};

function importLibrary(api_key, rename=false) {
	const data = [...LIEls.proposal_list.querySelectorAll(
		'tr:has(input[type="checkbox"]:checked)'
	)]
		.filter(i => rowid_to_filepath[i.dataset.rowid].cv_id !== null)
		.map(e => {
			const rowid = e.dataset.rowid;
			return {
				'filepath': rowid_to_filepath[rowid].filepath,
				'id': rowid_to_filepath[rowid].cv_id
			};
		});

	const was_continuous_review = continuousReviewOpen;
	const submitted_paths = new Set(data.map(entry => entry.filepath));
	hide([LIEls.views.list], [LIEls.views.loading]);
	sendAPI('POST', '/libraryimport', api_key, {rename_files: rename}, data)
	.then(response => response.json())
	.then(json => {
		const imported_paths = new Set(
			(json.result.imported || []).flatMap(entry => entry.filepaths)
		);
		const failures = [
			...(json.result.failed || []),
			...(json.result.skipped || [])
		];

		LIEls.proposal_list.querySelectorAll('tr').forEach(row => {
			const item = rowid_to_filepath[row.dataset.rowid];
			if (item && imported_paths.has(item.filepath))
				row.remove();
		});

		if (failures.length) {
			const failed_paths = new Set(
				failures.flatMap(entry => entry.filepaths || [])
			);
			LIEls.proposal_list.querySelectorAll('tr').forEach(row => {
				const item = rowid_to_filepath[row.dataset.rowid];
				const checkbox = row.querySelector('input[type="checkbox"]');
				if (item && submitted_paths.has(item.filepath))
					checkbox.checked = failed_paths.has(item.filepath);
			});
			const first = failures[0];
			LIEls.import_result.innerText =
				`${imported_paths.size} imported · ${failures.length} volume${failures.length === 1 ? '' : 's'} need attention. ${first.reason}`;
			hide(
				[LIEls.views.loading],
				[LIEls.views.list, LIEls.import_result]
			);
			return;
		};

		hide([LIEls.import_result]);
		continuousReviewOpen = false;
		if (was_continuous_review && continuousTaskId !== null) {
			continuousPanelDismissed = false;
			hide([LIEls.views.loading], [LIEls.views.continuous]);
		} else {
			hide([LIEls.views.loading], [LIEls.views.start]);
		};
	})
	.catch(e => showImportError(e));
};

function renderContinuousProgress(checked, total, imported, remaining) {
	LIEls.continuous.progress.max = Math.max(total, 1);
	LIEls.continuous.progress.value = checked;
	LIEls.continuous.checked.innerText = `${checked} / ${total}`;
	LIEls.continuous.imported.innerText = imported;
	LIEls.continuous.remaining.innerText = remaining;
};

// The review count is deliberately not taken from the running task's message.
// Holds outlive the pass that produced them -- nothing imported them, so the
// next pass finds the same files and queues the same folders -- and the durable
// snapshot is the only place that knows the whole backlog.
function renderContinuousReviewCount(folder_count) {
	continuousReviewFolderCount = folder_count;
	LIEls.continuous.review.innerText = folder_count;
	LIEls.buttons.continuous_review.innerText =
		`Review Holds (${folder_count})`;
	LIEls.buttons.continuous_review.disabled = folder_count === 0;
};

function updateContinuousProgress(message) {
	LIEls.continuous.status.innerText = message || 'Queued behind another background task...';
	const match = (message || '').match(
		/Continuous import: (\d+)\/(\d+) folders checked · (\d+) volumes imported · (\d+) need review · (\d+) left/
	);
	if (!match)
		return;

	renderContinuousProgress(
		parseInt(match[1]),
		parseInt(match[2]),
		match[3],
		match[5]
	);
};

// Durable job state, readable whether or not a task happens to be running.
// `with_items` is for the moments that actually need the held rows -- opening
// the review list -- rather than the poll, which only renders a count.
function fetchContinuousSnapshot(api_key, with_items=false) {
	const params = with_items ? {} : {items: 0};
	return fetchAPI('/libraryimport/continuous', api_key, params)
	.then(json => {
		const snapshot = json.result;
		if (snapshot.review_items_included)
			continuousReviewCache = snapshot.review_items || [];
		continuousLastSnapshotAt = Date.now();
		renderContinuousReviewCount(snapshot.review_folders_outstanding || 0);
		return snapshot;
	});
};

// Paint a pass that is not currently running: finished, paused, or interrupted.
// Returns whether there was anything worth showing.
function applyContinuousSnapshot(snapshot) {
	const job = snapshot.job;
	if (job === null && !snapshot.review_folders_outstanding)
		return false;

	if (job !== null)
		renderContinuousProgress(
			job.checked_folders,
			job.total_folders,
			job.imported_volumes,
			job.remaining_folders
		);

	return true;
};

// A pass that imports nothing looks identical to a pass that did nothing, so
// say which reasons held the folders back.
function describeReviewReasons(job) {
	const labels = {
		tie: 'tied',
		weak_score: 'too weak to auto-import',
		no_candidate: 'no candidate found'
	};
	const reasons = (job && job.review_reasons) || {};
	const parts = Object.entries(reasons)
		.filter(([, amount]) => amount > 0)
		.map(([reason, amount]) => `${amount} ${labels[reason] || reason}`);

	// Scoped to this pass on purpose. The Review Holds count beside it is the
	// whole outstanding backlog, which can include folders held earlier.
	return parts.length ? ` This pass held: ${parts.join(', ')}.` : '';
};

function describeFinishedJob(job) {
	if (job === null)
		return 'Continuous import finished. Any folders Kapowarr could not match were left untouched for review.';

	if (job.status === 'paused')
		return `Continuous import paused after ${job.checked_folders}/${job.total_folders} folders. Everything already imported is preserved; start it again to continue where it left off.${describeReviewReasons(job)}`;

	if (job.status === 'running')
		return `Continuous import was interrupted after ${job.checked_folders}/${job.total_folders} folders. It resumes automatically the next time Kapowarr starts.${describeReviewReasons(job)}`;

	return `Continuous import finished: ${job.checked_folders}/${job.total_folders} folders checked, ${job.imported_volumes} volumes imported.${describeReviewReasons(job)} Anything held is waiting under Review Holds.`;
};

function showContinuousTask(task) {
	continuousTaskId = task.id;
	continuousWasRunning = true;
	if (!continuousPanelDismissed && !continuousReviewOpen) {
		hide(
			[
				LIEls.views.start,
				LIEls.views.loading,
				LIEls.views.error,
				LIEls.views.no_result,
				LIEls.views.no_cv,
				LIEls.views.list
			],
			[LIEls.views.continuous]
		);
	};
	updateContinuousProgress(task.message);
	LIEls.buttons.continuous_stop.disabled = continuousStopRequested;
};

function refreshContinuousReviewCache(api_key) {
	// Reads the durable job rather than the running task's details. A task is
	// dropped from the queue the moment it finishes, so asking it for the review
	// queue only worked while a pass happened to be in flight.
	return fetchContinuousSnapshot(api_key, true)
		.then(() => continuousReviewCache);
};

function openContinuousReview(api_key) {
	const show_items = items => {
		if (!items.length) {
			LIEls.continuous.status.innerText =
				'No review holds have accumulated yet.';
			return;
		};
		continuousReviewOpen = true;
		continuousPanelDismissed = true;
		renderProposalResults(items, true);
	};

	refreshContinuousReviewCache(api_key)
	.then(show_items)
	.catch(error => {
		if (continuousReviewCache.length)
			show_items(continuousReviewCache);
		else
			showImportError(error);
	});
};

function stopContinuousImport(api_key) {
	if (continuousTaskId === null)
		return;

	const task_id = continuousTaskId;
	continuousStopRequested = true;
	LIEls.buttons.continuous_stop.disabled = true;
	LIEls.continuous.status.innerText =
		'Stop requested. Pausing as soon as the current operation can exit safely...';

	// Stop is the primary action. Do not make it wait behind a potentially slow
	// details snapshot. Once the backend has acknowledged the cooperative stop,
	// refreshing held-review details is best-effort only; the durable job state
	// remains the source of truth.
	sendAPI('DELETE', `/system/tasks/${task_id}`, api_key)
	.then(() => {
		refreshContinuousReviewCache(api_key)
		.catch(() => continuousReviewCache);
	})
	.catch(e => {
		continuousStopRequested = false;
		LIEls.buttons.continuous_stop.disabled = false;
		return showImportError(e);
	});
};

function pollContinuousTask(api_key) {
	if (continuousPoll !== null)
		clearInterval(continuousPoll);

	const refresh = () => fetchAPI('/system/tasks', api_key)
	.then(json => {
		const task = json.result.find(
			t => t.action === 'continuous_library_import'
		);
		if (task) {
			showContinuousTask(task);
			if (Date.now() - continuousLastSnapshotAt >= 15000)
				fetchContinuousSnapshot(api_key).catch(() => null);
			return;
		};

		if (!continuousWasRunning)
			return;

		// The pass has left the queue. Everything it did is still on disk, so
		// read the final state from the durable job instead of reporting
		// whatever this page happened to have in memory.
		continuousTaskId = null;
		continuousWasRunning = false;
		LIEls.buttons.continuous_stop.disabled = true;
		fetchContinuousSnapshot(api_key)
		.then(snapshot => {
			applyContinuousSnapshot(snapshot);
			LIEls.continuous.status.innerText = continuousStopRequested
				? 'Continuous import stopped. Everything already imported is preserved, and the captured review holds are available below.'
				: describeFinishedJob(snapshot.job);
		})
		.catch(() => {
			LIEls.continuous.status.innerText = continuousStopRequested
				? 'Continuous import stopped. Everything already imported is preserved, and the captured review holds are available below.'
				: describeFinishedJob(null);
		});
	});

	refresh();
	continuousPoll = setInterval(refresh, 1000);
};

// A pass that already finished, or one interrupted by a restart, leaves nothing
// in the task queue -- so without this the page opens on the start screen with
// no sign that a backlog of held folders is waiting.
function showSavedContinuousState(api_key) {
	return fetchContinuousSnapshot(api_key)
	.then(snapshot => {
		if (!applyContinuousSnapshot(snapshot))
			return;
		// `task` is non-empty when a pass is queued or running; the poll owns the
		// panel in that case and will paint live progress into it.
		if (
			(snapshot.task && snapshot.task.id !== undefined)
			|| continuousTaskId !== null
			|| continuousPanelDismissed
		)
			return;

		LIEls.continuous.status.innerText = describeFinishedJob(snapshot.job);
		LIEls.buttons.continuous_stop.disabled = true;
		hide([LIEls.views.start], [LIEls.views.continuous]);
	})
	.catch(() => null);
};

function startContinuousImport(api_key) {
	continuousPanelDismissed = false;
	continuousReviewOpen = false;
	continuousStopRequested = false;
	fetchAPI('/system/tasks', api_key)
	.then(json => {
		const existing = json.result.find(
			t => t.action === 'continuous_library_import'
		);
		if (existing) {
			showContinuousTask(existing);
			return null;
		};

		// Holds from earlier passes are not cleared here. Nothing imported them,
		// so they are still outstanding until this pass re-checks those folders
		// and overwrites them; the next snapshot poll reports the real count.
		continuousLastSnapshotAt = 0;
		LIEls.buttons.continuous_stop.disabled = false;
		hide([LIEls.views.start], [LIEls.views.continuous]);
		updateContinuousProgress('Starting the longbox conveyor...');
		return sendAPI(
			'POST',
			'/system/tasks',
			api_key,
			{},
			{cmd: 'continuous_library_import'}
		);
	})
	.then(response => response === null ? null : response.json())
	.then(json => {
		if (json !== null) {
			continuousTaskId = json.result.id;
			continuousWasRunning = true;
		};
		pollContinuousTask(api_key);
	})
	.catch(e => showImportError(e));
};

function cancelProposalView() {
	if (continuousReviewOpen) {
		returnFromReviewList();
		return;
	};

	hide(
		[
			LIEls.views.list,
			LIEls.views.no_result,
			LIEls.views.no_cv,
			LIEls.views.error
		],
		[LIEls.views.start]
	);
};

// code run on load

usingApiKey()
.then(api_key => {
	LIEls.buttons.run.onclick = e => loadProposal(api_key);
	LIEls.buttons.continuous.onclick = e => startContinuousImport(api_key);
	LIEls.buttons.continuous_back.onclick = e => {
		continuousPanelDismissed = true;
		continuousReviewOpen = false;
		hide([LIEls.views.continuous], [LIEls.views.start]);
	};
	LIEls.buttons.continuous_review.onclick = e => openContinuousReview(api_key);
	LIEls.buttons.continuous_stop.onclick = e => stopContinuousImport(api_key);
	LIEls.buttons.import.onclick = e => importLibrary(api_key, false);
	LIEls.buttons.import_rename.onclick = e => importLibrary(api_key, true);
	pollContinuousTask(api_key);
	showSavedContinuousState(api_key);
});

LIEls.search.bar.action = 'javascript:searchCV();';
LIEls.select_all.onchange = e => toggleSelectAll();
LIEls.buttons.cancel.forEach(b => b.onclick = e => cancelProposalView());
