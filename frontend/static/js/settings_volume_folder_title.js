const VOLUME_FOLDER_SERIES_TOKENS = {
	original: '{series_name}',
	drop: '{series_name_no_article}',
	sort: '{clean_series_name}'
};

function setVolumeFolderSeriesToken(format, style) {
	const target = VOLUME_FOLDER_SERIES_TOKENS[style];
	if (!target)
		return format;

	return Object.values(VOLUME_FOLDER_SERIES_TOKENS).reduce(
		(result, token) => result.split(token).join(target),
		format
	);
};

function detectVolumeFolderSeriesToken(format) {
	const matches = Object.entries(VOLUME_FOLDER_SERIES_TOKENS)
		.filter(([, token]) => format.includes(token));
	return matches.length === 1 ? matches[0][0] : 'custom';
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
			['drop', 'Drop article (Rocketfellers)'],
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
		help.innerText = "A convenience preset for the volume-folder naming token. 'Drop article' uses {series_name_no_article}; 'Move to end' uses {clean_series_name}. Both handle leading 'The' or 'A'. It only affects newly generated folder names; existing and manually customized folders are left alone.";
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
