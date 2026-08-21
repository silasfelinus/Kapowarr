const PUBLISHER_PROFILES = {
	'marvel comics': 'Major U.S. superhero publisher behind Spider-Man, the X-Men, the Avengers, Daredevil, and many connected Marvel-universe titles.',
	'dc comics': 'Major U.S. superhero publisher behind Superman, Batman, Wonder Woman, the Justice League, and the wider DC universe.',
	'image comics': 'Creator-owned comics publisher founded by comics artists in 1992. Its catalogue spans superheroes, science fiction, horror, fantasy, crime, and more.',
	'dark horse comics': 'Independent U.S. publisher known for creator-owned comics, graphic novels, manga, and licensed properties.',
	'idw publishing': 'U.S. publisher of original and licensed comics, with a catalogue that has included major film, television, game, and toy properties.',
	'boom studios': 'Independent U.S. publisher of original and licensed comics. Its family of imprints has included BOOM!, KaBOOM!, and Archaia.',
	'dynamite entertainment': 'U.S. publisher known for licensed characters, pulp and adventure properties, adaptations, and creator-owned comics.',
	'oni press': 'Independent U.S. publisher focused on creator-driven comics and graphic novels across many genres.',
	'titan comics': 'UK-based publisher of original and licensed comics and graphic novels, including many television, film, and game properties.',
	'archie comics': 'Long-running U.S. publisher best known for Archie, Betty, Veronica, Jughead, Riverdale, and related characters.',
	'valiant entertainment': 'U.S. publisher centered on a shared superhero universe including characters such as X-O Manowar, Bloodshot, and Harbinger.',
	'mad cave studios': 'Independent U.S. publisher of genre comics and graphic novels, including science fiction, fantasy, horror, crime, and adventure.',
	'vault comics': 'Independent U.S. publisher best known for creator-owned science fiction, fantasy, horror, and other speculative comics.',
	'dstlry': 'Creator-focused comics publisher launched in the 2020s, emphasizing premium-format releases and creator-owned work.',
	'skybound': 'Creator-focused entertainment company and Image Comics publishing partner founded by Robert Kirkman, with genre and creator-owned titles.',
	'viz media': 'English-language publisher and distributor specializing in Japanese manga and related media.',
	'kodansha': 'Major Japanese publisher. Its English-language manga catalogue includes releases through Kodansha USA and related imprints.',
	'yen press': 'English-language publisher specializing in manga, light novels, and related Japanese and Korean works.',
	'seven seas entertainment': 'North American publisher specializing in translated manga, light novels, webtoons, and related works.',
	'fantagraphics': 'Independent publisher known for alternative comics, art comics, graphic novels, and archival editions of historically important work.',
	'drawn and quarterly': 'Independent publisher known for literary comics, graphic novels, memoir, international work, and alternative cartooning.',
	'top cow productions': 'Image-associated comics studio and publisher known for properties including Witchblade and The Darkness.',
	'zenescope entertainment': 'U.S. publisher known for its Grimm Fairy Tales line and connected fantasy, horror, and adventure titles.',
	'antarctic press': 'Independent U.S. publisher with a long-running catalogue of creator-driven comics across many genres.',
	'humanoids': 'Publisher of international and creator-owned comics with roots in the French Métal Hurlant tradition and European graphic storytelling.'
};

function normalizePublisherName(name) {
	return String(name || '')
		.toLowerCase()
		.replace(/&/g, 'and')
		.replace(/[^a-z0-9]+/g, ' ')
		.trim();
};

function publisherDescription(name) {
	const normalized = normalizePublisherName(name);
	if (PUBLISHER_PROFILES[normalized])
		return PUBLISHER_PROFILES[normalized];

	const aliases = {
		'marvel': 'marvel comics',
		'dc': 'dc comics',
		'image': 'image comics',
		'dark horse': 'dark horse comics',
		'boom': 'boom studios',
		'titan': 'titan comics',
		'valiant': 'valiant entertainment',
		'mad cave': 'mad cave studios',
		'seven seas': 'seven seas entertainment',
		'top cow': 'top cow productions',
		'zenescope': 'zenescope entertainment',
		'drawn quarterly': 'drawn and quarterly'
	};
	const canonical = aliases[normalized];
	if (canonical && PUBLISHER_PROFILES[canonical])
		return PUBLISHER_PROFILES[canonical];

	return 'No built-in profile yet. This publisher name comes directly from the release catalogue; the link below can help identify it.';
};

function publisherAutomationLabel(publisher) {
	if (publisher.root_folder_id === null)
		return 'Off';
	return publisher.auto_search ? 'Auto-add & grab' : 'Auto-add & monitor';
};

function openPublisherInfo(publisher) {
	const dialog = document.querySelector('#publisher-info-dialog');
	if (!dialog)
		return;

	document.querySelector('#publisher-info-name').innerText = publisher.publisher;
	document.querySelector('#publisher-info-description').innerText =
		publisherDescription(publisher.publisher);
	document.querySelector('#publisher-info-stored').innerText =
		String(publisher.release_count || 0);
	document.querySelector('#publisher-info-week').innerText =
		String(publisherWeekCount(publisher));
	document.querySelector('#publisher-info-automation').innerText =
		publisherAutomationLabel(publisher);

	const search = document.querySelector('#publisher-info-search');
	search.href = 'https://en.wikipedia.org/wiki/Special:Search?search='
		+ encodeURIComponent(`${publisher.publisher} comics publisher`);

	if (typeof dialog.showModal === 'function')
		dialog.showModal();
	else
		dialog.setAttribute('open', '');
};

function closePublisherInfo() {
	const dialog = document.querySelector('#publisher-info-dialog');
	if (!dialog)
		return;
	if (typeof dialog.close === 'function')
		dialog.close();
	else
		dialog.removeAttribute('open');
};

function enableGrabAllPublishers(api_key, button) {
	const root_folder_id = parseInt(PullListEls.root_folder.value);
	if (Number.isNaN(root_folder_id)) {
		alert('Add a root folder before enabling publisher automation.');
		return;
	};

	const count = pullListState.publishers.length;
	if (!count) {
		setCheckStatus('There are no listed publishers to enable yet.', true);
		return;
	};

	const confirmed = window.confirm(
		`Set all ${count} currently listed publishers to Auto-add & grab using `
		+ 'the selected root folder? Publishers discovered later will remain off '
		+ 'until you apply this again.'
	);
	if (!confirmed)
		return;

	button.disabled = true;
	sendAPI('POST', '/pulllist/publishers/grab-all', api_key, {}, {root_folder_id})
		.then(response => response.json())
		.then(json => loadPublishers(api_key).then(() => json))
		.then(json => setCheckStatus(
			`Auto-add & grab enabled for ${json.result.updated} publishers.`
		))
		.catch(error => {
			reportPullListClientError(api_key, 'publisher grab all', error);
			setCheckStatus(
				`Could not enable all publishers: ${pullListErrorMessage(error)}`,
				true
			);
		})
		.finally(() => {
			button.disabled = false;
		});
};

usingApiKey().then(api_key => {
	const rule_list = document.querySelector('#publisher-rule-list');
	const grab_all = document.querySelector('#publisher-grab-all');
	const close = document.querySelector('#publisher-info-close');
	const dialog = document.querySelector('#publisher-info-dialog');

	if (rule_list) {
		rule_list.addEventListener('click', event => {
			const info_button = event.target.closest('.publisher-info-button');
			if (!info_button)
				return;
			const rule = info_button.closest('.publisher-rule');
			const name = rule.querySelector('.publisher-rule-name').innerText;
			const publisher = pullListState.publishers.find(
				item => item.publisher === name
			);
			if (publisher)
				openPublisherInfo(publisher);
		});
	};

	if (grab_all)
		grab_all.onclick = () => enableGrabAllPublishers(api_key, grab_all);
	if (close)
		close.onclick = closePublisherInfo;
	if (dialog) {
		dialog.addEventListener('click', event => {
			if (event.target === dialog)
				closePublisherInfo();
		});
	};
});
