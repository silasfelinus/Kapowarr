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
	const original_clear_library_view = clearLibraryView;
	let cover_observer = null;

	function disconnectCoverObserver() {
		if (cover_observer !== null) {
			cover_observer.disconnect();
			cover_observer = null;
		};
	};

	// `rect` comes from the IntersectionObserver entry, never from
	// `getBoundingClientRect`. This used to measure each image itself,
	// which forces a synchronous style and layout flush -- against a
	// document holding a poster card for every volume in the library, all
	// of them materialized up front by design. One observer callback
	// carries every image that entered the 1800px overscan band during a
	// fling, so a single scroll gesture could stall the main thread on
	// dozens of full-document layouts in a row: the scroll visibly waited
	// on the covers.
	//
	// The observer has already measured all of it. `entry.boundingClientRect`
	// is that measurement, computed off the critical path, and it is the
	// same rectangle this asked the DOM for.
	function loadCover(img, rect, viewport_height) {
		if (!img.dataset.src)
			return;

		if (rect !== null && 'fetchPriority' in img) {
			img.fetchPriority = rect.bottom >= 0 && rect.top <= viewport_height
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
			// Everything is requested at once here, so a priority hint would
			// have nothing to order.
			images.forEach(img => loadCover(img, null, 0));
			return;
		};

		cover_observer = new IntersectionObserver(
			entries => {
				// Read once for the whole batch rather than once per image.
				const viewport_height = window.innerHeight
					|| document.documentElement.clientHeight;
				entries.forEach(entry => {
					if (!entry.isIntersecting)
						return;
					loadCover(
						entry.target,
						entry.boundingClientRect,
						viewport_height
					);
					cover_observer.unobserve(entry.target);
				});
			},
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

	clearLibraryView = function(view) {
		if (view === 'list')
			disconnectCoverObserver();
		return original_clear_library_view(view);
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