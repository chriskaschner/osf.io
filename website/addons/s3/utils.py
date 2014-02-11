import re
from urllib import quote
from datetime import datetime

from boto.iam import IAMConnection
from boto.exception import BotoServerError

from website.util import rubeus

from api import S3Key,  get_bucket_list
from settings import CORS_RULE, ALLOWED_ORIGIN, OSF_USER, OSF_USER_POLICY, OSF_USER_POLICY_NAME

URLADDONS = {
    'delete': 's3/delete/',
    'upload': 's3/upload/',
    'download': 's3/download/',
    'view': 's3/view/'
}


def adjust_cors(s3wrapper):
    rules = s3wrapper.get_cors_rules()

    if not [rule for rule in rules if rule.to_xml() == CORS_RULE]:
        rules.add_rule(['PUT', 'GET'], ALLOWED_ORIGIN, allowed_header={'*'})
        s3wrapper.set_cors_rules(rules)


#TODO remove if not needed in newest hgrid
def checkFolders(s3wrapper, keyList):
    for k in keyList:
        if k.parentFolder is not None and k.parentFolder not in [x.name for x in keyList]:
            newKey = s3wrapper.create_folder(k.pathTo)
            keyList.append(S3Key(newKey))


def wrapped_key_to_json(wrapped_key, node_api, node_url):
    return {
        rubeus.KIND: _key_type_to_rubeus(wrapped_key.type),
        'name': wrapped_key.name,
        'size': (wrapped_key.size, wrapped_key.size) if wrapped_key.size is not None else '--',
        'lastMod': wrapped_key.lastMod.strftime("%Y/%m/%d %I:%M %p") if wrapped_key.lastMod is not None else '--',
        'ext': wrapped_key.extension if wrapped_key.extension is not None else '--',
        'urls': {
            'download': node_api + URLADDONS['download'] + quote(wrapped_key.fullPath) + '/' if wrapped_key.type == 'file' else None,
            'delete': node_api + URLADDONS['delete'] + quote(wrapped_key.fullPath) + '/'if wrapped_key.type == 'file' else None,
            'view': node_url + URLADDONS['view'] + quote(wrapped_key.fullPath) + '/'if wrapped_key.type == 'file' else None,
            'fetch': node_api + 's3/hgrid/' + wrapped_key.fullPath if wrapped_key.type == 'folder' else None,
            'upload': node_api + 's3/upload/'
        }
    }


def _key_type_to_rubeus(key_type):
    if key_type == 'folder':
        return rubeus.FOLDER
    else:
        return rubeus.FILE


def key_upload_path(wrapped_key, url):
    if wrapped_key.type != 'folder':
        return quote(url + URLADDONS['upload'])
    else:
        return quote(url + URLADDONS['upload'] + wrapped_key.fullPath + '/')


def get_bucket_drop_down(user_settings):
    try:
        dropdown_list = ''
        for bucket in get_bucket_list(user_settings):
                dropdown_list += '<li role="presentation"><a href="#">' + \
                    bucket.name + '</a></li>'
        return dropdown_list
    except BotoServerError:
        return False

def create_version_list(wrapper, key_name, node_api):
    versions = wrapper.get_file_versions(key_name)
    return [{
            'id': x.version_id if x.version_id != 'null' else 'Pre-versioning',
            'date': _format_date(x.last_modified),
            'download': _get_download_url(key_name, x.version_id, node_api),
            } for x in versions]


def _format_date(date):
    m = re.search(
        '(.+?)-(.+?)-(\d*)T(\d*):(\d*):(\d*)', str(date))
    if m is not None:
        dt = datetime(int(m.group(1)), int(m.group(2)),
                      int(m.group(3)), int(m.group(4)), int(m.group(5)))
        return dt.strftime("%Y/%m/%d %I:%M %p")
    else:
        return '--'


def _get_download_url(key_name, version_id, node_api):
    url = node_api + 's3/download/' + quote(key_name) + '/'
    if version_id != 'null':
        return url + '?vid=' + version_id + '/'
    else:
        return url


def serialize_bucket(s3wrapper):
    return [{
            'name': x.name,
            'path': x.fullPath,
            'version_id': s3wrapper.bucket.get_key(x.fullPath).version_id,
            } for x in s3wrapper.get_wrapped_keys()]


def create_osf_user(access_key, secret_key, name):
    connection = IAMConnection(access_key, secret_key)
    try:
        connection.get_user(OSF_USER.format(name))
    except BotoServerError:
        connection.create_user(OSF_USER.format(name))

    try:
        connection.get_user_policy(OSF_USER.format(name), OSF_USER_POLICY)
    except BotoServerError:
        connection.put_user_policy(OSF_USER.format(name), OSF_USER_POLICY_NAME, OSF_USER_POLICY)

    return connection.create_access_key(OSF_USER.format(name))['create_access_key_response']['create_access_key_result']['access_key']


def remove_osf_user(user_settings, name):
    connection = IAMConnection(user_settings.access_key, user_settings.secret_key)
    connection.delete_access_key(user_settings.access_key, OSF_USER.format(name))
    connection.delete_user_policy(OSF_USER.format(name), OSF_USER_POLICY_NAME)
    return connection.delete_user(OSF_USER.format(name))
