const TaskEls = {
	pre_build: {
		task: document.querySelector('.pre-build-els .task-entry'),
		history: document.querySelector('.pre-build-els .history-entry')
	},
	intervals: document.querySelector('#task-intervals'),
	history: document.querySelector('#history'),
	buttons: {
		refresh: document.querySelector('#refresh-button'),
		clear: document.querySelector('#clear-button')
	}
};

//
// Task planning
//
// Everything on this page used to be rounded to whole hours, so the three
// tasks that run more often than that -- Watched Folder Import and Feed Sync
// every 15 minutes, and any task due in the next half hour -- all read
// "0 hours", "0 hours ago", "in 0 hours". Zero is the one number that says
// the schedule is broken, and it was the number every sub-hourly task showed.
function describeDuration(seconds) {
	const total = Math.max(0, Math.round(seconds));
	if (total < 60) return `${total} second${total === 1 ? '' : 's'}`;

	// Each unit covers up to the point the next one starts, so a quarter
	// hour reads as 15 minutes rather than rounding away to nothing.
	const units = [
		['minute', 60, 3600],
		['hour', 3600, 86400],
		['day', 86400, Infinity]
	];
	for (const [unit, size, upto] of units) {
		if (total >= upto) continue;
		const count = Math.round(total / size);
		return `${count} ${unit}${count === 1 ? '' : 's'}`;
	}
};

function convertInterval(interval) {
	return describeDuration(interval);
};

function convertTime(epoch, future) {
	if (epoch === null) return 'Never';
	const delta = Math.abs(Date.now() / 1000 - epoch);
	// Under a minute either way, naming a number is worse than not: a task
	// that just ran did not run "0 minutes ago", it ran just now.
	if (delta < 60) return future ? 'any moment' : 'just now';
	if (future) return `in ${describeDuration(delta)}`;
	else return `${describeDuration(delta)} ago`;
};

function fillPlanning(api_key) {
	fetchAPI('/system/tasks/planning', api_key)
	.then(json => {
		TaskEls.intervals.innerHTML = '';
		json.result.forEach(task => {
			const entry = TaskEls.pre_build.task.cloneNode(true);
			entry.dataset.task_name = task.task_name;

			entry.querySelector('.name-column').innerText = task.display_name;
			entry.querySelector('.interval-column').innerText =
				convertInterval(task.interval);
			entry.querySelector('.prev-column').innerText =
				convertTime(task.last_run, false);
			entry.querySelector('.next-column').innerText =
				convertTime(task.next_run, true);
			entry.querySelector('button').onclick =
				e => sendAPI('POST', '/system/tasks', api_key, {}, {'cmd': task.task_name})

			TaskEls.intervals.appendChild(entry);
		});
		mapButtons();
	});
};

//
// Task history
//
function fillHistory(api_key) {
	fetchAPI('/system/tasks/history', api_key)
	.then(json => {
		TaskEls.history.innerHTML = '';
		json.result.forEach(obj => {
			const entry = TaskEls.pre_build.history.cloneNode(true);

			entry.querySelector('.title-column').innerText = obj.display_title;

			var d = new Date(obj.run_at * 1000);
			var formatted_date = d.toLocaleString('en-CA').slice(0,10) + ' ' + d.toTimeString().slice(0,5)
			entry.querySelector('.date-column').innerText = formatted_date;

			TaskEls.history.appendChild(entry);
		});
	});
};

function clearHistory(api_key) {
	sendAPI('DELETE', '/system/tasks/history', api_key)
	TaskEls.history.innerHTML = '';
};

// code run on load

usingApiKey()
.then(api_key => {
	fillHistory(api_key);
	fillPlanning(api_key);
	TaskEls.buttons.refresh.onclick = e => fillHistory(api_key);
	TaskEls.buttons.clear.onclick = e => clearHistory(api_key);
	document.addEventListener('kapowarr:task-ended', () => {
		fillHistory(api_key);
		fillPlanning(api_key);
	});
});
