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

	// Which providers this dialog asks. Remembered, because someone who has
	// switched a slow provider off does not want it back on the next file.
	const PROVIDER_CHOICE_KEY = 'metadata_search_providers';

	// Comfortably past the server's own per-provider budget, so a search
	// that is merely slow still gets to finish and only one that is never
	// coming back is abandoned.
	const SEARCH_GIVE_UP_MS = 45000;
	let providers = null;

	function chosenProviders() {
		if (providers === null)
			return null;
		const chosen = providers.filter(p => p.on).map(p => p.id);
		// All of them selected is the same request as not asking for any in
		// particular, and the shorter URL is the one worth sending.
		return chosen.length === providers.length ? null : chosen;
	};

	function rememberProviderChoice() {
		try {
			localStorage.setItem(PROVIDER_CHOICE_KEY, JSON.stringify(
				providers.filter(p => p.on).map(p => p.id)
			));
		} catch (e) {};
	};

	function recallProviderChoice() {
		try {
			const stored = JSON.parse(
				localStorage.getItem(PROVIDER_CHOICE_KEY) || 'null'
			);
			return Array.isArray(stored) ? stored : null;
		} catch (e) {
			return null;
		};
	};

	function renderProviderToggles() {
		const row = document.querySelector('#match-search-providers');
		if (row === null || providers === null)
			return;

		row.replaceChildren(...providers.map(provider => {
			const label = document.createElement('label');
			label.className = 'provider-toggle';

			const box = document.createElement('input');
			box.type = 'checkbox';
			box.checked = provider.on;
			box.onchange = () => {
				provider.on = box.checked;
				// Never all off: a search that asks nobody is not a search,
				// and the backend would quietly fall back to all of them.
				if (!providers.some(p => p.on)) {
					provider.on = true;
					box.checked = true;
					return;
				};
				rememberProviderChoice();
			};

			label.append(box, document.createTextNode(` ${provider.name}`));
			return label;
		}));
		row.classList.toggle('hidden', providers.length < 2);
	};

	function loadProviders() {
		if (providers !== null)
			return Promise.resolve(providers);

		return usingApiKey()
		.then(apiKey => fetchAPI('/volumes/search/providers', apiKey))
		.then(json => {
			const remembered = recallProviderChoice();
			providers = (json.result || []).map(p => ({
				id: p.id,
				name: p.name,
				on: remembered === null ? true : remembered.includes(p.id)
			}));
			// A remembered set that no longer names any configured provider
			// would leave the dialog unable to search at all.
			if (!providers.some(p => p.on))
				providers.forEach(p => { p.on = true; });
			renderProviderToggles();
			return providers;
		})
		.catch(() => {
			// Without the list there are no toggles, and a search that asks
			// everyone is exactly what it did before.
			providers = [];
			return providers;
		});
	};

	function getMatchSearchStatus() {
		let status = document.querySelector('#match-search-status');
		if (status)
			return status;

		status = document.createElement('p');
		status.id = 'match-search-status';
		status.className = 'continuous-note';
		status.setAttribute('role', 'status');
		// Below the provider toggles, not above them: the toggles belong
		// with the query that uses them, and the status reports on what
		// came back, so it reads better next to the results.
		(document.querySelector('#match-search-providers')
			|| LIEls.search.bar).after(status);
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

	// The bracketed tail a scene release carries: year, format, scanner,
	// group. All of it is noise to a metadata provider, and a file that
	// reached this dialog has no proposed title to use instead -- so the
	// query starts as the whole filename. "Amnesiac 001 (2025) (ADULT)
	// (Digital) (Deluxe) (ASO) (Blue Orchid)" is not a search anybody
	// wants to make; "Amnesiac" is.
	const RELEASE_TAGS = /[([{][^)\]}]*[)\]}]/g;
	// A trailing cover date, then a trailing issue number: both belong to
	// the file rather than to the series. The issue number has to be
	// preceded by whitespace or "1950-06-00" loses its last two digits and
	// leaves a hanging hyphen.
	const TRAILING_DATE = /[\s,]+\d{4}(?:-\d{1,2}){1,2}$/;
	const TRAILING_ISSUE = /\s+#?\d{1,4}(?:\.\d+)?$/;
	// Whatever punctuation the removals left dangling.
	const TRAILING_PUNCTUATION = /[\s,;:.\-–—_]+$/;

	function seriesFrom(text) {
		const bare = (text || '')
			.replace(RELEASE_TAGS, ' ')
			.replace(/\s+/g, ' ')
			.trim()
			.replace(TRAILING_DATE, '')
			.replace(TRAILING_ISSUE, '')
			.replace(TRAILING_PUNCTUATION, '')
			.trim();
		// Stripping everything means the guess was wrong about what the
		// name is; the whole thing is a better starting point than nothing.
		return bare || (text || '').trim();
	};

	function rowSearchQuery(rowid) {
		const item = rowid_to_filepath[rowid] || {};
		const row = document.querySelector(`tr[data-rowid="${rowid}"]`);
		const proposed = stripResultYear(row?.querySelector('a')?.innerText);
		return proposed || seriesFrom(item.file_title) || '';
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
		if (error && error.name === 'AbortError')
			return Promise.resolve(
				'The metadata providers did not answer. Try again, or switch '
				+ 'one off above and search the rest.'
			);

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

		const chosen = chosenProviders();
		// The browser gives up too, a little after the server would.
		//
		// A browser opens about six connections per host, and a search that
		// never resolves keeps one of them for as long as it lasts. Enough
		// of those and every other page of Kapowarr is unreachable from
		// that browser while other sites are fine -- which is what a
		// "freeze" turned out to be. The server bounds each provider at
		// twenty seconds; this is the backstop for a response that never
		// arrives at all.
		const giveUp = new AbortController();
		const abandon = setTimeout(() => giveUp.abort(), SEARCH_GIVE_UP_MS);

		searchInFlight = usingApiKey()
		.then(apiKey => fetchAPI('/volumes/search', apiKey,
			chosen === null ? {query} : {query, providers: chosen.join(',')},
			true, {signal: giveUp.signal}))
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
			clearTimeout(abandon);
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
		// Selected, so replacing it is one keystroke and keeping it is none.
		LIEls.search.input.select();

		// It does not search on its own any more. Silas: "we should kill
		// whatever is auto-searching, it would be better to edit first
		// anyway." He is right -- opening the dialog fired a query for the
		// whole release filename, which is the worst question available and
		// the most expensive one to ask, and it had to finish before the
		// field could usefully be edited. The guess is offered, not acted
		// on.
		loadProviders().then(() => {
			renderProviderToggles();
			setSearchStatus(
				LIEls.search.input.value
					? 'Edit the title if you need to, then search.'
					: 'Enter a title to search.'
			);
		});
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
