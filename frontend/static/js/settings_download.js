function fillSettings(api_key) {
	Promise.all([
		fetchAPI('/settings', api_key),
		fetchAPI('/settings/acquisition', api_key)
	])
	.then(([settingsJson, acquisitionJson]) => {
		const settings = settingsJson.result;
		const acquisition = acquisitionJson.result;
		document.querySelector('#download-folder-input').value = settings.download_folder;
		document.querySelector('#concurrent-direct-downloads-input').value = settings.concurrent_direct_downloads;
		document.querySelector('#download-timeout-input').value = ((settings.failing_download_timeout || 0) / 60) || '';
		document.querySelector('#seeding-handling-input').value = settings.seeding_handling;
		document.querySelector('#delete-downloads-input').checked = settings.delete_completed_downloads;
		fillOrderedPreference('#pref-table', settings.service_preference);
		fillOrderedPreference(
			'#source-pref-table',
			acquisition.acquisition_source_preference,
			{direct: 'Direct', torrent: 'Torrent', usenet: 'Usenet'}
		);
		document.querySelector('#getcomics-quality-input').value = acquisition.getcomics_quality_preference;
		document.querySelector('#pack-preference-input').value = acquisition.pack_preference;
	});
};

function saveSettings(api_key) {
	document.querySelector("#save-button p").innerText = 'Saving';
	document.querySelector('#download-folder-input').classList.remove('error-input');
	const settingsData = {
		'download_folder': document.querySelector('#download-folder-input').value,
		'concurrent_direct_downloads': parseInt(document.querySelector('#concurrent-direct-downloads-input').value),
		'failing_download_timeout': parseInt(document.querySelector('#download-timeout-input').value || 0) * 60,
		'seeding_handling': document.querySelector('#seeding-handling-input').value,
		'delete_completed_downloads': document.querySelector('#delete-downloads-input').checked,
		'service_preference': [...document.querySelectorAll('#pref-table select')].map(e => e.value)
	};
	const acquisitionData = {
		'acquisition_source_preference': [...document.querySelectorAll('#source-pref-table select')].map(e => e.value),
		'getcomics_quality_preference': document.querySelector('#getcomics-quality-input').value,
		'pack_preference': document.querySelector('#pack-preference-input').value
	};
	Promise.all([
		sendAPI('PUT', '/settings', api_key, {}, settingsData),
		sendAPI('PUT', '/settings/acquisition', api_key, {}, acquisitionData)
	])
	.then(() =>
		document.querySelector("#save-button p").innerText = 'Saved'
	)
	.catch(e => {
		document.querySelector("#save-button p").innerText = 'Failed';
		e.json().then(e => {
			if (
				e.error === "InvalidKeyValue"
				&& e.result.key === "download_folder"
				||
				e.error === "FolderNotFound"
			)
				document.querySelector('#download-folder-input').classList.add('error-input');

			else
				console.log(e);
		});
	});
};

//
// Empty download folder
//
function emptyFolder(api_key) {
	sendAPI('DELETE', '/activity/folder', api_key)
	.then(response => {
		document.querySelector('#empty-download-folder').innerText = 'Done';
	});
};

//
// Ordered preferences
//
function fillOrderedPreference(tableSelector, pref, labels = {}) {
	const selects = document.querySelectorAll(`${tableSelector} select`);
	for (let i = 0; i < pref.length; i++) {
		const current = pref[i];
		const select = selects[i];
		select.onchange = e => updatePrefOrder(e, tableSelector);
		pref.forEach(option => {
			const entry = document.createElement('option');
			entry.value = option;
			entry.innerText = labels[option] || option;
			if (option === current)
				entry.selected = true;
			select.appendChild(entry);
		});
	};
};

function updatePrefOrder(e, tableSelector) {
	const otherSelects = document.querySelectorAll(
		`${tableSelector} select:not([data-place="${e.target.dataset.place}"])`
	);
	for (let i = 0; i < otherSelects.length; i++) {
		if (otherSelects[i].value === e.target.value) {
			const firstSelect = document.querySelector(`${tableSelector} select`);
			const allValues = [...firstSelect.options].map(option => option.value);
			const usedValues = new Set(
				[...document.querySelectorAll(`${tableSelector} select`)].map(select => select.value)
			);
			const openValue = allValues.filter(value => !usedValues.has(value))[0];
			otherSelects[i].value = openValue;
			break;
		};
	};
};

// code run on load
usingApiKey()
.then(api_key => {
	fillSettings(api_key);

	document.querySelector('#save-button').onclick = e => saveSettings(api_key);
	document.querySelector('#empty-download-folder').onclick = e => emptyFolder(api_key);
});
