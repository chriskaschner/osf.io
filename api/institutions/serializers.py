from django.apps import apps
from rest_framework import serializers as ser
from rest_framework import exceptions

from modularodm import Q

from osf.models import AbstractNode as Node
from website.util import permissions as osf_permissions

from api.base.serializers import JSONAPISerializer, RelationshipField, LinksField, JSONAPIRelationshipSerializer, \
    BaseAPISerializer
from api.base.exceptions import RelationshipPostMakesNoChanges


class InstitutionSerializer(JSONAPISerializer):

    filterable_fields = frozenset([
        'id',
        'name',
        'auth_url',
    ])

    name = ser.CharField(read_only=True)
    id = ser.CharField(read_only=True, source='_id')
    logo_path = ser.CharField(read_only=True)
    description = ser.CharField(read_only=True)
    auth_url = ser.CharField(read_only=True)
    links = LinksField({'self': 'get_api_url', })

    nodes = RelationshipField(
        related_view='institutions:institution-nodes',
        related_view_kwargs={'institution_id': '<_id>'},
    )

    registrations = RelationshipField(
        related_view='institutions:institution-registrations',
        related_view_kwargs={'institution_id': '<_id>'}
    )

    users = RelationshipField(
        related_view='institutions:institution-users',
        related_view_kwargs={'institution_id': '<_id>'}
    )

    def get_api_url(self, obj):
        return obj.absolute_api_v2_url

    def get_absolute_url(self, obj):
        return obj.absolute_api_v2_url

    class Meta:
        type_ = 'institutions'


class NodeRelated(JSONAPIRelationshipSerializer):
    id = ser.CharField(source='_id', required=False, allow_null=True)
    class Meta:
        type_ = 'nodes'

class InstitutionNodesRelationshipSerializer(BaseAPISerializer):
    data = ser.ListField(child=NodeRelated())
    links = LinksField({'self': 'get_self_url',
                        'html': 'get_related_url'})

    def get_self_url(self, obj):
        return obj['self'].nodes_relationship_url

    def get_related_url(self, obj):
        return obj['self'].nodes_url

    class Meta:
        type_ = 'nodes'

    def create(self, validated_data):
        inst = self.context['view'].get_object()['self']
        user = self.context['request'].user
        node_dicts = validated_data['data']

        changes_flag = False
        for node_dict in node_dicts:
            node = Node.load(node_dict['_id'])
            if not node:
                raise exceptions.NotFound(detail='Node with id "{}" was not found'.format(node_dict['_id']))
            if not node.has_permission(user, osf_permissions.WRITE):
                raise exceptions.PermissionDenied(detail='Write permission on node {} required'.format(node_dict['_id']))
            if not node.is_affiliated_with_institution(inst):
                node.add_affiliated_institution(inst, user, save=True)
                changes_flag = True

        if not changes_flag:
            raise RelationshipPostMakesNoChanges

        ConcreteNode = apps.get_model('osf.Node')
        return {
            'data': list(ConcreteNode.find_by_institutions(inst, Q('is_deleted', 'ne', True))),
            'self': inst
        }
