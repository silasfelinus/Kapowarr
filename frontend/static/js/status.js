const StatEls = {
	health_section: document.querySelector('#health-section'),
	health_list: document.querySelector('#health-list'),
	comicvine_rows: document.querySelector('#comicvine-rows'),
	comicvine_empty: document.querySelector('#comicvine-empty'),
	version: document.querySelector('#version'),
	python_version: document.querySelector('#python-version'),
	database_version: document.querySelector('#database-version'),
	database_location: document.querySelector('#database-location'),
	data_folder: document.querySelector('#data-folder'),
	os: document.querySelector('#os'),
	runs_64bit: document.querySelector('#runs-64bit'),
	buttons: {
		copy: document.querySelector('#copy-about'),
		restart: document.querySelector('#restart-button'),
		shutdown: document.querySelector('#shutdown-button')
	}
};

const about_table = `
| Key| Value |
|--------|--------|
| Kapowarr version | {k_version} |
| Python version | {p_version} |
| Database version | {d_version} |
| Database location | {d_loc} |
| Data folder | {folder} |
| OS | {os} |
| Can run 64bit | {runs_64bit} |

`;

// code run on load

usingApiKey()
.then(api_key => {
	fillHealth(api_key);
	fillComicVineActivity(api_key);

	fetchAPI('/system/about', api_key)
	.then(json => {
		StatEls.version.innerText = json.result.version;
		StatEls.python_version.innerText = json.result.python_version;
		StatEls.database_version.innerText = json.result.database_version;
		StatEls.database_location.innerText = json.result.database_location;
		StatEls.data_folder.innerText = json.result.data_folder;
		StatEls.os.innerText = json.result.os;
		StatEls.runs_64bit.innerText = json.result.runs_64bit ? 'Yes' : 'No';
		
		StatEls.buttons.copy.onclick = e => {
			copy(about_table
				.replace('{k_version}', json.result.version)
				.replace('{p_version}', json.result.python_version)
				.replace('{d_version}', json.result.database_version)
				.replace('{d_loc}', json.result.database_location)
				.replace('{folder}', json.result.data_folder)
				.replace('{os}', json.result.os)
				.replace('{runs_64bit}', json.result.runs_64bit)
			);
		};
	});
	StatEls.buttons.restart.onclick =
		e => {
			StatEls.buttons.restart.innerText = 'Restarting';
			socket.disconnect();
			sendAPI('POST', '/system/power/restart', api_key);
			setTimeout(() => window.location.reload(), 1000);
		};
	StatEls.buttons.shutdown.onclick =
		e => {
			StatEls.buttons.shutdown.innerText = 'Shutting down';
			socket.disconnect();
			sendAPI('POST', '/system/power/shutdown', api_key);
			setTimeout(() => window.location.reload(), 1000);
		};
});


function comicvineOutcomeSummary(entry) {
	const parts = [];
	if (entry.success)
		parts.push(`${entry.success} ok`);
	if (entry.rate_limit)
		parts.push(`${entry.rate_limit} rate limited`);
	if (entry.not_found)
		parts.push(`${entry.not_found} not found`);
	if (entry.invalid_key)
		parts.push(`${entry.invalid_key} invalid key`);
	if (entry.other_error)
		parts.push(`${entry.other_error} failed`);
	return parts.join(' · ') || 'no outcome recorded yet';
};

function fillComicVineActivity(api_key) {
	fetchAPI('/system/comicvine-activity', api_key)
	.then(json => {
		const operations = json.result.operations || [];
		StatEls.comicvine_rows.innerHTML = '';
		StatEls.comicvine_empty.classList.toggle('hidden', operations.length !== 0);

		const fragment = document.createDocumentFragment();
		operations.forEach(entry => {
			const row = document.createElement('tr');

			const name = document.createElement('th');
			name.textContent = entry.operation;

			const count = document.createElement('td');
			count.textContent = `${entry.operations} · ${comicvineOutcomeSummary(entry)}`;

			row.append(name, count);
			fragment.appendChild(row);
		});
		StatEls.comicvine_rows.appendChild(fragment);
	})
	.catch(e => {
		console.error(e);
		StatEls.comicvine_empty.classList.remove('hidden');
	});
};

function fillHealth(api_key) {
	fetchAPI('/system/health', api_key)
	.then(json => {
		const warnings = json.result;
		StatEls.health_list.innerHTML = '';

		if (!warnings.length) {
			StatEls.health_section.classList.add('hidden');
			return;
		}

		warnings.forEach(w => {
			const li = document.createElement('li');

			const source = document.createElement('strong');
			source.textContent = w.source + ': ';
			li.appendChild(source);

			const message = document.createElement('span');
			message.textContent = w.message;
			li.appendChild(message);

			StatEls.health_list.appendChild(li);
		});

		StatEls.health_section.classList.remove('hidden');
	})
	.catch(e => {
		// Leave the section hidden -- a failed health check itself
		// shouldn't leave a broken/half-built UI on the Status page.
	});
}


function copy(text) {
	range = document.createRange();
	selection = document.getSelection();

	let container = document.createElement("span");
	container.textContent = text;
	container.ariaHidden = true;
	container.style.all = "unset";
	container.style.position = "fixed";
	container.style.top = 0;
	container.style.clip = "rect(0, 0, 0, 0)";
	container.style.whiteSpace = "pre";
	container.style.userSelect = "text";
	
	document.body.appendChild(container);
	
	try {
		range.selectNodeContents(container);
		selection.addRange(range);
		document.execCommand("copy");
		StatEls.buttons.copy.innerText = 'Copied';
	}
	catch (err) {
		// Failed
		StatEls.buttons.copy.innerText = 'Failed';
	}
	finally {
		selection.removeAllRanges();
		document.body.removeChild(container);
	}
}
