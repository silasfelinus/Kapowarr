function parseContinuousWork(message) {
	const text = message || '';
	const counts = text.match(
		/Continuous import: (\d+)\/(\d+) folders checked/
	);
	if (!counts)
		return null;

	const result = {
		checked: parseInt(counts[1]),
		total: parseInt(counts[2]),
		folder_index: null,
		folder: null,
		phase: null,
		current: null,
		phase_total: null,
		title: null,
		cooldown: /ComicVine rate limit reached; cooling down/.test(text)
	};

	if (result.checked < result.total)
		result.folder_index = result.checked + 1;

	let detail = text.match(
		/(?:^| · )([^·]+): matching title (\d+)\/(\d+) · ([^·]+)$/
	);
	if (detail) {
		result.folder = detail[1].trim();
		result.phase = 'matching';
		result.current = parseInt(detail[2]);
		result.phase_total = parseInt(detail[3]);
		result.title = detail[4].trim();
		return result;
	};

	detail = text.match(
		/(?:^| · )([^·]+): importing volume (\d+)\/(\d+)$/
	);
	if (detail) {
		result.folder = detail[1].trim();
		result.phase = 'importing';
		result.current = parseInt(detail[2]);
		result.phase_total = parseInt(detail[3]);
		return result;
	};

	detail = text.match(
		/(?:^| · )([^·]+): shared search for (\d+) parsed titles$/
	);
	if (detail) {
		result.folder = detail[1].trim();
		result.phase = 'shared-search';
		result.current = 0;
		result.phase_total = parseInt(detail[2]);
		return result;
	};

	return result;
};

function installContinuousImportProgressUI() {
	const status = document.querySelector('#continuous-status');
	const stats = document.querySelector('#continuous-window .continuous-stats tbody');
	const actions = document.querySelector('#continuous-window .action-container');
	if (!status || !stats || !actions)
		return;

	const firstLabel = stats.querySelector('tr td:first-child');
	if (firstLabel && firstLabel.innerText === 'Folders checked')
		firstLabel.innerText = 'Folders completed';

	let row = document.querySelector('#continuous-inner-progress-row');
	if (!row) {
		row = document.createElement('tr');
		row.id = 'continuous-inner-progress-row';
		row.hidden = true;
		row.innerHTML = `
			<td>Current folder</td>
			<td>
				<span id="continuous-inner-progress-label"></span>
				<progress id="continuous-inner-progress" value="0" max="1"></progress>
			</td>
		`;
		stats.appendChild(row);
	};

	const label = row.querySelector('#continuous-inner-progress-label');
	const progress = row.querySelector('#continuous-inner-progress');
	progress.style.display = 'block';
	progress.style.width = '100%';
	progress.style.marginTop = '.25rem';

	if (!document.querySelector('#continuous-recheck-button')) {
		const reset = document.createElement('button');
		reset.type = 'button';
		reset.id = 'continuous-recheck-button';
		reset.dataset.recheckReviewHolds = 'true';
		reset.innerText = 'Reset & Re-evaluate Holds';
		reset.title = (
			'Retry only the folders currently under Review Holds with the latest matcher. '
			+ 'Other untracked folders are not added to this pass.'
		);
		reset.onclick = () => {
			const primaryReset = document.querySelector(
				'#recheck-review-holds-start-button'
			);
			if (primaryReset)
				primaryReset.click();
		};
		actions.appendChild(reset);
	};

	if (!document.querySelector('#continuous-rescan-button')) {
		const rescan = document.createElement('button');
		rescan.type = 'button';
		rescan.id = 'continuous-rescan-button';
		rescan.dataset.rescanUntrackedLibrary = 'true';
		rescan.innerText = 'Rescan Untracked Library';
		rescan.title = (
			'Build a fresh Continuous Auto-Import pass from every currently unimported '
			+ 'folder in the configured roots.'
		);
		rescan.onclick = () => {
			const primaryRescan = document.querySelector(
				'#rescan-untracked-library-start-button'
			);
			if (primaryRescan)
				primaryRescan.click();
		};
		actions.appendChild(rescan);
	};

	let lastDetailedWork = null;
	const render = () => {
		const parsed = parseContinuousWork(status.innerText);
		if (!parsed || parsed.checked >= parsed.total) {
			row.hidden = true;
			return;
		};

		if (parsed.phase)
			lastDetailedWork = parsed;

		if (parsed.cooldown) {
			const previous = lastDetailedWork;
			row.hidden = false;
			label.innerText = previous && previous.folder
				? `Folder ${parsed.folder_index}/${parsed.total} · ${previous.folder} · ComicVine cooldown; this folder will retry`
				: `Folder ${parsed.folder_index}/${parsed.total} · ComicVine cooldown; this folder will retry`;
			if (previous && previous.phase_total) {
				progress.max = Math.max(previous.phase_total, 1);
				progress.value = previous.current || 0;
			} else {
				progress.max = 1;
				progress.value = 0;
			};
			return;
		};

		if (!parsed.phase) {
			row.hidden = true;
			return;
		};

		row.hidden = false;
		progress.max = Math.max(parsed.phase_total || 1, 1);
		progress.value = parsed.current || 0;

		if (parsed.phase === 'matching') {
			label.innerText = (
				`Folder ${parsed.folder_index}/${parsed.total} · ${parsed.folder} · `
				+ `matching title ${parsed.current}/${parsed.phase_total}`
				+ (parsed.title ? ` · ${parsed.title}` : '')
			);
		} else if (parsed.phase === 'importing') {
			label.innerText = (
				`Folder ${parsed.folder_index}/${parsed.total} · ${parsed.folder} · `
				+ `importing volume ${parsed.current}/${parsed.phase_total}`
			);
		} else {
			label.innerText = (
				`Folder ${parsed.folder_index}/${parsed.total} · ${parsed.folder} · `
				+ `shared search across ${parsed.phase_total} parsed titles`
			);
		};
	};

	new MutationObserver(render).observe(status, {
		childList: true,
		characterData: true,
		subtree: true
	});
	render();
};

if (typeof module !== 'undefined' && module.exports)
	module.exports = {parseContinuousWork};

if (typeof document !== 'undefined') {
	if (document.readyState === 'loading')
		document.addEventListener('DOMContentLoaded', installContinuousImportProgressUI);
	else
		installContinuousImportProgressUI();
};
