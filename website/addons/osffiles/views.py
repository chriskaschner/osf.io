"""

"""

import re
import os
import cgi
import json
import time
import zipfile
import tarfile
from cStringIO import StringIO
import httplib as http
import logging

import pygments
import pygments.lexers
import pygments.formatters
from hurry.filesize import size, alternative

from framework import request, redirect, secure_filename, send_file
from framework.auth import must_have_session_auth
from framework.git.exceptions import FileNotModified
from framework.auth import get_current_user, get_api_key
from framework.exceptions import HTTPError
from framework.analytics import get_basic_counters, update_counters
from website.project.views.node import _view_project
from website.project.decorators import must_not_be_registration, must_be_valid_project, \
    must_be_contributor, must_be_contributor_or_public, must_have_addon
from website import settings

from .model import NodeFile


logger = logging.getLogger(__name__)


@must_be_contributor_or_public
@must_have_addon('osffiles', 'node')
def osffiles_widget(*args, **kwargs):
    node = kwargs['node'] or kwargs['project']
    osffiles = node.get_addon('osffiles')
    rv = {
        'complete': True,
    }
    rv.update(osffiles.config.to_json())
    return rv

###

def prune_file_list(file_list, max_depth):
    if max_depth is None:
        return file_list
    return [file for file in file_list if len([c for c in file if c == '/']) <= max_depth]


def _clean_file_name(name):
    " HTML-escape file name and encode to UTF-8. "
    escaped = cgi.escape(name)
    encoded = unicode(escaped).encode('utf-8')
    return encoded



def osffiles_dummy_folder(node_settings, user, parent=None, **kwargs):

    node = node_settings.owner
    return {
        'uid': 'osffiles:{0}'.format(node_settings._id),
        'parent_uid': parent or 'null',
        'uploadUrl': os.path.join(
            node_settings.owner.api_url, 'osffiles'
        ) + '/',
        'type': 'folder',
        'sizeRead': '--',
        'dateModified': '--',
        'name': 'OSF Files',
        'can_edit': True,
        'can_view': True,
        'lazyLoad': node.api_url + 'osffiles/hgrid/',
        'lazyDummy': 0,
    }


@must_be_contributor_or_public
@must_have_addon('osffiles', 'node')
def get_osffiles(*args, **kwargs):

    user = kwargs['user']
    node_settings = kwargs['node_addon']
    parent = request.args.get('parent', 'null')

    can_edit = node_settings.owner.can_edit(user)
    can_view = node_settings.owner.can_view(user)

    info = []

    if can_view:
        for name, fid in node_settings.owner.files_current.iteritems():
            fobj = NodeFile.load(fid)
            unique, total = get_basic_counters(
                'download:{0}:{1}'.format(
                    node_settings.owner._id,
                    fobj.path.replace('.', '_')
                )
            )
            item = {}

            # URLs
            item['view'] = fobj.url
            item['download'] = fobj.api_url
            item['delete'] = fobj.api_url

            item['can_edit'] = can_edit

            item['uid'] = fid
            item['downloads'] = total if total else 0
            item['parent_uid'] = parent or 'null'
            item['type'] = 'file'
            item['name'] = _clean_file_name(fobj.path)
            item['ext'] = _clean_file_name(fobj.path.split('.')[-1])
            item['sizeRead'] = [
                float(fobj.size),
                size(fobj.size, system=alternative)
            ]
            item['size'] = str(fobj.size)
            item['dateModified'] = [
                time.mktime(fobj.date_modified.timetuple()),
                fobj.date_modified.strftime('%Y/%m/%d %I:%M %p')
            ]
            info.append(item)

    return info


@must_be_valid_project # returns project
@must_be_contributor_or_public # returns user, project
@must_have_addon('osffiles', 'node')
def list_file_paths(*args, **kwargs):

    node_to_use = kwargs['node'] or kwargs['project']

    return {'files': [
        NodeFile.load(fid).path
        for fid in node_to_use.files_current.values()
    ]}


@must_have_session_auth # returns user
@must_be_valid_project # returns project
@must_be_contributor  # returns user, project
@must_not_be_registration
@must_have_addon('osffiles', 'node')
def upload_file_public(*args, **kwargs):

    user = kwargs['user']
    api_key = get_api_key()
    node_settings = kwargs['node_addon']
    node = kwargs['node'] or kwargs['project']

    do_redirect = request.form.get('redirect', False)

    uploaded_file = request.files.get('file')
    uploaded_file_content = uploaded_file.read()
    uploaded_file.seek(0, os.SEEK_END)
    uploaded_file_size = uploaded_file.tell()
    uploaded_file_content_type = uploaded_file.content_type
    uploaded_filename = secure_filename(uploaded_file.filename)

    try:
        fobj = node.add_file(
            user,
            api_key,
            uploaded_filename,
            uploaded_file_content,
            uploaded_file_size,
            uploaded_file_content_type
        )
    except FileNotModified as e:
        return [{
            'action_taken': None,
            'message': e.message,
            'name': uploaded_filename,
        }]

    unique, total = get_basic_counters(
        'download:{0}:{1}'.format(
            node._id,
            fobj.path.replace('.', '_')
        )
    )

    file_info = {
        'name': uploaded_filename,
        'sizeRead': [
            float(uploaded_file_size),
            size(uploaded_file_size, system=alternative),
        ],
        'size': str(uploaded_file_size),

        # URLs
        'view': fobj.url,
        'download': fobj.api_url,
        'delete': fobj.api_url,

        'ext': uploaded_filename.split('.')[-1],
        'type': 'file',
        'can_edit': True,
        'date_uploaded': fobj.date_uploaded.strftime('%Y/%m/%d %I:%M %p'),
        'dateModified': [
            time.mktime(fobj.date_uploaded.timetuple()),
            fobj.date_uploaded.strftime('%Y/%m/%d %I:%M %p'),
        ],
        'downloads': total if total else 0,
        'user_id': None,
        'user_fullname': None,
        'uid': fobj._id,
        'parent_uid': 'osffiles:{0}'.format(node_settings._id)
    }

    if do_redirect:
        return redirect(request.referrer)

    return [file_info], 201


@must_be_valid_project # returns project
@must_be_contributor_or_public # returns user, project
@update_counters('node:{pid}')
@update_counters('node:{nid}')
def view_file(*args, **kwargs):
    user = kwargs['user']
    node_to_use = kwargs['node'] or kwargs['project']

    file_name = kwargs['fid']
    file_name_clean = file_name.replace('.', '_')
    renderer = 'default'

    # Throw 404 and log error if file not found in files_versions
    try:
        latest_node_file_id = node_to_use.files_versions[file_name_clean][-1]
    except KeyError:
        logger.error('File {} not found in files_versions of component {}.'.format(
            file_name_clean, node_to_use._id
        ))
        raise HTTPError(http.NOT_FOUND)
    latest_node_file = NodeFile.load(latest_node_file_id)

    # Ensure NodeFile is attached to Node; should be fixed by actions or
    # improved data modeling in future
    if not latest_node_file.node:
        latest_node_file.node = node_to_use
        latest_node_file.save()

    download_path = latest_node_file.download_url

    file_path = os.path.join(
        settings.UPLOADS_PATH,
        node_to_use._primary_key,
        file_name
    )

    # Throw 404 and log error if file not found on disk
    if not os.path.isfile(file_path):
        logger.error('File {} not found on disk.'.format(file_path))
        raise HTTPError(http.NOT_FOUND)

    versions = []

    for idx, version in enumerate(list(reversed(node_to_use.files_versions[file_name_clean]))):
        node_file = NodeFile.load(version)
        number = len(node_to_use.files_versions[file_name_clean]) - idx
        unique, total = get_basic_counters('download:{}:{}:{}'.format(
            node_to_use._primary_key,
            file_name_clean,
            number,
        ))
        versions.append({
            'file_name': file_name,
            'number': number,
            'display_number': number if idx > 0 else 'current',
            'date_uploaded': node_file.date_uploaded.strftime('%Y/%m/%d %I:%M %p'),
            'total': total if total else 0,
            'committer_name': node_file.uploader.fullname,
            'committer_url': node_file.uploader.url,
        })

    file_size = os.stat(file_path).st_size
    if file_size > settings.MAX_RENDER_SIZE:

        rv = {
            'file_name': file_name,
            'rendered': ('<p>This file is too large to be rendered online. '
                         'Please <a href={path}>download the file</a> to view it locally.</p>'
                         .format(path=download_path)),
            'renderer': renderer,
            'versions': versions,

        }
        rv.update(_view_project(node_to_use, user))
        return rv

    _, file_ext = os.path.splitext(file_path.lower())

    is_img = False
    for fmt in settings.IMG_FMTS:
        fmt_ptn = '^.{0}$'.format(fmt)
        if re.search(fmt_ptn, file_ext):
            is_img = True
            break

    # TODO: this logic belongs in model
    # todo: add bzip, etc
    if is_img:
        # Append version number to image URL so that old versions aren't
        # cached incorrectly. Resolves #208 [openscienceframework.org]
        rendered='<img src="{url}osffiles/download/{fid}/?{vid}" />'.format(
            url=node_to_use.api_url, fid=file_name, vid=len(versions),
        )
    elif file_ext == '.zip':
        archive = zipfile.ZipFile(file_path)
        archive_files = prune_file_list(archive.namelist(), settings.ARCHIVE_DEPTH)
        archive_files = [secure_filename(fi) for fi in archive_files]
        file_contents = '\n'.join(['This archive contains the following files:'] + archive_files)
        file_path = 'temp.txt'
        renderer = 'pygments'
    elif file_path.lower().endswith('.tar') or file_path.endswith('.tar.gz'):
        archive = tarfile.open(file_path)
        archive_files = prune_file_list(archive.getnames(), settings.ARCHIVE_DEPTH)
        archive_files = [secure_filename(fi) for fi in archive_files]
        file_contents = '\n'.join(['This archive contains the following files:'] + archive_files)
        file_path = 'temp.txt'
        renderer = 'pygments'
    else:
        renderer = 'pygments'
        try:
            file_contents = open(file_path, 'r').read()
        except IOError:
            raise HTTPError(http.NOT_FOUND)

    if renderer == 'pygments':
        try:
            rendered = pygments.highlight(
                file_contents,
                pygments.lexers.guess_lexer_for_filename(file_path, file_contents),
                pygments.formatters.HtmlFormatter()
            )
        except pygments.util.ClassNotFound:
            rendered = ('<p>This file cannot be rendered online. '
                        'Please <a href={path}>download the file</a> to view it locally.</p>'
                        .format(path=download_path))

    rv = {
        'file_name': file_name,
        'rendered': rendered,
        'renderer': renderer,
        'versions': versions,
    }
    rv.update(_view_project(node_to_use, user))
    return rv


@must_be_valid_project # returns project
@must_be_contributor_or_public # returns user, project
def download_file(*args, **kwargs):

    node_to_use = kwargs['node'] or kwargs['project']
    filename = kwargs['fid']

    vid = len(node_to_use.files_versions[filename.replace('.', '_')])

    return redirect('{url}osffiles/download/{fid}/version/{vid}/'.format(
        url=node_to_use.api_url,
        fid=filename,
        vid=vid,
    ))


@must_be_valid_project # returns project
@must_be_contributor_or_public # returns user, project
@update_counters('download:{pid}:{fid}:{vid}')
@update_counters('download:{nid}:{fid}:{vid}')
@update_counters('download:{pid}:{fid}')
@update_counters('download:{nid}:{fid}')
def download_file_by_version(*args, **kwargs):
    node_to_use = kwargs['node'] or kwargs['project']
    filename = kwargs['fid']

    version_number = int(kwargs['vid']) - 1
    current_version = len(node_to_use.files_versions[filename.replace('.', '_')]) - 1

    content, content_type = node_to_use.get_file(filename, version=version_number)
    if content is None:
        raise HTTPError(http.NOT_FOUND)

    if version_number == current_version:
        file_path = os.path.join(settings.UPLOADS_PATH, node_to_use._primary_key, filename)
        return send_file(
            file_path,
            mimetype=content_type,
            as_attachment=True,
            attachment_filename=filename,
        )

    file_object = node_to_use.get_file_object(filename, version=version_number)
    filename_base, file_extension = os.path.splitext(file_object.path)
    returned_filename = '{base}_{tmstp}{ext}'.format(
        base=filename_base,
        ext=file_extension,
        tmstp=file_object.date_uploaded.strftime('%Y%m%d%H%M%S')
    )
    return send_file(
        StringIO(content),
        mimetype=content_type,
        as_attachment=True,
        attachment_filename=returned_filename,
    )


@must_have_session_auth
@must_be_valid_project # returns project
@must_be_contributor # returns user, project
@must_not_be_registration
def delete_file(*args, **kwargs):

    user = kwargs['user']
    api_key = get_api_key()
    filename = kwargs['fid']
    node_to_use = kwargs['node'] or kwargs['project']

    if node_to_use.remove_file(user, api_key, filename):
        return {}

    raise HTTPError(http.BAD_REQUEST)
