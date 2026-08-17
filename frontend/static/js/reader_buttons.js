// Add a reader action to downloaded issue rows without changing the existing
// issue-table builder. The reader itself decides whether the linked files are
// currently supported (CBZ/ZIP/loose images in the first slice).
function addReaderButtons() {
	const issues = document.querySelector('#issues-list');
	if (issues === null)
		return;

	issues.querySelectorAll('tr.issue-entry').forEach(entry => {
		const actions = entry.querySelector('.action-column');
		if (
			actions === null
			|| actions.querySelector('.reader-button') !== null
			|| !entry.querySelector('.issue-status')?.classList.contains('success')
		)
			return;

		const button = document.createElement('button'),
			icon = document.createElement('img');
		button.type = 'button';
		button.classList.add('reader-button');
		button.title = 'Read this issue';
		button.setAttribute('aria-label', 'Read this issue');
		icon.src = `${url_base}/static/img/files.svg`;
		icon.alt = '';
		button.appendChild(icon);
		button.onclick = () => {
			window.location.href = `${url_base}/reader/${entry.dataset.id}`;
		};
		actions.appendChild(button);
	});
};

const issue_list = document.querySelector('#issues-list');
if (issue_list !== null) {
	new MutationObserver(addReaderButtons).observe(issue_list, {
		childList: true,
		subtree: true,
		attributes: true,
		attributeFilter: ['class']
	});
	addReaderButtons();
};
