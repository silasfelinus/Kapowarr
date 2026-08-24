(() => {
	// Library Import used to open an empty ComicVine-only modal and wait for the
	// user to invent a query. Review Holds already know the proposed title (or
	// at least the filename that produced the hold), so editing should begin by
	// searching that title automatically. Keep the manual field as the escape
	// hatch when the first query is too specific.
	let searchInFlight = null;
	let searchInFlightQuery = null;

	const originalBuildProposalRow = buildProposalRow;
	const originalImportLibrary = importLibrary;

	function stripResultYear(title) {
		return (title || '').replace(/\s+\((?:19|20)\d{2}\)\s*$/, '').trim();
	};

	function getMatchSearchStatus() {
		let status = document.querySelector('#match-search-status');
		if (status)
			return status;

		status = document.createElement('p');
		status.id = 'match-search-status';
		status.className = 'continuous-note';
		status.setAttribute('role', 'status');
		LIEls.search.bar.after(status);
		return status;
	};

	function setSearchStatus(message, kind='') {
		const status = getMatchSearchStatus();
		status.innerText = message;
		status.classList.toggle('error', kind === 'error');
	};

	function setSearchBusy(busy) {
		const submit = LIEls.search.bar.querySelector('button[type="submit"]');
		if (submit)
			submit.disabled = busy;
		LIEls.search.input.setAttribute('aria-busy', busy ? 'true' : 'false');
	};

	function metadataIdentity(result) {
		const providerId = result.provider_id
			|| (result.comicvine_id !== null && result.comicvine_id !== undefined
				? 'comicvine'
				: null);
		const externalId = result.external_id !== null
			&& result.external_id !== undefined
			? result.external_id
			: result.comicvine_id;
		return {
			cv_id: result.comicvine_id ?? null,
			provider_id: providerId,
			external_id: externalId ?? null
		};
	};

	function rowSearchQuery(rowid) {
		const item = rowid_to_filepath[rowid] || {};
		const row = document.querySelector(`tr[data-rowid="${rowid}"]`);
		const proposed = stripResultYear(row?.querySelector('a')?.innerText);
		return proposed || item.file_title || '';
	};

	function applyMetadataMatch(result, groupNumber=null) {
		const rowid = LIEls.search.window.dataset.rowid;
		const rows = groupNumber === null
			? document.querySelectorAll(`tr[data-rowid="${rowid}"]`)
			: document.querySelectorAll(`tr[data-group_number="${groupNumber}"]`);
		const identity = metadataIdentity(result);

		rows.forEach(row => {
			const item = rowid_to_filepath[row.dataset.rowid];
			if (!item)
				return;
			item.cv_id = identity.cv_id;
			item.provider_id = identity.provider_id;
			item.external_id = identity.external_id;
			const link = row.querySelector('a');
			link.href = result.site_url || '';
			link.innerText = `${result.title} (${result.year || '?'})`;
			row.querySelector('.issue-count').innerText = result.issue_count ?? '';
		});
	};

	function renderSearchResults(results) {
		LIEls.search.results.innerHTML = '';
		results.forEach(result => {
			const entry = LIEls.pre_build.search_result.cloneNode(true);
			const title = entry.querySelector('td:nth-child(1) a');
			title.href = result.site_url || '';
			title.innerText = `${result.title} (${result.year || '?'})`;
			entry.querySelector('td:nth-child(2)').innerText = result.issue_count ?? '';

			const selectButton = entry.querySelector('td:nth-child(3) button');
			selectButton.onclick = () => {
				applyMetadataMatch(result);
				closeWindow();
			};

			const selectGroupButton = entry.querySelector('td:nth-child(4) button');
			selectGroupButton.onclick = () => {
				const rowid = LIEls.search.window.dataset.rowid;
				const groupNumber = document.querySelector(
					`tr[data-rowid="${rowid}"]`
				)?.dataset.group_number;
				applyMetadataMatch(result, groupNumber || null);
				closeWindow();
			};

			LIEls.search.results.appendChild(entry);
		});
		hide([], [LIEls.search.container]);
	};

	function describeSearchError(error) {
		if (!error || typeof error.json !== 'function')
			return Promise.resolve('Metadata search failed. Try again or use a broader title.');

		return error.json()
		.then(json => {
			if (json.error === 'CVRateLimitReached')
				return 'ComicVine is rate-limited right now. Other metadata searches may still work after the cooldown.';
			if (json.error)
				return `Metadata search failed: ${json.error}`;
			return 'Metadata search failed. Try again or use a broader title.';
		})
		.catch(() => 'Metadata search failed. Try again or use a broader title.');
	};

	window.searchCV = function() {
		const query = LIEls.search.input.value.trim();
		if (!query) {
			setSearchStatus('Enter a title to search.', 'error');
			LIEls.search.input.focus();
			return Promise.resolve();
		};

		// A double-click/Enter while the provider fan-out is still running used
		// to stack identical searches. Besides wasting provider rate limits, a
		// few of those at once can make the tab feel frozen. One query, one live
		// request. The field remains editable for the next attempt.
		if (searchInFlight !== null) {
			if (searchInFlightQuery === query)
				return searchInFlight;
			setSearchStatus(
				`Still searching for “${searchInFlightQuery}”. When it finishes, search again for “${query}”.`
			);
			return searchInFlight;
		};

		LIEls.search.results.innerHTML = '';
		hide([LIEls.search.container]);
		setSearchBusy(true);
		setSearchStatus(`Searching metadata providers for “${query}”…`);
		searchInFlightQuery = query;

		searchInFlight = usingApiKey()
		.then(apiKey => fetchAPI('/volumes/search', apiKey, {query}))
		.then(json => {
			const results = json.result || [];
			if (!results.length) {
				setSearchStatus(
					`No matches found for “${query}”. Try a shorter or fuzzier title.`
				);
				return;
			};
			renderSearchResults(results);
			setSearchStatus(
				`${results.length} match${results.length === 1 ? '' : 'es'} found. Choose one, or edit the search above.`
			);
		})
		.catch(error => describeSearchError(error).then(message => {
			setSearchStatus(message, 'error');
		}))
		.finally(() => {
			setSearchBusy(false);
			searchInFlight = null;
			searchInFlightQuery = null;
		});
		return searchInFlight;
	};

	window.openEditCVMatch = function(rowid) {
		LIEls.search.window.dataset.rowid = rowid;
		LIEls.search.results.innerHTML = '';
		hide([LIEls.search.container]);
		LIEls.search.input.value = rowSearchQuery(rowid);
		showWindow('cv-window');
		LIEls.search.input.focus();
		if (LIEls.search.input.value)
			searchCV();
		else
			setSearchStatus('Enter a title to search.');
	};

	window.buildProposalRow = function(result, rowid) {
		const row = originalBuildProposalRow(result, rowid);
		const item = rowid_to_filepath[rowid];
		item.file_title = result.file_title || '';
		item.provider_id = result.cv.provider_id
			|| (result.cv.id !== null && result.cv.id !== undefined ? 'comicvine' : null);
		item.external_id = result.cv.external_id !== null
			&& result.cv.external_id !== undefined
			? result.cv.external_id
			: result.cv.id;
		return row;
	};

	window.importLibrary = function(api_key, rename=false) {
		const data = [...LIEls.proposal_list.querySelectorAll(
			'tr:has(input[type="checkbox"]:checked)'
		)]
			.map(row => rowid_to_filepath[row.dataset.rowid])
			.filter(item => item && item.external_id !== null && item.external_id !== undefined)
			.map(item => ({
				filepath: item.filepath,
				id: item.cv_id,
				provider_id: item.provider_id || 'comicvine',
				external_id: item.external_id
			}));

		const wasContinuousReview = continuousReviewOpen;
		const submittedPaths = new Set(data.map(entry => entry.filepath));
		hide([LIEls.views.list], [LIEls.views.loading]);
		sendAPI('POST', '/libraryimport', api_key, {rename_files: rename}, data)
		.then(response => response.json())
		.then(json => {
			const imported = json.result.imported || [];
			const skipped = json.result.skipped || [];
			const failed = json.result.failed || [];
			const resolvedPaths = new Set([
				...imported.flatMap(entry => entry.filepaths || []),
				...skipped.flatMap(entry => entry.filepaths || [])
			]);
			const failedPaths = new Set(
				failed.flatMap(entry => entry.filepaths || [])
			);

			LIEls.proposal_list.querySelectorAll('tr').forEach(row => {
				const item = rowid_to_filepath[row.dataset.rowid];
				if (item && resolvedPaths.has(item.filepath))
					row.remove();
			});

			if (failed.length || skipped.length) {
				LIEls.proposal_list.querySelectorAll('tr').forEach(row => {
					const item = rowid_to_filepath[row.dataset.rowid];
					const checkbox = row.querySelector('input[type="checkbox"]');
					if (item && submittedPaths.has(item.filepath))
						checkbox.checked = failedPaths.has(item.filepath);
				});

				const parts = [`${imported.length} imported`];
				if (skipped.length)
					parts.push(`${skipped.length} skipped`);
				if (failed.length)
					parts.push(`${failed.length} still need${failed.length === 1 ? 's' : ''} attention`);
				const detail = (failed[0] || skipped[0]).reason;
				LIEls.import_result.innerText = `${parts.join(' · ')}. ${detail}`;
				hide(
					[LIEls.views.loading],
					[LIEls.views.list, LIEls.import_result]
				);
				return;
			};

			hide([LIEls.import_result]);
			continuousReviewOpen = false;
			if (wasContinuousReview && continuousTaskId !== null) {
				continuousPanelDismissed = false;
				hide([LIEls.views.loading], [LIEls.views.continuous]);
			} else {
				hide([LIEls.views.loading], [LIEls.views.start]);
			};
		})
		.catch(error => showImportError(error));
	};

	// library_import.js installed click handlers before this enhancer loaded;
	// point them at the provider-aware import function now.
	usingApiKey().then(apiKey => {
		LIEls.buttons.import.onclick = () => importLibrary(apiKey, false);
		LIEls.buttons.import_rename.onclick = () => importLibrary(apiKey, true);
	});

	LIEls.search.bar.action = 'javascript:searchCV();';
	const modalTitle = LIEls.search.window.querySelector('h2');
	if (modalTitle)
		modalTitle.innerText = 'Edit Metadata Match';
	const reviewNote = document.querySelector('#continuous-review-note');
	if (reviewNote)
		reviewNote.innerText = 'These rows were held by Continuous Auto-Import. Editing a match searches the configured metadata providers automatically; if the first title is too specific, edit it and search again.';
})();
