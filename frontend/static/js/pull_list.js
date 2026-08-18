const PullListEls = {
	table: document.querySelector('#pull-list'),
	empty_message: document.querySelector('#pull-list-empty-message'),
	buttons: {
		refresh: document.querySelector('#refresh-button'),
		check: document.querySelector('#check-button'),
		reading_lists: document.querySelector('#reading-lists-button')
	},
	entry: document.querySelector('.pre-build-els .pull-list-entry')
};

function fillList(api_key) {
	fetchAPI('/pulllist', api_key)
	.then(json => {
		PullListEls.table.innerHTML = '';

		PullListEls.empty_message.classList.toggle(
			'hidden', json.result.length > 0
		);

		json.result.forEach(obj => {
			const entry = PullListEls.entry.cloneNode(true);

			const volume_link = entry.querySelector('.volume-column a');
			volume_link.innerText = obj.volume_title;
			volume_link.href = `${url_base}/volumes/${obj.volume_id}`;

			entry.querySelector('.issue-column').innerText =
				obj.issue_number !== null ? `#${obj.issue_number}` : '';

			const source_link = entry.querySelector('.source-column a');
			source_link.innerText = obj.source;
			source_link.href = obj.link;

			const d = new Date(obj.checked_at * 1000);
			const formatted_date =
				d.toLocaleString('en-CA').slice(0, 10)
				+ ' '
				+ d.toTimeString().slice(0, 5);
			entry.querySelector('.checked-column').innerText = formatted_date;

			PullListEls.table.appendChild(entry);
		});
	});
};

function pollUntilCheckFinished(api_key) {
	fetchAPI('/system/tasks', api_key)
	.then(json => {
		const still_running = json.result.some(
			task => task.action === 'weekly_pull_list_check'
		);

		if (still_running) {
			setTimeout(() => pollUntilCheckFinished(api_key), 1500);
			return;
		};

		fillList(api_key);
		PullListEls.buttons.check.disabled = false;
		PullListEls.buttons.check.querySelector('img').classList.remove('spinning');
	});
};

function checkNow(api_key) {
	PullListEls.buttons.check.disabled = true;
	PullListEls.buttons.check.querySelector('img').classList.add('spinning');

	sendAPI(
		'POST', '/system/tasks', api_key, {},
		{'cmd': 'weekly_pull_list_check'}
	)
	.then(() => pollUntilCheckFinished(api_key));
};

// code run on load
usingApiKey()
.then(api_key => {
	fillList(api_key);
	PullListEls.buttons.refresh.onclick = e => fillList(api_key);
	PullListEls.buttons.check.onclick = e => checkNow(api_key);
	PullListEls.buttons.reading_lists.onclick = e => {
		window.location.href = `${url_base}/activity/reading-lists`;
	};
});
