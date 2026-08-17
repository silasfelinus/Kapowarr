(() => {
	let matchedOnly = false;
	const originalRenderProposalResults = window.renderProposalResults;

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
			.review-volume-controls label {
				display: inline-flex;
				align-items: center;
				gap: .5rem;
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
		`;
		document.head.appendChild(style);
	};

	function ensureControls() {
		let controls = document.querySelector('#review-volume-controls');
		if (controls)
			return controls;

		const tableContainer = document.querySelector('#list-window .table-container');
		if (!tableContainer)
			return null;

		controls = document.createElement('div');
		controls.id = 'review-volume-controls';
		controls.className = 'review-volume-controls';
		controls.innerHTML = `
			<label>
				<input type="checkbox" id="matched-only-input">
				Show matched only
			</label>
			<span id="review-volume-summary"></span>
		`;
		tableContainer.before(controls);

		const filter = controls.querySelector('#matched-only-input');
		filter.checked = matchedOnly;
		filter.onchange = () => {
			matchedOnly = filter.checked;
			applyMatchFilter();
		};
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

	function collapseRenderedRows() {
		installStyles();
		ensureControls();

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
		originalRenderProposalResults(results, fromContinuous);
		collapseRenderedRows();
	};

	// The collapsed UI represents a whole group, so the group-wide selector is
	// the only meaningful search action. Keep the existing callback, just make
	// it look like the normal Select button.
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

	// After a manual search selects a candidate for a previously unmatched
	// group, make that group importable and selected without requiring a second
	// checkbox click.
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
})();
