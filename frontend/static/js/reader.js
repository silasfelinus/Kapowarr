const reader = {
	stage: document.querySelector('#reader-stage'),
	page: document.querySelector('#reader-page'),
	status: document.querySelector('#reader-status'),
	title: document.querySelector('#reader-title'),
	page_count: document.querySelector('#reader-page-count'),
	back: document.querySelector('#reader-back'),
	previous: document.querySelector('#reader-prev'),
	next: document.querySelector('#reader-next'),
	fit: document.querySelector('#reader-fit')
};

const url_base = document.querySelector('#url_base').dataset.value;
const issue_id = parseInt(document.body.dataset.issueId);
let api_key = null;
let manifest = null;
let page_index = 0;
let touch_start = null;

function getStoredAuth() {
	try {
		return JSON.parse(localStorage.getItem('kapowarr')) || {};
	} catch (_) {
		return {};
	};
};

async function getApiKey() {
	const auth = getStoredAuth();
	if (
		auth.api_key
		&& auth.last_login
		&& auth.last_login >= (Date.now() / 1000 - 86400)
	) {
		return auth.api_key;
	};

	const response = await fetch(`${url_base}/api/auth`, {
		method: 'POST',
		headers: {'Content-Type': 'application/json'},
		body: '{}'
	});
	if (response.status === 401) {
		window.location.href = `${url_base}/login?redirect=${window.location.pathname}`;
		return null;
	};
	if (!response.ok)
		throw new Error(`Authentication failed (${response.status})`);

	const json = await response.json();
	const updated = {
		...auth,
		api_key: json.result.api_key,
		last_login: Date.now() / 1000
	};
	localStorage.setItem('kapowarr', JSON.stringify(updated));
	return updated.api_key;
};

function readerPageUrl(index) {
	return `${url_base}/api/reader/issues/${issue_id}/pages/${index}?api_key=${encodeURIComponent(api_key)}`;
};

function updateControls() {
	reader.previous.disabled = page_index <= 0;
	reader.next.disabled = !manifest || page_index >= manifest.page_count - 1;
	reader.page_count.innerText = manifest
		? `${page_index + 1} / ${manifest.page_count}`
		: '';
};

function preloadNextPage() {
	if (!manifest || page_index >= manifest.page_count - 1)
		return;
	const preload = new Image();
	preload.src = readerPageUrl(page_index + 1);
};

function showPage(index) {
	if (!manifest || manifest.page_count === 0)
		return;
	page_index = Math.max(0, Math.min(index, manifest.page_count - 1));
	reader.status.innerText = 'Turning the page…';
	reader.status.classList.remove('hidden');
	reader.page.classList.add('hidden');
	updateControls();

	reader.page.onload = () => {
		reader.status.classList.add('hidden');
		reader.page.classList.remove('hidden');
		preloadNextPage();
	};
	reader.page.onerror = () => {
		reader.page.classList.add('hidden');
		reader.status.innerText = 'That page could not be opened.';
		reader.status.classList.remove('hidden');
	};
	reader.page.src = readerPageUrl(page_index);
};

function previousPage() {
	if (page_index > 0)
		showPage(page_index - 1);
};

function nextPage() {
	if (manifest && page_index < manifest.page_count - 1)
		showPage(page_index + 1);
};

function setFit(mode) {
	reader.stage.classList.toggle('fit-page', mode === 'page');
	reader.stage.classList.toggle('fit-width', mode === 'width');
};

async function loadReader() {
	try {
		api_key = await getApiKey();
		if (!api_key)
			return;

		const response = await fetch(
			`${url_base}/api/reader/issues/${issue_id}?api_key=${encodeURIComponent(api_key)}`
		);
		if (!response.ok)
			throw new Error(`Reader manifest failed (${response.status})`);

		const json = await response.json();
		manifest = json.result;
		reader.title.innerText = `${manifest.volume_title} #${manifest.issue_number}`;
		reader.back.onclick = () => {
			window.location.href = `${url_base}/volumes/${manifest.volume_id}`;
		};

		if (!manifest.readable) {
			reader.status.innerText =
				'This issue has no pages the built-in reader can open yet. '
				+ 'CBZ/ZIP and loose images are supported; CBR/RAR and PDF are next.';
			updateControls();
			return;
		};

		showPage(0);
	} catch (error) {
		console.error(error);
		reader.status.innerText = 'The comic reader could not open this issue.';
	};
};

reader.back.onclick = () => history.back();
reader.previous.onclick = previousPage;
reader.next.onclick = nextPage;
reader.fit.onchange = () => setFit(reader.fit.value);

reader.stage.addEventListener('touchstart', event => {
	const touch = event.changedTouches[0];
	touch_start = {x: touch.clientX, y: touch.clientY};
}, {passive: true});

reader.stage.addEventListener('touchend', event => {
	if (touch_start === null)
		return;
	const touch = event.changedTouches[0],
		delta_x = touch.clientX - touch_start.x,
		delta_y = touch.clientY - touch_start.y;
	touch_start = null;
	if (Math.abs(delta_x) < 50 || Math.abs(delta_x) <= Math.abs(delta_y))
		return;
	if (delta_x < 0)
		nextPage();
	else
		previousPage();
}, {passive: true});

document.addEventListener('keydown', event => {
	if (event.key === 'ArrowLeft')
		previousPage();
	else if (event.key === 'ArrowRight' || event.key === ' ')
		nextPage();
	else if (event.key === 'Escape')
		reader.back.click();
});

setFit(reader.fit.value);
loadReader();
