const reader = {
	stage: document.querySelector('#reader-stage'),
	page: document.querySelector('#reader-page'),
	document: document.querySelector('#reader-document'),
	status: document.querySelector('#reader-status'),
	title: document.querySelector('#reader-title'),
	page_count: document.querySelector('#reader-page-count'),
	back: document.querySelector('#reader-back'),
	previous: document.querySelector('#reader-prev'),
	next: document.querySelector('#reader-next'),
	fit: document.querySelector('#reader-fit'),
	fit_label: document.querySelector('.reader-fit-label'),
	fullscreen: document.querySelector('#reader-fullscreen'),
	fullscreen_label: document.querySelector('.reader-fullscreen-label')
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

function readerDocumentUrl() {
	return `${url_base}/api/reader/issues/${issue_id}/document?api_key=${encodeURIComponent(api_key)}`;
};

function fullscreenElement() {
	return document.fullscreenElement || document.webkitFullscreenElement || null;
};

function fullscreenSupported() {
	const root = document.documentElement;
	return Boolean(root.requestFullscreen || root.webkitRequestFullscreen);
};

function syncFullscreenControl() {
	if (!fullscreenSupported()) {
		reader.fullscreen.classList.add('hidden');
		return;
	};

	const active = Boolean(fullscreenElement());
	reader.fullscreen.classList.remove('hidden');
	reader.fullscreen.title = active ? 'Exit fullscreen' : 'Enter fullscreen';
	reader.fullscreen.setAttribute(
		'aria-label',
		active ? 'Exit fullscreen' : 'Enter fullscreen'
	);
	reader.fullscreen_label.innerText = active ? 'Exit' : 'Fullscreen';
};

async function toggleFullscreen() {
	try {
		if (fullscreenElement()) {
			if (document.exitFullscreen)
				await document.exitFullscreen();
			else if (document.webkitExitFullscreen)
				await document.webkitExitFullscreen();
			return;
		};

		const root = document.documentElement;
		if (root.requestFullscreen)
			await root.requestFullscreen({navigationUI: 'hide'});
		else if (root.webkitRequestFullscreen)
			await root.webkitRequestFullscreen();
	} catch (error) {
		console.error('Fullscreen request failed', error);
	};
};

function updateControls() {
	const pages_mode = manifest && manifest.reader_mode === 'pages';
	reader.previous.disabled = !pages_mode || page_index <= 0;
	reader.next.disabled = !pages_mode || page_index >= manifest.page_count - 1;
	reader.page_count.innerText = !manifest
		? ''
		: manifest.reader_mode === 'pdf'
			? 'PDF'
			: manifest.page_count
				? `${page_index + 1} / ${manifest.page_count}`
				: '';
};

function preloadNextPage() {
	if (
		!manifest
		|| manifest.reader_mode !== 'pages'
		|| page_index >= manifest.page_count - 1
	)
		return;
	const preload = new Image();
	preload.src = readerPageUrl(page_index + 1);
};

function showPage(index) {
	if (
		!manifest
		|| manifest.reader_mode !== 'pages'
		|| manifest.page_count === 0
	)
		return;
	page_index = Math.max(0, Math.min(index, manifest.page_count - 1));
	reader.stage.classList.remove('pdf-mode');
	reader.document.classList.add('hidden');
	reader.fit_label.classList.remove('hidden');
	reader.previous.classList.remove('hidden');
	reader.next.classList.remove('hidden');
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

function showPdf() {
	reader.stage.classList.add('pdf-mode');
	reader.page.classList.add('hidden');
	reader.status.classList.add('hidden');
	reader.previous.classList.add('hidden');
	reader.next.classList.add('hidden');
	reader.fit_label.classList.add('hidden');
	reader.document.classList.remove('hidden');
	reader.document.src = readerDocumentUrl();
	updateControls();
};

function previousPage() {
	if (manifest && manifest.reader_mode === 'pages' && page_index > 0)
		showPage(page_index - 1);
};

function nextPage() {
	if (
		manifest
		&& manifest.reader_mode === 'pages'
		&& page_index < manifest.page_count - 1
	)
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
				'This issue has no content the built-in reader can open yet. '
				+ 'CBZ/ZIP, CBR/RAR, loose images, and PDF are supported.';
			updateControls();
			return;
		};

		if (manifest.reader_mode === 'pdf') {
			showPdf();
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
reader.fullscreen.onclick = toggleFullscreen;

document.addEventListener('fullscreenchange', syncFullscreenControl);
document.addEventListener('webkitfullscreenchange', syncFullscreenControl);

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
	else if (event.key === 'Escape' && !fullscreenElement())
		reader.back.click();
});

syncFullscreenControl();
setFit(reader.fit.value);
loadReader();
