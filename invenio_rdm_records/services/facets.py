# -*- coding: utf-8 -*-
#
# Copyright (C) 2020-2021 CERN.
# Copyright (C) 2020-2021 Northwestern University.
# Copyright (C)      2021 TU Wien.
#
# Invenio-RDM-Records is free software; you can redistribute it and/or modify
# it under the terms of the MIT License; see LICENSE file for more details.

"""Facet definitions."""

from flask_babelex import gettext as _
from invenio_records_resources.services.records.facets import (
    NestedTermsFacet,
    TermsFacet,
)
from invenio_vocabularies.contrib.subjects import SubjectsLabels
from invenio_vocabularies.services.facets import VocabularyLabels

from ..records.systemfields.access.field.record import AccessStatusEnum

# MSDLIVE CHANGE IN GENERAL - changed facet labels to have all words init upper cased
access_status = TermsFacet(
    field="access.status",
    label=_("Access Status"),
    value_labels={
        AccessStatusEnum.OPEN.value: _("Open"),
        AccessStatusEnum.EMBARGOED.value: _("Embargoed"),
        AccessStatusEnum.RESTRICTED.value: _("Restricted"),
        AccessStatusEnum.METADATA_ONLY.value: _("Metadata-only"),
    },
)


is_published = TermsFacet(
    field="is_published",
    label=_("Status"),
    value_labels={"true": _("Published"), "false": _("Unpublished")},
)


language = TermsFacet(
    field="metadata.languages.id",
    label=_("Languages"),
    value_labels=VocabularyLabels("languages"),
)


resource_type = NestedTermsFacet(
    field="metadata.resource_type.props.type",
    subfield="metadata.resource_type.props.subtype",
    splitchar="::",
    label=_("Resource Types"),
    value_labels=VocabularyLabels("resourcetypes"),
)


subject_nested = NestedTermsFacet(
    field="metadata.subjects.scheme",
    subfield="metadata.subjects.subject.keyword",
    label=_("Subjects"),
    value_labels=SubjectsLabels(),
)

#
# MSDLIVE CHANGE BEGIN - renamed subjects facet to Keywords and adding custom metadata
#
subject = TermsFacet(
    field='metadata.subjects.subject.keyword',
    label=_('Keywords'),
)

msdlive_project = TermsFacet(
    field='metadata.msdlive_projects.name.keyword',
    label=_('Project'),
)

msdlive_sector = TermsFacet(
    field='metadata.msdlive_sectors.sector.keyword',
    label=_('Sectors and Systems'),
)

msdlive_scenario = TermsFacet(
    field='metadata.msdlive_scenarios.scenario.keyword',
    label=_('Scenario'),
)

msdlive_temporal = TermsFacet(
    field='metadata.msdlive_temporals.resolution.keyword',
    label=_('Temporal Resolution'),
)

msdlive_spatial = TermsFacet(
    field='metadata.msdlive_spatials.resolution.keyword',
    label=_('Spatial Structure'),
)

msdlive_model = TermsFacet(
    field='metadata.msdlive_models.model.keyword',
    label=_('Model'),
)


# Add facets for how we've replaced RDM's concept of metadata-only
# Open, Metadata only, Restricted, Partially Restricted, Embargoed
file_status = TermsFacet(
    field='metadata.msdlive_file_location.location_type.keyword',
    label='File Status',
    value_labels={
        'local': 'In MSD-LIVE',
        'external': 'Metadata-only',
    },
)

#
# MSDLIVE CHANGE END
#
