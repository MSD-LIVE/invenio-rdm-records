# -*- coding: utf-8 -*-
#
# Copyright (C) 2021 CERN.
#
# Invenio-RDM-Records is free software; you can redistribute it and/or modify
# it under the terms of the MIT License; see LICENSE file for more details.

"""DataCite DOI Provider."""
import json
import warnings
import ostiapi
from flask import current_app
from invenio_records_resources.services.uow import RecordCommitOp, unit_of_work
from marshmallow_utils.html import strip_html

from invenio_rdm_records.resources.serializers import OSTIJSONSerializer
from invenio_pidstore.models import PIDStatus
from .base import PIDProvider


class OSTIClient:
    """OSTI Client."""

    def __init__(self, name, config_prefix=None, **kwargs):
        """Constructor."""
        self.name = name
        self._config_prefix = config_prefix or "OSTI"
        self._api = None

    def cfgkey(self, key):
        """Generate a configuration key."""
        return f"{self._config_prefix}_{key.upper()}"

    def cfg(self, key, default=None):
        """Get a application config value."""
        return current_app.config.get(self.cfgkey(key), default)

    # no need for this method for OSTI as DOI's are reserved via OSTI service API calls
    # def generate_doi(self, record):
    #     """Generate a DOI."""
    #     self.check_credentials()
    #     prefix = self.cfg('prefix')
    #     if not prefix:
    #         raise RuntimeError("Invalid DOI prefix configured.")
    #     doi_format = self.cfg('format', '{prefix}/{id}')
    #     if callable(doi_format):
    #         return doi_format(prefix, record)
    #     else:
    #         return doi_format.format(
    #             prefix=prefix,
    #             id=record.pid.pid_value
    #         )

    def check_credentials(self, **kwargs):
        """Returns if the client has the credentials properly set up.

        OSTI change - prefix has a different meaning now, not used as part of the DOI but as part of the accession_num
        """
        if not (self.cfg('username') and self.cfg('password')
                and self.cfg('accession_number_prefix')):
            warnings.warn(
                f"The {self.__class__.__name__} is misconfigured. Please "
                f"set {self.cfgkey('username')}, {self.cfgkey('password')}"
                f" and {self.cfgkey('accession_number_prefix')} in your configuration.",
                UserWarning
            )

    @property
    def api(self):
        """DataCite REST API client instance."""
        if self._api is None:
            self.check_credentials()
            self._api = ostiapi
            if self.cfg('test_mode'):
                self._api.testmode()

        return self._api


class OSTIPIDProvider(PIDProvider):
    """OSTI Provider class.

    Note that OSTI is only contacted when a DOI is reserved or
    registered, or any action posterior to it. PID creation requires
    contacting OSTI to reserve the DOI.
    """

    def __init__(
        self,
        id_,
        client=None,
        serializer=None,
        pid_type="doi",
        default_status=PIDStatus.NEW,
        **kwargs):
        """Constructor."""
        super().__init__(
            id_,
            client=(client or
                    OSTIClient("osti", config_prefix="OSTI")),
            pid_type=pid_type,
            default_status=default_status
        )
        self.serializer = serializer or OSTIJSONSerializer()
        self._config_prefix="OSTI"

    def cfgkey(self, key):
        """Generate a configuration key."""
        return f"{self._config_prefix}_{key.upper()}"

    def cfg(self, key, default=None):
        """Get a application config value."""
        return current_app.config.get(self.cfgkey(key), default)


    def generate_id(self, record, **kwargs):
        """Generate a unique DOI."""
        # This is called when user clicks button in UI to reserve a DOI for a draft

        # OSTI change: instead of generating the doi locally by combining the prefix and invenio's record's pid
        # call the osti python api to reserve the DOI. We can pick the accession_num that will be used later to update the
        # record in OSTI (like when it's published or when we need to update the metadata) so in order for accession_num
        # to uniquely identify the record in OSTI and in our system we combine record.pid.pid_value with the accession_number_prefix
        try:
            prefix = self.cfg('accession_number_prefix')
            username = self.cfg('username')
            password = self.cfg('password')
            # do NOT run the serializer dump_one as it will also validate the record and records do not need to have all required
            # fields entered before reserving a DOI (the dump_one code will throw an exception if the record isn't valid)
            # doc = self.serializer.dump_one(record)
            doc = {
                "title": "Placeholder Title"
            }
            if record.get('metadata').get('title'):
                doc['title']=record.get('metadata').get('title')
            doc['accession_num'] = f"{prefix}-{record.pid.pid_value}"
            doc['contract_nos'] = f"{self.cfg('contract_nos')}"
            doc['sponsor_org'] = f"{self.cfg('sponsor_org')}"
            current_app.logger.info("doc being sent to OSTI: " f"{doc}")
            osti_record = self.client.api.reserve(
                doc,
                username, password)
            current_app.logger.info("osti record returned from reserve" f"{osti_record}")
            error = self.parse_osti_error(osti_record)
            if error:
                current_app.logger.error("OSTI returned ERROR status with message " f"{error}" " " f"full record: {osti_record}")
                return False

            current_app.logger.info("DOI: " f"{osti_record.get('record').get('doi')}") #record.get('metadata').set
            return osti_record.get('record').get('doi')
        except Exception as e:
            current_app.logger.error("OSTI provider error when "
                                       f"reserving a DOI for record {record.pid.pid_value}")
            current_app.logger.error(e)
            return False

    def parse_osti_error(self, osti_record):
        # TODO add more logic to catch case when OSTI's validation fails (i.e. too long of an abstract)
        if osti_record.get("status") == "FAILURE":
            return osti_record.get("status_message")

    def can_modify(self, pid, **kwargs):
        """Checks if the PID can be modified."""
        return not pid.is_registered() and not pid.is_reserved()

    def register(self, pid, record, url=None, **kwargs):
        """Register a DOI via the OSTI API.

        :param pid: the PID to register.
        :param record: the record metadata for the DOI.
        :returns: `True` if is registered successfully.
        """
        # This is what is called when the record is published and the DOI needs to be minted
        local_success = super().register(pid)
        if not local_success:
            return False

        try:
            doc = self._corrected_dump_one(record)
            username = self.cfg('username')
            password = self.cfg('password')
            prefix = self.cfg('accession_number_prefix')

            # first generate the accession_num (A site-specified unique identifier to optionally identify the record) for this record
            # in the same way as was done in the generate_id method above and add it to the doc that the serializer returns
            # we need to pass this so OSTI will mint the already created DOI instead of generating a new one
            # always generate and send the accession_num to use with future calls to OSTI's api for this record (i.e. to mint or update an already minted):
            doc['accession_num'] = f"{prefix}-{record.pid.pid_value}"
            doc['contract_nos'] = f"{self.cfg('contract_nos')}"
            doc['sponsor_org'] = f"{self.cfg('sponsor_org')}"
            doc['site_url'] = url

            current_app.logger.debug("doc sent to OSTI " f"{doc}")

            osti_record = self.client.api.post(doc, username, password)
            error = self.parse_osti_error(osti_record)
            if error:
                self.persist_minting_error(record, error)
                current_app.logger.error(f"OSTI returned ERROR status with message: {error}, OSTI record returned: {osti_record}")
                return False

            current_app.logger.debug("osti DOI minted and returned " f"{osti_record}")
            return True
        except Exception as e:
            self.persist_minting_error(record, str(e))
            current_app.logger.error(f"OSTI provider error when registering DOI for {pid.pid_value}", exc_info=True)
            return False

    @unit_of_work()
    def persist_minting_error(self, record, error, uow=None):
        record.get('metadata').update({'msdlive_doi_minting_error': error})
        uow.register(RecordCommitOp(record))

    def update(self, pid, record=None, **kwargs):
        """Update metadata associated with a DOI.

        This can be called before/after a DOI is registered.
        :param pid: the PID to register.
        :param record: the record metadata for the DOI.
        :returns: `True` if is updated successfully.
        """
        # pid providers' update method only called when a record that has already been published is 'published' again.
        # In the UI it goes like this: publish a record. click the edit button and a new version is created in draft form.
        # update that draft as many times as you'd like (this update method NOT called) but once the updates are done the publish
        # button is clicked on the new version's draft in the UI and only THEN is this update method is called.
        try:
            # Set metadata
            prefix = self.cfg('accession_number_prefix')
            username = self.cfg('username')
            password = self.cfg('password')
            doc = self._corrected_dump_one(record)
            doc['contract_nos'] = f"{self.cfg('contract_nos')}"
            doc['sponsor_org'] = f"{self.cfg('sponsor_org')}"
            doc['accession_num'] = f"{prefix}-{record.pid.pid_value}"
            doc['site_url'] = kwargs.get('url')

            current_app.logger.info("doc sent to OSTI " f"{doc}")
            osti_record = self.client.api.post(doc, username, password)
            error = self.parse_osti_error(osti_record)
            if error:
                current_app.logger.error("OSTI returned ERROR status with message " f"{error}" " " f"full record: {osti_record}")
                return False

            current_app.logger.info("osti record returned from reserve" f"{json.dumps(osti_record)}")
            return True
        except Exception as e:
            current_app.logger.error("DataCite provider error when "
                                       f"updating DOI for {pid.pid_value}")
            current_app.logger.error(e)

            return False


    def delete(self, pid, **kwargs):
        """Delete/unregister a registered DOI.

        If the PID has not been reserved then it's deleted only locally.
        Otherwise, also it's deleted also remotely.
        :returns: `True` if is deleted successfully.
        """

        # according to OSTI docs there is no support for delete in either draft or published form
        current_app.logger.warning("There is no delete api for OSTI provider, this DOI will remain in draft form at OSTI:"
                                   f" {pid.pid_value}")

        return super().delete(pid, **kwargs)

    def validate(
        self, record, identifier=None, provider=None, **kwargs
    ):
        """Validate the attributes of the identifier.

        :returns: A tuple (success, errors). The first specifies if the
                  validation was passed successfully. The second one is an
                  array of error messages.
        """
        _, errors = super().validate(record, identifier, provider, **kwargs)

        return (True, []) if not errors else (False, errors)

    def _corrected_dump_one(self, record):
        """-------------------------------------------------------------------------------------------------------------
        RDM's marshmallow serializer munges all of the description fields into a single description, which can fail
        for OSTI because it has a 12000 character limit!

        Example dump_one results:
        {'title': 'Carina Test DOI 3',
         'description': 'This is a description. This is an abstract. This is teh methods. ',
         'dataset_type': 'SM',
         'keywords': 'test;',
         'publication_date': '03/14/2023',
         'authors': [{'last_name': 'Lansing', 'first_name': 'Carina'}],
         'accession_num': 'MSDLIVE-tx6gn-71y72',
         'contract_nos': '80478',
         'sponsor_org': 'USDOE Office of Science (SC), Biological and Environmental Research (BER)',
         'site_url': 'http://127.0.0.1/doi/10.11578/1529383'}

         Example record contents:
        {'id': 'tx6gn-71y72',
         'pid': {'pk': 15077, 'status': 'R', 'obj_type': 'rec', 'pid_type': 'recid'},
         'pids': {'doi': {'client': 'osti',
           'provider': 'osti',
           'identifier': '10.11578/1529383'}},
         'files': {'enabled': False},
         'access': {'files': 'public',
          'record': 'public',
          'embargo': {'until': None, 'active': False, 'reason': None}},
         '$schema': 'local://records/record-v5.0.0.json',
         'metadata': {'title': 'Carina Test DOI 3',
          'rights': [{'id': 'CC-BY-4.0'}],
          'version': 'v1',
          'creators': [{'person_or_org': {'name': 'Lansing, Carina',
             'type': 'personal',
             'given_name': 'Carina',
             'family_name': 'Lansing'}}],
          'subjects': [{'subject': 'test'}],
          'publisher': 'MSD-LIVE Data Repository',
          'description': '<p>This is a description.</p>',
          'resource_type': {'id': 'publication'},
          'msdlive_projects': [{'id': 'e9b4b8b1-6f1f-45f5-b2db-69e5255a8526',
            'name': 'State.'}],
          'publication_date': '2023-03-14',
          'additional_descriptions': [{'type': {'id': 'abstract'},
            'description': '<p>This is an abstract.</p>'},
           {'type': {'id': 'methods'}, 'description': '<p>This is teh methods.</p>'}]}}


        :param record:
        :return:
        -------------------------------------------------------------------------------------------------------------"""
        doc = self.serializer.dump_one(record)
        metadata = record.get('metadata')

        # First see if the record has an abstract.  If it does, use that for the description.
        abstract = None
        additional_descriptions = metadata.get('additional_descriptions', [])
        for desc in additional_descriptions:
            type = desc.get('type', {}).get('id')
            if type == 'abstract':
                abstract = desc.get('description')

        abstract = metadata.get('description') if not abstract else abstract

        # Strip off html tags
        if abstract:
            abstract = strip_html(abstract)
            # OSTI will throw an error if abstract is longer than 12000 characters
            abstract = abstract[:12000]
            doc['description'] = abstract

        return doc

    def _get_dummy_metadata(self, contract_nos, sponsor_org):
        return {
              "title": "My upcoming dataset",
              "dataset_type": "IP",
              "contract_nos": f"{contract_nos}",
              "sponsor_org": f"{sponsor_org}",
              "site_url": "https://sbrsfa.velo.pnnl.gov/datasets/?UUID=d2f86d79-d582-4dea-929b-eefe4ab34052#metadata2",
              "publication_date": "06/01/2022",
              "authors": [
                {
                  "first_name": "Neal",
                  "last_name": "Ensor",
                  "affiliation_name": "DOE OSTI",
                  "private_email": "ensorn@osti.gov",
                  "orcid_id": "0000-0001-5166-5705"
                }
              ]
            }
