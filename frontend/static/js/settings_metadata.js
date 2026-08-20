function fillSettings(api_key) {
	fetchAPI('/settings', api_key)
	.then(json => {
		document.querySelector('#date-type-input').value = json.result.date_type;
		document.querySelector('#metron-token-input').value = json.result.metron_api_token;
		document.querySelector('#metron-username-input').value = json.result.metron_username;
		document.querySelector('#metron-password-input').value = json.result.metron_password;
		document.querySelector('#gcd-username-input').value = json.result.gcd_username;
		document.querySelector('#gcd-password-input').value = json.result.gcd_password;
	});
};

function saveSettings(api_key) {
	document.querySelector("#save-button p").innerText = 'Saving';
	const data = {
		'date_type': document.querySelector('#date-type-input').value,
		'metron_api_token': document.querySelector('#metron-token-input').value,
		'metron_username': document.querySelector('#metron-username-input').value,
		'metron_password': document.querySelector('#metron-password-input').value,
		'gcd_username': document.querySelector('#gcd-username-input').value,
		'gcd_password': document.querySelector('#gcd-password-input').value
	};
	sendAPI('PUT', '/settings', api_key, {}, data)
	.then(response => response.json())
	.then(json => {
		document.querySelector("#save-button p").innerText = 'Saved';
	})
	.catch(e => {
		document.querySelector("#save-button p").innerText = 'Failed';
		console.log(e);
	});
};

// code run on load

usingApiKey()
.then(api_key => {
	fillSettings(api_key);
	document.querySelector('#save-button').onclick = e => saveSettings(api_key);
});
