(() => {
	let matchedOnly = false;
	let maintenanceInProgress = false;
	let manualReviewScanInFlight = false;
	let manualReviewScanDismissed = false;

	const originalRenderProposalResults = window.renderProposalResults;
	const originalShowImportError = window.showImportError;
	const originalLoadProposal = window.loadProposal;
	const originalStartContinuousImport = window.startContinuousImport;

	if (typeof originalRenderProposalResults !== 'function')
		return;

	const getProposalRows = () => [
		...document.querySelectorAll('.proposal-list tr[data-group_number]')
	];

	const getGroupRows = groupNumber => getProposalRows().filter(
		row => row.dataset.group_number === groupNumber
	);

	const rowHasMatch = row => Boolean(
		row.querySelector('a')?.innerText.trim()
	);

	const getRecheckButtons = () => [
		...document.querySelectorAll('[data-recheck-review-holds]')
	];

	const getRescanButtons = () => [
		...document.querySelectorAll('[data-rescan-untracked-library]')
	];

	function installStyles() {
		if (document.querySelector('#library-import-review-ui-style'))
			return;

		const style = document.createElement('style');
		style.id = 'library-import-review-ui-style';
		style.textContent = `
			.review-volume-controls {
				display: flex;
				justify-content: space-between;
				align-items: center;
				gap: 1rem;
				flex-wrap: wrap;
				padding: 0 1rem;
				color: var(--text-color);
			}
			.review-volume-controls label,
			.review-volume-actions {
				display: inline-flex;
				align-items: center;
				gap: .5rem;
			}
			.review-volume-controls label {
				cursor: pointer;
			}
			.volume-file-details > summary {
				cursor: pointer;
				font-weight: 600;
			}
			.volume-file-details ul {
				max-height: 14rem;
				overflow: auto;
				margin: .5rem 0 0 1.25rem;
				padding: 0;
			}
			.volume-file-details li {
				margin: .2rem 0;
				font-weight: 400;
				word-break: break-word;
			}
			.proposal-list tr[data-volume-head="true"][data-has-match="false"] {
				opacity: .72;
			}
			.search-results td:nth-child(3) {
				display: none;
			}
			#import-background-controls {
				max-width: 50rem;
				width: min(50rem, 100%);
				text-align: center;
			}
			#import-background-controls > p {
				margin: .25rem 0;
			}
			#review-scan-escape {
				text-align: center;
			}
			#review-scan-escape p {
				max-width: 42rem;
				margin: 0 auto .5rem;
				font-size: .9rem;
			}
		`;
		document.head.appendChild(style);
	};

	function setMaintenanceButtonsState(disabled, activeCommand=null) {
		getRecheckButtons().forEach(button => {
			button.disabled = disabled;
			button.innerText = activeCommand === 'recheck_continuous_library_import'
				? 'Re-evaluating...'
				: 'Reset & Re-evaluate Holds';
		});
		getRescanButtons().forEach(button => {
			button.disabled = disabled;
			button.innerText = activeCommand === 'rescan_continuous_library_import'
				? 'Rescanning...'
				: 'Rescan Untracked Library';
		});
	};

	function waitForTaskCompletion(apiKey, taskId, fallbackMessage) {
		return new Promise((resolve, reject) => {
			const poll = () => fetchAPI('/system/tasks', apiKey)
			.then(json => {
				const task = json.result.find(entry => entry.id === taskId);
				if (!task) {
					resolve();
					return;
				};

				if (LIEls?.continuous?.status)
					LIEls.continuous.status.innerText = task.message
						|| fallbackMessage;
				setTimeout(poll, 500);
			})
			.catch(reject);
			poll();
		});
	};

	function findActiveContinuousTask(apiKey) {
		return fetchAPI('/system/tasks', apiKey)
		.then(json => json.result.find(
			task => task.action === 'continuous_library_import'
		) || null);
	};

	function restartContinuousAfterMaintenance(apiKey) {
		if (typeof originalStartContinuousImport !== 'function')
			return Promise.reject(new Error(
				'Continuous Library Import could not be restarted.'
			));

		originalStartContinuousImport(apiKey);
		const startedAt = Date.now();
		const timeoutMs = 15000;

		return new Promise((resolve, reject) => {
			const poll = () => findActiveContinuousTask(apiKey)
			.then(task => {
				if (task !== null) {
					showContinuousTask(task);
					pollContinuousTask(apiKey);
					resolve(task);
					return;
				};

				if (Date.now() - startedAt >= timeoutMs) {
					reject(new Error(
						'Continuous Library Import did not restart after maintenance.'
					));
					return;
				};
				setTimeout(poll, 250);
			})
			.catch(reject);
			poll();
		});
	};

	function runContinuousMaintenance(config) {
		if (maintenanceInProgress || manualReviewScanInFlight)
			return;

		if (!window.confirm(config.confirmMessage))
			return;

		maintenanceInProgress = true;
		setMaintenanceButtonsState(true, config.command);

		usingApiKey()
		.then(apiKey => {
			// The poll schedules itself, so stopping it means telling it not
			// to schedule again as well as cancelling the pending timer --
			// clearing the handle alone leaves an in-flight request that
			// will arm the next one when it lands.
			stopContinuousPoll();

			continuousReviewOpen = false;
			continuousPanelDismissed = false;
			hide(
				[
					LIEls.views.start,
					LIEls.views.list,
					LIEls.views.loading,
					LIEls.views.error,
					LIEls.views.no_result,
					LIEls.views.no_cv
				],
				[LIEls.views.continuous]
			);
			LIEls.continuous.status.innerText = config.rebuildingMessage;
			LIEls.buttons.continuous_stop.disabled = true;
			LIEls.buttons.continuous_review.disabled = true;
			LIEls.buttons.continuous_back.disabled = true;

			return findActiveContinuousTask(apiKey)
			.then(activeTask => {
				if (activeTask !== null) {
					continuousTaskId = activeTask.id;
					LIEls.continuous.status.innerText = config.stoppingMessage;
					return sendAPI(
						'DELETE',
						`/system/tasks/${activeTask.id}`,
						apiKey
					);
				};
				return null;
			})
			.then(() => {
				continuousTaskId = null;
				continuousWasRunning = false;
				continuousStopRequested = false;
				return sendAPI(
					'POST',
					'/system/tasks',
					apiKey,
					{},
					{cmd: config.command}
				);
			})
			.then(response => response.json())
			.then(json => waitForTaskCompletion(
				apiKey,
				json.result.id,
				config.rebuildingMessage
			))
			.then(() => {
				continuousTaskId = null;
				continuousWasRunning = false;
				continuousStopRequested = false;
				continuousReviewCache = [];
				continuousReviewFolderCount = 0;
				continuousLastSnapshotAt = 0;
				LIEls.buttons.continuous_review.innerText = 'Review Holds (0)';
				LIEls.buttons.continuous_review.disabled = true;
				LIEls.continuous.status.innerText = config.restartMessage;
				return restartContinuousAfterMaintenance(apiKey);
			});
		})
		.catch(error => showImportError(error))
		.finally(() => {
			maintenanceInProgress = false;
			LIEls.buttons.continuous_back.disabled = false;
			setMaintenanceButtonsState(false);
			updatePrimaryControls();
		});
	};

	function resetAndRecheckContinuousReview() {
		runContinuousMaintenance({
			command: 'recheck_continuous_library_import',
			confirmMessage: 'Retry only the folders currently in Review Holds with the latest matching logic? Other untracked folders will not be added to this pass. Already imported files stay imported.',
			rebuildingMessage: 'Rebuilding the import snapshot from current Review Holds...',
			stoppingMessage: 'Stopping at the current folder boundary, then rebuilding the Review Holds pass...',
			restartMessage: 'Review Holds ready. Restarting Continuous Auto-Import...'
		});
	};

	function rescanUntrackedLibrary() {
		runContinuousMaintenance({
			command: 'rescan_continuous_library_import',
			confirmMessage: 'Rescan every configured root for currently unimported folders and rebuild Continuous Auto-Import from that full set? This can be much larger than the Review Holds backlog. Already imported files stay imported.',
			rebuildingMessage: 'Scanning the library for all current unimported folders...',
			stoppingMessage: 'Stopping at the current folder boundary, then scanning all current unimported folders...',
			restartMessage: 'Full untracked-library rescan ready. Restarting Continuous Auto-Import...'
		});
	};

	function ensurePrimaryControls() {
		installStyles();
		const start = document.querySelector('#start-window');
		const actions = document.querySelector('#start-window .import-mode-actions');
		if (!start || !actions)
			return;

		if (!document.querySelector('#recheck-review-holds-start-button')) {
			const reset = document.createElement('button');
			reset.type = 'button';
			reset.id = 'recheck-review-holds-start-button';
			reset.dataset.recheckReviewHolds = 'true';
			reset.innerText = 'Reset & Re-evaluate Holds';
			reset.title = 'Retry only the folders currently under Review Holds with the latest matcher. Other untracked folders are not added to this pass.';
			reset.onclick = resetAndRecheckContinuousReview;
			actions.appendChild(reset);
		};

		if (!document.querySelector('#rescan-untracked-library-start-button')) {
			const rescan = document.createElement('button');
			rescan.type = 'button';
			rescan.id = 'rescan-untracked-library-start-button';
			rescan.dataset.rescanUntrackedLibrary = 'true';
			rescan.innerText = 'Rescan Untracked Library';
			rescan.title = 'Build a fresh Continuous Auto-Import pass from every currently unimported folder in the configured roots.';
			rescan.onclick = rescanUntrackedLibrary;
			actions.appendChild(rescan);
		};

		if (!document.querySelector('#import-reset-note')) {
			const note = document.createElement('p');
			note.id = 'import-reset-note';
			note.className = 'continuous-note';
			note.innerText = 'Reset & Re-evaluate Holds retries only the outstanding Review Holds. Rescan Untracked Library deliberately rebuilds the pass from every currently unimported folder. Imported comics stay imported.';
			actions.after(note);
		};

		if (!document.querySelector('#import-background-controls')) {
			const dock = document.createElement('div');
			dock.id = 'import-background-controls';
			dock.hidden = true;
			dock.innerHTML = `
				<p><strong>Continuous Auto-Import is running in the background.</strong></p>
				<p id="import-background-status"></p>
				<div class="action-container">
					<button type="button" id="background-view-progress">View Progress</button>
					<button type="button" id="background-review-holds">Review Holds</button>
					<button type="button" id="background-stop-import">Stop Import</button>
					<button type="button" data-recheck-review-holds>Reset & Re-evaluate Holds</button>
					<button type="button" data-rescan-untracked-library>Rescan Untracked Library</button>
				</div>
			`;
			start.appendChild(dock);

			dock.querySelector('#background-view-progress').onclick = () => {
				continuousPanelDismissed = false;
				continuousReviewOpen = false;
				hide([LIEls.views.start, LIEls.views.loading, LIEls.views.list], [LIEls.views.continuous]);
			};
			dock.querySelector('#background-review-holds').onclick = () => {
				usingApiKey().then(apiKey => openContinuousReview(apiKey));
			};
			dock.querySelector('#background-stop-import').onclick = () => {
				usingApiKey().then(apiKey => stopContinuousImport(apiKey));
			};
			dock.querySelector('[data-recheck-review-holds]').onclick =
				resetAndRecheckContinuousReview;
			dock.querySelector('[data-rescan-untracked-library]').onclick =
				rescanUntrackedLibrary;
		};

		const loading = document.querySelector('#loading-window');
		if (loading && !document.querySelector('#review-scan-escape')) {
			const escape = document.createElement('div');
			escape.id = 'review-scan-escape';
			escape.hidden = true;
			escape.innerHTML = `
				<p>Review Scan is the older synchronous mode. You can return to the import options while it finishes, but starting another scan is held until this request settles.</p>
				<button type="button">Back to Import Options</button>
			`;
			escape.querySelector('button').onclick = () => {
				manualReviewScanDismissed = true;
				hide([LIEls.views.loading], [LIEls.views.start]);
				updatePrimaryControls();
			};
			loading.appendChild(escape);
		};
	};

	function updatePrimaryControls() {
		ensurePrimaryControls();
		const continuousStart = document.querySelector('#continuous-import-button');
		const reviewStart = document.querySelector('#run-import-button');
		const escape = document.querySelector('#review-scan-escape');
		const dock = document.querySelector('#import-background-controls');
		const dockStatus = document.querySelector('#import-background-status');

		const continuousActive = continuousTaskId !== null;
		const maintenanceBlocked = maintenanceInProgress || manualReviewScanInFlight;
		getRecheckButtons().forEach(button => button.disabled = maintenanceBlocked);
		getRescanButtons().forEach(button => button.disabled = maintenanceBlocked);
		if (continuousStart)
			continuousStart.disabled = continuousActive || maintenanceBlocked;
		if (reviewStart)
			reviewStart.disabled = continuousActive || maintenanceBlocked;
		if (escape)
			escape.hidden = !manualReviewScanInFlight;

		if (dock) {
			const showDock = continuousTaskId !== null && continuousPanelDismissed;
			dock.hidden = !showDock;
			if (showDock && dockStatus)
				dockStatus.innerText = LIEls.continuous.status.innerText || 'Working through the longboxes...';
			const review = dock.querySelector('#background-review-holds');
			if (review) {
				review.disabled = continuousReviewFolderCount === 0;
				review.innerText = `Review Holds (${continuousReviewFolderCount})`;
			};
		};
	};

	function ensureControls(fromContinuous=false) {
		let controls = document.querySelector('#review-volume-controls');
		if (!controls) {
			const tableContainer = document.querySelector('#list-window .table-container');
			if (!tableContainer)
				return null;

			controls = document.createElement('div');
			controls.id = 'review-volume-controls';
			controls.className = 'review-volume-controls';
			controls.innerHTML = `
				<div class="review-volume-actions">
					<label>
						<input type="checkbox" id="matched-only-input">
						Show matched only
					</label>
					<button type="button" id="recheck-review-holds-button" data-recheck-review-holds>
						Reset & Re-evaluate Holds
					</button>
				</div>
				<span id="review-volume-summary"></span>
			`;
			tableContainer.before(controls);

			const filter = controls.querySelector('#matched-only-input');
			filter.checked = matchedOnly;
			filter.onchange = () => {
				matchedOnly = filter.checked;
				applyMatchFilter();
			};

			controls.querySelector('#recheck-review-holds-button').onclick =
				resetAndRecheckContinuousReview;
		};

		const recheck = controls.querySelector('#recheck-review-holds-button');
		if (recheck)
			recheck.hidden = !fromContinuous;
		return controls;
	};

	function syncSelectAllState() {
		const selectAll = document.querySelector('#selectall-input');
		if (!selectAll)
			return;

		const eligible = getProposalRows().filter(row => (
			row.dataset.volumeHead === 'true'
			&& row.dataset.hasMatch === 'true'
			&& !row.hidden
		));
		const selected = eligible.filter(
			row => row.querySelector('input[type="checkbox"]')?.checked
		);

		selectAll.disabled = eligible.length === 0;
		selectAll.checked = eligible.length > 0 && selected.length === eligible.length;
		selectAll.indeterminate = selected.length > 0 && selected.length < eligible.length;
	};

	function updateSummary() {
		const summary = document.querySelector('#review-volume-summary');
		if (!summary)
			return;

		const heads = getProposalRows().filter(
			row => row.dataset.volumeHead === 'true'
		);
		const matched = heads.filter(
			row => row.dataset.hasMatch === 'true'
		).length;

		if (matchedOnly)
			summary.innerText = `${matched} matched volume groups shown · ${heads.length} total`;
		else
			summary.innerText = `${heads.length} volume groups · ${matched} with a proposed match`;
	};

	function applyMatchFilter() {
		getProposalRows().forEach(row => {
			if (row.dataset.volumeChild === 'true') {
				row.hidden = true;
				return;
			};
			row.hidden = matchedOnly && row.dataset.hasMatch !== 'true';
		});
		updateSummary();
		syncSelectAllState();
	};

	function setGroupChecked(groupNumber, checked) {
		getGroupRows(groupNumber).forEach(row => {
			const checkbox = row.querySelector('input[type="checkbox"]');
			if (checkbox && !checkbox.disabled)
				checkbox.checked = checked;
		});
		syncSelectAllState();
	};

	function refreshGroupMatchState(groupNumber, autoSelect=false) {
		const rows = getGroupRows(groupNumber);
		if (!rows.length)
			return;

		const matched = rows.some(rowHasMatch);
		rows.forEach(row => {
			row.dataset.hasMatch = matched ? 'true' : 'false';
			const checkbox = row.querySelector('input[type="checkbox"]');
			if (!checkbox)
				return;
			checkbox.disabled = !matched;
			if (!matched)
				checkbox.checked = false;
			else if (autoSelect)
				checkbox.checked = true;
		});
		applyMatchFilter();
	};

	function collapseRenderedRows(fromContinuous=false) {
		installStyles();
		ensureControls(fromContinuous);

		const groups = new Map();
		getProposalRows().forEach(row => {
			const groupNumber = row.dataset.group_number;
			if (!groups.has(groupNumber))
				groups.set(groupNumber, []);
			groups.get(groupNumber).push(row);
		});

		groups.forEach((rows, groupNumber) => {
			const head = rows[0];
			const matched = rows.some(rowHasMatch);
			const fileEntries = rows.map(row => {
				const cell = row.querySelector('.file-column');
				return {
					name: cell?.innerText || 'Untitled file',
					path: cell?.title || ''
				};
			});

			rows.forEach((row, index) => {
				row.dataset.volumeHead = index === 0 ? 'true' : 'false';
				row.dataset.volumeChild = index === 0 ? 'false' : 'true';
				row.dataset.hasMatch = matched ? 'true' : 'false';
				row.hidden = index !== 0;

				const checkbox = row.querySelector('input[type="checkbox"]');
				if (checkbox) {
					checkbox.disabled = !matched;
					checkbox.checked = matched;
				};
			});

			const headCheckbox = head.querySelector('input[type="checkbox"]');
			if (headCheckbox)
				headCheckbox.onchange = () => setGroupChecked(
					groupNumber,
					headCheckbox.checked
				);

			if (fileEntries.length > 1) {
				const fileCell = head.querySelector('.file-column');
				if (fileCell) {
					fileCell.innerHTML = '';
					fileCell.title = `${fileEntries.length} files in this volume group`;
					const details = document.createElement('details');
					details.className = 'volume-file-details';
					const summary = document.createElement('summary');
					summary.innerText = `${fileEntries.length} files`;
					const list = document.createElement('ul');
					fileEntries.forEach(file => {
						const item = document.createElement('li');
						item.innerText = file.name;
						item.title = file.path;
						list.appendChild(item);
					});
					details.append(summary, list);
					fileCell.appendChild(details);
				};
			};
		});

		const selectAll = document.querySelector('#selectall-input');
		if (selectAll)
			selectAll.onchange = () => {
				getProposalRows()
				.filter(row => (
					row.dataset.volumeHead === 'true'
					&& row.dataset.hasMatch === 'true'
					&& !row.hidden
				))
				.forEach(row => setGroupChecked(
					row.dataset.group_number,
					selectAll.checked
				));
				syncSelectAllState();
			};

		applyMatchFilter();
	};

	window.renderProposalResults = function(results, fromContinuous=false) {
		if (!fromContinuous && manualReviewScanInFlight) {
			manualReviewScanInFlight = false;
			const dismissed = manualReviewScanDismissed;
			manualReviewScanDismissed = false;
			updatePrimaryControls();
			if (dismissed) {
				hide([LIEls.views.loading], [LIEls.views.start]);
				return;
			};
		};

		originalRenderProposalResults(results, fromContinuous);
		collapseRenderedRows(fromContinuous);
	};

	window.showImportError = function(error) {
		if (manualReviewScanInFlight) {
			manualReviewScanInFlight = false;
			const dismissed = manualReviewScanDismissed;
			manualReviewScanDismissed = false;
			updatePrimaryControls();
			if (dismissed)
				return Promise.resolve();
		};
		return originalShowImportError(error);
	};

	const searchResults = document.querySelector('.search-results');
	if (searchResults) {
		const relabelGroupSelect = () => {
			searchResults.querySelectorAll('td:nth-child(4) button').forEach(
				button => button.innerText = 'Select'
			);
		};
		new MutationObserver(relabelGroupSelect).observe(
			searchResults,
			{childList: true, subtree: true}
		);
	};

	document.addEventListener('click', event => {
		const groupSelect = event.target.closest('.search-results td:nth-child(4) button');
		if (!groupSelect)
			return;

		const rowid = document.querySelector('#cv-window')?.dataset.rowid;
		const row = rowid === undefined ? null : document.querySelector(
			`.proposal-list tr[data-rowid="${rowid}"]`
		);
		const groupNumber = row?.dataset.group_number;
		if (!groupNumber)
			return;

		setTimeout(() => refreshGroupMatchState(groupNumber, true), 0);
	}, true);

	ensurePrimaryControls();
	usingApiKey().then(apiKey => {
		if (typeof originalLoadProposal === 'function')
			LIEls.buttons.run.onclick = () => {
				manualReviewScanInFlight = true;
				manualReviewScanDismissed = false;
				updatePrimaryControls();
				originalLoadProposal(apiKey);
			};
	});
	setInterval(updatePrimaryControls, 1000);
	updatePrimaryControls();
})();