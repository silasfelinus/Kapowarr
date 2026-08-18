# -*- coding: utf-8 -*-

"""Torznab settings API routes attached to Kapowarr's existing API blueprint."""

from flask import request

from backend.implementations.torznab import TorznabIndexers
from frontend.api import api, auth, error_handler, return_api


@api.route('/torznab-indexers', methods=['GET', 'POST'])
@error_handler
@auth
def api_torznab_indexers():
    if request.method == 'GET':
        return return_api(TorznabIndexers.get_all())

    data: dict = request.get_json()
    result = TorznabIndexers.add(
        data.get('title'),
        data.get('base_url'),
        data.get('api_key'),
        data.get('categories', '7030'),
        data.get('enabled', True)
    ).get_data()
    return return_api(result, code=201)


@api.route('/torznab-indexers/test', methods=['POST'])
@error_handler
@auth
def api_torznab_indexers_test():
    data: dict = request.get_json()
    result = TorznabIndexers.test(
        data.get('base_url'),
        data.get('api_key')
    )
    return return_api({'success': result})


@api.route('/torznab-indexers/<int:id>', methods=['GET', 'PUT', 'DELETE'])
@error_handler
@auth
def api_torznab_indexer(id: int):
    indexer = TorznabIndexers.get_one(id)

    if request.method == 'GET':
        return return_api(indexer.get_data())

    if request.method == 'PUT':
        data: dict = request.get_json()
        indexer.update(data)
        return return_api(indexer.get_data())

    indexer.delete()
    return return_api({})
