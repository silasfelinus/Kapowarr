// Poster-card builder for the library grid.
//
// This module used to own a second, complete rendering path: it built a
// card for every volume in the library up front and then hydrated the
// covers around the viewport with an IntersectionObserver, because the
// expensive part of a poster card is its image and there were thousands of
// them alive at once.
//
// Windowing removes the problem the observer existed to solve. `volumes.js`
// now keeps only the cards near the viewport -- the same band the observer
// was choosing between -- so every card that exists is one whose cover is
// wanted. Requesting it directly is both simpler and earlier than waiting
// for an intersection callback to say what the window already decided.
//
// What is left is the part that was always specific to a poster card: it
// carries an image, so it needs its space reserved before that image
// arrives, or every cover that loads reflows the grid under the reader's
// thumb.
window.installVolumesGalleryRenderer(() => {
	view_builders.list = function(entry, volume, api_key, fragment) {
		const list_entry = pre_build_els.list_entry.cloneNode(true);

		list_entry.ariaLabel =
			`View the volume ${volume.title} (${volume.year}) Volume ${volume.volume_number}`;
		list_entry.href = `${url_base}/volumes/${volume.id}`;
		list_entry.dataset.volumeId = volume.id;

		const img = list_entry.querySelector('.list-img');
		// `loading` and `decoding` still matter inside the window: a fling
		// can cross the whole band before any of it is needed, and neither
		// hint should block the scroll to find out.
		img.loading = 'lazy';
		img.decoding = 'async';
		img.src = `${url_base}/api/volumes/${volume.id}/cover?api_key=${api_key}`;

		const list_title = list_entry.querySelector('.list-title');
		list_title.innerText =
		list_title.title =
			`${volume.title} (${volume.year})`;
		list_entry.querySelector('.list-volume').innerText =
			`Volume ${volume.volume_number}`;

		entry.list_entry = list_entry;
		fragment.appendChild(list_entry);
	};
});

// The header search reads as a library-wide lookup. Do not silently narrow it
// with a saved Wanted/Monitored filter: that made an already-added, complete
// volume appear absent while Add Volume correctly showed it as present.
library_els.search.container.addEventListener('submit', () => {
	if (
		library_els.search.input.value.trim() === ''
		|| library_els.view_options.filter.value === ''
	)
		return;

	library_els.view_options.filter.value = '';
	setLocalStorage({'lib_filter': ''});
});
