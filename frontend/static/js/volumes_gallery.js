// Poster-gallery renderer for large libraries.
//
// The normal volumes.js renderer deliberately keeps only a bounded DOM runway;
// that prevents a multi-thousand-volume library from freezing the main thread,
// but it also means fast scrolling reaches the end of the currently materialized
// cards and waits for another batch. Poster galleries have a nicer split:
// establish cheap geometry/text for the whole result set, then hydrate expensive
// images only around the viewport.
window.installVolumesGalleryRenderer(() => {
	const original_build_library_view = buildLibraryView;
	let cover_observer = null;

	function disconnectCoverObserver() {
		if (cover_observer !== null) {
			cover_observer.disconnect();
			cover_observer = null;
		};
	};

	function loadCover(img) {
		if (!img.dataset.src)
			return;

		const rect = img.getBoundingClientRect();
		if ('fetchPriority' in img) {
			img.fetchPriority = rect.bottom >= 0 && rect.top <= window.innerHeight
				? 'high'
				: 'low';
		};
		img.src = img.dataset.src;
		delete img.dataset.src;
	};

	function observeCovers(images) {
		disconnectCoverObserver();

		if (typeof window.IntersectionObserver !== 'function') {
			// Native loading=lazy is still a useful fallback on older WebViews.
			images.forEach(loadCover);
			return;
		};

		cover_observer = new IntersectionObserver(
			entries => entries.forEach(entry => {
				if (!entry.isIntersecting)
					return;
				loadCover(entry.target);
				cover_observer.unobserve(entry.target);
			}),
			{
				// Several screens of overscan means a normal fling should encounter
				// already-requested covers without downloading all 3,000 at once.
				rootMargin: '1800px 0px 1800px 0px'
			}
		);
		images.forEach(img => cover_observer.observe(img));
	};

	function buildPosterShell(entry, volume, api_key, fragment, images) {
		const list_entry = pre_build_els.list_entry.cloneNode(true);
		list_entry.ariaLabel =
			`View the volume ${volume.title} (${volume.year}) Volume ${volume.volume_number}`;
		list_entry.classList.add(`vol-${volume.id}`);
		list_entry.href = `${url_base}/volumes/${volume.id}`;
		list_entry.dataset.volumeId = volume.id;

		const img = list_entry.querySelector('.list-img');
		// Empty src can request the current document in some browsers. Keep the
		// element completely source-less until the observer chooses to hydrate it.
		img.removeAttribute('src');
		img.loading = 'lazy';
		img.decoding = 'async';
		img.dataset.src =
			`${url_base}/api/volumes/${volume.id}/cover?api_key=${api_key}`;
		images.push(img);

		const list_title = list_entry.querySelector('.list-title');
		list_title.innerText =
		list_title.title =
			`${volume.title} (${volume.year})`;
		list_entry.querySelector('.list-volume').innerText =
			`Volume ${volume.volume_number}`;

		entry.list_entry = list_entry;
		entry.download_status = getVolumeDownloadStatus(volume.id);
		entry.renderMonitored();
		entry.renderProgressBar();
		entry.renderDownloadStatus();
		fragment.appendChild(list_entry);
	};

	function buildCompletePosterIndex(api_key, generation, on_first_batch) {
		clearLibraryView('list');
		library_built_views.list = true;
		library_render_pending.list = true;
		disconnectCoverObserver();

		// Give the loading state a paint before doing the one bulk shell build.
		scheduleLibraryPaint(() => {
			if (generation !== library_render_generation)
				return;

			const fragment = document.createDocumentFragment();
			const images = [];
			for (const volume of library_volumes) {
				let entry = library_entries.get(volume.id);
				if (entry === undefined)
					entry = createLibraryEntry(volume, api_key);
				buildPosterShell(entry, volume, api_key, fragment, images);
			};

			library_els.views.list.insertBefore(
				fragment,
				library_els.views.list.querySelector('.space-taker')
			);
			library_render_offsets.list = library_volumes.length;
			library_render_pending.list = false;
			library_els.mass_edit.button.disabled = false;

			if (on_first_batch !== null)
				on_first_batch();

			// Observe only after the complete shell grid is attached, so geometry and
			// scroll height are final before any cover requests begin.
			observeCovers(images);
		});
	};

	buildLibraryView = function(view, api_key, generation, on_first_batch=null) {
		if (view !== 'list') {
			return original_build_library_view(
				view,
				api_key,
				generation,
				on_first_batch
			);
		};

		buildCompletePosterIndex(api_key, generation, on_first_batch);
	};
});
