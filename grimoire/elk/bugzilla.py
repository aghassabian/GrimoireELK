#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
# Bugzilla to Elastic class helper
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

from time import time
from dateutil import parser
import json
import logging
import requests
from urllib.parse import urlparse

from .enrich import Enrich

from .utils import get_time_diff_days

class BugzillaEnrich(Enrich):

    def __init__(self, bugzilla):
        super().__init__()
        self.perceval_backend = bugzilla
        self.elastic = None

    def set_elastic(self, elastic):
        self.elastic = elastic

    def get_field_date(self):
        return "delta_ts"

    def get_fields_uuid(self):
        return ["assigned_to_uuid", "reporter_uuid"]

    @classmethod
    def get_sh_identity(cls, user):
        """ Return a Sorting Hat identity using bugzilla user data """

        def fill_list_identity(identity, user_list_data):
            """ Fill identity with user data in first item in list """
            identity['username'] = user_list_data[0]['__text__']
            if 'name' in user_list_data[0]:
                identity['name'] = user_list_data[0]['name']

        identity = {}
        for field in ['name', 'email', 'username']:
            # Basic fields in Sorting Hat
            identity[field] = None
        if 'reporter' in user:
            fill_list_identity(identity, user['reporter'])
        if 'assigned_to' in user:
            fill_list_identity(identity, user['assigned_to'])
        if 'who' in user:
            fill_list_identity(identity, user['who'])
        if 'Who' in user:
            identity['username'] = user['Who']
        if 'qa_contact' in user:
            fill_list_identity(identity, user['qa_contact'])
        if 'changed_by' in user: 
            identity['name'] = user['changed_by']

        return identity


    def get_identities(self, item):
        ''' Return the identities from an item '''

        identities = []

        if 'activity' in item:
            for event in item['activity']:
                identities.append(self.get_sh_identity(event))
        if 'long_desc' in item:
            for comment in item['long_desc']:
                identities.append(self.get_sh_identity(comment))
        elif 'assigned_to' in item:
            identities.append(self.get_sh_identity({'assigned_to':
                                                    item['assigned_to']}))
        elif 'reporter' in item:
            identities.append(self.get_sh_identity({'reporter':
                                                    item['reporter']}))
        elif 'qa_contact' in item:
            identities.append(self.get_sh_identity({'qa_contact':
                                                    item['qa_contact']}))


        return identities

    def enrich_issue(self, issue):

        def get_bugzilla_url():
            u = urlparse(self.perceval_backend.url)
            return u.scheme+"//"+u.netloc

        # Fix dates
        date_ts = parser.parse(issue['creation_ts'][0]['__text__'])
        issue['creation_ts'] = date_ts.strftime('%Y-%m-%dT%H:%M:%S')
        date_ts = parser.parse(issue['delta_ts'][0]['__text__'])
        issue['delta_ts'] = date_ts.strftime('%Y-%m-%dT%H:%M:%S')

        # Add extra JSON fields used in Kibana (enriched fields)
        issue['number_of_comments'] = 0
        issue['time_to_last_update_days'] = None
        issue['url'] = None

        issue['number_of_comments'] = len(issue['long_desc'])
        issue['url'] = get_bugzilla_url() + "show_bug.cgi?id=" + issue['bug_id'][0]['__text__']
        issue['time_to_last_update_days'] = \
            get_time_diff_days(issue['creation_ts'], issue['delta_ts'])

        # Sorting Hat integration: reporter and assigned_to uuids
        if 'assigned_to' in issue:
            identity = BugzillaEnrich.get_sh_identity({'assigned_to':issue['assigned_to']})
            issue['assigned_to_uuid'] = self.get_uuid(identity, self.get_connector_name())
        if 'reporter' in issue:
            identity = BugzillaEnrich.get_sh_identity({'reporter':issue['reporter']})
            issue['reporter_uuid'] = self.get_uuid(identity, self.get_connector_name())

        return issue


    def enrich_items(self, items):
#         if self.perceval_backend.detail == "list":
#             self.issues_list_to_es(items)
#         else:
#             self.issues_to_es(items)
        self.issues_to_es(items)


    def issues_list_to_es(self, items):

        elastic_type = "issues_list"

        max_items = self.elastic.max_items_bulk
        current = 0
        total = 0
        bulk_json = ""

        url = self.elastic.index_url+'/' + elastic_type + '/_bulk'

        logging.debug("Adding items to %s (in %i packs)" % (url, max_items))

        # In this client, we will publish all data in Elastic Search
        for issue in items:
            if current >= max_items:
                task_init = time()
                requests.put(url, data=bulk_json)
                bulk_json = ""
                total += current
                current = 0
                logging.debug("bulk packet sent (%.2f sec, %i total)"
                              % (time()-task_init, total))
            data_json = json.dumps(issue)
            bulk_json += '{"index" : {"_id" : "%s" } }\n' % (issue["bug_id"])
            bulk_json += data_json +"\n"  # Bulk document
            current += 1
        task_init = time()
        total += current
        requests.put(url, data=bulk_json)
        logging.debug("bulk packet sent (%.2f sec, %i total)"
                      % (time()-task_init, total))


    def issues_to_es(self, items):

        elastic_type = "issues"

        max_items = self.elastic.max_items_bulk
        current = 0
        bulk_json = ""

        url = self.elastic.index_url+'/' + elastic_type + '/_bulk'

        logging.debug("Adding items to %s (in %i packs)" % (url, max_items))

        for issue in items:
            if current >= max_items:
                requests.put(url, data=bulk_json)
                bulk_json = ""
                current = 0
            self.enrich_issue(issue)
            data_json = json.dumps(issue)
            bulk_json += '{"index" : {"_id" : "%s" } }\n' % (issue["bug_id"])
            bulk_json += data_json +"\n"  # Bulk document
            current += 1
        requests.put(url, data=bulk_json)

        logging.debug("Adding issues to ES Done")


    def get_elastic_mappings(self):
        ''' Specific mappings needed for ES '''

        mapping = '''
        {
            "properties": {
               "product": {
                  "type": "string",
                  "index":"not_analyzed"
               },
               "component": {
                  "type": "string",
                  "index":"not_analyzed"
               },
               "assigned_to": {
                  "type": "string",
                  "index":"not_analyzed"
               }
            }
        }
        '''

        return {"items":mapping}
