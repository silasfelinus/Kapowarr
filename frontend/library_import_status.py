# -*- coding: utf-8 -*-

"""Durable Continuous Library Import status route.

Decorates the existing `api` blueprint (see `frontend.discover`'s docstring
convention) rather than editing `frontend/api.py` inline.

Everything the Library Import page knew about a continuous pass used to come
from the task queue: the folder counts were parsed out of the running task's
status message, and the review queue was read from that task's details. The
queue is process-local and a task is dropped from it the moment it finishes, so
the page lost the entire pass -- progress and review holds alike -- as soon as
the run ended, and again on every reload or from any other device. The
checkpoints were never lost; there was simply no way to ask for them.

This route asks the durable tables directly, so a finished, paused, interrupted
or resumed pass is all equally visible.
"""

from typing import Any, Dict

from flask import request

from backend.features.library_import_diagnostics import get_postmortem_path
from backend.features.library_import_persistent import (
    PersistentContinuousLibraryImport, RecheckContinuousLibraryImport)
from backend.features.library_import_state import (
    JOB_RUNNING, count_outstanding_review_folders, get_active_job,
    get_job_summary, get_outstanding_review_items, mark_job_paused)
from backend.features.tasks import TaskHandler
from frontend.api import api, auth, error_handler, return_api

CONTINUOUS_ACTIONS = (
    PersistentContinuousLibraryImport.action,
    RecheckContinuousLibraryImport.action
)


def _running_task() -> Dict[str, Any]:
    """Report whether a continuous pass is queued or running right now."""
    for task in TaskHandler().get_all():
        if task['action'] in CONTINUOUS_ACTIONS:
            return {
                'id': task['id'],
                'action': task['action'],
                'status': task['status'],
                'message': task['message']
            }
    return {}


@api.route('/libraryimport/continuous', methods=['GET'])
@error_handler
@auth
def api_library_import_continuous():
    # The page polls this while a pass runs but only needs the held rows when
    # the user opens the review list, and that list can be hundreds of folders.
    # `items=0` asks for the counters alone.
    include_items = request.args.get('items') != '0'

    if include_items:
        # Read before the counters: reconciling held rows against the `files`
        # table can retire folders whose files have since been imported by
        # hand, and the job summary should be reported after that has settled.
        review_items = get_outstanding_review_items()
        outstanding = len({item.get('folder') for item in review_items})
    else:
        review_items = []
        outstanding = count_outstanding_review_folders()

    job = get_active_job()
    summary = get_job_summary(int(job['id'])) if job is not None else None
    task = _running_task()

    if summary is not None:
        # A job row says what the last worker wrote, not whether one is alive
        # now. A pass killed mid-folder leaves `running` behind with nothing
        # running, and reporting that verbatim let the page claim a pass was in
        # progress while the task queue was empty -- so the status line said
        # running, the durable state said otherwise, and Stop had no task to
        # act on. Say which of the two it is.
        summary['is_live'] = bool(task)
        summary['is_stalled'] = (
            summary['status'] == JOB_RUNNING and not task
        )

    return return_api({
        'job': summary,
        'review_items': review_items,
        'review_items_included': include_items,
        'review_folders_outstanding': outstanding,
        # Held folders are recorded with the evidence behind the decision. When
        # a pass imports nothing, this file is what says why.
        'review_postmortem_file': get_postmortem_path(),
        'task': task
    })


@api.route('/libraryimport/continuous/stop', methods=['POST'])
@error_handler
@auth
def api_library_import_continuous_stop():
    """Stop the pass, whether or not a worker is still alive to ask.

    Stopping used to be purely a task-queue operation, so it did nothing at all
    when the queue had no entry to delete -- which is exactly the state a pass
    interrupted mid-folder leaves behind. The button appeared to do nothing and
    the job stayed marked running forever, because only a process restart ever
    reconciled it.

    A live task is stopped cooperatively, at the folder boundary it chooses. A
    job with no worker is marked paused here instead: nothing is running, so
    there is nothing to wait for, and pausing is what makes it resumable.
    """
    task = _running_task()
    if task:
        TaskHandler().remove(int(task['id']))
        return return_api({'stopped': 'task', 'task_id': task['id']})

    job = get_active_job()
    if job is None or job['status'] != JOB_RUNNING:
        # Already paused or finished. Not an error: the button's job is to
        # leave the pass stopped, and it is.
        return return_api({'stopped': 'nothing'})

    mark_job_paused(int(job['id']))
    return return_api({'stopped': 'stalled_job', 'job_id': job['id']})
