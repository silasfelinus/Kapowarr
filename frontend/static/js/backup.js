const BackupEls = {
	create: document.querySelector('#backup-now'),
	upload: document.querySelector('#backup-upload'),
	status: document.querySelector('#backup-status'),
	rows: document.querySelector('#backup-rows'),
	empty: document.querySelector('#backup-empty'),
	interval: document.querySelector('#backup-interval'),
	keep: document.querySelector('#backup-keep'),
	save_schedule: document.querySelector('#backup-schedule-save'),
	schedule_summary: document.querySelector('#backup-schedule-summary')
};

let backup_api_key = null;

function formatBackupSize(bytes) {
	if (bytes < 1024)
		return `${bytes} B`;
	if (bytes < 1024 * 1024)
		return `${(bytes / 1024).toFixed(1)} KB`;
	return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

function formatBackupDate(epoch) {
	return new Date(epoch * 1000).toLocaleString();
};

function backupTypeLabel(kind) {
	return kind === 'pre-restore' ? 'Pre-restore' : 'Automatic / manual';
};

function backupButton(label, class_name, handler) {
	const button = document.createElement('button');
	button.type = 'button';
	button.className = `backup-action ${class_name}`.trim();
	button.textContent = label;
	button.onclick = handler;
	return button;
};

async function requireBackupResponse(response) {
	if (!response.ok)
		throw response;
	return response;
};

async function sendBackupPost(endpoint) {
	const response = await sendAPI('POST', endpoint, backup_api_key);
	await requireBackupResponse(response);
	return response.json();
};

async function refreshBackups() {
	if (!backup_api_key)
		return;

	try {
		const json = await fetchAPI('/system/backups', backup_api_key);
		renderBackups(json.result);
	} catch (error) {
		console.error(error);
		BackupEls.status.textContent = 'Could not load backups.';
	};
};

function renderBackups(backups) {
	BackupEls.rows.innerHTML = '';
	const fragment = document.createDocumentFragment();

	backups.forEach(backup => {
		const row = document.createElement('tr');

		const created = document.createElement('td');
		created.textContent = formatBackupDate(backup.created_at);

		const type = document.createElement('td');
		type.textContent = backupTypeLabel(backup.kind);

		const version = document.createElement('td');
		version.textContent = backup.app_version || 'Unknown';
		if (backup.database_version !== null)
			version.title = `Database version ${backup.database_version}`;

		const size = document.createElement('td');
		size.textContent = formatBackupSize(backup.size);

		const actions = document.createElement('td');
		actions.className = 'backup-actions';

		const download = document.createElement('a');
		download.className = 'backup-action secondary';
		download.textContent = 'Download';
		download.href = `${url_base}/api/system/backups/${encodeURIComponent(backup.filename)}?api_key=${encodeURIComponent(backup_api_key)}`;

		const restore = backupButton('Restore', 'secondary', async () => {
			if (!confirm(
				`Restore ${backup.filename}?\n\nKapowarr will first back up the current database, then restart.`
			))
				return;

			disableBackupActions(true);
			BackupEls.status.textContent = 'Validating backup and preparing restore…';
			try {
				const json = await sendBackupPost(
					`/system/backups/${encodeURIComponent(backup.filename)}/restore`
				);
				BackupEls.status.textContent =
					`Restarting… current state was preserved as ${json.result.pre_restore_backup}.`;
				setTimeout(() => window.location.href = `${url_base}/system/status`, 5000);
			} catch (error) {
				console.error(error);
				BackupEls.status.textContent = 'Restore was not staged. The current database was left in place.';
				disableBackupActions(false);
			};
		});

		const remove = backupButton('Delete', 'danger', async () => {
			if (!confirm(`Delete ${backup.filename}?`))
				return;
			try {
				const response = await sendAPI(
					'DELETE',
					`/system/backups/${encodeURIComponent(backup.filename)}`,
					backup_api_key
				);
				await requireBackupResponse(response);
				BackupEls.status.textContent = 'Backup deleted.';
				refreshBackups();
			} catch (error) {
				console.error(error);
				BackupEls.status.textContent = 'Could not delete backup.';
			};
		});

		actions.append(download, restore, remove);
		row.append(created, type, version, size, actions);
		fragment.appendChild(row);
	});

	BackupEls.rows.appendChild(fragment);
	BackupEls.empty.classList.toggle('hidden', backups.length !== 0);
};

function disableBackupActions(disabled) {
	BackupEls.create.disabled = disabled;
	BackupEls.upload.disabled = disabled;
	BackupEls.rows.querySelectorAll('button').forEach(button => {
		button.disabled = disabled;
	});
};

async function createBackupNow() {
	disableBackupActions(true);
	BackupEls.status.textContent = 'Creating a consistent database backup…';
	try {
		const json = await sendBackupPost('/system/backups');
		BackupEls.status.textContent = `Created ${json.result.filename}.`;
		await refreshBackups();
	} catch (error) {
		console.error(error);
		BackupEls.status.textContent = 'Could not create backup.';
	} finally {
		disableBackupActions(false);
	};
};

async function restoreUploadedBackup() {
	const file = BackupEls.upload.files[0];
	if (!file)
		return;

	if (!confirm(
		`Restore ${file.name}?\n\nKapowarr will validate it, preserve the current database, then restart.`
	)) {
		BackupEls.upload.value = '';
		return;
	};

	disableBackupActions(true);
	BackupEls.status.textContent = 'Uploading and validating backup…';
	try {
		const form = new FormData();
		form.append('restore', file);
		const response = await fetch(
			`${url_base}/api/system/backups/restore?api_key=${encodeURIComponent(backup_api_key)}`,
			{method: 'POST', body: form}
		);
		await requireBackupResponse(response);
		const json = await response.json();
		BackupEls.status.textContent =
			`Restarting… current state was preserved as ${json.result.pre_restore_backup}.`;
		setTimeout(() => window.location.href = `${url_base}/system/status`, 5000);
	} catch (error) {
		console.error(error);
		BackupEls.status.textContent = 'Uploaded backup was rejected. The current database was left in place.';
		disableBackupActions(false);
		BackupEls.upload.value = '';
	};
};

function describeSchedule(interval, keep) {
	const every = interval === 1 ? 'every day' : `every ${interval} days`;
	const kept = keep === 1 ? '1 backup' : `${keep} backups`;
	return `Kapowarr automatically creates a database backup ${every} and keeps the newest ${kept}.`;
};

async function loadSchedule() {
	if (!backup_api_key)
		return;

	try {
		const json = await fetchAPI('/settings', backup_api_key);
		const interval = json.result.backup_interval_days,
			keep = json.result.backup_keep_count;
		BackupEls.interval.value = interval;
		BackupEls.keep.value = keep;
		BackupEls.schedule_summary.textContent = describeSchedule(interval, keep);
	} catch (error) {
		console.error(error);
	};
};

async function saveSchedule() {
	if (!backup_api_key)
		return;

	const interval = Number(BackupEls.interval.value),
		keep = Number(BackupEls.keep.value);

	// Checked here as well as by the backend so a typo produces a sentence
	// rather than a rejected request the user has to interpret.
	if (!Number.isInteger(interval) || interval < 1 || interval > 365) {
		BackupEls.status.textContent = 'Run every must be between 1 and 365 days.';
		return;
	};
	if (!Number.isInteger(keep) || keep < 1 || keep > 100) {
		BackupEls.status.textContent = 'Backups to keep must be between 1 and 100.';
		return;
	};

	BackupEls.save_schedule.disabled = true;
	BackupEls.status.textContent = 'Saving schedule…';
	try {
		const response = await sendAPI('PUT', '/settings', backup_api_key, {}, {
			backup_interval_days: interval,
			backup_keep_count: keep
		});
		await requireBackupResponse(response);
		BackupEls.schedule_summary.textContent = describeSchedule(interval, keep);
		BackupEls.status.textContent = 'Schedule saved.';
		// Lowering the count takes effect at the next backup, not retroactively,
		// so the list on screen is still accurate -- but reload it anyway in case
		// a backup ran while this page was open.
		refreshBackups();
	} catch (error) {
		console.error(error);
		BackupEls.status.textContent = 'Could not save the schedule.';
		loadSchedule();
	} finally {
		BackupEls.save_schedule.disabled = false;
	};
};

BackupEls.create.onclick = createBackupNow;
BackupEls.upload.onchange = restoreUploadedBackup;
BackupEls.save_schedule.onclick = saveSchedule;

usingApiKey()
.then(api_key => {
	backup_api_key = api_key;
	loadSchedule();
	refreshBackups();
});
