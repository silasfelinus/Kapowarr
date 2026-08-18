const DiscoverEls = {
	table: document.querySelector('#discover-list'),
	empty_message: document.querySelector('#discover-empty-message'),
	loading_message: document.querySelector('#discover-loading-message'),
	buttons: {
		refresh: document.querySelector('#refresh-button')
	},
	page_turner: {
		previous: document.querySelector('#previous-page'),
		next: document.querySelector('#next-page'),
		number: document.querySelector('#page-number')
	},
	entry: document.querySelector('.pre-build-els .discover-entry')
};

var page = 1;
var max_page = 1;

function updatePageTurner() {
	DiscoverEls.page_turner.number.innerText = `Page ${page} / ${max_page}`;
	DiscoverEls.page_turner.previous.disabled = page <= 1;
	DiscoverEls.page_turner.next.disabled = page >= max_page;
};

function fillList(api_key) {
	hide([DiscoverEls.empty_message], [DiscoverEls.loading_message]);
	DiscoverEls.buttons.refresh.querySelector('img').classList.add('spinning');

	fetchAPI('/discover', api_key, {page: page})
	.then(json => {
		page = json.result.page;
		max_page = json.result.max_page;
		updatePageTurner();

		DiscoverEls.table.innerHTML = '';

		DiscoverEls.empty_message.classList.toggle(
			'hidden', json.result.items.length > 0
		);

		json.result.items.forEach(obj => {
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

			const status_link = entry.querySelector('.status-column a');
			if (obj.volume_id !== null) {
				status_link.innerText = 'In Library';
				status_link.href = `${url_base}/volumes/${obj.volume_id}`;
				status_link.classList.add('in-library');
			} else {
				status_link.innerText = 'Search & Add';
				status_link.href = `${url_base}/add?q=${encodeURIComponent(series)}`;
				status_link.classList.add('not-added');
			};

			DiscoverEls.table.appendChild(entry);
		});

		hide([DiscoverEls.loading_message]);
		DiscoverEls.buttons.refresh.querySelector('img').classList.remove('spinning');
	});
};

function goToPreviousPage(api_key) {
	if (page <= 1) return;
	page--;
	fillList(api_key);
};

function goToNextPage(api_key) {
	if (page >= max_page) return;
	page++;
	fillList(api_key);
};

// code run on load
usingApiKey()
.then(api_key => {
	fillList(api_key);
	DiscoverEls.buttons.refresh.onclick = e => fillList(api_key);
	DiscoverEls.page_turner.previous.onclick = e => goToPreviousPage(api_key);
	DiscoverEls.page_turner.next.onclick = e => goToNextPage(api_key);
});
