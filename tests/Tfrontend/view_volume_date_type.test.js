const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const page = fs.readFileSync(
    path.join(root, 'frontend/static/js/view_volume.js'), 'utf8'
);
const template = fs.readFileSync(
    path.join(root, 'frontend/templates/view_volume.html'), 'utf8'
);

test('volume issue date column does not claim cover dates are release dates', () => {
    assert.match(template, /class="issue-date">Issue Date<\/th>/);
    assert.match(page, /fetchAPI\('\/settings', api_key\)/);
    assert.match(page, /date_type === 'cover_date'/);
    assert.match(page, /cover_date \? 'Cover Date' : 'In-Store Date'/);
    assert.match(page, /Printed cover month, not the on-sale date/);
    assert.match(page, /Select In Store Date/);
});
