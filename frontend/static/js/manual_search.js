function manualSearchHeading(volume_title, issue_title=null) {
	const target = issue_title
		? `${volume_title} — ${issue_title}`
		: volume_title;
	return `Manual search — ${target}`;
};

function finishManualSearchAction(button, fail_reason=null) {
	const img = button.querySelector('img');
	img.classList.remove('spinning');

	if (fail_reason === null) {
		img.src = `${url_base}/static/img/check.svg`;
		button.classList.remove('error');
		button.title = 'Added to download queue';
		return;
	};

	img.src = `${url_base}/static/img/download.svg`;
	button.classList.add('error');
	button.title = fail_reason;
};

if (typeof module !== 'undefined') {
	module.exports = {
		manualSearchHeading,
		finishManualSearchAction
	};
};
