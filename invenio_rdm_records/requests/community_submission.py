# -*- coding: utf-8 -*-
#
# Copyright (C) 2021-2022 CERN.
#
# Invenio-RDM-Records is free software; you can redistribute it and/or modify
# it under the terms of the MIT License; see LICENSE file for more details.

"""Community submission request."""

from flask_babelex import lazy_gettext as _
from invenio_records_resources.services.uow import RecordCommitOp, RecordIndexOp
from invenio_requests.customizations import actions

from ..proxies import current_rdm_records_service as service
from .base import ReviewRequest


#
# Actions
#
class SubmitAction(actions.SubmitAction):
    """Submit action."""

    def execute(self, identity, uow):
        """Execute the submit action."""
        draft = self.request.topic.resolve()
        service._validate_draft(identity, draft)
        # Set the record's title as the request title.
        self.request["title"] = draft.metadata["title"]
        super().execute(identity, uow)


class AcceptAction(actions.AcceptAction):
    """Accept action."""

    def execute(self, identity, uow):
        """Accept record into community."""
        # Resolve the topic and community - the request type only allow for
        # community receivers and record topics.
        draft = self.request.topic.resolve()
        community = self.request.receiver.resolve()
        service._validate_draft(identity, draft)

        # Unset review from record (still accessible from request)
        # The curator (receiver) should still have access, via the community
        # The creator (uploader) should also still have access, because
        # they're the uploader
        draft.parent.review = None

        # TODO:
        # - Put below into a service method
        # - Check permissions

        # Add community to record.
        is_default = self.request.type.set_as_default
        draft.parent.communities.add(
            community, request=self.request, default=is_default
        )

        # MSDLIVE CHANGE BEGIN
        # 1. let the record belong to all of the selected project/communities
        projects = draft.metadata.get('msdlive_projects');
        if projects:
            # skip the project that is the same as the community already added
            for project in projects:
                if project.get('id') != str(community.id):
                    draft.parent.communities.add(
                        project.get('id'), request=None, default=False
                    )

        # 2. send an email about the record being accepted
        from invenio_communities.proxies import current_communities
        from invenio_communities.members.records.api import Member
        from invenio_access.permissions import system_identity

        community_dict = current_communities.service.read(identity=system_identity, id_=community.id).to_dict()
        self_html = f'{community_dict["links"]["self_html"]}/requests/{self.request.id}'

        members = Member.get_members(community.id)
        # get owner, managers and curators. There should be an easier way
        recipients = [
            m.relations.user.dereference() for m in members
            if m.user_id
               and m.role in ["owner", "manager", "curator"]  #
        ]
        recipients = [r["email"] for r in recipients]

        body = f'The following record has been accepted and published.\n\nView submission: {self_html}'
        mail_data = {"recipients": recipients,
                     "body": body,
                     "subject": "MSD-LIVE Submission Accepted",
                     "sender": "info@msdlive.org", "cc": ["info@msdlive.org"]}
        send_email.delay(mail_data)
        # MSDLIVE CHANGE END
        uow.register(RecordCommitOp(draft.parent))

        # Publish the record
        # TODO: Ensure that the accpeting user has permissions to publish.
        service.publish(identity, draft.pid.pid_value, uow=uow)
        super().execute(identity, uow)


class DeclineAction(actions.DeclineAction):
    """Decline action."""

    def execute(self, identity, uow):
        """Execute action."""
        # Keeps the record and the review connected so the user can see the
        # outcome of the request
        # The receiver (curator) won't have access anymore to the draft
        # The creator (uploader) should still have access to the record/draft
        draft = self.request.topic.resolve()
        super().execute(identity, uow)

        # TODO: this shouldn't be required BUT because of the caching mechanism
        # in the review systemfield, the review should be set with the updated
        # request object
        draft.parent.review = self.request
        uow.register(RecordCommitOp(draft.parent))
        # update draft to reflect the new status
        uow.register(RecordIndexOp(draft, indexer=service.indexer))

        # MSD-LIVE CHANGE send an email about the record being declined
        from invenio_communities.proxies import current_communities
        from invenio_communities.members.records.api import Member
        from invenio_access.permissions import system_identity

        community = self.request.receiver.resolve()
        community_dict = current_communities.service.read(identity=system_identity, id_=community.id).to_dict()
        self_html = f'{community_dict["links"]["self_html"]}/requests/{self.request.id}'

        members = Member.get_members(community.id)
        # get owner, managers and curators. There should be an easier way
        recipients = [
            m.relations.user.dereference() for m in members
            if m.user_id
               and m.role in ["owner", "manager", "curator"]  #
        ]
        recipients = [r["email"] for r in recipients]

        body = f'The following record has been declined and is back in draft form.\n\nView submission: {self_html}'
        mail_data = {"recipients": recipients,
                     "body": body,
                     "subject": "MSD-LIVE Submission Declined",
                     "sender": "info@msdlive.org", "cc": ["info@msdlive.org"]}
        send_email.delay(mail_data)
        # MSDLIVE CHANGE END


class CancelAction(actions.CancelAction):
    """Decline action."""

    def execute(self, identity, uow):
        """Execute action."""
        # Remove draft from request
        # Same reasoning as in 'decline'
        draft = self.request.topic.resolve()
        draft.parent.review = None
        uow.register(RecordCommitOp(draft.parent))
        # update draft to reflect the new status
        uow.register(RecordIndexOp(draft, indexer=service.indexer))
        super().execute(identity, uow)


class ExpireAction(actions.CancelAction):
    """Expire action."""

    def execute(self, identity, uow):
        """Execute action."""
        # Same reasoning as in 'decline'
        draft = self.request.topic.resolve()

        # TODO: What more to do? simply close the request? Similarly to
        # decline, how does a user resubmits the request to the same community.
        super().execute(identity, uow)

        # TODO: this shouldn't be required BUT because of the caching mechanism
        # in the review systemfield, the review should be set with the updated
        # request object
        draft.parent.review = self.request
        uow.register(RecordCommitOp(draft.parent))
        # update draft to reflect the new status
        uow.register(RecordIndexOp(draft, indexer=service.indexer))


#
# Request
#
class CommunitySubmission(ReviewRequest):
    """Review request for submitting a record to a community."""

    type_id = "community-submission"
    name = _("Community submission")

    block_publish = True
    set_as_default = True

    creator_can_be_none = False
    topic_can_be_none = False
    allowed_creator_ref_types = ["user"]
    allowed_receiver_ref_types = ["community"]
    allowed_topic_ref_types = ["record"]
    needs_context = {
        "community_roles": ["owner", "manager", "curator"],
    }

    available_actions = {
        "create": actions.CreateAction,
        "submit": SubmitAction,
        "delete": actions.DeleteAction,
        "accept": AcceptAction,
        "cancel": CancelAction,
        "decline": DeclineAction,
        "expire": ExpireAction,
    }


# MSD-LIVE CHANGE schedule an email to be sent
from celery import shared_task
@shared_task(ignore_result=True)
def send_email(mail_data):
    """Construct and send email."""
    from invenio_mail.tasks import send_email
    send_email(mail_data)
