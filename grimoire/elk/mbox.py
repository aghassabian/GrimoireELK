#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
#
# Copyright (C) 2015 Bitergia
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.
#
# Authors:
#   Alvaro del Castillo San Felix <acs@bitergia.com>
#

import json
import logging
import requests

from dateutil import parser

from grimoire.elk.enrich import Enrich

from sortinghat import api

class MBoxEnrich(Enrich):

    def __init__(self, mbox, sortinghat=True, db_projects_map = None):
        super().__init__(sortinghat, db_projects_map)
        self.elastic = None
        self.perceval_backend = mbox
        self.index_mbox = "mbox"

    def set_elastic(self, elastic):
        self.elastic = elastic

    def get_field_date(self):
        return "metadata__updated_on"

    def get_field_unique_id(self):
        return "ocean-unique-id"

    def get_fields_uuid(self):
        return ["from_uuid"]

    def get_elastic_mappings(self):

        mapping = """
        {
            "properties": {
                "From": {
                  "type": "string",
                  "index":"not_analyzed"
                  },
                 "Delivered-To" : {
                   "type": "string",
                   "index":"not_analyzed"
                 },
                 "list" : {
                   "type": "string",
                   "index":"not_analyzed"
                 }
           }
        } """

        return {"items":mapping}

    def get_identities(self, item):
        """ Return the identities from an item """
        identities = []

        for identity in ['From']:
            if identity in item and item[identity]:
                user = self.get_sh_identity(item[identity])
                identities.append(user)
        return identities

    def get_sh_identity(self, from_data):
        # "From": "hwalsh at wikiledia.net (Heat Walsh)"

        identity = {}

        # First desofuscate the email
        EMAIL_OBFUSCATION_PATTERNS = [' at ', '_at_', ' en ']
        for pattern in EMAIL_OBFUSCATION_PATTERNS:
            if from_data.find(pattern) != -1:
                from_data = from_data.replace(pattern, '@')

        fields_from = from_data.split(" ",1)
        identity['username'] = None  # email does not have username
        identity['email'] = fields_from[0]
        identity['name'] = None
        if len(fields_from) == 2:
            # Name also included
            identity['name'] = fields_from[1].replace("(","").replace(")","")
        return identity

    def get_item_sh(self, item):
        """ Add sorting hat enrichment fields """
        eitem = {}  # Item enriched

        # Enrich SH
        identity  = self.get_sh_identity(item["From"])
        eitem["from_uuid"] = self.get_uuid(identity, self.get_connector_name())
        # bot
        u = api.unique_identities(self.sh_db, eitem["from_uuid"])[0]
        if u.profile:
            eitem["from_bot"] = u.profile.is_bot
        else:
            eitem["from_bot"] = 0  # By default, identities are not bots

        if identity['email']:
            try:
                eitem["domain"] = identity['email'].split("@")[1]
            except IndexError:
                logging.warning("Bad email format: %s" % (identity['email']))
                eitem["domain"] = None
        return eitem

    def get_item_project(self, item):
        """ Get project mapping enrichment field """
        # "origin": "dltk-commits"
        # /mnt/mailman_archives/dltk-dev.mbox/dltk-dev.mbox
        ds_name = "mls"  # data source name in projects map
        mls_list = item['__metadata__']['origin']
        path = "/mnt/mailman_archives/"
        path += mls_list+".mbox/"+mls_list+".mbox"

        try:
            project = (self.prjs_map[ds_name][path])
        except KeyError:
            # logging.warning("Project not found for list %s" % (mls_list))
            project = None
        return {"project": project}

    def get_rich_item(self, item):
        eitem = {}
        # Fields that are the same in item and eitem
        copy_fields = ["Date","Delivered-To","From","Subject","message-id","ocean-unique-id"]
        for f in copy_fields:
            if f in item:
                eitem[f] = item[f]
            else:
                eitem[f] = None
        # Fields which names are translated
        map_fields = {}
        for fn in map_fields:
            eitem[map_fields[fn]] = commit[fn]
        # Enrich dates
        eitem["email_date"] = parser.parse(item["metadata__updated_on"]).isoformat()
        eitem["list"] = item["__metadata__"]["origin"]

        if self.sortinghat:
            eitem.update(self.get_item_sh(item))

        if self.prjs_map:
            eitem.update(self.get_item_project(item))

        return eitem

    def enrich_items(self, items):
        max_items = self.elastic.max_items_bulk
        current = 0
        bulk_json = ""

        url = self.elastic.index_url+'/items/_bulk'

        logging.debug("Adding items to %s (in %i packs)" % (url, max_items))

        for item in items:
            if current >= max_items:
                requests.put(url, data=bulk_json)
                bulk_json = ""
                current = 0

            rich_item = self.get_rich_item(item)
            data_json = json.dumps(rich_item)
            bulk_json += '{"index" : {"_id" : "%s" } }\n' % \
                (rich_item[self.get_field_unique_id()])
            bulk_json += data_json +"\n"  # Bulk document
            current += 1
        try:
            requests.put(url, data = bulk_json)
        except UnicodeEncodeError:
            # Related to body.encode('iso-8859-1'). mbox data
            logging.error("Encoding error ... converting bulk to iso-8859-1")
            bulk_json = bulk_json.encode('iso-8859-1','ignore')
            requests.put(url, data=bulk_json)