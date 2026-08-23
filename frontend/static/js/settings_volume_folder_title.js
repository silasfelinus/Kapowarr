function setVolumeFolderSeriesToken(format, style) {
	if (style === 'sort')
		return format.split('{series_name}').join('{clean_series_name}');
	if (style === 'original')
		return format.split('{clean_series_name}').join('{series_name}');
	return format;
};

function detectVolumeFolderSeriesToken(format) {
	const hasOriginal = format.includes('{series_name}');
	const hasClean = format.includes('{clean_series_name}');
	if (hasOriginal && !hasClean)
		return 'original';
	if (hasClean && !hasOriginal)
		return 'sort';
	return 'custom';
};

if (typeof module !== 'undefined')
	module.exports = {
		setVolumeFolderSeriesToken,
		detectVolumeFolderSeriesToken
	};

if (typeof document !== 'undefined') {
	const namingInput = document.querySelector('#volume-folder-naming-input');
	if (namingInput) {
		const row = document.createElement('tr');
		const heading = document.createElement('th');
		const label = document.createElement('label');
		label.htmlFor = 'volume-folder-title-style-input';
		label.innerText = 'Leading Article in Folder Title';
		heading.appendChild(label);

		const cell = document.createElement('td');
		const select = document.createElement('select');
		select.id = 'volume-folder-title-style-input';
		[
			['original', 'Keep at front (The Rocketfellers)'],
			['sort', 'Move to end (Rocketfellers, The)'],
			['custom', 'Custom naming tokens']
		].forEach(([value, text]) => {
			const option = document.createElement('option');
			option.value = value;
			option.innerText = text;
			if (value === 'custom')
				option.disabled = true;
			select.appendChild(option);
		});

		const help = document.createElement('p');
		help.innerText = "A convenience preset for the volume-folder naming token. 'Move to end' uses {clean_series_name}, which sorts leading 'The' or 'A' after the title. It only affects newly generated folder names; existing and manually customized folders are left alone.";
		cell.appendChild(select);
		cell.appendChild(help);
		row.appendChild(heading);
		row.appendChild(cell);
		namingInput.closest('tr').insertAdjacentElement('afterend', row);

		const syncSelect = format => {
			select.value = detectVolumeFolderSeriesToken(format);
		};

		select.onchange = () => {
			if (select.value === 'custom')
				return;
			namingInput.value = setVolumeFolderSeriesToken(
				namingInput.value,
				select.value
			);
			namingInput.dispatchEvent(new Event('input'));
		};
		namingInput.addEventListener('input', () => syncSelect(namingInput.value));
		syncSelect(namingInput.value);

		usingApiKey()
		.then(api_key => fetchAPI('/settings', api_key))
		.then(json => syncSelect(json.result.volume_folder_naming))
		.catch(() => syncSelect(namingInput.value));
	};
};
