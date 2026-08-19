const BackupEls = {
	create: document.querySelector('#backup-now'),
	upload: document.querySelector('#backup-upload'),
	status: document.querySelector('#backup-status'),
	rows: document.querySelector('#backup-rows'),
	empty: document.querySelector('#backup-empty')
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

BackupEls.create.onclick = createBackupNow;
BackupEls.upload.onchange = restoreUploadedBackup;

usingApiKey()
.then(api_key => {
	backup_api_key = api_key;
	refreshBackups();
});
