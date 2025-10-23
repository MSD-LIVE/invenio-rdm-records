# -*- coding: utf-8 -*-
#
# Copyright (C) 2021 CERN.
#
# Invenio-RDM-Records is free software; you can redistribute it and/or modify
# it under the terms of the MIT License; see LICENSE file for more details.

"""DataCite DOI Provider."""

import warnings
from datetime import datetime

from elinkapi import Elink
from elinkapi.affiliation import Affiliation
from elinkapi.query import Query
from elinkapi.record import Identifier, Organization, Person, ProductType, Record
from flask import current_app
from invenio_pidstore.models import PIDStatus
from invenio_records_resources.services.uow import RecordCommitOp, unit_of_work
from marshmallow_utils.html import strip_html

from invenio_rdm_records.resources.serializers import OSTIJSONSerializer

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

    def check_credentials(self, **kwargs):
        """Returns if the client has the credentials properly set up.

        OSTI change - prefix has a different meaning now, not used as part of the DOI but as part of the accession_num
        """
        # TODO: don't need accession_number_prefix anymore with new api
        if not (self.cfg("api_token") and self.cfg("accession_number_prefix")):
            warnings.warn(
                f"The {self.__class__.__name__} is misconfigured. Please "
                f"set {self.cfgkey('api_token')} and {self.cfgkey('accession_number_prefix')} "
                f"in your configuration.",
                UserWarning,
            )

    @property
    def api(self):
        """OSTI E-Link API client instance."""
        if self._api is None:
            self.check_credentials()
            # Initialize Elink client
            self._api = Elink()

            # Set API token
            api_token = self.cfg("api_token")
            if api_token:
                self._api.set_api_token(api_token)

            # Set target URL based on test mode
            if self.cfg("test_mode"):
                self._api.set_target_url(
                    "https://review.osti.gov/elink2api/"
                )  # Test URL
            else:
                self._api.set_target_url(
                    "https://www.osti.gov/elink2api/"
                )  # Production URL

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
        **kwargs,
    ):
        """Constructor."""
        super().__init__(
            id_,
            client=(client or OSTIClient("osti", config_prefix="OSTI")),
            pid_type=pid_type,
            default_status=default_status,
        )
        self.serializer = serializer or OSTIJSONSerializer()
        self._config_prefix = "OSTI"

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
        # call the osti python api to reserve the DOI. We can pick the site_unique_id that will be used later to update the
        # record in OSTI (like when it's published or when we need to update the metadata) so in order for site_unique_id
        # to uniquely identify the record in OSTI and in our system we combine record.pid.pid_value with the accession_number_prefix
        try:
            prefix = self.cfg("accession_number_prefix")

            # Create a minimal record for DOI reservation
            title = "Placeholder Title"
            if record.get("metadata").get("title"):
                title = record.get("metadata").get("title")

            elink_record = Record(
                title=title,
                # site_ownership_code=prefix, #Zoe commented this out and added line below, I think AI had this wrong
                site_ownership_code="MSD-LIVE",
                product_type=ProductType.Dataset.value,  # Default to Dataset type
                site_unique_id=f"{prefix}-{record.pid.pid_value}",  # Docs say: Site-specified unique accession number for this record
            )

            current_app.logger.info(
                f"Record being sent to OSTI for DOI reservation: {elink_record}"
            )

            # Reserve the DOI
            osti_record = self.client.api.reserve_doi(elink_record)

            current_app.logger.info(
                f"OSTI record returned from reserve_doi: {osti_record}"
            )

            # Check for errors
            if not osti_record or not osti_record.doi:
                current_app.logger.error(
                    "OSTI returned ERROR status when reserving a DOI"
                )
                return False

            current_app.logger.info(f"DOI: {osti_record.doi}")
            return osti_record.doi

        except Exception as e:
            current_app.logger.error(
                "OSTI provider error when "
                f"reserving a DOI for record {record.pid.pid_value}"
            )
            current_app.logger.error(e)
            return False

    def parse_osti_error(self, osti_record):
        """Parse error from OSTI response.

        The new elinkapi library returns objects rather than dictionaries,
        so we need to handle errors differently.
        """
        # If the record is None or doesn't have a doi, it's an error
        if not osti_record or not hasattr(osti_record, "doi") or not osti_record.doi:
            return "Failed to get a valid response from OSTI"

        return None

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
            # Get serialized data from the current serializer
            doc = self._corrected_dump_one(record)
            prefix = self.cfg("accession_number_prefix")

            # Create an OSTI record
            elink_record = Record(
                title=doc.get("title"),
                description=doc.get("description"),
                product_type=ProductType.Dataset.value,
                site_ownership_code="MSD-LIVE",
                site_unique_id=f"{prefix}-{record.pid.pid_value}",
                site_url=url,
                access_limitations=["UNL"],
                released_to_osti_date=datetime.now().date(),
            )

            # Add authors if available
            if "authors" in doc and doc["authors"]:
                persons = []
                for author in doc["authors"]:
                    person = Person(
                        type="AUTHOR",
                        first_name=author.get("first_name", ""),
                        last_name=author.get("last_name", ""),
                    )
                    # Add affiliation if available
                    if "affiliation_name" in author:
                        person.affiliations = [
                            Affiliation(name=author.get("affiliation_name"))
                        ]
                    # Add ORCID if available
                    if "orcid_id" in author:
                        person.orcid = author.get("orcid_id")
                    persons.append(person)
                elink_record.persons = persons

            # Add keywords if available
            if "keywords" in doc and doc["keywords"]:
                keywords = doc["keywords"].split(";")
                # Remove empty strings
                keywords = [k.strip() for k in keywords if k.strip()]
                if keywords:
                    elink_record.keywords = keywords

            # Add publication date if available
            if "publication_date" in doc:
                try:
                    # Convert from MM/DD/YYYY to datetime.date
                    date_parts = doc["publication_date"].split("/")
                    if len(date_parts) == 3:
                        month, day, year = map(int, date_parts)
                        elink_record.publication_date = datetime(
                            year, month, day
                        ).date()
                except Exception as e:
                    current_app.logger.warning(f"Could not parse publication date: {e}")

            # Add organizations
            # NOTE: OSTI requires at least one RESEARCHING organization, and ONE SPONSOR org
            # and the SPONSOR org must also have a contract number with it
            # or it throws an error
            elink_record.organizations = [
                Organization(
                    type="RESEARCHING",
                    name="Pacific Northwest National Lab (United States)",
                ),
                Organization(
                    type="SPONSOR", 
                    name="USDOE Office of Science (SC), Biological and Environmental Research (BER)",
                    identifiers=[Identifier(type="CN_DOE", value="AC05-76RL01830")],
                ),
            ]
            elink_record.identifiers = [
                Identifier(type="CN_DOE", value="AC05-76RL01830")
            ]

            current_app.logger.debug(f"Record being sent to OSTI: {elink_record}")

            # Look up the osti_id for our dataset
            doi = pid.pid_value
            query: Query = self.client.api.query_records(doi=doi)
            records = query.data

            if len(records) == 0:
                raise Exception(f"Could not find OSTI reserved DOI {doi}")

            osti_id = records[0].osti_id

            # Register the DOI
            osti_record = self.client.api.update_record(
                osti_id, elink_record, state="submit"
            )

            if not osti_record or not osti_record.doi:
                error = "Failed to register DOI with OSTI"
                self.persist_minting_error(record, error)
                current_app.logger.error(error)
                return False

            current_app.logger.debug(f"OSTI DOI minted and returned: {osti_record.doi}")
            return True
        except Exception as e:
            self.persist_minting_error(record, str(e))
            current_app.logger.error(
                f"OSTI provider error when registering DOI for {pid.pid_value}",
                exc_info=True,
            )
            return False

    @unit_of_work()
    def persist_minting_error(self, record, error, uow=None):
        record.get("metadata").update({"msdlive_doi_minting_error": error})
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
            # Get serialized data from the current serializer
            doc = self._corrected_dump_one(record)
            prefix = self.cfg("accession_number_prefix")

            # Create an OSTI record.  Apparently when you update, it replaces the entire
            # record, so you have to have all the same fields as when you registered it.
            elink_record = Record(
                title=doc.get("title"),
                description=doc.get("description"),
                product_type=ProductType.Dataset.value,
                site_ownership_code="MSD-LIVE",
                access_limitations=["UNL"],
                site_unique_id=f"{prefix}-{record.pid.pid_value}",
                site_url=kwargs.get("url"),
            )

            # Add authors if available
            if "authors" in doc and doc["authors"]:
                persons = []
                for author in doc["authors"]:
                    person = Person(
                        type="AUTHOR",
                        first_name=author.get("first_name", ""),
                        last_name=author.get("last_name", ""),
                    )
                    # Add affiliation if available
                    if "affiliation_name" in author:
                        person.affiliations = [
                            Affiliation(name=author.get("affiliation_name"))
                        ]
                    # Add ORCID if available
                    if "orcid_id" in author:
                        person.orcid = author.get("orcid_id")
                    persons.append(person)
                elink_record.persons = persons

            # Add keywords if available
            if "keywords" in doc and doc["keywords"]:
                keywords = doc["keywords"].split(";")
                # Remove empty strings
                keywords = [k.strip() for k in keywords if k.strip()]
                if keywords:
                    elink_record.keywords = keywords

            # Add publication date if available
            if "publication_date" in doc:
                try:
                    # Convert from MM/DD/YYYY to datetime.date
                    date_parts = doc["publication_date"].split("/")
                    if len(date_parts) == 3:
                        month, day, year = map(int, date_parts)
                        elink_record.publication_date = datetime(
                            year, month, day
                        ).date()
                except Exception as e:
                    current_app.logger.warning(f"Could not parse publication date: {e}")

            # Add organizations
            # NOTE: OSTI requires at least one RESEARCHING organization, and ONE SPONSOR org
            # and the SPONSOR org must also have a contract number with it
            # or it throws an error
            elink_record.organizations = [
                Organization(
                    type="RESEARCHING",
                    name="Pacific Northwest National Lab (United States)",
                ),
                Organization(
                    type="SPONSOR", 
                    name="USDOE Office of Science (SC), Biological and Environmental Research (BER)",
                    identifiers=[Identifier(type="CN_DOE", value="AC05-76RL01830")],
                ),
            ]
            elink_record.identifiers = [
                Identifier(type="CN_DOE", value="AC05-76RL01830")
            ]

            current_app.logger.debug(f"Record being sent to OSTI: {elink_record}")
            # Get the OSTI ID from the DOI
            osti_id = pid.pid_value

            # Look up the osti_id for our dataset
            doi = pid.pid_value
            query: Query = self.client.api.query_records(doi=doi)
            records = query.data

            if len(records) == 0:
                raise Exception(f"Could not find OSTI reserved DOI {doi}")

            osti_id = records[0].osti_id

            # Update the DOI
            osti_record = self.client.api.update_record(
                osti_id, elink_record, state="submit"
            )

            if not osti_record:
                current_app.logger.error("OSTI returned ERROR status when updating DOI")
                return False

            current_app.logger.info(f"DOI updated: {osti_record.doi}")
            return True

        except Exception:
            current_app.logger.error(
                f"DataCite provider error when updating DOI for {pid.pid_value}",
                exc_info=True,
            )

            return False

    def delete(self, pid, **kwargs):
        """Delete/unregister a registered DOI.

        If the PID has not been reserved then it's deleted only locally.
        Otherwise, also it's deleted also remotely.
        :returns: `True` if is deleted successfully.
        """

        # according to OSTI docs there is no support for delete in either draft or published form
        current_app.logger.warning(
            "There is no delete api for OSTI provider, this DOI will remain in draft form at OSTI:"
            f" {pid.pid_value}"
        )

        return super().delete(pid, **kwargs)

    def validate(self, record, identifier=None, provider=None, **kwargs):
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
        metadata = record.get("metadata")

        # First see if the record has an abstract.  If it does, use that for the description.
        abstract = None
        additional_descriptions = metadata.get("additional_descriptions", [])
        for desc in additional_descriptions:
            type = desc.get("type", {}).get("id")
            if type == "abstract":
                abstract = desc.get("description")

        abstract = metadata.get("description") if not abstract else abstract

        # Strip off html tags
        if abstract:
            abstract = strip_html(abstract)
            # OSTI will throw an error if abstract is longer than 12000 characters
            abstract = abstract[:12000]
            doc["description"] = abstract
        else:
            doc["description"] = "No description provided by author."

        return doc

    def _get_dummy_metadata(self, prefix):
        """Create a dummy record for testing."""
        site_ownership_code = self.cfg("site_ownership_code")
        # Create a person
        person = Person(
            type="AUTHOR",
            first_name="Neal",
            last_name="Ensor",
            email=["ensorn@osti.gov"],
            orcid="0000-0001-5166-5705",
            contributor_type="Researcher",
        )

        # Create a record
        record = Record(
            title="My upcoming dataset",
            product_type=ProductType.Dataset.value,
            site_ownership_code=site_ownership_code,
            site_unique_id=f"{prefix}-test-123",
            site_url="https://sbrsfa.velo.pnnl.gov/datasets/?UUID=d2f86d79-d582-4dea-929b-eefe4ab34052#metadata2",
            publication_date=datetime(2022, 6, 1).date(),
            persons=[person],
        )

        return record
