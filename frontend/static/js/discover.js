const DiscoverEls = {
	table: document.querySelector('#discover-list'),
	empty_message: document.querySelector('#discover-empty-message'),
	loading_message: document.querySelector('#discover-loading-message'),
	mode_description: document.querySelector('#discover-mode-description'),
	buttons: {
		refresh: document.querySelector('#refresh-button'),
		recent: document.querySelector('#recent-button'),
		for_you: document.querySelector('#for-you-button')
	},
	page_turner_container: document.querySelector('#discover-page-turner'),
	page_turner: {
		previous: document.querySelector('#previous-page'),
		next: document.querySelector('#next-page'),
		number: document.querySelector('#page-number')
	},
	entry: document.querySelector('.pre-build-els .discover-entry')
};

var page = 1;
var max_page = 1;
var mode = 'recent';

function updateModeControls() {
	DiscoverEls.buttons.recent.classList.toggle('active', mode === 'recent');
	DiscoverEls.buttons.for_you.classList.toggle('active', mode === 'for-you');
	DiscoverEls.page_turner_container.classList.toggle('hidden', mode === 'for-you');

	if (mode === 'for-you') {
		DiscoverEls.mode_description.innerText =
			'For You ranks recent releases using explainable title and franchise overlap with comics already in your library.';
	} else {
		DiscoverEls.mode_description.innerText =
			'Recent releases from GetComics that are not already represented in your library.';
	};
};

function updatePageTurner() {
	DiscoverEls.page_turner.number.innerText = `Page ${page} / ${max_page}`;
	DiscoverEls.page_turner.previous.disabled = page <= 1;
	DiscoverEls.page_turner.next.disabled = page >= max_page;
};

function fillList(api_key) {
	hide([DiscoverEls.empty_message], [DiscoverEls.loading_message]);
	DiscoverEls.buttons.refresh.querySelector('img').classList.add('spinning');
	updateModeControls();

	const params = {mode: mode};
	if (mode === 'recent') params.page = page;

	fetchAPI('/discover', api_key, params)
	.then(json => {
		page = json.result.page;
		max_page = json.result.max_page;
		updatePageTurner();

		DiscoverEls.table.innerHTML = '';

		// Discover is for finding things to add. The backend keeps library
		// matches in the payload because recommendation scoring also uses that
		// provenance, but the Recent browser should not spend rows telling us
		// about volumes we already own.
		const visible_items = json.result.items.filter(obj => obj.volume_id === null);

		DiscoverEls.empty_message.classList.toggle(
			'hidden', visible_items.length > 0
		);
		if (visible_items.length === 0) {
			DiscoverEls.empty_message.innerText = mode === 'for-you'
				? 'No strong recommendations found in the recent release window yet.'
				: 'No new-to-your-library releases found on this GetComics page.';
		};

		visible_items.forEach(obj => {
			const entry = DiscoverEls.entry.cloneNode(true);
			const series = obj.series || obj.display_title;

			const cover_column = entry.querySelector('.cover-column');
			const img = cover_column.querySelector('img');
			if (obj.cover) {
				img.src = obj.cover;
				img.alt = series;
				img.onerror = e => cover_column.classList.add('no-cover');
			} else {
				cover_column.classList.add('no-cover');
			};

			const title_link = entry.querySelector('.title-column a');
			title_link.innerText = obj.year !== null ? `${series} (${obj.year})` : series;
			title_link.title = `${obj.display_title} - open on GetComics`;
			title_link.href = obj.link;

			const reason_column = entry.querySelector('.reason-column');
			if (obj.recommendation_reason) {
				const reason_link = document.createElement('a');
				reason_link.innerText = obj.recommendation_reason;
				reason_link.href = `${url_base}/volumes/${obj.related_volume_id}`;
				reason_column.appendChild(reason_link);
			} else {
				reason_column.innerText = mode === 'recent' ? 'Recent release' : '';
			};

			const status_link = entry.querySelector('.status-column a');
			status_link.innerText = 'Search & Add';
			status_link.href = `${url_base}/add?q=${encodeURIComponent(series)}`;
			status_link.classList.add('not-added');

			DiscoverEls.table.appendChild(entry);
		});

		hide([DiscoverEls.loading_message]);
		DiscoverEls.buttons.refresh.querySelector('img').classList.remove('spinning');
	});
};

function setMode(api_key, new_mode) {
	if (mode === new_mode) return;
	mode = new_mode;
	page = 1;
	fillList(api_key);
};

function goToPreviousPage(api_key) {
	if (mode !== 'recent' || page <= 1) return;
	page--;
	fillList(api_key);
};

function goToNextPage(api_key) {
	if (mode !== 'recent' || page >= max_page) return;
	page++;
	fillList(api_key);
};

// code run on load
usingApiKey()
.then(api_key => {
	fillList(api_key);
	DiscoverEls.buttons.refresh.onclick = e => fillList(api_key);
	DiscoverEls.buttons.recent.onclick = e => setMode(api_key, 'recent');
	DiscoverEls.buttons.for_you.onclick = e => setMode(api_key, 'for-you');
	DiscoverEls.page_turner.previous.onclick = e => goToPreviousPage(api_key);
	DiscoverEls.page_turner.next.onclick = e => goToNextPage(api_key);
});
